# -*- coding: utf-8 -*-
"""Evaluate image-only coalescing of duplicate automatic rail rows."""

import json
from pathlib import Path

import rail_first_experiment as base
from rail_followup_experiment import run_stage_b


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "\u65b0\u6570\u636e"
OUT_PATH = HERE / "\u5bfc\u8f68\u4f18\u5148\u540e\u7eed\u5b9e\u9a8c" / "rail_selection_variants.json"


def center_y(rail, x):
    return ((rail["top_line"]["a"] + rail["bottom_line"]["a"]) / 2) + rail["angle_deg"] * 0 + rail["top_line"]["b"] * x


def score(rail, width, kind):
    span = (rail["x_range"][1] - rail["x_range"][0]) / width
    edge = max(0.0, rail["bot_edge_sup"])
    residual = max(0.0, rail["resid_max_px"])
    density = max(0.0, rail.get("band_edge_density", 0.0))
    if kind == "entities":
        return rail["n_entities"]
    if kind == "coverage":
        return rail["n_entities"] * span
    if kind == "geometry":
        return rail["n_entities"] * span * (0.5 + edge) * (0.5 + density) / (1 + residual / 30)
    raise ValueError(kind)


def select_rails(rails, width, threshold, kind):
    if not rails:
        return []
    x = width / 2
    ordered = sorted(rails, key=lambda rail: center_y(rail, x))
    groups = []
    for rail in ordered:
        y = center_y(rail, x)
        if not groups or y - groups[-1]["last_y"] > threshold:
            groups.append({"last_y": y, "rails": [rail]})
        else:
            groups[-1]["rails"].append(rail)
            groups[-1]["last_y"] = y
    return [max(group["rails"], key=lambda rail: score(rail, width, kind)) for group in groups]


def evaluate(stem, image_path, threshold, kind):
    image = base.read_image(image_path)
    height, width = image.shape[:2]
    edges = base.canny_edges(image)
    entities = base.detect_entities(image)
    rows = base.group_rows(entities, width, edges)
    rails = [rail for rail in (base.build_corridor(row, width, height, edges) for row in rows) if rail]
    rails = select_rails(rails, width, threshold, kind)
    rail_boxes, _ = run_stage_b(image, edges, rails)
    tags, user_rails, _ = base.load_annotations(NEW_DIR / f"{stem}.json")
    predictions = [box for index in sorted(rail_boxes) for box in rail_boxes[index]]
    ground_truth = [tag["bbox"] for tag in tags]
    matches = {}
    for iou_threshold in (0.3, 0.5, 0.7):
        matches[str(iou_threshold)] = len(base.match_greedy(predictions, ground_truth, iou_threshold))
    rail_eval, false_rails = base.evaluate_rails(rails, user_rails, tags, width, height)
    return {
        "image": stem,
        "threshold": threshold,
        "kind": kind,
        "rails": len(rails),
        "false_rails": len(false_rails),
        "predictions": len(predictions),
        "gt": len(ground_truth),
        "matches": matches,
        "rail_eval": rail_eval,
    }


def main():
    results = []
    for annotation in sorted(NEW_DIR.glob("*.json")):
        stem = annotation.stem
        image_path = next((NEW_DIR / f"{stem}{ext}" for ext in (".jpg", ".jpeg") if (NEW_DIR / f"{stem}{ext}").exists()), None)
        if image_path is None:
            continue
        for threshold in (30, 45, 60, 75):
            for kind in ("entities", "coverage", "geometry"):
                result = evaluate(stem, image_path, threshold, kind)
                results.append(result)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    for kind in ("entities", "coverage", "geometry"):
        for threshold in (30, 45, 60, 75):
            subset = [item for item in results if item["kind"] == kind and item["threshold"] == threshold]
            total = {
                key: sum(item[key] for item in subset)
                for key in ("rails", "false_rails", "predictions", "gt")
            }
            total["m05"] = sum(item["matches"]["0.5"] for item in subset)
            print(f"{kind:8s} t={threshold}: rails={total['rails']} false={total['false_rails']} preds={total['predictions']} m05={total['m05']}")


if __name__ == "__main__":
    main()
