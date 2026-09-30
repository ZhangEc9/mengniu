"""Image-only long-line corridor proposals with full-candidate evaluation."""

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import rail_first_experiment as base
from rail_detector_ablation import legacy_relative_detector, map_boxes


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "导轨优先后续实验" / "长线配对"
PARAMETERS = {
    "hough_threshold": 90,
    "line_min_width_fraction": 0.25,
    "line_max_gap_width_fraction": 0.025,
    "max_angle_deg": 12.0,
    "line_merge_angle_deg": 0.75,
    "line_merge_distance_px": 6.0,
    "pair_max_angle_difference_deg": 1.0,
    "pair_min_overlap_width_fraction": 0.3,
    "corridor_height_fraction": (0.025, 0.10),
    "pair_nms_iou": 0.4,
}


def line_value(line, horizontal):
    return line["intercept"] + line["slope"] * horizontal


def detect_long_lines(image):
    height, width = image.shape[:2]
    edges = base.canny_edges(image)
    detected = cv2.HoughLinesP(
        edges, 1, np.pi / 720,
        threshold=PARAMETERS["hough_threshold"],
        minLineLength=width * PARAMETERS["line_min_width_fraction"],
        maxLineGap=width * PARAMETERS["line_max_gap_width_fraction"],
    )
    proposals = []
    if detected is not None:
        for coordinates in detected.reshape(-1, 4):
            left, top, right, bottom = [float(value) for value in coordinates]
            if left > right:
                left, right, top, bottom = right, left, bottom, top
            if right - left < 1:
                continue
            slope = (bottom - top) / (right - left)
            angle = math.degrees(math.atan(slope))
            if abs(angle) > PARAMETERS["max_angle_deg"]:
                continue
            proposals.append({
                "intercept": top - slope * left,
                "slope": slope,
                "angle_deg": angle,
                "left": left,
                "right": right,
                "length": right - left,
                "merged_count": 1,
            })
    merged = []
    for proposal in sorted(proposals, key=lambda line: line["length"], reverse=True):
        host = None
        for other in merged:
            overlap_left = max(proposal["left"], other["left"])
            overlap_right = min(proposal["right"], other["right"])
            if overlap_left >= overlap_right:
                continue
            if abs(proposal["angle_deg"] - other["angle_deg"]) > PARAMETERS["line_merge_angle_deg"]:
                continue
            distances = [
                abs(line_value(proposal, horizontal) - line_value(other, horizontal))
                for horizontal in (overlap_left, (overlap_left + overlap_right) / 2, overlap_right)
            ]
            if max(distances) <= PARAMETERS["line_merge_distance_px"]:
                host = other
                break
        if host is None:
            merged.append(dict(proposal))
        else:
            host["left"] = min(host["left"], proposal["left"])
            host["right"] = max(host["right"], proposal["right"])
            host["merged_count"] += 1
    return sorted(merged, key=lambda line: line_value(line, width / 2)), edges


def make_pairs(lines, width, height):
    pairs = []
    for first_index, first in enumerate(lines):
        for second_index in range(first_index + 1, len(lines)):
            second = lines[second_index]
            if abs(first["angle_deg"] - second["angle_deg"]) > PARAMETERS["pair_max_angle_difference_deg"]:
                continue
            left = max(first["left"], second["left"])
            right = min(first["right"], second["right"])
            if right - left < PARAMETERS["pair_min_overlap_width_fraction"] * width:
                continue
            gaps = [line_value(second, horizontal) - line_value(first, horizontal) for horizontal in (left, right)]
            if min(gaps) < PARAMETERS["corridor_height_fraction"][0] * height:
                continue
            if max(gaps) > PARAMETERS["corridor_height_fraction"][1] * height:
                continue
            slope = (first["slope"] + second["slope"]) / 2
            center_intercept = (first["intercept"] + second["intercept"]) / 2
            midpoint = (left + right) / 2
            gap = line_value(second, midpoint) - line_value(first, midpoint)
            top_intercept = center_intercept - gap / 2
            bottom_intercept = center_intercept + gap / 2
            polygon = np.asarray([
                [left, top_intercept + slope * left], [right, top_intercept + slope * right],
                [right, bottom_intercept + slope * right], [left, bottom_intercept + slope * left],
            ], dtype=float)
            pairs.append({
                "line_indices": [first_index, second_index],
                "x_range": [left, right], "gap": gap, "inner_gap": gap,
                "angle_deg": math.degrees(math.atan(slope)),
                "top_line": {"a": top_intercept, "b": slope},
                "bottom_line": {"a": bottom_intercept, "b": slope},
                "poly": polygon,
                "sources": ["line_supported", "line_supported"],
            })
    return pairs


def edge_support(edges, line, left, right):
    height, width = edges.shape
    return base.line_edge_support(edges, line["a"], line["b"], left, right, width, height)


def corridor_iou(first, second):
    first_area = abs(cv2.contourArea(first["poly"].astype(np.float32)))
    second_area = abs(cv2.contourArea(second["poly"].astype(np.float32)))
    intersection, _ = cv2.intersectConvexConvex(first["poly"].astype(np.float32), second["poly"].astype(np.float32))
    union = first_area + second_area - intersection
    return float(intersection / union) if union > 0 else 0.0


def choose_pairs(pairs):
    kept = []
    for pair in sorted(pairs, key=lambda proposal: proposal["score"], reverse=True):
        if all(corridor_iou(pair, other) < PARAMETERS["pair_nms_iou"] for other in kept):
            kept.append(pair)
    return kept


