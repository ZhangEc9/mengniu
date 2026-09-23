import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluate_price_recall import load_gt_boxes, match_boxes


def read_image(path):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图像：{path}")
    return image


def save_image(path, image):
    success, encoded = cv2.imencode(path.suffix, image)
    if not success:
        raise RuntimeError(f"无法编码图像：{path}")
    encoded.tofile(str(path))


def intersection_over_union(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    overlap = max(0, right - left) * max(0, bottom - top)
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return overlap / (first_area + second_area - overlap) if overlap else 0.0


def contour_boxes(image, rail_top, rail_bottom, block_size, constant):
    gray = cv2.cvtColor(image[rail_top:rail_bottom], cv2.COLOR_BGR2GRAY)
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block_size, constant)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        left, top, width, height = cv2.boundingRect(contour)
        if 45 <= width <= 115 and 30 <= height <= 65:
            boxes.append([left, rail_top + top, left + width, rail_top + top + height])
    boxes.sort(key=lambda box: -(box[2] - box[0]) * (box[3] - box[1]))
    accepted = []
    for box in boxes:
        if all(intersection_over_union(box, other) < 0.5 for other in accepted):
            accepted.append(box)
    return sorted(accepted, key=lambda box: box[0]), mask


def detect_slots(image, rail_top, rail_bottom, augment):
    boxes, mask = contour_boxes(image, rail_top, rail_bottom, 21, 5)
    if augment:
        passes = [(rail_top, rail_bottom, 31, 1),
                  (rail_top + 2, rail_bottom - 5, 21, 5),
                  (rail_top + 9, rail_bottom - 3, 21, 5)]
        for pass_top, pass_bottom, block_size, constant in passes:
            additional, _ = contour_boxes(image, pass_top, pass_bottom, block_size, constant)
            for candidate in additional:
                width = candidate[2] - candidate[0]
                if all(max(0, min(candidate[2], existing[2]) - max(candidate[0], existing[0])) < 0.2 * width
                       for existing in boxes):
                    boxes.append(candidate)
    return sorted(boxes, key=lambda box: box[0]), mask


def main():
    parser = argparse.ArgumentParser(description="顶部导轨物理边缘候选实验，不使用 GT 生成框")
    parser.add_argument("--rail-top", type=int, default=93)
    parser.add_argument("--rail-bottom", type=int, default=160)
    parser.add_argument("--augment", action="store_true", help="补充多阈值下有独立边缘的候选，不等距补框")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    test_dir = Path(__file__).resolve().parent
    stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
    image = read_image(test_dir / "images" / f"{stem}.jpg")
    if not 0 <= args.rail_top < args.rail_bottom <= image.shape[0]:
        parser.error("导轨范围不在图像内")
    output_dir = args.output_dir or test_dir / ("顶部导轨多阈值找框实验" if args.augment else "顶部导轨物理找框实验")
    output_dir.mkdir(parents=True, exist_ok=True)

    boxes, mask = detect_slots(image, args.rail_top, args.rail_bottom, args.augment)
    save_image(output_dir / "threshold_mask.png", mask)
    visual = image.copy()
    for index, box in enumerate(boxes, 1):
        cv2.rectangle(visual, tuple(box[:2]), tuple(box[2:]), (0, 255, 0), 2)
        cv2.putText(visual, str(index), (box[0], max(20, box[1] - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    save_image(output_dir / "slots_vis.jpg", visual)
    predictions = [{"bbox": [round(box[0] / image.shape[1] * 1000),
                             round(box[1] / image.shape[0] * 1000),
                             round(box[2] / image.shape[1] * 1000),
                             round(box[3] / image.shape[0] * 1000)]} for box in boxes]
    _, _, ground_truth = load_gt_boxes(test_dir / "ground_truth" / f"{stem}.json")
    top_ground_truth = [item for item in ground_truth if item["pixel_box"][1] < 250]
    matches, _, _ = match_boxes(top_ground_truth, predictions, image.shape[1], image.shape[0], 0.5, False)
    matched_gt = {match["gt_index"] for match in matches}
    matched_pred = {match["pred_index"] for match in matches}
    report = {
        "image": stem, "rail_pixels": [args.rail_top, args.rail_bottom],
        "method": "adaptive_threshold_contours_not_grid_interpolation",
        "augmented_multi_threshold": args.augment,
        "candidate_boxes": boxes, "top_gt": len(top_ground_truth),
        "predicted": len(boxes), "matched_iou_05": len(matches),
        "recall_iou_05": len(matches) / len(top_ground_truth),
        "precision_iou_05": len(matches) / len(boxes) if boxes else 0,
        "missed_gt_indices": sorted(set(range(len(top_ground_truth))) - matched_gt),
        "unmatched_pred_indices": sorted(set(range(len(boxes))) - matched_pred),
        "matches": matches,
        "note": "顶部导轨区间由人工目视选定；GT 只用于评测。单图开发结果不能证明泛化。",
    }
    (output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "slots.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["index", "x0", "y0", "x1", "y1", "matched_gt_index", "iou", "gt_price"])
        by_prediction = {match["pred_index"]: match for match in matches}
        for index, box in enumerate(boxes):
            match = by_prediction.get(index)
            writer.writerow([index, *box, match["gt_index"] if match else "",
                             round(match["iou"], 4) if match else "",
                             top_ground_truth[match["gt_index"]]["label"] if match else ""])
    print(json.dumps({key: report[key] for key in ("predicted", "top_gt", "matched_iou_05",
                                                  "precision_iou_05", "missed_gt_indices")}, ensure_ascii=False))
    print(output_dir)


if __name__ == "__main__":
    main()
