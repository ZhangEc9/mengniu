import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluate_price_recall import load_gt_boxes, load_predictions, match_boxes
from top_rail_slot_experiment import detect_slots, read_image, save_image


def normalized(box, width, height):
    left, top, right, bottom = box
    return {"bbox": [round(left / width * 1000), round(top / height * 1000),
                     round(right / width * 1000), round(bottom / height * 1000)]}


def main():
    parser = argparse.ArgumentParser(description="固定算法在未调参图上评测，不以 GT 生成候选")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--rail-top", type=int, required=True)
    parser.add_argument("--rail-bottom", type=int, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    image = read_image(args.image)
    height, width = image.shape[:2]
    gt_width, gt_height, gt_boxes = load_gt_boxes(args.gt)
    if (width, height) != (gt_width, gt_height):
        raise ValueError("标注尺寸与输入图像不一致")
    if not 0 <= args.rail_top < args.rail_bottom <= height:
        parser.error("导轨范围不在图像内")

    boxes, _ = detect_slots(image, args.rail_top, args.rail_bottom, augment=True)
    rail_gt = [item for item in gt_boxes if args.rail_top <= item["pixel_box"][1] < args.rail_bottom]
    matches, _, _ = match_boxes(rail_gt, [normalized(box, width, height) for box in boxes],
                                width, height, 0.5, False)
    report = {
        "image": str(args.image), "image_sha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
        "gt": str(args.gt), "rail_pixels": [args.rail_top, args.rail_bottom],
        "rail_selection": "image-only visual selection before running detector; GT not used in inference",
        "rail_gt_count": len(rail_gt), "predicted_count": len(boxes), "matched_iou_05": len(matches),
        "precision": len(matches) / len(boxes) if boxes else 0,
        "recall": len(matches) / len(rail_gt) if rail_gt else 0,
        "boxes": boxes, "matched_gt_indices": [match["gt_index"] for match in matches],
        "missed_gt_indices": sorted(set(range(len(rail_gt))) - {match["gt_index"] for match in matches}),
    }
    if args.baseline:
        baseline = load_predictions(args.baseline)
        rail_baseline = [tag for tag in baseline if args.rail_top <=
                         float(tag["bbox"][1]) / 1000 * height < args.rail_bottom]
        baseline_matches, _, _ = match_boxes(rail_gt, rail_baseline, width, height, 0.5, True)
        report["baseline"] = {"pipeline": str(args.baseline), "rail_predictions": len(rail_baseline),
                              "matched_iou_05": len(baseline_matches),
                              "price_matched": sum(bool(match["price_ok"]) for match in baseline_matches)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    visual = image.copy()
    import cv2
    for box in boxes:
        cv2.rectangle(visual, tuple(box[:2]), tuple(box[2:]), (0, 255, 0), 2)
    save_image(args.output_dir / "candidate_vis.jpg", visual)
    (args.output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("rail_gt_count", "predicted_count", "matched_iou_05",
                                                  "precision", "recall")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
