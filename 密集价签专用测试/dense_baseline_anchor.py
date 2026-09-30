"""Reproduce the effective dense-tag references without another API request."""

import hashlib
import json
from pathlib import Path

import top_rail_slot_experiment as detector
import rail_numbered_read as numbered
from evaluate_price_recall import load_gt_boxes, load_predictions, match_boxes


HERE = Path(__file__).resolve().parent
STEM = "45727462_6_first_normal_1782463362055_B04C0D8E"
OUTPUT = HERE / "导轨优先后续实验" / "有效基线复现"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(predictions, annotation_path, check_price=True):
    width, height, truth = load_gt_boxes(annotation_path)
    matches, _, _ = match_boxes(truth, predictions, width, height, 0.5, check_price)
    return {
        "gt": len(truth), "predictions": len(predictions), "matched_0.5": len(matches),
        "cached_price_matched": sum(bool(match["price_ok"]) for match in matches) if check_price else None,
        "annotation_sha256": sha256(annotation_path),
    }


def normalized(boxes, width, height):
    return [
        {"bbox": [round(box[0] / width * 1000), round(box[1] / height * 1000),
                  round(box[2] / width * 1000), round(box[3] / height * 1000)]}
        for box in boxes
    ]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    image_path = HERE / "images" / f"{STEM}.jpg"
    image = detector.read_image(image_path)
    height, width = image.shape[:2]
    original_annotation = HERE / "ground_truth" / f"{STEM}.json"
    new_annotation = HERE / "新数据" / f"{STEM}.json"
    cached_pipeline = HERE / "密集型多阈值组合实验" / "standard_pred" / f"{STEM}.pipeline.json"
    predictions = load_predictions(cached_pipeline)
    old_metrics = evaluate(predictions, original_annotation)
    new_metrics = evaluate(predictions, new_annotation)
    if (old_metrics["predictions"], old_metrics["matched_0.5"], old_metrics["cached_price_matched"]) != (99, 54, 27):
        raise AssertionError(old_metrics)

    top_boxes, _ = detector.detect_slots(image, 93, 160, augment=True)
    _, _, original_truth = load_gt_boxes(original_annotation)
    top_truth = [tag for tag in original_truth if tag["pixel_box"][1] < 250]
    top_matches, _, _ = match_boxes(top_truth, normalized(top_boxes, width, height), width, height, 0.5, False)
    if (len(top_boxes), len(top_truth), len(top_matches)) != (19, 17, 17):
        raise AssertionError((len(top_boxes), len(top_truth), len(top_matches)))
    numbered.render_numbered_strip(image, top_boxes, 93, 160, 0, width, OUTPUT / "top_numbered_reference.jpg")

    red_dir = HERE / "整排编号一次读价实验" / "red_circle_20260923_165708"
    input_path = red_dir / "input.json"
    metadata = json.loads(input_path.read_text(encoding="utf-8"))
    slots = numbered.make_slots(image, *metadata["rail"], *metadata["x_range"])
    if slots != metadata["slots"]:
        raise AssertionError("Legacy red-region candidate geometry changed")
    price_path = red_dir / "readings.json"
    raw_readings = json.loads(price_path.read_text(encoding="utf-8"))
    readings = {int(index): reading for index, reading in raw_readings.items()}
    scoped_truth = [
        tag for tag in original_truth
        if metadata["rail"][0] - 15 <= tag["pixel_box"][1] < metadata["rail"][1] + 15
        and metadata["x_range"][0] <= (tag["pixel_box"][0] + tag["pixel_box"][2]) / 2 < metadata["x_range"][1]
    ]
    red_predictions = normalized(slots, width, height)
    for index, prediction in enumerate(red_predictions, 1):
        reading = readings[index]
        prediction["price"] = reading["price"] if reading.get("readable") is True else ""
    red_matches, _, _ = match_boxes(scoped_truth, red_predictions, width, height, 0.5, True)
    red_price_matches = sum(bool(match["price_ok"]) for match in red_matches)
    if (len(slots), len(scoped_truth), len(red_matches), red_price_matches) != (7, 6, 6, 6):
        raise AssertionError((len(slots), len(scoped_truth), len(red_matches), red_price_matches))
    numbered.render_numbered_strip(
        image, slots, *metadata["rail"], *metadata["x_range"], OUTPUT / "red_numbered_reference.jpg",
    )
    report = {
        "image": STEM,
        "image_sha256": sha256(image_path),
        "api_requests": 0,
        "scope": "Frozen legacy geometry reproduction and cached-price replay; NOT a new API reading or automatic region localization.",
        "legacy_99": {"original_gt": old_metrics, "new_gt": new_metrics},
        "top": {
            "region_source": "legacy manually chosen image region",
            "rail": [93, 160], "boxes": top_boxes, "predictions": len(top_boxes),
            "gt": len(top_truth), "matched_0.5": len(top_matches),
        },
        "red": {
            "region_source": "legacy manually chosen continuous segment",
            "rail": metadata["rail"], "x_range": metadata["x_range"], "boxes": slots,
            "predictions": len(slots), "gt": len(scoped_truth),
            "matched_0.5": len(red_matches), "cached_price_matched": red_price_matches,
        },
        "source_sha256": {
            path.name: sha256(path)
            for path in (Path(__file__), HERE / "top_rail_slot_experiment.py", HERE / "rail_numbered_read.py")
        },
        "cached_response_sha256": {"legacy_pipeline": sha256(cached_pipeline), "red_readings": sha256(price_path)},
    }
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"legacy_original": old_metrics, "legacy_new": new_metrics,
                      "top": [len(top_boxes), len(top_matches)],
                      "red": [len(slots), len(red_matches), red_price_matches]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
