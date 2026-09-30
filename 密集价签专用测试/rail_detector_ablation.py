"""Separate corridor localization from local detector changes."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np

import rail_first_experiment as base
import top_rail_slot_experiment as legacy
from rail_followup_experiment import run_stage_b


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "导轨优先后续实验" / "检测器消融"
PASSES = ((0, 0, 21, 5), (0, 0, 31, 1), (2, 5, 21, 5), (9, 3, 21, 5))


def legacy_relative_detector(crop, corridor_height):
    scale = corridor_height / 67.0
    accepted = []
    main_count = 0
    for index, (top_offset, bottom_offset, block_size, constant) in enumerate(PASSES):
        top_offset = round(top_offset * scale)
        bottom_offset = round(bottom_offset * scale)
        stop = crop.shape[0] - bottom_offset
        if stop <= top_offset:
            continue
        gray = cv2.cvtColor(crop[top_offset:stop], cv2.COLOR_BGR2GRAY)
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block_size, constant,
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        proposed = []
        for contour in contours:
            left, top, width, height = cv2.boundingRect(contour)
            if 45 * scale <= width <= 115 * scale and 30 * scale <= height <= 65 * scale:
                proposed.append([left, top + top_offset, left + width, top + top_offset + height])
        proposed = sorted(base.dedup_iou(proposed), key=lambda box: box[0])
        if index == 0:
            accepted = list(proposed)
            main_count = len(accepted)
        else:
            for candidate in proposed:
                width = candidate[2] - candidate[0]
                if all(
                    max(0, min(candidate[2], other[2]) - max(candidate[0], other[0])) < 0.2 * width
                    for other in accepted
                ):
                    accepted.append(candidate)
    touching = sum(box[1] <= 2 or box[3] >= crop.shape[0] - 2 for box in accepted)
    return sorted(accepted, key=lambda box: box[0]), touching, main_count


def legacy_fixed_detector(crop, corridor_height):
    accepted = []
    main_count = 0
    for index, (top_offset, bottom_offset, block_size, constant) in enumerate(PASSES):
        stop = crop.shape[0] - bottom_offset
        if stop <= top_offset:
            continue
        gray = cv2.cvtColor(crop[top_offset:stop], cv2.COLOR_BGR2GRAY)
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block_size, constant,
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        proposed = []
        for contour in contours:
            left, top, width, height = cv2.boundingRect(contour)
            if 45 <= width <= 115 and 30 <= height <= 65:
                proposed.append([left, top + top_offset, left + width, top + top_offset + height])
        proposed = sorted(base.dedup_iou(proposed), key=lambda box: box[0])
        if index == 0:
            accepted = list(proposed)
            main_count = len(accepted)
        else:
            for candidate in proposed:
                width = candidate[2] - candidate[0]
                if all(
                    max(0, min(candidate[2], other[2]) - max(candidate[0], other[0])) < 0.2 * width
                    for other in accepted
                ):
                    accepted.append(candidate)
    touching = sum(box[1] <= 2 or box[3] >= crop.shape[0] - 2 for box in accepted)
    return sorted(accepted, key=lambda box: box[0]), touching, main_count


def flatten_user_rail(image, polygon):
    edges = base.user_rail_edges(polygon)
    top = edges["top"]
    bottom = edges["bottom"]
    slope = (top[1][1] - top[0][1]) / (top[1][0] - top[0][0])
    midpoint = (edges["x0"] + edges["x1"]) / 2
    top_y = base.y_on_edge(top, midpoint)
    bottom_y = base.y_on_edge(bottom, midpoint)
    rail = {
        "x_range": [max(0, edges["x0"]), min(image.shape[1], edges["x1"])],
        "angle_deg": math.degrees(math.atan(slope)),
        "inner_gap": bottom_y - top_y,
        "top_line": {"a": top_y - slope * midpoint, "b": slope},
        "bottom_line": {"a": bottom_y - slope * midpoint, "b": slope},
    }
    return base.flatten_corridor(image, rail)


def metrics(boxes, truth):
    return {
        "predictions": len(boxes), "gt": len(truth),
        "m03": len(base.match_greedy(boxes, truth, 0.3)),
        "m05": len(base.match_greedy(boxes, truth, 0.5)),
        "m07": len(base.match_greedy(boxes, truth, 0.7)),
        "boxes": boxes,
    }


def map_boxes(boxes, inverse):
    quads = [base.map_box_back(box, inverse) for box in boxes]
    return [[quad[:, 0].min(), quad[:, 1].min(), quad[:, 0].max(), quad[:, 1].max()] for quad in quads]


def multiband_detector(crop, corridor_height):
    proposed = []
    for fraction in (0.5, 0.65, 0.8, 1.0):
        band_height = max(16, round(corridor_height * fraction))
        for position in (0.0, 0.5, 1.0):
            start = round((crop.shape[0] - band_height) * position)
            stop = min(crop.shape[0], start + band_height)
            if stop <= start:
                continue
            boxes, _, _ = legacy_relative_detector(crop[start:stop], stop - start)
            proposed.extend([[box[0], box[1] + start, box[2], box[3] + start] for box in boxes])
    accepted = sorted(base.dedup_iou(proposed, 0.5), key=lambda box: box[0])
    touching = sum(box[1] <= 2 or box[3] >= crop.shape[0] - 2 for box in accepted)
    return accepted, touching, len(accepted)


def run_oracle_diagnostic():
    results = []
    for annotation in sorted(base.NEW_DIR.glob("*.json")):
        image_path = next(path for path in base.NEW_DIR.glob(annotation.stem + ".j*") if path.suffix != ".json")
        image = base.read_image(image_path)
        tags, rails, _ = base.load_annotations(annotation)
        per_rail = []
        for index, rail in enumerate(rails):
            truth = [
                tag["bbox"] for tag in tags
                if base.point_in_poly(
                    ((tag["bbox"][0] + tag["bbox"][2]) / 2, (tag["bbox"][1] + tag["bbox"][3]) / 2),
                    rail["poly"],
                )
            ]
            crop, inverse, corridor_height = flatten_user_rail(image, rail["poly"])
            variants = {}
            for name, detector in (
                ("current", base.detect_tags_in_corridor),
                ("legacy_relative", legacy_relative_detector),
                ("legacy_fixed", legacy_fixed_detector),
                ("multiband", multiband_detector),
            ):
                boxes, _, _ = detector(crop, corridor_height)
                variants[name] = metrics(map_boxes(boxes, inverse), truth)
            per_rail.append({"rail": index, "variants": variants})
        results.append({"image": annotation.stem, "rails": per_rail})
    return {"scope": "GT corridors: diagnostic only, NOT automatic results", "results": results}


def run_automatic(detector):
    original = base.detect_tags_in_corridor
    base.detect_tags_in_corridor = detector
    results = []
    try:
        for annotation in sorted(base.NEW_DIR.glob("*.json")):
            image_path = next(path for path in base.NEW_DIR.glob(annotation.stem + ".j*") if path.suffix != ".json")
            image = base.read_image(image_path)
            height, width = image.shape[:2]
            edges = base.canny_edges(image)
            entities = base.detect_entities(image)
            rows = base.group_rows(entities, width, edges)
            rails = [rail for rail in (base.build_corridor(row, width, height, edges) for row in rows) if rail]
            rail_boxes, details = run_stage_b(image, edges, rails)
            boxes = [box for index in sorted(rail_boxes) for box in rail_boxes[index]]
            tags, user_rails, _ = base.load_annotations(annotation)
            result = {"image": annotation.stem, "metrics": metrics(boxes, [tag["bbox"] for tag in tags])}
            result["per_rail"] = []
            for index, user_rail in enumerate(user_rails):
                truth = [
                    tag["bbox"] for tag in tags
                    if base.point_in_poly(
                        ((tag["bbox"][0] + tag["bbox"][2]) / 2, (tag["bbox"][1] + tag["bbox"][3]) / 2),
                        user_rail["poly"],
                    )
                ]
                result["per_rail"].append({"rail": index, "metrics": metrics(boxes, truth)})
            results.append(result)
            print(annotation.stem[:8], {key: value for key, value in result["metrics"].items() if key != "boxes"})
    finally:
        base.detect_tags_in_corridor = original
    return {"scope": "automatic corridors, all predictions included", "results": results}


def self_check():
    annotation = next(base.NEW_DIR.glob("45727462*.json"))
    image = base.read_image(annotation.with_suffix(".jpg"))
    old_boxes, _ = legacy.detect_slots(image, 93, 160, True)
    boxes, _, _ = legacy_relative_detector(image[93:160], 67)
    mapped = [[box[0], box[1] + 93, box[2], box[3] + 93] for box in boxes]
    assert mapped == old_boxes, (mapped, old_boxes)
    tags, _, _ = base.load_annotations(annotation)
    truth = [tag["bbox"] for tag in tags if tag["bbox"][1] < 250]
    result = metrics(mapped, truth)
    assert result["m05"] == 17 and len(mapped) == 19
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-only", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"legacy_reproduction": self_check(), "oracle_diagnostic": run_oracle_diagnostic()}
    if not args.oracle_only:
        report["automatic_legacy_relative"] = run_automatic(legacy_relative_detector)
        report["automatic_legacy_fixed"] = run_automatic(legacy_fixed_detector)
        report["automatic_multiband"] = run_automatic(multiband_detector)
    report["source_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), HERE / "rail_first_experiment.py", HERE / "top_rail_slot_experiment.py")
    }
    path = OUTPUT / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("legacy reproduction: 19 candidates, 17/17 matches; PASS")
    print("report:", path)


if __name__ == "__main__":
    main()
