# -*- coding: utf-8 -*-
"""Analyze global duplicate suppression on the full-candidate audit."""

import json
from pathlib import Path

import rail_first_experiment as base


HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "\u5bfc\u8f68\u4f18\u5148\u540e\u7eed\u5b9e\u9a8c" / "\u5168\u91cf\u5019\u9009\u57fa\u7ebf"
NEW_DIR = HERE / "\u65b0\u6570\u636e"
OUT_PATH = HERE / "\u5bfc\u8f68\u4f18\u5148\u540e\u7eed\u5b9e\u9a8c" / "global_dedup_variants.json"


def area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def nms(boxes, threshold):
    kept = []
    for box in sorted(boxes, key=area, reverse=True):
        if all(base.iou(box, other) < threshold for other in kept):
            kept.append(box)
    return kept


def evaluate(predictions, ground_truth):
    result = {}
    for threshold in (0.3, 0.5, 0.7):
        matches = base.match_greedy(predictions, ground_truth, threshold)
        result[str(threshold)] = {
            "matched": len(matches),
            "precision": len(matches) / len(predictions) if predictions else 0.0,
            "recall": len(matches) / len(ground_truth) if ground_truth else 0.0,
        }
    return result


def main():
    report = []
    for item_path in sorted(DATA_DIR.glob("*.json")):
        if item_path.name == "summary.json":
            continue
        item = json.loads(item_path.read_text(encoding="utf-8"))
        stem = item["image"]
        _, _, annotations = base.load_annotations(NEW_DIR / f"{stem}.json")
        tags, _, _ = base.load_annotations(NEW_DIR / f"{stem}.json")
        del annotations
        ground_truth = [tag["bbox"] for tag in tags]
        raw = [box for rail in item["auto_rails"] for box in rail["boxes"]]
        variants = {"raw": {"count": len(raw), "metrics": evaluate(raw, ground_truth)}}
        for nms_threshold in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            deduped = nms(raw, nms_threshold)
            variants[f"nms_{nms_threshold:g}"] = {
                "count": len(deduped),
                "metrics": evaluate(deduped, ground_truth),
            }
        report.append({"image": stem, "variants": variants})

    totals = {}
    for item in report:
        for name, variant in item["variants"].items():
            entry = totals.setdefault(name, {"count": 0, "matched_05": 0, "gt": 0})
            entry["count"] += variant["count"]
            entry["matched_05"] += variant["metrics"]["0.5"]["matched"]
            gt = next(
                len(base.load_annotations(NEW_DIR / f"{item['image']}.json")[0])
                for _ in [0]
            )
            entry["gt"] += gt
    OUT_PATH.write_text(json.dumps({"images": report, "totals": totals}, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, entry in totals.items():
        precision = entry["matched_05"] / entry["count"] if entry["count"] else 0.0
        recall = entry["matched_05"] / entry["gt"] if entry["gt"] else 0.0
        print(f"{name}: count={entry['count']} m05={entry['matched_05']} precision={precision:.3f} recall={recall:.3f}")
    print("report ->", OUT_PATH)


if __name__ == "__main__":
    main()
