# -*- coding: utf-8 -*-

import argparse
import csv
import json
import re
from pathlib import Path


DEFAULT_GT_DIR = Path(r"D:\Shixi\mengniu\标注数据集_AnyLabeling")
DEFAULT_PRED_DIR = Path(r"D:\Shixi\mengniu\基线一次_修正版\有价签\final_results")
DEFAULT_VIS_DIR = Path(r"D:\Shixi\mengniu\基线一次_修正版\有价签\vis_images")
DEFAULT_OUTPUT_DIR = Path(r"D:\Shixi\mengniu\评测结果_基线一次_修正版_有价签")


def rectangle_from_points(points):
    if not points:
        return None
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def normalized_bbox_to_pixels(bbox, image_width, image_height):
    if not bbox or len(bbox) < 4:
        return None
    x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
    option_a = (
        min(x1, x2) / 1000.0 * image_width,
        min(y1, y2) / 1000.0 * image_height,
        max(x1, x2) / 1000.0 * image_width,
        max(y1, y2) / 1000.0 * image_height,
    )
    option_b = (
        min(y1, y2) / 1000.0 * image_width,
        min(x1, x2) / 1000.0 * image_height,
        max(y1, y2) / 1000.0 * image_width,
        max(x1, x2) / 1000.0 * image_height,
    )
    width_a = option_a[2] - option_a[0]
    height_a = option_a[3] - option_a[1]
    width_b = option_b[2] - option_b[0]
    height_b = option_b[3] - option_b[1]
    if height_a > image_height * 0.45 or (
        height_a > width_a * 2.5 and width_b > height_b * 0.5
    ):
        return option_b
    return option_a


def iou(box_a, box_b):
    if not box_a or not box_b:
        return 0.0
    ix1 = max(box_a[0], box_b[0])
    iy1 = max(box_a[1], box_b[1])
    ix2 = min(box_a[2], box_b[2])
    iy2 = min(box_a[3], box_b[3])
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def optional_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_gt_label(label):
    text = str(label or "").strip()
    if not text or text.upper() == "NA":
        return {"kind": "unclear"}

    if text.upper().startswith("S_"):
        second_prices = text[2:].split("_")
        if len(second_prices) == 2:
            return {
                "kind": "second_item",
                "base_price": float(second_prices[0]),
                "second_price": float(second_prices[1]),
            }

    normalized = text
    for prefix in ("PRICE_", "BUNDLE_", "SECOND_", "S_", "B_", "P_"):
        if normalized.upper().startswith(prefix):
            normalized = normalized[len(prefix):]

    if normalized.upper().startswith("S_"):
        second_prices = normalized[2:].split("_")
        if len(second_prices) == 2:
            return {
                "kind": "second_item",
                "base_price": float(second_prices[0]),
                "second_price": float(second_prices[1]),
            }
        second_chinese = re.search(r"第二[件瓶][^0-9]{0,6}(\d+(?:\.\d+)?)", normalized)
        if second_chinese:
            return {"kind": "second_item", "second_price": float(second_chinese.group(1))}

    quantity_match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:元)?(?:[_xX*])(\d+)", normalized)
    if quantity_match:
        return {
            "kind": "bundle",
            "amount": float(quantity_match.group(1)),
            "quantity": int(quantity_match.group(2)),
        }

    chinese_bundle = re.search(
        r"(?:两|2|\d+)\s*件\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元\s*(?:两|2|\d+)\s*件",
        normalized,
    )
    if chinese_bundle:
        amount = chinese_bundle.group(1) or chinese_bundle.group(2)
        quantity_match = re.search(r"(?:两|2|\d+)\s*件", normalized)
        quantity_text = quantity_match.group(0) if quantity_match else "2件"
        quantity = 2 if "两" in quantity_text else int(re.search(r"\d+", quantity_text).group())
        return {"kind": "bundle", "amount": float(amount), "quantity": quantity}

    second_chinese = re.search(r"第二[件瓶][^0-9]{0,6}(\d+(?:\.\d+)?)", normalized)
    if second_chinese:
        return {"kind": "second_item", "second_price": float(second_chinese.group(1))}

    number_match = re.search(r"\d+(?:\.\d+)?", normalized)
    if number_match:
        return {"kind": "regular", "amount": float(number_match.group(0))}
    return {"kind": "unknown", "text": text}


