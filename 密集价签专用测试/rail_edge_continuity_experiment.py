# -*- coding: utf-8 -*-
"""Test exact long-edge continuity as a geometry-only row gate."""

import csv
import json
from pathlib import Path

import numpy as np

import rail_first_experiment as base
import analyze_rows as feature_source


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "新数据"
OUTPUT = HERE / "导轨优先后续实验" / "长边界连续性"


def sample_line(edges, a, b, x0, x1, width, height, offset=0, window=1, samples=80):
    values = []
    for x in np.linspace(x0, x1, samples):
        xi = min(width - 1, max(0, int(round(x))))
        yi = int(round(a + b * x + offset))
        if not (0 <= yi < height):
            values.append(0)
            continue
        lo = max(0, yi - window)
        hi = min(height, yi + window + 1)
        values.append(int(edges[lo:hi, xi].max() > 0))
    return np.asarray(values, dtype=np.uint8)


def row_edge_features(row, edges):
    height, width = edges.shape
    members = row["members"]
    centers = np.asarray([member["cx"] for member in members], float)
    tops = np.asarray([member["bbox"][1] for member in members], float)
    bottoms = np.asarray([member["bbox"][3] for member in members], float)
    center_y = row["a"] + row["b"] * float(np.median(centers))
    top_offset = float(np.median(tops - (row["a"] + row["b"] * centers)))
    bottom_offset = float(np.median(bottoms - (row["a"] + row["b"] * centers)))
    bottom_exact = sample_line(edges, row["a"], row["b"], row["x0"], row["x1"], width, height, bottom_offset, 0)
    bottom_narrow = sample_line(edges, row["a"], row["b"], row["x0"], row["x1"], width, height, bottom_offset, 2)
    top_exact = sample_line(edges, row["a"], row["b"], row["x0"], row["x1"], width, height, top_offset, 0)
    below = sample_line(edges, row["a"], row["b"], row["x0"], row["x1"], width, height, bottom_offset + 12, 1)
    return {
        "center_y": center_y,
        "bottom_exact": float(bottom_exact.mean()),
        "bottom_narrow": float(bottom_narrow.mean()),
        "top_exact": float(top_exact.mean()),
        "below": float(below.mean()),
        "bottom_run": float(max_run(bottom_exact)),
        "bottom_narrow_run": float(max_run(bottom_narrow)),
        "bottom_contrast": float(bottom_narrow.mean() - below.mean()),
    }


def max_run(values):
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best / max(1, len(values))


def collect():
    records = []
    for annotation_path in sorted(NEW_DIR.glob("*.json")):
        stem = annotation_path.stem
        image_path = next((NEW_DIR / f"{stem}{suffix}" for suffix in (".jpg", ".jpeg") if (NEW_DIR / f"{stem}{suffix}").exists()), None)
        if image_path is None:
            continue
        image = base.read_image(image_path)
        height, width = image.shape[:2]
        edges = base.canny_edges(image)
        rows = base.group_rows(base.detect_entities(image), width)
        tags, user_rails, _ = base.load_annotations(annotation_path)
        bands = [base.user_rail_edges(rail["poly"]) for rail in user_rails]
        for index, row in enumerate(rows):
            feature = feature_source.row_features(row, width, edges)
            edge_feature = row_edge_features(row, edges)
            mid_x = (row["x0"] + row["x1"]) / 2
            row_y = row["a"] + row["b"] * mid_x
            label = 0
            for band in bands:
                band_y = (base.y_on_edge(band["top"], mid_x) + base.y_on_edge(band["bottom"], mid_x)) / 2
                overlap = min(row["x1"], band["x1"]) - max(row["x0"], band["x0"])
                if abs(row_y - band_y) <= 80 and overlap > 0.5 * (row["x1"] - row["x0"]):
                    label = 1
                    break
            records.append({"image": stem, "row": index, "label": label, **feature, **edge_feature})
    return records


def f1_at(records, score_name, threshold):
    truth = np.asarray([record["label"] for record in records], int)
    predicted = np.asarray([record[score_name] >= threshold for record in records])
    tp = int(np.logical_and(predicted, truth == 1).sum())
    fp = int(np.logical_and(predicted, truth == 0).sum())
    fn = int(np.logical_and(~predicted, truth == 1).sum())
    return 2 * tp / max(1, 2 * tp + fp + fn), tp, fp, fn


def threshold_from_train(records, score_name):
    values = sorted(set(record[score_name] for record in records))
    best = (values[0] if values else 0.0, -1.0)
    for threshold in values:
        result = f1_at(records, score_name, threshold)
        if result[0] > best[1]:
            best = (threshold, result[0])
    return best


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = collect()
    scores = {
        "bottom_exact": lambda row: row["bottom_exact"],
        "bottom_narrow": lambda row: row["bottom_narrow"],
        "bottom_run": lambda row: row["bottom_run"],
        "bottom_contrast": lambda row: row["bottom_contrast"],
        "combined": lambda row: row["bottom_narrow"] + row["bottom_contrast"] + 0.5 * row["bottom_run"],
    }
    for name, function in scores.items():
        for record in records:
            record[name] = float(function(record))
    images = sorted({record["image"] for record in records})
    folds = []
    for held_out in images:
        train = [record for record in records if record["image"] != held_out]
        test = [record for record in records if record["image"] == held_out]
        fold = {"image": held_out}
        for score_name in scores:
            threshold, train_f1 = threshold_from_train(train, score_name)
            test_f1, tp, fp, fn = f1_at(test, score_name, threshold)
            fold[score_name] = {"threshold": threshold, "train_f1": train_f1, "test_f1": test_f1, "tp": tp, "fp": fp, "fn": fn}
        folds.append(fold)
    field_names = list(dict.fromkeys(["image", "row", "label", *scores.keys(), "bottom_narrow", "top_exact", "below"]))
    with (OUTPUT / "row_features.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    report = {"records": len(records), "positive": sum(record["label"] for record in records), "folds": folds}
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for score_name in scores:
        tp = sum(fold[score_name]["tp"] for fold in folds)
        fp = sum(fold[score_name]["fp"] for fold in folds)
        fn = sum(fold[score_name]["fn"] for fold in folds)
        print(f"{score_name}: TP/FP/FN={tp}/{fp}/{fn} precision={tp/max(1,tp+fp):.3f} recall={tp/max(1,tp+fn):.3f}")
    print("report:", OUTPUT / "report.json")


if __name__ == "__main__":
    main()
