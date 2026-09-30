# -*- coding: utf-8 -*-
"""仲裁合并 v5：view1（API松框+价）与 view2（本地框+价）按相对顺序对齐去重，
替代 v4 的 IoU 去重。对比 v4 vs v5（同一批已存响应，0 API 调用）。"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import rail_first_experiment as R
import arbitration_loop as AL
from sequence_alignment import align

EXP = HERE / "API端点导轨实验"
OUT = HERE / "仲裁闭环实验"


def evaluate(entries, user_tags):
    preds = [e["bbox"] for e in entries]
    gts = [t["bbox"] for t in user_tags]
    matches = R.match_greedy(preds, gts, 0.5)
    price_ok = sum(1 for m in matches
                   if entries[m["pred"]]["price"]
                   and AL.norm_price(entries[m["pred"]]["price"])
                   == AL.norm_price(user_tags[m["gt"]]["label"]))
    # 重复：同一 GT 被两个以上输出框覆盖（IoU≥0.3 计）
    per_gt = {}
    for m in R.match_greedy(preds, gts, 0.3):
        per_gt[m["gt"]] = per_gt.get(m["gt"], 0) + 1
    dups = sum(1 for v in per_gt.values() if v >= 2)
    return {"preds": len(preds), "m30": len(R.match_greedy(preds, gts, 0.3)),
            "m50": len(matches), "price_ok": price_ok, "dup_tags": dups}


def main():
    stems = sorted(p.stem for p in (HERE / "新数据").glob("*.json"))
    report = []
    tv4 = {"preds": 0, "m30": 0, "m50": 0, "price_ok": 0, "dup_tags": 0}
    tv5 = dict(tv4)
    for stem in stems:
        img_dir = OUT / stem
        full_c = img_dir / "full_content.txt"
        if not full_c.exists():
            continue
        img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                         if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
        image = R.read_image(img_path)
        H, W = image.shape[:2]
        user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")
        corridors, _ = AL.build_corridors(stem, image)

        view1 = AL.parse_full_image(full_c.read_text(encoding="utf-8"), W, H)
        view1 = [it for it in view1 if it["price"] is not None]

        # 收集各走廊的 view2 与其中的 view1
        corridors_view2 = {}
        corridors_view1 = {}
        for nf in sorted(img_dir.glob("num*_content.txt")):
            ri = int(nf.stem.split("_")[0].replace("num", ""))
            if ri >= len(corridors):
                continue
            rail = corridors[ri]
            crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
            boxes_crop, _, _ = R.detect_tags_in_corridor(crop, ch)
            readings = AL.parse_numbered(nf.read_text(encoding="utf-8"), len(boxes_crop))
            v2 = []
            for idx, box_crop in enumerate(boxes_crop, 1):
                r = readings.get(idx, {})
                price = AL.norm_price(r.get("price")) if r.get("readable") is True else None
                if not price:
                    continue
                corners = np.array([[box_crop[0], box_crop[1]], [box_crop[2], box_crop[1]],
                                    [box_crop[2], box_crop[3]], [box_crop[0], box_crop[3]]],
                                   dtype=np.float64)
                mapped = np.append(corners, np.ones((4, 1)), axis=1) @ M_inv.T
                bbox = [mapped[:, 0].min(), mapped[:, 1].min(),
                        mapped[:, 0].max(), mapped[:, 1].max()]
                v2.append({"bbox": bbox, "price": price,
                           "cx": (bbox[0] + bbox[2]) / 2})
            if not v2:
                continue
            corridors_view2[ri] = v2
            corridors_view1[ri] = [it for it in view1
                                   if AL.center_in(it["bbox"], rail)]
            # 从 view1 中移除已入走廊的（避免最终重复添加）
            in_ri = set(id(it) for it in corridors_view1[ri])
            view1 = [it for it in view1 if id(it) not in in_ri]

        # v4：IoU 去重合并
        def merge_v4():
            final = []
            for ri, v2 in corridors_view2.items():
                for it in v2:
                    final.append({"bbox": it["bbox"], "price": it["price"], "src": "view2"})
            v1_rest = [it for items in corridors_view1.values() for it in items] + view1
            for it in v1_rest:
                overl = [v for items in corridors_view2.values() for v in items
                         if R.iou(it["bbox"], v["bbox"]) >= 0.3]
                if not overl:
                    final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
                    continue
                if any(v["price"] == it["price"] for v in overl):
                    final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
            return final

        # v5：顺序对齐合并（每个走廊内 view2 为锚，view1 为序列）
        def merge_v5():
            final = []
            for ri, v2 in corridors_view2.items():
                for it in v2:
                    final.append({"bbox": it["bbox"], "price": it["price"], "src": "view2"})
                v1_seq = sorted(corridors_view1.get(ri, []), key=lambda it: it["bbox"][0])
                anchors = [( (it["bbox"][0] + it["bbox"][2]) / 2, None) for it in v2]
                match_v1_to_v2, _ = align_v1(v1_seq, anchors)
                paired_v2 = set()
                for i, it in enumerate(v1_seq):
                    j = match_v1_to_v2[i]
                    if j is None:
                        final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
                        continue
                    paired_v2.add(j)
                    if v2[j]["price"] == it["price"]:
                        final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
                    # 异价：view2 已在列（更准的本地框 + 2×读价），view1 丢弃
                # view2 中未被 view1 配对的：已在列
            for it in view1:
                final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
            return final

        def align_v1(seq, anchors):
            """view1 框（有价）对 view2 锚点的单调对齐：价格一致 0、不一致 1、
            view1 框无匹配 0.8。"""
            K, N = len(seq), len(anchors)
            if K == 0 or N == 0:
                return [None] * K, 0.0

            def pcost(i, j):
                return 0.0 if seq[i]["price"] == anchors[j][1] else 1.0

            INF = float("inf")
            dp = [[INF] * (N + 1) for _ in range(K + 1)]
            bk = [[None] * (N + 1) for _ in range(K + 1)]
            for j in range(N + 1):
                dp[0][j] = 0.0
                bk[0][j] = ("skip_item", j - 1) if j >= 1 else None
            for i in range(1, K + 1):
                for j in range(N + 1):
                    c = dp[i - 1][j] + 0.8
                    if c < dp[i][j]:
                        dp[i][j] = c
                        bk[i][j] = ("unmatched", j)
                    if j >= 1 and dp[i - 1][j - 1] < INF:
                        c = dp[i - 1][j - 1] + pcost(i - 1, j - 1)
                        if c < dp[i][j]:
                            dp[i][j] = c
                            bk[i][j] = ("match", j - 1)
                    if j >= 1 and dp[i][j - 1] < dp[i][j]:
                        dp[i][j] = dp[i][j - 1]
                        bk[i][j] = ("skip_item", j - 1)
            match = [None] * K
            i, j = K, N
            while i > 0 or j > 0:
                act, pj = bk[i][j]
                if act == "match":
                    match[i - 1] = pj
                    i, j = i - 1, pj
                elif act == "skip_item":
                    j = pj
                else:
                    i = i - 1
            return match, dp[K][N]

        f4 = merge_v4()
        f5 = merge_v5()
        e4 = evaluate(f4, user_tags)
        e5 = evaluate(f5, user_tags)
        for k in tv4:
            tv4[k] += e4[k]
            tv5[k] += e5[k]
        report.append({"image": stem[:8], "v4": e4, "v5": e5})
        print(f"{stem[:8]}: v4 preds={e4['preds']} m50={e4['m50']} 价={e4['price_ok']} "
              f"重复签={e4['dup_tags']} | v5 m50={e5['m50']} 价={e5['price_ok']} "
              f"重复签={e5['dup_tags']}")

    (OUT / "summary_v5.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"合计 v4(IoU去重): preds={tv4['preds']} m50={tv4['m50']} 价={tv4['price_ok']} 重复签={tv4['dup_tags']}")
    print(f"合计 v5(顺序对齐): preds={tv5['preds']} m50={tv5['m50']} 价={tv5['price_ok']} 重复签={tv5['dup_tags']}")


if __name__ == "__main__":
    main()