def pred_amounts_and_quantity(prediction):
    tag_type = str(prediction.get("tag_type") or "").strip()
    price = optional_float(prediction.get("price"))
    bundle_price = optional_float(prediction.get("bundle_price"))
    bundle_quantity = prediction.get("bundle_quantity")
    second_price = optional_float(prediction.get("second_item_price"))
    try:
        bundle_quantity = int(bundle_quantity)
    except (TypeError, ValueError):
        bundle_quantity = None
    if tag_type == "second_item_promotion":
        return {"kind": "second_item", "second_price": second_price or price}
    if tag_type == "bundle_promotion":
        return {"kind": "bundle", "amount": bundle_price or price, "quantity": bundle_quantity}
    return {"kind": "regular", "amount": price}


def price_matches(gt_box, prediction):
    gt_value = parse_gt_label(gt_box["label"])
    pred_value = pred_amounts_and_quantity(prediction)
    if gt_value["kind"] in ("unclear", "unknown") or pred_value["kind"] in ("unclear", "unknown"):
        return False
    if gt_value["kind"] == "second_item" or pred_value["kind"] == "second_item":
        gt_price = gt_value.get("second_price", gt_value.get("base_price"))
        pred_price = pred_value.get("second_price", pred_value.get("amount"))
        return optional_float(gt_price) == optional_float(pred_price)
    if gt_value["kind"] == "bundle" or pred_value["kind"] == "bundle":
        amount_equal = optional_float(gt_value.get("amount")) == optional_float(pred_value.get("amount"))
        gt_quantity = gt_value.get("quantity")
        pred_quantity = pred_value.get("quantity")
        quantity_equal = gt_quantity is None or pred_quantity is None or gt_quantity == pred_quantity
        return amount_equal and quantity_equal
    return optional_float(gt_value.get("amount")) == optional_float(pred_value.get("amount"))


def load_gt_boxes(gt_path):
    data = json.loads(gt_path.read_text(encoding="utf-8"))
    width = int(data.get("imageWidth") or 0)
    height = int(data.get("imageHeight") or 0)
    if width <= 0 or height <= 0:
        raise ValueError(f"GT 尺寸无效: {gt_path}")
    boxes = []
    for shape in data.get("shapes", []):
        if str(shape.get("shape_type", "rectangle")).lower() not in ("rectangle", "rect"):
            continue
        pixel_box = rectangle_from_points(shape.get("points", []))
        if not pixel_box:
            continue
        if parse_gt_label(shape.get("label"))["kind"] == "second_item":
            continue
        boxes.append({
            "pixel_box": pixel_box,
            "label": str(shape.get("label", "")).strip(),
            "tag_type": str((shape.get("flags") or {}).get("tag_type", "")).strip(),
            "raw_price_text": str((shape.get("flags") or {}).get("raw_price_text", "")).strip(),
            "difficult": bool(shape.get("difficult", False)),
        })
    return width, height, boxes


def load_predictions(pipeline_path):
    data = json.loads(pipeline_path.read_text(encoding="utf-8"))
    step2 = data.get("step2_price_tags") or {}
    seen = set()
    predictions = []
    for source in list(step2.get("tags") or []) + list(step2.get("promotion_tags") or []):
        if not isinstance(source, dict) or not source.get("bbox") or len(source.get("bbox")) < 4:
            continue
        raw_text = " ".join([
            str(source.get("raw_price_text", "")),
            str(source.get("raw_promotion_text", "")),
        ])
        if (
            str(source.get("tag_type", "")) == "second_item_promotion"
            or re.search(r"第二[件瓶]", raw_text)
        ):
            continue
        signature = (
            tuple(round(float(value), 3) for value in source["bbox"][:4]),
            str(source.get("tag_type", "")),
            str(source.get("price", "")),
        )
        if signature in seen:
            continue
        seen.add(signature)
        item = dict(source)
        item["_signature"] = signature
        predictions.append(item)
    return predictions


def match_boxes(gt_boxes, predictions, image_width, image_height, iou_threshold, check_price):
    candidates = []
    for gt_index, gt_box in enumerate(gt_boxes):
        for pred_index, prediction in enumerate(predictions):
            pred_pixel_box = normalized_bbox_to_pixels(prediction.get("bbox"), image_width, image_height)
            overlap = iou(gt_box["pixel_box"], pred_pixel_box)
            if overlap < iou_threshold:
                continue
            price_ok = price_matches(gt_box, prediction) if check_price else None
            candidates.append((overlap, gt_index, pred_index, pred_pixel_box, price_ok))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    matched_gt = set()
    matched_pred = set()
    matches = []
    for overlap, gt_index, pred_index, pred_pixel_box, price_ok in candidates:
        if gt_index in matched_gt or pred_index in matched_pred:
            continue
        matched_gt.add(gt_index)
        matched_pred.add(pred_index)
        matches.append({
            "gt_index": gt_index,
            "pred_index": pred_index,
            "iou": overlap,
            "pred_pixel_box": pred_pixel_box,
            "price_ok": price_ok,
        })
    return matches, matched_gt, matched_pred


