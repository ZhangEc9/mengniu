"""API semantic regions plus local geometric boundary verification."""

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import run_full_pipeline as pipeline


SYSTEM_PROMPT = "你是货架价签区域定位助手。只根据图像定位，不读价，不生成单张价签框，不按商品间距补框。只返回合法JSON。"
REGION_PROMPT = """找出图中所有真实的价签导轨/价签排区域。此步不是找单个价签，而是为后续本地图像算法提供每条价签带。
价签排是货架前沿连续的一排小纸质/电子价签。空导轨、灯带、格栅、商品上的数字都不是价签排。
每条价签排单独返回：沿整段实际价签的上下边缘画窄四边形，左右覆盖可见整条价签排。
上边要在价签纸上方，下边要在纸下方，不能只包含大号价格数字，也不要包含一整层商品。
倾斜排必须给出倾斜的四边形，不能强行画成水平窄矩形而截掉两端；留意最顶排、最底排和左右边缘。
同一排悬挂的大促销纸可以不纳入窄导轨带，但应另外返回kind:"hanging"区域，不要漏掉。
四个点严格按左上、右上、右下、左下排列。坐标为当前图片归一化0到1000的[x,y]，不是像素。
只输出{"regions":[{"id":1,"kind":"row","polygon":[[0,100],[1000,100],[1000,150],[0,150]]}]}。
不要输出价格和单张价签框。没有价签区域返回空regions。"""


def read_image(path):
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    return image


def save_image(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix, image)
    if not success:
        raise ValueError(f"Cannot encode image: {path}")
    encoded.tofile(str(path))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Api:
    def __init__(self, max_calls, timeout):
        self.max_calls = max_calls
        self.timeout = timeout
        self.calls = 0
        self.lock = threading.Lock()

    def call(self, image_path, system_prompt, prompt, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        request = {"image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                   "system_text": system_prompt, "text": prompt,
                   "endpoint_sha256": hashlib.sha256(pipeline.PRICE_API_URL.encode()).hexdigest()}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result_path = output_dir / "result.json"
        if result_path.exists():
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == fingerprint:
                return saved["parsed"]
            raise ValueError(f"Cache mismatch; use a new output directory: {output_dir}")
        save_json(output_dir / "request.json", {**request, "fingerprint": fingerprint})
        if not pipeline.PRICE_APPID or not pipeline.PRICE_SECRET:
            raise RuntimeError("Missing configured price API credentials")
        image_url = pipeline.upload_to_mengniu_oss(image_path)
        if not image_url:
            raise RuntimeError("OSS upload failed")
        body = {"image": image_url, "system_text": system_prompt, "text": prompt, "userId": "",
                "envSystemName": "", "userName": "", "envSystemVersion": "", "unionId": "", "apiVersion": "0.0.2"}
        started = time.monotonic()
        attempts = []
        for attempt in range(3):
            with self.lock:
                if self.calls >= self.max_calls:
                    raise RuntimeError("API call budget exhausted")
                self.calls += 1
                call_index = self.calls
            print(f"[API {call_index}/{self.max_calls}] {image_path.name} attempt={attempt + 1}", flush=True)
            timestamp = str(int(time.time() * 1000))
            headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
                       "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
                       "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
            try:
                response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=(12, self.timeout))
                break
            except requests.ConnectTimeout as exc:
                attempts.append({"attempt": attempt + 1, "error": str(exc)})
                save_json(output_dir / "connection_errors.json", attempts)
                if attempt == 2:
                    raise
        (output_dir / "response.txt").write_text(response.text, encoding="utf-8")
        response.raise_for_status()
        envelope = response.json()
        if envelope.get("status") != 0:
            raise RuntimeError(f"API business status: {envelope.get('status')}")
        content = ((envelope.get("payload") or {}).get("result") or {}).get("page_content", "")
        parsed = pipeline.parse_robust_json(content)
        if parsed is None:
            raise ValueError("API response could not be parsed")
        save_json(result_path, {"fingerprint": fingerprint, "parsed": parsed,
                               "elapsed_sec": time.monotonic() - started})
        return parsed


