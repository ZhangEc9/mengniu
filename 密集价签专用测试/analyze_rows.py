# -*- coding: utf-8 -*-
"""对比真排（人工导轨附近）与假排的可分特征，为冻结排级门槛选依据。
复用 rail_first_experiment 的检测函数；人工标注只用于打标，不参与检测。"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rail_first_experiment as R

NEW_DIR = HERE / "新数据"
OUT = HERE / "导轨优先几何实验" / "row_features.csv"


def row_features(row, width, edges=None):
    members = row["members"]
    hs = np.array([m["h"] for m in members], float)
    ws = np.array([m["w"] for m in members], float)
    ordered = sorted(members, key=lambda m: m["bbox"][0])
    gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(ordered, ordered[1:])]
    gaps = [g for g in gaps if 0 <= g < 3 * ordered[0]["h"]]
    span = row["x1"] - row["x0"]
    feat = {
        "n": len(members), "h_med": float(np.median(hs)),
        "h_cv": float(hs.std() / max(1e-6, hs.mean())),
        "w_med": float(np.median(ws)), "w_cv": float(ws.std() / max(1e-6, ws.mean())),
        "w_over_h": float(np.median(ws) / max(1e-6, np.median(hs))),
        "gap_med": float(np.median(gaps)) if gaps else -1,
        "gap_ratio": float(np.median(gaps)) / max(1e-6, np.median(ws)) if gaps else -1,
        "ent_per_100px": 100.0 * len(members) / max(1e-6, span),
        "span_ratio": span / width,
        "angle": row["angle"],
    }
    # 底边/顶边对齐率：实体边缘贴排向直线的比例（价签挂同一导轨 -> 高）
    a_, b_ = row["a"], row["b"]
    bot = np.array([m["bbox"][3] for m in members], float)
    top = np.array([m["bbox"][1] for m in members], float)
    cxs = np.array([m["cx"] for m in members], float)
    pred = a_ + b_ * cxs
    feat["bot_align"] = float((np.abs(bot - (pred + np.median(bot - pred))) <= 6).mean())
    feat["top_align"] = float((np.abs(top - (pred + np.median(top - pred))) <= 6).mean())
    # 底线边缘支持：沿排向线下方 0-12px 采样 Canny 命中率
    if edges is not None:
        h_img, w_img = edges.shape
        bot_med = float(np.median(bot))
        xs = np.linspace(row["x0"], row["x1"], 40)
        hits = 0
        for x in xs:
            xi = min(int(round(x)), w_img - 1)
            yi = int(round(a_ + b_ * x + (bot_med - (a_ + b_ * np.median(cxs)))))
            yi0, yi1 = max(0, yi - 2), min(h_img, yi + 13)
            if yi0 < yi1 and edges[yi0:yi1, xi].max() > 0:
                hits += 1
        feat["bot_edge_sup"] = hits / 40.0
    else:
        feat["bot_edge_sup"] = -1
    return feat


def main():
    rows_out = []
    stems = sorted(p.stem for p in NEW_DIR.glob("*.json"))
    for stem in stems:
        img_path = next((NEW_DIR / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                         if (NEW_DIR / f"{stem}{ext}").exists()), None)
        if img_path is None:
            continue
        image = R.read_image(img_path)
        height, width = image.shape[:2]
        edges = R.canny_edges(image)
        entities = R.detect_entities(image)
        rows = R.group_rows(entities, width)
        user_tags, user_rails, _ = R.load_annotations(NEW_DIR / f"{stem}.json")
        rail_bands = []
        for ur in user_rails:
            e = R.user_rail_edges(ur["poly"])
            inside = [t["bbox"] for t in user_tags
                      if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                          (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
            rail_bands.append({"top": e["top"], "bottom": e["bottom"],
                               "x0": e["x0"], "x1": e["x1"], "n_tags": len(inside)})
        for ri, row in enumerate(rows):
            feat = row_features(row, width, edges)
            feat["image"] = stem[:8]
            feat["row"] = ri
            # 打标：排中心线中点是否落在某人工走廊中心线 ±80px 内，且横向重叠过半
            mid_x = (row["x0"] + row["x1"]) / 2
            row_y = row["a"] + row["b"] * mid_x
            label = 0
            for band in rail_bands:
                uy = (R.y_on_edge(band["top"], mid_x) + R.y_on_edge(band["bottom"], mid_x)) / 2
                ov = min(row["x1"], band["x1"]) - max(row["x0"], band["x0"])
                if abs(row_y - uy) <= 80 and ov > 0.5 * (row["x1"] - row["x0"]):
                    label = 1
                    break
            feat["true_row"] = label
            rows_out.append(feat)

    cols = ["image", "row", "true_row", "n", "h_med", "h_cv", "w_med", "w_cv",
            "w_over_h", "gap_med", "gap_ratio", "ent_per_100px", "span_ratio", "angle",
            "bot_align", "top_align", "bot_edge_sup"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(cols) + "\n")
        for r in rows_out:
            f.write(",".join(str(round(r[c], 3)) if isinstance(r[c], float) else str(r[c])
                             for c in cols) + "\n")

    trues = [r for r in rows_out if r["true_row"] == 1]
    falses = [r for r in rows_out if r["true_row"] == 0]
    lines = [f"真排 {len(trues)} 假排 {len(falses)}", ""]
    for key in ("n", "h_cv", "w_cv", "w_over_h", "gap_ratio", "ent_per_100px", "span_ratio",
                "angle", "bot_align", "top_align", "bot_edge_sup"):
        tvals = sorted(r[key] for r in trues)
        fvals = sorted(r[key] for r in falses)
        def pct(vals, p):
            i = min(len(vals) - 1, max(0, int(p * len(vals))))
            return vals[i]
        lines.append(f"{key}: 真排 p10/p50/p90 = {pct(tvals,0.1):.3f}/{pct(tvals,0.5):.3f}/{pct(tvals,0.9):.3f}"
                     f"  假排 = {pct(fvals,0.1):.3f}/{pct(fvals,0.5):.3f}/{pct(fvals,0.9):.3f}")
    text = "\n".join(lines)
    (HERE / "导轨优先几何实验" / "row_features_report.txt").write_text(text, encoding="utf-8")
    print("rows:", len(rows_out), "true:", len(trues))


if __name__ == "__main__":
    main()