def match_metrics(predictions, tags):
    truth = [tag["bbox"] for tag in tags]
    record = {"predictions": len(predictions), "gt": len(truth)}
    for threshold in (0.3, 0.5, 0.7):
        matches = base.match_greedy(predictions, truth, threshold)
        matched_predictions = {match["pred"] for match in matches}
        matched_truth = {match["gt"] for match in matches}
        record[str(threshold)] = {
            "matched": len(matches),
            "precision": len(matches) / len(predictions) if predictions else 0.0,
            "recall": len(matches) / len(truth) if truth else 0.0,
            "unmatched_predictions": sorted(set(range(len(predictions))) - matched_predictions),
            "missed_gt": sorted(set(range(len(truth))) - matched_truth),
            "matches": matches,
        }
    return record


def evaluate_coverage(pairs, user_rails, tags):
    results = []
    for index, user_rail in enumerate(user_rails):
        truth = [
            tag["bbox"] for tag in tags
            if base.point_in_poly(
                ((tag["bbox"][0] + tag["bbox"][2]) / 2, (tag["bbox"][1] + tag["bbox"][3]) / 2),
                user_rail["poly"],
            )
        ]
        contained = [
            any(base.corridor_contains(pair, box, 0) for pair in pairs)
            for box in truth
        ]
        results.append({
            "rail": index, "gt": len(truth), "fully_contained_tags": sum(contained),
            "containment": sum(contained) / len(truth) if truth else 0.0,
        })
    return results


def overlay(image, selected, predictions, tags, user_rails, path):
    rendered = image.copy()
    for user_rail in user_rails:
        cv2.polylines(rendered, [user_rail["poly"].astype(np.int32)], True, (255, 100, 0), 2)
    for pair in selected:
        cv2.polylines(rendered, [pair["poly"].astype(np.int32)], True, (0, 180, 255), 2)
    matches = base.match_greedy(predictions, [tag["bbox"] for tag in tags], 0.5)
    matched = {match["pred"] for match in matches}
    for index, box in enumerate(predictions):
        coordinates = [round(value) for value in box]
        color = (0, 200, 0) if index in matched else (0, 0, 255)
        cv2.rectangle(rendered, tuple(coordinates[:2]), tuple(coordinates[2:]), color, 1)
    base.save_image(path, rendered)


def process(image_path):
    image = base.read_image(image_path)
    height, width = image.shape[:2]
    lines, edges = detect_long_lines(image)
    pairs = make_pairs(lines, width, height)
    for pair in pairs:
        left, right = pair["x_range"]
        supports = [edge_support(edges, pair[name], left, right) for name in ("top_line", "bottom_line")]
        pair["edge_support"] = supports
        pair["score"] = (right - left) / width * min(supports)
        crop, inverse, corridor_height = base.flatten_corridor(image, pair)
        boxes, _, _ = legacy_relative_detector(crop, corridor_height)
        pair["boxes"] = map_boxes(boxes, inverse)
        pair["current_boxes"] = map_boxes(base.detect_tags_in_corridor(crop, corridor_height)[0], inverse)
    eligible = [pair for pair in pairs if len(pair["boxes"]) >= 2]
    selected = choose_pairs(eligible)
    tags, user_rails, _ = base.load_annotations(image_path.with_suffix(".json"))
    scope = {
        "all_pairs_relative": [box for pair in pairs for box in pair["boxes"]],
        "selected_pairs_relative": [box for pair in selected for box in pair["boxes"]],
        "all_pairs_current": [box for pair in pairs for box in pair["current_boxes"]],
        "selected_pairs_current": [box for pair in selected for box in pair["current_boxes"]],
    }
    metrics = {name: match_metrics(boxes, tags) for name, boxes in scope.items()}
    predictions = scope["selected_pairs_relative"]
    output_dir = OUTPUT / image_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay(image, selected, predictions, tags, user_rails, output_dir / "overlay.jpg")
    record = {
        "image": image_path.stem,
        "long_lines": len(lines), "pairs": len(pairs), "selected_pairs": len(selected),
        "metrics": metrics, "coverage": evaluate_coverage(selected, user_rails, tags),
        "selected": selected, "all_proposals": pairs,
        "predictions": predictions,
        "annotation_sha256": hashlib.sha256(image_path.with_suffix(".json").read_bytes()).hexdigest(),
    }
    (output_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=json_value), encoding="utf-8")
    print(image_path.stem[:8], "lines", len(lines), "pairs", len(pairs), "selected", len(selected), "preds", len(predictions), "m05", metrics["selected_pairs_relative"]["0.5"]["matched"], flush=True)
    return record


def json_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-prefix", default="")
    args = parser.parse_args()
    results = []
    for image_path in sorted(base.NEW_DIR.iterdir()):
        if image_path.suffix.lower() not in (".jpg", ".jpeg") or not image_path.stem.startswith(args.image_prefix):
            continue
        if not image_path.with_suffix(".json").exists():
            continue
        results.append(process(image_path))
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "parameters": PARAMETERS,
        "scope": "six-image development experiment, not a blind validation or production route",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "results": results,
    }
    filename = "summary.json" if not args.image_prefix else f"summary_{args.image_prefix}.json"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_value), encoding="utf-8")
    print("report", OUTPUT / filename)


if __name__ == "__main__":
    main()
