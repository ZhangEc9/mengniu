# -*- coding: utf-8 -*-
"""序列对齐补全：模型"读数序列"（全量、有序、位置编造）与本地检测框（真位置、
有缺）做单调对齐——本地框为锚点，模型序列填充本地漏检的签（插值位置）。

数据源（全部已存盘，0 API 调用）：
  - 本地锚点：仲裁闭环 num{ri}_content.txt（阶段B框+编号读价）+ rails.json 走廊
  - 模型序列：中心点读价实验 content_rail{k}.txt（价格/顺序真实）

评测：锚点、补全项、完成态分别对照人工 GT（IoU0.5 + 价格；补全项另报
位置误差比 = |补全中心x - 最近GT中心x| / GT节距，≤0.5 算可用）。"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import rail_first_experiment as R
import api_rail_experiment as A
import arbitration_loop as AL

EXP = HERE / "API端点导轨实验"
CPE = HERE / "中心点读价实验"
OUT = HERE / "序列对齐补全"
EXCLUDE = "45035165"

SKIP_COST_MISMATCH = 1.0      # 锚点与模型项价格不一致
SKIP_COST_NULL_ANCHOR = 0.25  # 无价锚点吸附任意模型项
UNMATCH_COST = 0.8            # 锚点无匹配（介于同价 0 与异价 1 之间）


def norm_price(s):
    return AL.norm_price(s)


def parse_strip_items(content):
    import run_full_pipeline as pipeline
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("rails") or parsed.get("items") or parsed.get("tags")
    items = []
    for it in parsed or []:
        if isinstance(it, dict) and it.get("center") and len(it["center"]) >= 2 \
                and norm_price(it.get("price")):
            items.append({"price": norm_price(it["price"]), "center": it["center"]})
    items.sort(key=lambda it: it["center"][0])   # 按编造 x 排序 = 读数顺序
    return [it["price"] for it in items]


def align(anchors, model_seq):
    """单调对齐 DP：锚点/模型项一一对应（严格递增），锚点允许无匹配
    （代价 UNMATCH_COST，介于同价 0 与异价 1 之间），未被消费的模型项即补全候选。
    返回 (match_of_anchor[len(anchors)]，总代价)。"""
    K, N = len(anchors), len(model_seq)
    if K == 0 or N == 0:
        return [None] * K, 0.0

    def pcost(i, j):
        p = anchors[i][1]
        if p is None:
            return SKIP_COST_NULL_ANCHOR
        return 0.0 if model_seq[j] == p else SKIP_COST_MISMATCH

    INF = float("inf")
    dp = [[INF] * (N + 1) for _ in range(K + 1)]
    bk = [[None] * (N + 1) for _ in range(K + 1)]
    for j in range(N + 1):
        dp[0][j] = 0.0
        bk[0][j] = ("skip_item", j - 1) if j >= 1 else None
    for i in range(1, K + 1):
        for j in range(N + 1):
            # 锚点 i 无匹配
            c = dp[i - 1][j] + UNMATCH_COST
            if c < dp[i][j]:
                dp[i][j] = c
                bk[i][j] = ("unmatched", j)
            # 锚点 i 消费模型项 j
            if j >= 1 and dp[i - 1][j - 1] < INF:
                c = dp[i - 1][j - 1] + pcost(i - 1, j - 1)
                if c < dp[i][j]:
                    dp[i][j] = c
                    bk[i][j] = ("match", j - 1)
            # 模型项 j 留空（成为补全候选）
            if j >= 1 and dp[i][j - 1] < dp[i][j]:
                dp[i][j] = dp[i][j - 1]
                bk[i][j] = ("skip_item", j - 1)
    match_of_anchor = [None] * K
    consumed = []
    i, j = K, N
    while i > 0 or j > 0:
        act, pj = bk[i][j]
        if act == "match":
            match_of_anchor[i - 1] = pj
            consumed.append(pj)
            i, j = i - 1, pj
        elif act == "skip_item":
            j = pj
        else:  # unmatched
            i = i - 1
    consumed.sort()
    return match_of_anchor, dp[K][N]


def main():
    stems = [s for s in sorted(p.stem for p in (HERE / "新数据").glob("*.json"))
             if not s.startswith(EXCLUDE)]
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    totals = {"rails": 0, "anchors": 0, "anchor_prices": 0, "anchor_m50": 0, "anchor_price": 0,
              "fills": 0, "fills_interpolated": 0, "fill_pos_ok": 0,
              "fill_price_cmp": 0, "fill_price_ok": 0,
              "completed_m50": 0, "completed_price": 0}

    for stem in stems:
        cpe_dir = CPE / stem
        cpe_summary_f = CPE / "summary.json"
        if not cpe_dir.exists() or not cpe_summary_f.exists():
            continue
        cpe_summary = json.loads(cpe_summary_f.read_text(encoding="utf-8"))
        cpe_map = {}
        for img in cpe_summary:
            if img["image"] == stem[:8]:
                for r in img["rails"]:
                    cpe_map[r["rail"]] = r["corridor"]
        if not cpe_map:
            continue
        img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                         if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
        image = R.read_image(img_path)
        user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")
        corridors, _ = AL.build_corridors(stem, image)

        for k, ur in enumerate(user_rails):
            ri = cpe_map.get(k)
            if ri is None or ri >= len(corridors):
                continue
            num_f = HERE / "仲裁闭环实验" / stem / f"num{ri}_content.txt"
            rail_f = cpe_dir / f"content_rail{k}.txt"
            if not num_f.exists() or not rail_f.exists():
                continue
            rail = corridors[ri]
            crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
            boxes_crop, _, _ = R.detect_tags_in_corridor(crop, ch)
            readings = AL.parse_numbered(num_f.read_text(encoding="utf-8"), len(boxes_crop))
            anchors = []
            for idx, box_crop in enumerate(boxes_crop, 1):
                r_ = readings.get(idx, {})
                price = norm_price(r_.get("price")) if r_.get("readable") is True else None
                if price is None:
                    continue   # 对齐锚点只用读出价格的框（无价框不可信）
                corners = np.array([[box_crop[0], box_crop[1]], [box_crop[2], box_crop[1]],
                                    [box_crop[2], box_crop[3]], [box_crop[0], box_crop[3]]],
                                   dtype=np.float64)
                mapped = np.append(corners, np.ones((4, 1)), axis=1) @ M_inv.T
                bbox = [mapped[:, 0].min(), mapped[:, 1].min(),
                        mapped[:, 0].max(), mapped[:, 1].max()]
                anchors.append({"bbox": bbox, "price": price, "cx": (bbox[0] + bbox[2]) / 2})
            anchors.sort(key=lambda a_: a_["cx"])
            model_seq = parse_strip_items(rail_f.read_text(encoding="utf-8"))
            if len(anchors) < 2 or not model_seq:
                continue

            match_of_anchor, cost = align([(a_["cx"], a_["price"]) for a_ in anchors], model_seq)

            gts = [t for t in user_tags
                   if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                       (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
            gt_centers = sorted(((t["bbox"][0] + t["bbox"][2]) / 2,
                                 norm_price(t.get("label"))) for t in gts)
            if len(gt_centers) >= 2:
                pitch = float(np.median([gt_centers[i + 1][0] - gt_centers[i][0]
                                         for i in range(len(gt_centers) - 1)]))
            else:
                pitch = float(np.median([a_["bbox"][2] - a_["bbox"][0] for a_ in anchors]))
            med_w = float(np.median([a_["bbox"][2] - a_["bbox"][0] for a_ in anchors]))
            med_h = float(np.median([a_["bbox"][3] - a_["bbox"][1] for a_ in anchors]))

            def m50_price(entries):
                preds = [e["bbox"] for e in entries]
                gt_bboxes = [t["bbox"] for t in gts]
                matches = R.match_greedy(preds, gt_bboxes, 0.5)
                price_ok = sum(1 for m in matches
                               if entries[m["pred"]]["price"]
                               and norm_price(entries[m["pred"]]["price"])
                               == norm_price(gts[m["gt"]]["label"]))
                return len(matches), price_ok

            a_m50, a_price = m50_price([{"bbox": a_["bbox"], "price": a_["price"]}
                                        for a_ in anchors])

            # 补全项：未被锚点消费的模型项
            fills = []
            consumed = sorted(m_ for m_ in match_of_anchor if m_ is not None)
            for j, p in enumerate(model_seq):
                if j in consumed:
                    continue
                prev_m = max((m_ for m_ in consumed if m_ < j), default=None)
                next_m = min((m_ for m_ in consumed if m_ > j), default=None)
                if prev_m is not None and next_m is not None:
                    between = [m_ for m_ in range(prev_m + 1, next_m) if m_ not in consumed]
                    pos = between.index(j) + 1
                    x_prev = anchors[match_of_anchor.index(prev_m)]["cx"]
                    x_next = anchors[match_of_anchor.index(next_m)]["cx"]
                    x = x_prev + (x_next - x_prev) * pos / (len(between) + 1)
                    src = "interpolated"
                elif prev_m is not None:
                    x = anchors[match_of_anchor.index(prev_m)]["cx"] + pitch
                    src = "extrapolated"
                elif next_m is not None:
                    x = anchors[match_of_anchor.index(next_m)]["cx"] - pitch
                    src = "extrapolated"
                else:
                    continue
                x = float(np.clip(x, rail["x_range"][0] - 0.5 * pitch,
                                  rail["x_range"][1] + 0.5 * pitch))
                cy = (rail["top_line"]["a"] + rail["bottom_line"]["a"]) / 2 \
                    + rail["top_line"]["b"] * x
                fills.append({"bbox": [x - med_w / 2, cy - med_h / 2,
                                       x + med_w / 2, cy + med_h / 2],
                              "price": p, "src": src, "cx": x})

            # 补全项评测
            for f_ in fills:
                if not gt_centers:
                    continue
                nearest = min(range(len(gt_centers)),
                              key=lambda i: abs(f_["cx"] - gt_centers[i][0]))
                dx_ratio = abs(f_["cx"] - gt_centers[nearest][0]) / max(1e-6, pitch)
                f_["pos_ratio"] = round(dx_ratio, 2)
                f_["pos_ok"] = dx_ratio <= 0.5
                totals["fill_pos_ok"] += int(f_["pos_ok"])
                if f_["pos_ok"] and gt_centers[nearest][1]:
                    totals["fill_price_cmp"] += 1
                    f_["price_ok_gt"] = (norm_price(f_["price"]) == gt_centers[nearest][1])
                    totals["fill_price_ok"] += int(f_["price_ok_gt"])
            totals["fills"] += len(fills)
            totals["fills_interpolated"] += sum(1 for f_ in fills if f_["src"] == "interpolated")

            completed = ([{"bbox": a_["bbox"], "price": a_["price"]} for a_ in anchors
                          if a_["price"]] +
                         [{"bbox": f_["bbox"], "price": f_["price"]} for f_ in fills])
            c_m50, c_price = m50_price(completed)
            totals["rails"] += 1
            totals["anchors"] += len(anchors)
            totals["anchor_prices"] += sum(1 for a_ in anchors if a_["price"])
            totals["anchor_m50"] += a_m50
            totals["anchor_price"] += a_price
            totals["completed_m50"] += c_m50
            totals["completed_price"] += c_price

            report.append({"image": stem[:8], "rail": k, "corridor": ri,
                           "anchors": len(anchors),
                           "anchor_prices": sum(1 for a_ in anchors if a_["price"]),
                           "model_seq": len(model_seq), "align_cost": round(cost, 2),
                           "anchor_m50": a_m50, "anchor_price_ok": a_price,
                           "fills": len(fills),
                           "fills_interpolated": sum(1 for f_ in fills if f_["src"] == "interpolated"),
                           "fills_pos_ok": sum(1 for f_ in fills if f_.get("pos_ok")),
                           "fill_price_ok": sum(1 for f_ in fills if f_.get("price_ok_gt")),
                           "fill_price_cmp": sum(1 for f_ in fills if "price_ok_gt" in f_),
                           "gt_tags": len(gts), "gt_pitch": round(pitch, 1),
                           "completed_m50": c_m50, "completed_price_ok": c_price,
                           "fills_detail": [{"src": f_["src"], "price": f_["price"],
                                             "cx": round(f_["cx"]), "pos_ratio": f_.get("pos_ratio"),
                                             "pos_ok": f_.get("pos_ok")} for f_ in fills]})

    (OUT / "alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    for r in report:
        print(f"{r['image']} rail{r['rail']}: 锚{r['anchors']}(价{r['anchor_prices']}) "
              f"序列{r['model_seq']} 补全{r['fills']}(插值{r['fills_interpolated']}) "
              f"位准{r['fills_pos_ok']}/{r['fills']} "
              f"价准{r['fill_price_ok']}/{r['fill_price_cmp']} | "
              f"锚m50={r['anchor_m50']} 完成m50={r['completed_m50']} "
              f"完成价={r['completed_price_ok']}")
    t = totals
    print(f"合计 {t['rails']}轨: 锚点{t['anchors']}(价{t['anchor_prices']}) 锚m50={t['anchor_m50']} "
          f"锚价={t['anchor_price']} | 补全{t['fills']}(插值{t['fills_interpolated']}) "
          f"位准{t['fill_pos_ok']}/{t['fills']} 价准{t['fill_price_ok']}/{t['fill_price_cmp']} | "
          f"完成态 m50={t['completed_m50']} 价={t['completed_price']}")


if __name__ == "__main__":
    main()
