import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run_full_pipeline as pipeline
from top_rail_slot_experiment import read_image, save_image

SYSTEM_PROMPT = "你是零售价签读数助手。图中仅有一个目标价签。只读这个价签的实际零售价格，不猜测，不补齐模糊数字，不读商品包装上的数字。"
USER_PROMPT = "请只输出JSON对象：{\"price\": \"xx.xx\" 或 null, \"readable\": true 或 false}。价格必须完整且清晰可辨；若存在多个互相冲突的价格或不清楚，则 price 为 null 且 readable 为 false。不要返回bbox、解释或其它文本。"
SCORED_USER_PROMPT = "请只输出JSON对象：{\"price\": \"xx.xx\" 或 null, \"readable\": true 或 false, \"price_confidence\": 0到1之间的数字}。price_confidence 只表示当前价格数字是否被完整、清晰地读对，不是价签位置置信度；看不清必须返回 null、false、0。不要猜数字、返回bbox或解释。"
REGION_SYSTEM_PROMPT = "你是价签数字转录助手。图像是单张零售价签的右上价格区域，不要猜测看不清的数字，也不要补全小数。"
REGION_USER_PROMPT = "仅转录图中完整清晰的价格数字，返回JSON对象 {\"price\": \"xx.xx\" 或 null, \"readable\": true 或 false}。若不完整或模糊，则返回 null 和 false。不要解释。"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="调用现有价签 API 对物理候选裁块单独读价")
    parser.add_argument("--indices", type=int, nargs="+", default=[2, 3, 5])
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--horizontal-margin", type=float, default=0.08)
    parser.add_argument("--vertical-margin", type=float, default=0.15)
    parser.add_argument("--scale", type=float, default=2.2)
    parser.add_argument("--price-region", action="store_true", help="只裁价签右上价格区域，不包含条码")
    parser.add_argument("--with-price-confidence", action="store_true", help="要求 API 额外返回价格读数置信度")
    parser.add_argument("--slots-csv", type=Path, help="默认读取顶部导轨物理找框实验/slots.csv")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not 0 <= args.horizontal_margin <= 0.2 or not 0 <= args.vertical_margin <= 0.4 or args.scale <= 0:
        parser.error("裁块留白或缩放参数不合理")
    test_dir = Path(__file__).resolve().parent
    stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
    image_path = test_dir / "images" / f"{stem}.jpg"
    image = read_image(image_path)
    slots_path = args.slots_csv or test_dir / "顶部导轨物理找框实验" / "slots.csv"
    with slots_path.open(encoding="utf-8-sig", newline="") as handle:
        slots = {int(row["index"]): row for row in csv.DictReader(handle)}
    if any(index not in slots for index in args.indices):
        parser.error("候选序号不存在")
    if not pipeline.PRICE_APPID or not pipeline.PRICE_SECRET:
        raise RuntimeError("缺少价签 API 配置")
    system_prompt = REGION_SYSTEM_PROMPT if args.price_region else SYSTEM_PROMPT
    user_prompt = REGION_USER_PROMPT if args.price_region else USER_PROMPT
    if args.with_price_confidence:
        if args.price_region:
            parser.error("本次只对完整价签裁块测试 price_confidence")
        user_prompt = SCORED_USER_PROMPT
    output_dir = args.output_dir or test_dir / "顶部导轨局部读价实验" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir.mkdir(parents=True, exist_ok=False)
    metadata = {"image_sha256": sha256(image_path.read_bytes()), "slot_indices": args.indices,
                "slots_csv": str(slots_path), "slots_sha256": sha256(slots_path.read_bytes()),
                "model_note": "现有 AISM 价签 API（项目配置的 Qwen3-VL-Plus）",
                "api_endpoint_sha256": sha256(pipeline.PRICE_API_URL.encode()),
                "prompts": {"system": system_prompt, "user": user_prompt},
                "timeout": args.timeout, "price_region": args.price_region,
                "with_price_confidence": args.with_price_confidence,
                "crop_margin": {"horizontal_width_fraction": args.horizontal_margin,
                                                   "vertical_height_fraction": args.vertical_margin}, "scale": args.scale,
                "note": "该次仅为单尺度诊断，不满足多次共识验收；GT 不参与裁剪或推理。"}
    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    for index in args.indices:
        row = slots[index]
        left, top, right, bottom = [int(row[key]) for key in ("x0", "y0", "x1", "y1")]
        width, height = right - left, bottom - top
        if args.price_region:
            crop_box = [max(0, left + round(width * 0.48)), max(0, top + round(height * 0.10)),
                        min(image.shape[1], right), min(image.shape[0], top + round(height * 0.75))]
        else:
            margin_x, margin_y = round(width * args.horizontal_margin), round(height * args.vertical_margin)
            crop_box = [max(0, left - margin_x), max(0, top - margin_y),
                        min(image.shape[1], right + margin_x), min(image.shape[0], bottom + margin_y)]
        crop = image[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
        enlarged = cv2.resize(crop, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_CUBIC)
        crop_path = output_dir / f"slot_{index:02d}_x{args.scale:g}.jpg"
        save_image(crop_path, enlarged)
        result = {"index": index, "slot_bbox": [left, top, right, bottom], "crop_bbox": crop_box,
                  "crop_sha256": sha256(crop_path.read_bytes()),
                  "gt_price_for_evaluation_only": row["gt_price"] or None,
                  "gt_match_iou_for_evaluation_only": row["iou"] or None}
        started = time.monotonic()
        try:
            image_url = pipeline.upload_to_mengniu_oss(crop_path)
            if not image_url:
                raise RuntimeError("OSS 上传失败")
            result["uploaded_url_sha256"] = sha256(image_url.encode())
            body = {"image": image_url, "system_text": system_prompt, "text": user_prompt,
                    "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
                    "unionId": "", "apiVersion": "0.0.2"}
            timestamp = str(int(time.time() * 1000))
            headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
                       "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
                       "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
            response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=args.timeout)
            (output_dir / f"slot_{index:02d}_response.txt").write_text(response.text, encoding="utf-8")
            envelope = response.json()
            result["http_status"] = response.status_code
            result["business_status"] = envelope.get("status")
            if response.status_code != 200 or envelope.get("status") != 0:
                raise RuntimeError(f"API 错误: HTTP {response.status_code}, status {envelope.get('status')}")
            page_content = (envelope.get("payload") or {}).get("result", {}).get("page_content", "")
            result["page_content"] = page_content
            parsed = pipeline.parse_robust_json(page_content)
            result["parsed"] = parsed
            result["price"] = parsed.get("price") if isinstance(parsed, dict) else None
            result["readable"] = parsed.get("readable") if isinstance(parsed, dict) else None
            if args.with_price_confidence and isinstance(parsed, dict):
                try:
                    score = float(parsed.get("price_confidence"))
                    result["price_confidence"] = score if 0 <= score <= 1 else None
                except (TypeError, ValueError):
                    result["price_confidence"] = None
            result["price_matches_gt"] = str(result["price"]) == str(row["gt_price"]) if row["gt_price"] else None
        except Exception as exc:
            result["error"] = str(exc)
        result["elapsed_sec"] = round(time.monotonic() - started, 3)
        results.append(result)
        (output_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: result.get(key) for key in ("index", "price", "gt_price_for_evaluation_only",
                                                          "price_matches_gt", "error", "elapsed_sec")}, ensure_ascii=False), flush=True)
    print(output_dir)


if __name__ == "__main__":
    main()
