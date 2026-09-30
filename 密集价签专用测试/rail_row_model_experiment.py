# -*- coding: utf-8 -*-
"""Leave-one-image-out test of a learned geometry-only rail-row scorer.

Annotations are used only to create offline row labels and score predictions.
No annotation coordinates or labels enter image-time feature generation.
"""

import csv
import copy
import json
from pathlib import Path

import numpy as np

import rail_first_experiment as base
import analyze_rows as feature_source
from rail_followup_experiment import run_stage_b


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "新数据"
OUTPUT = HERE / "导轨优先后续实验" / "排级模型留一图片验证"
FEATURES = (
    "n", "h_med", "h_cv", "w_med", "w_cv", "w_over_h", "gap_med",
    "gap_ratio", "ent_per_100px", "span_ratio", "angle", "bot_align",
    "top_align", "bot_edge_sup",
)


def make_samples():
    samples = []
    for annotation_path in sorted(NEW_DIR.glob("*.json")):
        stem = annotation_path.stem
        image_path = next(
            (NEW_DIR / f"{stem}{suffix}" for suffix in (".jpg", ".jpeg")
             if (NEW_DIR / f"{stem}{suffix}").exists()),
            None,
        )
        if image_path is None:
            continue
        image = base.read_image(image_path)
        height, width = image.shape[:2]
        edges = base.canny_edges(image)
        entities = base.detect_entities(image)
        rows = base.group_rows(entities, width, edges)
        tags, user_rails, _ = base.load_annotations(annotation_path)
        bands = []
        for rail in user_rails:
            band = base.user_rail_edges(rail["poly"])
            bands.append({**band, "poly": rail["poly"]})
        for row_index, row in enumerate(rows):
            row["bot_edge_sup"] = base.bottom_edge_support(row, edges)
            feature = feature_source.row_features(row, width, edges)
            corridor = base.build_corridor(row, width, height, edges)
            label = 0
            best_containment = 0.0
            if corridor:
                for band in bands:
                    inside = [
                        tag["bbox"] for tag in tags
                        if base.point_in_poly(
                            ((tag["bbox"][0] + tag["bbox"][2]) / 2,
                             (tag["bbox"][1] + tag["bbox"][3]) / 2),
                            band["poly"],
                        )
                    ]
                    if not inside:
                        continue
                    contained = sum(
                        base.corridor_contains(corridor, box, base.TRUNCATION_MARGIN)
                        for box in inside
                    ) / len(inside)
                    x_overlap = max(
                        0.0,
                        min(corridor["x_range"][1], band["x1"])
                        - max(corridor["x_range"][0], band["x0"]),
                    )
                    overlap_ratio = x_overlap / max(1.0, min(
                        corridor["x_range"][1] - corridor["x_range"][0],
                        band["x1"] - band["x0"],
                    ))
                    rank = contained * overlap_ratio
                    if rank > best_containment:
                        best_containment = rank
                label = int(best_containment >= 0.5)
            samples.append({
                "image": stem,
                "row": row_index,
                "feature": feature,
                "label": label,
                "containment_score": round(best_containment, 4),
                "row_data": row,
                "image_path": image_path,
            })
    return samples


def sigmoid(value):
    value = np.clip(value, -30, 30)
    return 1.0 / (1.0 + np.exp(-value))


def fit_logistic(x, y, regularization=0.1, steps=3000, learning_rate=0.05):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    normalized = (x - mean) / scale
    normalized = np.column_stack((np.ones(len(normalized)), normalized))
    weights = np.zeros(normalized.shape[1], dtype=float)
    for _ in range(steps):
        probability = sigmoid(normalized @ weights)
        gradient = normalized.T @ (probability - y) / len(y)
        gradient[1:] += regularization * weights[1:] / len(y)
        weights -= learning_rate * gradient
    return {"mean": mean, "scale": scale, "weights": weights}


def predict(model, x):
    normalized = (x - model["mean"]) / model["scale"]
    normalized = np.column_stack((np.ones(len(normalized)), normalized))
    return sigmoid(normalized @ model["weights"])


def best_training_threshold(probabilities, labels):
    candidates = sorted(set([0.0, 1.0, *probabilities.tolist()]))
    best = (0.5, -1.0)
    for threshold in candidates:
        predicted = probabilities >= threshold
        tp = int(np.logical_and(predicted, labels == 1).sum())
        fp = int(np.logical_and(predicted, labels == 0).sum())
        fn = int(np.logical_and(~predicted, labels == 1).sum())
        f1 = 2 * tp / max(1, 2 * tp + fp + fn)
        if f1 > best[1]:
            best = (float(threshold), f1)
    return best


def row_scores(samples):
    matrix = []
    for sample in samples:
        row = sample["feature"]
        matrix.append([float(row[name]) for name in FEATURES])
    return np.asarray(matrix, dtype=float)


