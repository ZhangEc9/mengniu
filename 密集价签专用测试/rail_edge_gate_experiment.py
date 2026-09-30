# -*- coding: utf-8 -*-
"""Apply edge-continuity gates to Stage B and evaluate full tag metrics."""

import json
import copy
from pathlib import Path

import rail_first_experiment as base
import rail_edge_continuity_experiment as continuity
from rail_followup_experiment import run_stage_b
from rail_detector_ablation import legacy_relative_detector


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "新数据"
OUTPUT = HERE / "导轨优先后续实验" / "连续性门槛"


def select_threshold(train, score_name):
    values = sorted(set(row[score_name] for row in train))
    best = (values[0] if values else 0.0, -1.0)
    for threshold in values:
        f1, _, _, _ = continuity.f1_at(train, score_name, threshold)
        if f1 > best[1]:
            best = (threshold, f1)
    return best


def image_records(stem, image_path):
    image = base.read_image(image_path)
    height, width = image.shape[:2]
    edges = base.canny_edges(image)
    rows = base.group_rows(base.detect_entities(image), width)
    tags, _, _ = base.load_annotations(NEW_DIR / f"{stem}.json")
    for row in rows:
        row["bot_edge_sup"] = base.bottom_edge_support(row, edges)
    records = []
    for index, row in enumerate(rows):
        basic = continuity.row_edge_features(row, edges)
        records.append({"image": stem, "row": index, "row_data": row, **basic})
    return image, edges, tags, records


def evaluate_selected(image, edges, tags, selected, detector=None):
    height, width = image.shape[:2]
    rails = []
    for record in selected:
        rail = base.build_corridor(record["row_data"], width, height, edges)
        if rail:
            rails.append(rail)
    rail_boxes, _ = run_stage_b(image, edges, copy.deepcopy(rails), detector=detector)
    predictions = [box for index in sorted(rail_boxes) for box in rail_boxes[index]]
    truth = [tag["bbox"] for tag in tags]
    metrics = {"predictions": len(predictions), "ground_truth": len(truth)}
    for threshold in (0.3, 0.5, 0.7):
        matched = len(base.match_greedy(predictions, truth, threshold))
        metrics[f"matched_{threshold}"] = matched
        metrics[f"precision_{threshold}"] = matched / len(predictions) if predictions else 0.0
        metrics[f"recall_{threshold}"] = matched / len(truth) if truth else 0.0
    regular_truth = [tag["bbox"] for tag in tags if tag["tag_type"] != "bundle_promotion"]
    promotion_truth = [tag["bbox"] for tag in tags if tag["tag_type"] == "bundle_promotion"]
    metrics["non_promotion_gt"] = len(regular_truth)
    metrics["non_promotion_matched_0.5"] = len(base.match_greedy(predictions, regular_truth, 0.5))
    metrics["promotion_gt"] = len(promotion_truth)
    metrics["promotion_matched_0.5"] = len(base.match_greedy(predictions, promotion_truth, 0.5))
    return {"rows": len(rails), "metrics": metrics, "boxes": predictions}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cached = {}
    for annotation in sorted(NEW_DIR.glob("*.json")):
        stem = annotation.stem
        image_path = next((NEW_DIR / f"{stem}{suffix}" for suffix in (".jpg", ".jpeg") if (NEW_DIR / f"{stem}{suffix}").exists()), None)
        if image_path is not None:
            cached[stem] = image_records(stem, image_path)
    images = sorted(cached)
    labels = {(record["image"], record["row"]): record["label"] for record in continuity.collect()}
    for image in images:
        for record in cached[image][3]:
            record["label"] = labels[(image, record["row"])]
    scores = ("bottom_exact", "bottom_narrow", "bottom_contrast")
    report = []
    for held_out in images:
        train = [record for image in images if image != held_out for record in cached[image][3]]
        thresholds = {score: select_threshold(train, score)[0] for score in scores}
        image, edges, tags, records = cached[held_out]
        entry = {"image": held_out, "thresholds": thresholds, "variants": {}}
        for score in scores:
            selected = [record for record in records if record[score] >= thresholds[score]]
            entry["variants"][score] = evaluate_selected(image, edges, tags, selected)
            entry["variants"][score + "_legacy_relative"] = evaluate_selected(
                image, edges, tags, selected, detector=legacy_relative_detector,
            )
        report.append(entry)
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    for score in tuple(report[0]["variants"]) if report else ():
        totals = {"rows": 0, "predictions": 0, "matched_0.3": 0, "matched_0.5": 0, "matched_0.7": 0, "ground_truth": 0}
        for entry in report:
            metrics = entry["variants"][score]["metrics"]
            totals["rows"] += entry["variants"][score]["rows"]
            for key in totals:
                if key in metrics:
                    totals[key] += metrics[key]
        print(f"{score}: rows={totals['rows']} preds={totals['predictions']} m05={totals['matched_0.5']} gt={totals['ground_truth']}")
    print("report:", OUTPUT / "report.json")


if __name__ == "__main__":
    main()