def parse_regions(parsed, width, height):
    items = parsed.get("regions") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        raise ValueError("Expected regions array")
    regions, invalid = [], []
    for index, item in enumerate(items):
        try:
            poly = np.asarray(item["polygon"], dtype=float)
            if poly.size == 8:
                poly = poly.reshape(4, 2)
            elif poly.shape == (4, 4):
                points = np.unique(poly.reshape(-1, 2), axis=0)
                if len(points) != 4:
                    raise ValueError("Ambiguous segment endpoints")
                poly = points
            if poly.shape == (4, 2):
                ordered = poly[np.argsort(poly[:, 0], kind="stable")]
                left = ordered[:2][np.argsort(ordered[:2, 1])]
                right = ordered[2:][np.argsort(ordered[2:, 1])]
                poly = np.asarray([left[0], right[0], right[1], left[1]])
            if poly.shape != (4, 2) or not np.isfinite(poly).all() or poly.min() < 0 or poly.max() > 1000:
                raise ValueError("Invalid normalized polygon")
            poly *= [width / 1000, height / 1000]
            if not cv2.isContourConvex(poly.astype(np.float32)) or cv2.contourArea(poly.astype(np.float32)) <= 0:
                raise ValueError("Non-convex or empty region")
            if poly[0, 0] >= poly[1, 0] or poly[3, 0] >= poly[2, 0] or np.mean(poly[:2, 1]) >= np.mean(poly[2:, 1]):
                raise ValueError("Incorrect polygon ordering")
            regions.append({"id": index + 1, "kind": item.get("kind", "row"), "api_polygon": poly.tolist()})
        except (KeyError, ValueError, TypeError) as exc:
            invalid.append({"item": item, "error": str(exc)})
    return regions, invalid


def region_overlay(image, regions, path):
    canvas = image.copy()
    for region in regions:
        poly = np.asarray(region["api_polygon"], dtype=np.int32)
        cv2.polylines(canvas, [poly], True, (0, 180, 255), 3)
        cv2.putText(canvas, f"R{region['id']}:{region['kind']}", (int(poly[0, 0]) + 6, max(22, int(poly[0, 1]) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 180, 255), 2, cv2.LINE_AA)
    save_image(path, canvas)


def process_image(path, output, api):
    image = read_image(path)
    height, width = image.shape[:2]
    directory = output / path.stem
    regions, invalid = parse_regions(api.call(path, SYSTEM_PROMPT, REGION_PROMPT, directory / "region_api"), width, height)
    save_json(directory / "regions.json", {"image": str(path), "image_size": [width, height],
              "regions": regions, "invalid": invalid, "gt_used_in_inference": False})
    region_overlay(image, regions, directory / "region_overlay.jpg")
    return {"image": path.stem, "regions": len(regions), "invalid": invalid}


def main():
    parser = argparse.ArgumentParser(description="API price-tag-region experiment, not individual-tag detection")
    parser.add_argument("--images", type=Path, default=HERE / "新数据")
    parser.add_argument("--output", type=Path, default=HERE / "API区域本地找框实验" / "region_v1")
    parser.add_argument("--stems", nargs="*")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    images = sorted(path for path in args.images.iterdir() if path.suffix.lower() in (".jpg", ".jpeg", ".png")
                    and (not args.stems or any(path.stem.startswith(prefix) for prefix in args.stems)))
    api = Api(args.max_calls, args.timeout)
    summaries = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(process_image, path, args.output, api): path for path in images}
        for future in as_completed(pending):
            try:
                summaries.append(future.result())
            except Exception as exc:
                summaries.append({"image": pending[future].stem, "error": str(exc)})
            save_json(args.output / "run_summary.json", {"api_calls_this_run": api.calls, "images": summaries})
            print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