def metric_totals(image_items, include_missing_predictions, iou_threshold, check_price):
    totals = {"gt_boxes": 0, "pred_boxes": 0, "matched": 0, "price_matched": 0, "gt_price_defined": 0}
    for item in image_items:
        if not include_missing_predictions and not item["has_prediction"]:
            continue
        totals["gt_boxes"] += len(item["gt_boxes"])
        totals["gt_price_defined"] += sum(
            1 for box in item["gt_boxes"]
            if parse_gt_label(box["label"])["kind"] not in ("unclear", "unknown")
        )
        if not item["has_prediction"]:
            continue
        totals["pred_boxes"] += len(item["predictions"])
        matches, _, _ = match_boxes(
            item["gt_boxes"], item["predictions"], item["image_width"], item["image_height"],
            iou_threshold, check_price,
        )
        totals["matched"] += len(matches)
        totals["price_matched"] += sum(1 for match in matches if match["price_ok"])
    return {
        **totals,
        "recall": totals["matched"] / totals["gt_boxes"] if totals["gt_boxes"] else 0.0,
        "hit_rate": totals["matched"] / totals["pred_boxes"] if totals["pred_boxes"] else 0.0,
        "price_recall": totals["price_matched"] / totals["gt_price_defined"] if totals["gt_price_defined"] else 0.0,
    }


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="计算 AnyLabeling 标注与基线预测框的召回率/命中率")
    parser.add_argument("--gt-dir", default=str(DEFAULT_GT_DIR))
    parser.add_argument("--pred-dir", default=str(DEFAULT_PRED_DIR))
    parser.add_argument("--vis-dir", default=str(DEFAULT_VIS_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--check-price", action="store_true", help="命中框再比较金额/件数")
    parser.add_argument("--count-missing-as-fn", action="store_true", help="无预测 JSON 的 GT 图片计入漏检")
    args = parser.parse_args()

    gt_dir = Path(args.gt_dir)
    pred_dir = Path(args.pred_dir)
    vis_dir = Path(args.vis_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    gt_files = {}
    duplicates = []
    for path in gt_dir.rglob("*.json"):
        if path.stem in gt_files:
            duplicates.append(str(path))
            continue
        gt_files[path.stem] = path

    pred_files = {
        path.name[: -len(".pipeline.json")]: path
        for path in pred_dir.glob("*.pipeline.json")
    }

    image_items = []
    missing_prediction = []
    errors = []
    for stem, gt_path in sorted(gt_files.items()):
        try:
            image_width, image_height, gt_boxes = load_gt_boxes(gt_path)
        except Exception as exc:
            errors.append({"file": str(gt_path), "error": str(exc)})
            continue
        pred_path = pred_files.get(stem)
        vis_exists = bool(list(vis_dir.glob(f"{stem}.vis.*")))
        if not pred_path:
            missing_prediction.append(stem)
            image_items.append({
                "stem": stem, "gt_path": str(gt_path), "pred_path": "", "has_prediction": False,
                "image_width": image_width, "image_height": image_height,
                "gt_boxes": gt_boxes, "predictions": [], "vis_exists": vis_exists,
            })
            continue
        try:
            predictions = load_predictions(pred_path)
        except Exception as exc:
            errors.append({"file": str(pred_path), "error": str(exc)})
            continue
        image_items.append({
            "stem": stem, "gt_path": str(gt_path), "pred_path": str(pred_path), "has_prediction": True,
            "image_width": image_width, "image_height": image_height,
            "gt_boxes": gt_boxes, "predictions": predictions, "vis_exists": vis_exists,
        })

    paired_stems = set(gt_files) - set(missing_prediction)
    extra_prediction = sorted(set(pred_files) - paired_stems)
    thresholds = sorted({0.3, 0.5, 0.7, float(args.iou)})
    threshold_results = {
        str(threshold): {
            "paired_only": metric_totals(image_items, False, threshold, args.check_price),
            "missing_as_fn": metric_totals(image_items, True, threshold, args.check_price),
        }
        for threshold in thresholds
    }

    per_image_rows = []
    gt_rows = []
    prediction_rows = []
    for item in image_items:
        if not item["has_prediction"]:
            per_image_rows.append({
                "image": item["stem"], "gt_boxes": len(item["gt_boxes"]), "pred_boxes": 0,
                "matched": 0, "recall": 0.0, "hit_rate": 0.0, "missing_prediction": 1,
            })
            for gt_index, gt_box in enumerate(item["gt_boxes"]):
                gt_rows.append({
                    "image": item["stem"], "gt_index": gt_index, "label": gt_box["label"],
                    "gt_box": ";".join(f"{value:.1f}" for value in gt_box["pixel_box"]),
                    "matched": 0, "iou": "0.0000", "price_ok": "",
                    "status": "missing_prediction" if args.count_missing_as_fn else "no_prediction_file",
                    "pred_box": "",
                })
            continue

        matches, _, _ = match_boxes(
            item["gt_boxes"], item["predictions"], item["image_width"], item["image_height"],
            float(args.iou), args.check_price,
        )
        gt_count = len(item["gt_boxes"])
        pred_count = len(item["predictions"])
        per_image_rows.append({
            "image": item["stem"], "gt_boxes": gt_count, "pred_boxes": pred_count,
            "matched": len(matches), "recall": len(matches) / gt_count if gt_count else 0.0,
            "hit_rate": len(matches) / pred_count if pred_count else 0.0, "missing_prediction": 0,
        })
        match_by_gt = {match["gt_index"]: match for match in matches}
        for gt_index, gt_box in enumerate(item["gt_boxes"]):
            match = match_by_gt.get(gt_index)
            gt_rows.append({
                "image": item["stem"], "gt_index": gt_index, "label": gt_box["label"],
                "gt_box": ";".join(f"{value:.1f}" for value in gt_box["pixel_box"]),
                "matched": int(bool(match)), "iou": f"{match['iou']:.4f}" if match else "0.0000",
                "price_ok": "" if not args.check_price or not match else int(bool(match["price_ok"])),
                "status": "matched" if match else "missed",
                "pred_box": ";".join(f"{value:.1f}" for value in match["pred_pixel_box"]) if match else "",
            })
        match_by_pred = {match["pred_index"]: match for match in matches}
        gt_by_index = {index: box for index, box in enumerate(item["gt_boxes"])}
        for pred_index, prediction in enumerate(item["predictions"]):
            match = match_by_pred.get(pred_index)
            pred_pixel_box = normalized_bbox_to_pixels(prediction.get("bbox"), item["image_width"], item["image_height"])
            prediction_rows.append({
                "image": item["stem"], "pred_index": pred_index, "price": prediction.get("price", ""),
                "raw_price_text": prediction.get("raw_price_text", ""), "tag_type": prediction.get("tag_type", ""),
                "confidence": prediction.get("confidence", ""), "price_confidence": prediction.get("price_confidence", ""),
                "pred_norm_bbox": ";".join(str(value) for value in prediction.get("bbox", [])),
                "pred_pixel_box": ";".join(f"{value:.1f}" for value in pred_pixel_box),
                "matched": int(bool(match)), "iou": f"{match['iou']:.4f}" if match else "0.0000",
                "gt_label": gt_by_index[match["gt_index"]]["label"] if match else "",
                "price_ok": "" if not args.check_price or not match else int(bool(match["price_ok"])),
                "status": "matched" if match else "false_positive",
            })

    main_threshold = float(args.iou)
    summary = {
        "gt_dir": str(gt_dir), "pred_dir": str(pred_dir), "vis_dir": str(vis_dir),
        "iou_threshold": main_threshold, "check_price": args.check_price,
        "exclude_second_item": True,
        "gt_images": len(gt_files), "paired_images": len(paired_stems),
        "missing_prediction_images": len(missing_prediction), "extra_prediction_images": len(extra_prediction),
        "duplicate_gt_files": duplicates, "errors": errors,
        "paired_only_main_iou": threshold_results[str(main_threshold)]["paired_only"],
        "missing_as_fn_main_iou": threshold_results[str(main_threshold)]["missing_as_fn"],
        "threshold_results": threshold_results,
        "metric_definition": {
            "recall": "命中的人工标注框数 / 人工标注框总数",
            "hit_rate": "命中的预测框数 / 预测框总数，等价 detection precision",
            "price_recall": "位置命中且价格/件数正确的人数 / 有人工金额的标注框数",
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "missing_prediction_images.json").write_text(json.dumps(missing_prediction, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(
        output_dir / "per_image_summary.csv",
        ["image", "gt_boxes", "pred_boxes", "matched", "recall", "hit_rate", "missing_prediction"],
        per_image_rows,
    )
    write_csv(
        output_dir / "gt_boxes.csv",
        ["image", "gt_index", "label", "gt_box", "matched", "iou", "price_ok", "status", "pred_box"],
        gt_rows,
    )
    write_csv(
        output_dir / "predictions.csv",
        ["image", "pred_index", "price", "raw_price_text", "tag_type", "confidence", "price_confidence", "pred_norm_bbox", "pred_pixel_box", "matched", "iou", "gt_label", "price_ok", "status"],
        prediction_rows,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n结果已保存到: {output_dir}")


if __name__ == "__main__":
    main()
