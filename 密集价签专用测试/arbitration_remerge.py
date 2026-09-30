# -*- coding: utf-8 -*-
"""用已存盘的 API 响应离线重算仲裁合并 v4（去重并集），不花任何调用。"""
import json
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
OUT = HERE / "仲裁闭环实验"

all_rows = []
for stem in sorted(p.stem for p in (HERE / "新数据").glob("*.json")):
    img_dir = OUT / stem
    full_c = img_dir / "full_content.txt"
    if not full_c.exists():
        continue
    img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                     if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
    image = R.read_image(img_path)
    H, W = image.shape[:2]
    user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")

    corridors, entities = AL.build_corridors(stem, image)

    view1 = AL.parse_full_image(full_c.read_text(encoding="utf-8"), W, H)

    view2 = []
    num_files = sorted(img_dir.glob("num*_content.txt"))
    for nf in num_files:
        ri = int(nf.stem.split("_")[0].replace("num", ""))
        if ri >= len(corridors):
            continue
        rail = corridors[ri]
        crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
        boxes_crop, _, _ = R.detect_tags_in_corridor(crop, ch)
        readings = AL.parse_numbered(nf.read_text(encoding="utf-8"), len(boxes_crop))
        n_readable = sum(1 for r in readings.values()
                         if r.get("readable") is True and AL.norm_price(r.get("price")))
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
            view2.append({"bbox": bbox, "price": price, "rail": ri, "readable_rail": n_readable})

    # 合并 v4：去重并集 + 价格一致性归属
    final = []
    for it in view2:
        final.append({"bbox": it["bbox"], "price": it["price"], "src": "view2", "rail": it["rail"]})
    v1_dup = 0
    for it in view1:
        if it["price"] is None:
            continue
        overl = [v for v in view2 if R.iou(it["bbox"], v["bbox"]) >= 0.3]
        if not overl:
            final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
            continue
        # 重叠对：同价 → 保留 view1（同一签）；异价 → 保留 view2（2× 近距读数更可信）
        if any(v["price"] == it["price"] for v in overl):
            final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})
        else:
            v1_dup += 1

    def evaluate(entries):
        preds = [e["bbox"] for e in entries]
        gts = [t["bbox"] for t in user_tags]
        matches = R.match_greedy(preds, gts, 0.5)
        price_ok = sum(1 for m in matches
                       if AL.norm_price(entries[m["pred"]]["price"])
                       == AL.norm_price(user_tags[m["gt"]]["label"]))
        return {"preds": len(preds), "gts": len(gts),
                "m30": len(R.match_greedy(preds, gts, 0.3)),
                "m50": len(matches), "price_ok": price_ok}

    v1_ev = evaluate([{"bbox": it["bbox"], "price": it["price"]} for it in view1])
    fin_ev = evaluate(final)
    per_rail = {}
    for e in final:
        if e["src"] == "view2":
            per_rail[e["rail"]] = per_rail.get(e["rail"], 0) + 1
    all_rows.append({"image": stem[:8], "v1": v1_ev, "v1_dup_with_view2": v1_dup,
                     "view2": len(view2), "view2_per_rail": per_rail, "final": fin_ev})
    print(f"{stem[:8]}: ①{v1_ev} -> v4{fin_ev} (view2={len(view2)}, 去重view1={v1_dup})")

(OUT / "summary_v4.json").write_text(
    json.dumps(all_rows, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
tb = {"preds": 0, "gts": 0, "m30": 0, "m50": 0, "price_ok": 0}
tf = dict(tb)
for r in all_rows:
    for k in tb:
        tb[k] += r["v1"][k]
        tf[k] += r["final"][k]
print(f"合计 ①全图: {tb}")
print(f"合计 仲裁v4: {tf}")
