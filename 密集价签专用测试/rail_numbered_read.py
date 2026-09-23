"""Locate rail stickers locally and read numbered slots in one API request."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run_full_pipeline as pipeline
from evaluate_price_recall import load_gt_boxes, load_predictions, match_boxes, normalized_bbox_to_pixels
from top_rail_slot_experiment import detect_slots, read_image, save_image


SYSTEM_PROMPT = "你是零售价签读数助手。图上方的编号是人工画在图片外的索引，不是价签上的价格。只转录各彩色框内实体价签的实际零售价格，不猜测。"
USER_PROMPT = (
    "图中是一整排价签，每个绿色框上方有唯一 ID。请按 ID 分别读绿色框内的实际商品价签，"
    "不能按等距顺序推断，不能把邻框价格挪入本框；若框包含多个价签、没有完整价签或数字模糊，"
    "该 ID 返回 price:null、readable:false。必须返回每个 ID，且只输出 JSON 数组："
    '[{"id":1,"price":"8.90","readable":true},...]。不要输出或修改 bbox。'
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_slots(image, rail_top, rail_bottom, x_min, x_max):
    candidates, _ = detect_slots(image, rail_top, rail_bottom, augment=True)
    height, width = image.shape[:2]
    slots = []
    for box in candidates:
        left, top, right, bottom = box
        if not x_min <= (left + right) / 2 < x_max:
            continue
        slots.append([max(0, left - 5), max(0, top - 6), min(width, right + 5), min(height, bottom + 10)])
    return slots


def render_numbered_strip(image, slots, rail_top, rail_bottom, x_min, x_max, output_path):
    image_height, image_width = image.shape[:2]
    crop_top, crop_bottom = max(0, rail_top - 16), min(image_height, rail_bottom + 16)
    crop_left, crop_right = max(0, x_min - 20), min(image_width, x_max + 20)
    header_height = 40
    canvas = np.full((header_height + crop_bottom - crop_top, crop_right - crop_left, 3), 255, dtype=np.uint8)
    canvas[header_height:] = image[crop_top:crop_bottom, crop_left:crop_right]
    for index, (left, top, right, bottom) in enumerate(slots, 1):
        cv2.rectangle(canvas, (left - crop_left, top - crop_top + header_height),
                      (right - crop_left, bottom - crop_top + header_height), (0, 180, 0), 2)
        cv2.putText(canvas, f"ID{index:02d}", (left - crop_left, 28), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(canvas, ((left + right) // 2 - crop_left, 34),
                 ((left + right) // 2 - crop_left, top - crop_top + header_height - 2), (0, 180, 0), 1)
    save_image(output_path, cv2.resize(canvas, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    return [crop_left, crop_top, crop_right, crop_bottom]


def parse_readings(content, count):
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("tags")
    if not isinstance(parsed, list):
        raise ValueError("API did not return a JSON list")
    readings = {}
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Malformed reading")
        index = int(item.get("id"))
        if index < 1 or index > count or index in readings:
            raise ValueError(f"Duplicate or unexpected ID: {index}")
        readings[index] = item
    if len(readings) != count:
        raise ValueError(f"Missing IDs: {sorted(set(range(1, count + 1)) - readings.keys())}")
    return readings


def evaluate(args, slots, readings, output_dir):
    if not args.gt:
        return
    width, height, truth = load_gt_boxes(args.gt)
    rail_truth = [item for item in truth if args.rail_top - 15 <= item["pixel_box"][1] < args.rail_bottom + 15
                  and args.x_min <= (item["pixel_box"][0] + item["pixel_box"][2]) / 2 < args.x_max]
    predictions = []
    for index, (left, top, right, bottom) in enumerate(slots, 1):
        reading = readings[index]
        price = reading.get("price") if reading.get("readable") is True else None
        predictions.append({"bbox": [round(left / width * 1000), round(top / height * 1000),
                                      round(right / width * 1000), round(bottom / height * 1000)],
                            "price": str(price) if price is not None else "", "id": index})
    matches, _, _ = match_boxes(rail_truth, predictions, width, height, 0.5, True)
    report = {"rail_gt_count": len(rail_truth), "local_slots": len(slots), "position_matches": len(matches),
              "exact_price_matches": sum(bool(item["price_ok"]) for item in matches),
              "matches": [{**item, "gt_price": rail_truth[item["gt_index"]]["label"],
                           "api_price": predictions[item["pred_index"]]["price"]} for item in matches]}
    if args.baseline:
        baseline = [item for item in load_predictions(args.baseline)
                    if args.rail_top - 15 <= normalized_bbox_to_pixels(item["bbox"], width, height)[1] < args.rail_bottom + 15
                    and args.x_min <= sum(normalized_bbox_to_pixels(item["bbox"], width, height)[::2]) / 2 < args.x_max]
        original, _, _ = match_boxes(rail_truth, baseline, width, height, 0.5, True)
        report["baseline"] = {"predictions": len(baseline), "position_matches": len(original),
                              "exact_price_matches": sum(bool(item["price_ok"]) for item in original)}
    (output_dir / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "matches"}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="单排本地找框、整排一次 API 按编号读价的离线试验")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--rail-top", type=int, required=True)
    parser.add_argument("--rail-bottom", type=int, required=True)
    parser.add_argument("--x-min", type=int, default=0)
    parser.add_argument("--x-max", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gt", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    image = read_image(args.image)
    if args.x_max is None:
        args.x_max = image.shape[1]
    if not 0 <= args.rail_top < args.rail_bottom <= image.shape[0]:
        parser.error("Invalid rail range")
    if not 0 <= args.x_min < args.x_max <= image.shape[1]:
        parser.error("Invalid x range")
    slots = make_slots(image, args.rail_top, args.rail_bottom, args.x_min, args.x_max)
    if not slots:
        raise ValueError("No local slots detected; refusing API request")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    upload_path = args.output_dir / "numbered_strip_x2.jpg"
    crop_bounds = render_numbered_strip(image, slots, args.rail_top, args.rail_bottom, args.x_min, args.x_max, upload_path)
    metadata = {"image": str(args.image), "image_sha256": sha256(args.image.read_bytes()),
                "rail": [args.rail_top, args.rail_bottom], "x_range": [args.x_min, args.x_max],
                "crop_box": crop_bounds, "slots": slots,
                "uploaded_image_sha256": sha256(upload_path.read_bytes()),
                "prompts": {"system": SYSTEM_PROMPT, "user": USER_PROMPT},
                "note": "Rail chosen visually; slots found without GT. GT and baseline used only for evaluation."}
    (args.output_dir / "input.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if not pipeline.PRICE_APPID or not pipeline.PRICE_SECRET:
        raise RuntimeError("Missing API configuration")
    image_url = pipeline.upload_to_mengniu_oss(upload_path)
    if not image_url:
        raise RuntimeError("OSS upload failed")
    body = {"image": image_url, "system_text": SYSTEM_PROMPT, "text": USER_PROMPT,
            "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
            "unionId": "", "apiVersion": "0.0.2"}
    timestamp = str(int(time.time() * 1000))
    headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
               "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
               "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
    response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=args.timeout)
    (args.output_dir / "response.txt").write_text(response.text, encoding="utf-8")
    response.raise_for_status()
    envelope = response.json()
    if envelope.get("status") != 0:
        raise RuntimeError(f"API business status: {envelope.get('status')}")
    content = ((envelope.get("payload") or {}).get("result") or {}).get("page_content", "")
    readings = parse_readings(content, len(slots))
    (args.output_dir / "readings.json").write_text(json.dumps(readings, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluate(args, slots, readings, args.output_dir)
    print(f"Output: {args.output_dir}")


if __name__ == "__main__":
    main()
