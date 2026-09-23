import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluate_price_recall import load_gt_boxes, load_predictions, match_boxes


def metric(gt_boxes, predictions, width, height):
    matches, _, _ = match_boxes(gt_boxes, predictions, width, height, 0.5, True)
    correct = sum(bool(match["price_ok"]) for match in matches)
    return {
        "predictions": len(predictions), "position_matches": len(matches),
        "price_correct": correct, "matched_wrong_price": len(matches) - correct,
        "unmatched_predictions": len(predictions) - len(matches),
        "position_recall": len(matches) / len(gt_boxes),
        "prediction_hit_rate": len(matches) / len(predictions) if predictions else 0,
        "price_recall": correct / len(gt_boxes),
    }, matches


def main():
    parser = argparse.ArgumentParser(description="只扫描 price_confidence，不使用 confidence")
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    width, height, gt_boxes = load_gt_boxes(args.gt)
    predictions = load_predictions(args.pred)
    baseline, original_matches = metric(gt_boxes, predictions, width, height)
    original_status = {match["pred_index"]: "correct_price" if match["price_ok"] else "wrong_price"
                       for match in original_matches}
    thresholds = (0, 0.85, 0.90, 0.92, 0.94, 0.96, 0.98, 0.99)
    rows = []
    for threshold in thresholds:
        retained = [prediction for prediction in predictions
                    if prediction.get("price_confidence") is None
                    or float(prediction["price_confidence"]) >= threshold]
        dropped_indices = [index for index, prediction in enumerate(predictions)
                           if prediction.get("price_confidence") is not None
                           and float(prediction["price_confidence"]) < threshold]
        metrics, _ = metric(gt_boxes, retained, width, height)
        removed = {"unmatched": 0, "wrong_price": 0, "correct_price": 0}
        for index in dropped_indices:
            removed[original_status.get(index, "unmatched")] += 1
        rows.append({"price_confidence_min": threshold,
                     "scored_retained": sum(p.get("price_confidence") is not None for p in retained),
                     "unscored_retained": sum(p.get("price_confidence") is None for p in retained),
                     "removed_unmatched_before_rematch": removed["unmatched"],
                     "removed_wrong_price_before_rematch": removed["wrong_price"],
                     "removed_correct_price_before_rematch": removed["correct_price"], **metrics})
    report = {"gt": str(args.gt), "pred": str(args.pred), "iou": 0.5,
              "field": "price_confidence", "unscored_policy": "keep_and_mark_for_review",
              "note": "阈值后重新按 IoU 一对一匹配；移除分类按过滤前匹配。单图扫描不是阈值标定。",
              "baseline": baseline, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with args.output.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['price_confidence_min']:.2f} 保留={row['predictions']} "
              f"定位={row['position_matches']} 价格正确={row['price_correct']} "
              f"误框={row['unmatched_predictions']} 错价={row['matched_wrong_price']} "
              f"删误框/错价/正确={row['removed_unmatched_before_rematch']}/"
              f"{row['removed_wrong_price_before_rematch']}/{row['removed_correct_price_before_rematch']}")


if __name__ == "__main__":
    main()
