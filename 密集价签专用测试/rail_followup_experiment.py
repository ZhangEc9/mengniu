# -*- coding: utf-8 -*-
"""Audit all automatic corridors and Stage-B candidates."""

import json
from pathlib import Path

import cv2
import numpy as np

import rail_first_experiment as base


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "\u65b0\u6570\u636e"
OUT_DIR = HERE / "\u5bfc\u8f68\u4f18\u5148\u540e\u7eed\u5b9e\u9a8c" / "\u5168\u91cf\u5019\u9009\u57fa\u7ebf"


def run_stage_b(image, edges, rails, detector=None):
    detector = detector or base.detect_tags_in_corridor
    rail_boxes = {}
    details = {}
    for rail_index, rail in enumerate(rails):
        mask = np.zeros(edges.shape, np.uint8)
        cv2.fillPoly(mask, [rail["poly"].astype(np.int32)], 1)
        rail["band_edge_density"] = float(edges[mask > 0].mean() / 255.0)
        skip = None
        if rail["bot_edge_sup"] < base.ROW_BOT_EDGE_SUP_MIN:
            skip = "skipped_row_no_rail_edge"
        elif rail["band_edge_density"] < base.DENSE_BAND_MIN:
            skip = "skipped_not_dense"
        if skip:
            rail_boxes[rail_index] = []
            details[rail_index] = {"stage_b": skip, "candidates": 0}
            continue
        crop, inv, ch = base.flatten_corridor(image, rail)
        boxes, touching, main_count = detector(crop, ch)
        expanded = False
        if boxes and touching > base.CORRIDOR_EXPAND_TOUCH_FRAC * len(boxes):
            base.expand_corridor(rail, base.CORRIDOR_EXPAND_FACTOR)
            crop, inv, ch = base.flatten_corridor(image, rail)
            boxes, touching, main_count = detector(crop, ch)
            expanded = True
        quads = [base.map_box_back(box, inv) for box in boxes]
        mapped = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads]
        rail_boxes[rail_index] = mapped
        details[rail_index] = {
            "stage_b": "ran",
            "candidates": len(mapped),
            "main_pass": main_count,
            "touching_corridor_edge": touching,
            "expanded": expanded,
            "flattened_h": round(ch, 1),
        }
    return rail_boxes, details


def evaluate_image(stem, image_path):
    image = base.read_image(image_path)
    height, width = image.shape[:2]
    edges = base.canny_edges(image)
    entities = base.detect_entities(image)
    rows = base.group_rows(entities, width, edges)
    rails = [r for r in (base.build_corridor(row, width, height, edges) for row in rows) if r]
    rail_boxes, details = run_stage_b(image, edges, rails)
    tags, user_rails, _ = base.load_annotations(NEW_DIR / f"{stem}.json")
    predictions = [box for index in sorted(rail_boxes) for box in rail_boxes[index]]
    ground_truth = [tag["bbox"] for tag in tags]
    global_match = {}
    for threshold in (0.3, 0.5, 0.7):
        matches = base.match_greedy(predictions, ground_truth, threshold)
        global_match[str(threshold)] = {
            "matched": len(matches),
            "precision": len(matches) / len(predictions) if predictions else 0.0,
            "recall": len(matches) / len(ground_truth) if ground_truth else 0.0,
            "matches": matches,
        }
    rail_eval, false_rails = base.evaluate_rails(rails, user_rails, tags, width, height)
    conditional = []
    for user_index, record in enumerate(rail_eval):
        inside = [
            tag["bbox"] for tag in tags
            if base.point_in_poly(
                ((tag["bbox"][0] + tag["bbox"][2]) / 2, (tag["bbox"][1] + tag["bbox"][3]) / 2),
                user_rails[user_index]["poly"],
            )
        ]
        scoped = [box for index in record["segments"] for box in rail_boxes[index]]
        conditional.append({
            "user_rail": user_index,
            "matched_rail": record["matched"],
            "segments": record["segments"],
            "user_tags": len(inside),
            "preds": len(scoped),
            "m03": len(base.match_greedy(scoped, inside, 0.3)),
            "m05": len(base.match_greedy(scoped, inside, 0.5)),
            "m07": len(base.match_greedy(scoped, inside, 0.7)),
        })
    auto_rails = []
    for index, rail in enumerate(rails):
        auto_rails.append({
            "index": index,
            "angle_deg": round(rail["angle_deg"], 3),
            "x_range": [round(value, 1) for value in rail["x_range"]],
            "n_entities": rail["n_entities"],
            "gap_ratio": rail["gap_ratio"],
            "bot_edge_sup": rail["bot_edge_sup"],
            "edge_support": rail["edge_support"],
            "resid_max_px": rail["resid_max_px"],
            "band_edge_density": round(rail["band_edge_density"], 4),
            "detail": details[index],
            "boxes": rail_boxes[index],
        })
    return {
        "image": stem,
        "size": [width, height],
        "entities_total": len(entities),
        "rows_total": len(rows),
        "auto_rails": auto_rails,
        "all_predictions": len(predictions),
        "all_ground_truth": len(ground_truth),
        "global_match": global_match,
        "rail_eval": rail_eval,
        "false_auto_rails": false_rails,
        "conditional_stage_b": conditional,
    }


def main():
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for annotation in sorted(NEW_DIR.glob("*.json")):
        stem = annotation.stem
        image_path = next((NEW_DIR / f"{stem}{ext}" for ext in (".jpg", ".jpeg") if (NEW_DIR / f"{stem}{ext}").exists()), None)
        if image_path is None:
            continue
        result = evaluate_image(stem, image_path)
        results.append(result)
        (out_dir / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
        print(f"{stem[:8]}: rails={len(result['auto_rails'])} false={len(result['false_auto_rails'])} preds={result['all_predictions']} m03={result['global_match']['0.3']['matched']} m05={result['global_match']['0.5']['matched']}")
    summary = {
        "experiment": "full_candidate_audit_baseline",
        "results": results,
        "totals": {
            "images": len(results),
            "auto_rails": sum(len(item["auto_rails"]) for item in results),
            "false_auto_rails": sum(len(item["false_auto_rails"]) for item in results),
            "predictions": sum(item["all_predictions"] for item in results),
            "ground_truth": sum(item["all_ground_truth"] for item in results),
            "matched_0.3": sum(item["global_match"]["0.3"]["matched"] for item in results),
            "matched_0.5": sum(item["global_match"]["0.5"]["matched"] for item in results),
            "matched_0.7": sum(item["global_match"]["0.7"]["matched"] for item in results),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("summary ->", out_dir / "summary.json")


if __name__ == "__main__":
    main()