def detection_metrics(boxes, tags):
    truth = [tag["bbox"] for tag in tags]
    result = {"predictions": len(boxes), "ground_truth": len(truth)}
    for threshold in (0.3, 0.5, 0.7):
        matches = base.match_greedy(boxes, truth, threshold)
        result[f"matched_{threshold}"] = len(matches)
        result[f"precision_{threshold}"] = len(matches) / len(boxes) if boxes else 0.0
        result[f"recall_{threshold}"] = len(matches) / len(truth) if truth else 0.0
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    samples = make_samples()
    images = sorted({sample["image"] for sample in samples})
    all_x = row_scores(samples)
    all_y = np.asarray([sample["label"] for sample in samples], dtype=int)
    lookup = {image: [i for i, sample in enumerate(samples) if sample["image"] == image] for image in images}
    folds = []
    probability_by_row = {}

    for held_out in images:
        train_indices = [i for image in images if image != held_out for i in lookup[image]]
        test_indices = lookup[held_out]
        train_y = all_y[train_indices]
        if len(np.unique(train_y)) < 2:
            continue
        model = fit_logistic(all_x[train_indices], train_y)
        train_probability = predict(model, all_x[train_indices])
        threshold, train_f1 = best_training_threshold(train_probability, train_y)
        test_probability = predict(model, all_x[test_indices])
        test_labels = all_y[test_indices]
        predicted = test_probability >= threshold
        tp = int(np.logical_and(predicted, test_labels == 1).sum())
        fp = int(np.logical_and(predicted, test_labels == 0).sum())
        fn = int(np.logical_and(~predicted, test_labels == 1).sum())
        row_record = []
        for index, probability in zip(test_indices, test_probability):
            probability_by_row[index] = float(probability)
            row_record.append({
                "row": samples[index]["row"],
                "label": int(samples[index]["label"]),
                "probability": round(float(probability), 5),
                "predicted": bool(probability >= threshold),
                "containment_score": samples[index]["containment_score"],
            })
        folds.append({
            "held_out_image": held_out,
            "train_rows": len(train_indices),
            "train_positive_rows": int(train_y.sum()),
            "threshold_selected_on_training_only": threshold,
            "training_f1_at_threshold": train_f1,
            "test_rows": len(test_indices),
            "test_positive_rows": int(test_labels.sum()),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / max(1, tp + fp),
            "recall": tp / max(1, tp + fn),
            "rows": row_record,
        })

    image_detection = []
    original_detector = base.detect_tags_in_corridor
    try:
        for image in images:
            group = [samples[i] for i in lookup[image]]
            held_image = base.read_image(group[0]["image_path"])
            height, width = held_image.shape[:2]
            edges = base.canny_edges(held_image)
            auto_rails = []
            for sample in group:
                if sample["row_data"]["n"] < base.ROW_MIN_ENTITIES:
                    continue
                rail = base.build_corridor(sample["row_data"], width, height, edges)
                if rail:
                    auto_rails.append((sample, rail))
            image_boxes = {}
            for sample, rail in auto_rails:
                idx = samples.index(sample)
                model_positive = any(
                    fold["held_out_image"] == image
                    and any(row["row"] == sample["row"] and row["predicted"] for row in fold["rows"])
                    for fold in folds
                )
                image_boxes[idx] = (rail, model_positive)

            tags, _, _ = base.load_annotations(NEW_DIR / f"{image}.json")
            all_rows_boxes = []
            model_rows_boxes = []
            model_rails = []
            selected_indices = set()
            for idx, (rail, model_positive) in image_boxes.items():
                if model_positive:
                    selected_indices.add(idx)
                    model_rails.append(rail)
            if image_boxes:
                base.detect_tags_in_corridor = original_detector
                all_result, _ = run_stage_b(held_image, edges, copy.deepcopy([value[0] for value in image_boxes.values()]))
                all_rows_boxes = [box for index in sorted(all_result) for box in all_result[index]]
                base.detect_tags_in_corridor = original_detector
                if model_rails:
                    model_result, _ = run_stage_b(held_image, edges, copy.deepcopy(model_rails))
                    model_rows_boxes = [box for index in sorted(model_result) for box in model_result[index]]
            image_detection.append({
                "image": image,
                "all_rows": detection_metrics(all_rows_boxes, tags),
                "model_selected_rows": detection_metrics(model_rows_boxes, tags),
                "selected_rows": len(selected_indices),
                "candidate_rows": len(image_boxes),
            })
    finally:
        base.detect_tags_in_corridor = original_detector

    labels = all_y.tolist()
    report = {
        "experiment": "leave_one_image_out_geometry_row_classifier",
        "warning": "Only six annotated images; diagnostic, not a production classifier. Annotation used for offline labels/evaluation only.",
        "label_rule": "auto corridor contains at least half of in-rail tag boxes and overlaps at least half of rail width; labels only train/evaluate, never generate image features.",
        "features": list(FEATURES),
        "dataset": {
            "images": images,
            "rows": len(samples),
            "positive_rows": sum(labels),
            "negative_rows": len(labels) - sum(labels),
        },
        "folds": folds,
        "detection": image_detection,
        "detection_totals": {
            key: sum(item["model_selected_rows"][key] for item in image_detection)
            for key in ("predictions", "ground_truth", "matched_0.3", "matched_0.5", "matched_0.7")
        },
    }
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    with (OUTPUT / "row_predictions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("image", "row", "label", "containment_score", "probability", "predicted"))
        writer.writeheader()
        for fold in folds:
            for row in fold["rows"]:
                writer.writerow({"image": fold["held_out_image"], **row})
    print(json.dumps(report["dataset"], ensure_ascii=False))
    for fold in folds:
        print(f"{fold['held_out_image'][:8]} P/R={fold['precision']:.3f}/{fold['recall']:.3f} TP/FP/FN={fold['tp']}/{fold['fp']}/{fold['fn']}")
    print("geometry-only all-row matched@0.5:", sum(item["all_rows"]["matched_0.5"] for item in image_detection))
    print("model-selected matched@0.5:", report["detection_totals"]["matched_0.5"], "preds:", report["detection_totals"]["predictions"])
    print("report:", OUTPUT / "report.json")


if __name__ == "__main__":
    main()
