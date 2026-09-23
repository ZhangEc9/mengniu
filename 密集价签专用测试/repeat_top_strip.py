import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run_full_pipeline as pipeline
from evaluate_price_recall import load_gt_boxes, match_boxes


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate_top(tags, ground_truth, strip_height, image_height):
    predictions = []
    for tag in tags:
        if not isinstance(tag, dict) or not tag.get("bbox") or len(tag["bbox"]) < 4:
            continue
        bbox = tag["bbox"]
        predictions.append({**tag, "bbox": [
            bbox[0], round(bbox[1] * strip_height / image_height),
            bbox[2], round(bbox[3] * strip_height / image_height),
        ]})
    matches, _, _ = match_boxes(ground_truth, predictions, 2000, image_height, 0.5, True)
    return {
        "candidate_count": len(tags),
        "top_gt": len(ground_truth),
        "matched": len(matches),
        "price_matched": sum(bool(match["price_ok"]) for match in matches),
        "matches": [{"gt_index": match["gt_index"], "pred_index": match["pred_index"],
                     "iou": match["iou"], "price_ok": match["price_ok"]} for match in matches],
    }


def main():
    parser = argparse.ArgumentParser(description="固定顶部条带输入，逐次记录原始响应与后处理差异")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 必须大于零")

    test_dir = Path(__file__).resolve().parent
    stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
    image_path = test_dir / "密集条带放大复检结果" / "crops" / f"{stem}_strip_00_x2.jpg"
    source_path = test_dir / "images" / f"{stem}.jpg"
    gt_path = test_dir / "ground_truth" / f"{stem}.json"
    prompt_suffix = "_final"
    prompt_paths = [ROOT / "prompts" / f"price_{kind}_prompt{prompt_suffix}.txt"
                    for kind in ("system", "user")]
    image_bytes = image_path.read_bytes()
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (1000, 4000):
        raise ValueError("输入应为原图 y=0..500 的 2 倍放大条带（4000×1000）")
    image_width, image_height, gt_boxes = load_gt_boxes(gt_path)
    if (image_width, image_height) != (2000, 1500):
        raise ValueError("标注图尺寸变化，需重新核对映射坐标")
    top_gt = [box for box in gt_boxes if box["pixel_box"][1] < 250]
    system_prompt, user_prompt = [path.read_text(encoding="utf-8").strip() for path in prompt_paths]
    if not pipeline.PRICE_APPID or not pipeline.PRICE_SECRET:
        raise RuntimeError("未配置价签识别接口凭据")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = args.output_dir or test_dir / "顶部同输入重复测试" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "run_id": run_id,
        "input_image": str(image_path), "input_sha256": sha256(image_bytes),
        "source_image_sha256": sha256(source_path.read_bytes()),
        "crop_original_pixels": [0, 0, 2000, 500], "scale": 2,
        "upload_dimensions": [4000, 1000], "bbox_scale": 1000,
        "prompt_suffix": prompt_suffix,
        "prompt_sha256": {path.name: sha256(path.read_bytes()) for path in prompt_paths},
        "prompt_text": {"system": system_prompt, "user": user_prompt},
        "api_endpoint_sha256": sha256(pipeline.PRICE_API_URL.encode()),
        "pipeline_sha256": sha256((ROOT / "run_full_pipeline.py").read_bytes()),
        "postprocess_sha256": sha256((ROOT / "price_postprocess.py").read_bytes()),
        "evaluator_sha256": sha256((ROOT / "evaluate_price_recall.py").read_bytes()),
        "request_timeout_seconds": args.timeout, "repeats": args.repeats,
        "ground_truth": str(gt_path), "ground_truth_count_top": len(top_gt),
    }
    write_json(output_dir / "metadata.json", metadata)
    image_url = pipeline.upload_to_mengniu_oss(image_path)
    if not image_url:
        raise RuntimeError("OSS 上传失败；metadata 已保存")
    metadata["uploaded_url_sha256"] = sha256(image_url.encode())
    write_json(output_dir / "metadata.json", metadata)

    body = {"image": image_url, "system_text": system_prompt, "text": user_prompt,
            "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
            "unionId": "", "apiVersion": "0.0.2"}
    summaries = []
    for index in range(1, args.repeats + 1):
        attempt = {"index": index, "started_at": datetime.now().astimezone().isoformat()}
        started = time.monotonic()
        try:
            timestamp = str(int(time.time() * 1000))
            headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
                       "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
                       "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
            response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=args.timeout)
            attempt["elapsed_sec"] = round(time.monotonic() - started, 3)
            attempt["http_status"] = response.status_code
            (output_dir / f"attempt_{index:02d}_response.txt").write_text(response.text, encoding="utf-8")
            envelope = response.json()
            attempt["business_status"] = envelope.get("status")
            page_content = (envelope.get("payload") or {}).get("result", {}).get("page_content", "")
            write_json(output_dir / f"attempt_{index:02d}_raw.json", {
                "http_status": response.status_code, "business_status": envelope.get("status"),
                "page_content": page_content,
            })
            if response.status_code != 200 or envelope.get("status") != 0:
                raise RuntimeError(f"API 错误: HTTP {response.status_code}, status {envelope.get('status')}, message {envelope.get('message')}")
            parsed = pipeline.parse_robust_json(page_content)
            regular, promotion = pipeline.split_price_and_promotion_tags(parsed)
            clean = [tag for tag in regular if tag.get("price") and str(tag["price"]).strip().lower() not in ("none", "null", "")]
            filtered = pipeline.smart_consecutive_repetition_filter(clean, img_w=4000, img_h=1000)
            filtered = pipeline.filter_price_range(filtered)
            promotions = pipeline.filter_price_range(pipeline.valid_promotion_tags(promotion))
            write_json(output_dir / f"attempt_{index:02d}_stages.json", {
                "parsed": parsed, "raw_regular": regular, "raw_promotion": promotion,
                "nonempty_price": clean, "filtered_regular": filtered, "filtered_promotion": promotions,
            })
            attempt["stage_counts"] = {"raw_regular": len(regular), "nonempty_price": len(clean),
                                        "filtered_regular": len(filtered), "filtered_promotion": len(promotions)}
            attempt["raw_top_eval"] = evaluate_top(clean, top_gt, 500, image_height)
            attempt["filtered_top_eval"] = evaluate_top(filtered, top_gt, 500, image_height)
        except Exception as exc:
            attempt["elapsed_sec"] = round(time.monotonic() - started, 3)
            attempt["error"] = str(exc)
        summaries.append(attempt)
        write_json(output_dir / "summary.json", {"metadata": "metadata.json", "attempts": summaries})
        print(f"{index}/{args.repeats}: {json.dumps(attempt, ensure_ascii=False)}", flush=True)
    print(f"结果目录: {output_dir}")


if __name__ == "__main__":
    main()
