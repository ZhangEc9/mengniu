#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import csv
import difflib
import hashlib
import json
import logging
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def default_config_path(value: Optional[str]) -> Optional[Path]:
    if value:
        return Path(value)
    default = Path("config.json")
    return default if default.exists() else None


def merge_config(config: Dict[str, Any]) -> Dict[str, Any]:
    agents = config.get("agents") or {}
    request = config.get("request") or {}
    auth = agents.get("authorization") or os.environ.get("MENGNIU_API_TOKEN", "")
    return {
        "quality_url": agents.get("quality_url", ""),
        "image_base_url": agents.get("image_base_url", ""),
        "price_url": agents.get("price_url", ""),
        "sku_url": agents.get("sku_url", ""),
        "authorization": auth,
        "user_id": agents.get("user_id", ""),
        "user_system_name": agents.get("user_system_name", ""),
        "user_name": agents.get("user_name", ""),
        "environment_version": agents.get("environment_version", ""),
        "union_id": agents.get("union_id", ""),
        "api_version": agents.get("api_version", "0.0.2"),
        "timeout": int(request.get("timeout", 300)),
        "retries": int(request.get("retries", 2)),
    }


def load_flow_credentials() -> Tuple[str, str]:
    """Read credentials from the platform-generated example without duplicating secrets in config."""
    example_path = Path("generated-code.py")
    if not example_path.is_file():
        return "", ""
    source = example_path.read_text(encoding="utf-8-sig")
    appid_match = re.search(r'^appid\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    secret_match = re.search(r'^secret\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    return (
        appid_match.group(1) if appid_match else "",
        secret_match.group(1) if secret_match else "",
    )


def encode_local_image(path: Path) -> str:
    mime_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def image_source(path: Path, image_base_url: str = "") -> str:
    text = str(path)
    if text.lower().startswith(("http://", "https://")):
        return text
    if path.exists():
        if image_base_url:
            return f"{image_base_url.rstrip('/')}/{urllib.parse.quote(path.name)}"
        return encode_local_image(path)
    raise FileNotFoundError(f"图片不存在：{path}")


def http_post_json(
    url: str,
    payload: Dict[str, Any],
    authorization: str,
    timeout: int,
    retries: int,
    appid: str = "",
    secret: str = "",
) -> Any:
    # Keep the on-wire JSON representation identical to requests.post(..., json=body)
    # used by the platform-generated example so its server-side signature check matches.
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization if authorization.lower().startswith("bearer ") else f"Bearer {authorization}"
    if appid and secret:
        timestamp = str(int(time.time() * 1000))
        # Match the JSON serialization used by the platform-generated sample code.
        sign_text = f"{json.dumps(payload)}{secret}{timestamp}"
        headers.update(
            {
                "X-MN-APP-ID": appid,
                "X-MN-SIGN": hashlib.md5(sign_text.encode("utf-8")).hexdigest(),
                "X-MN-TIMESTAMP": timestamp,
            }
        )

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < retries:
            sleep_seconds = 2 ** attempt
            logging.warning("请求失败，%s 秒后重试：%s", sleep_seconds, last_error)
            time.sleep(sleep_seconds)

    raise RuntimeError(f"模型服务请求失败：{last_error}")


def extract_model_content(response: Any) -> Any:
    if not isinstance(response, dict):
        return response

    # The Mengniu flow API returns model output in payload.result.page_content.
    payload = response.get("payload")
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            for key in ("page_content", "content", "output", "data"):
                if key in result and result[key] not in (None, ""):
                    return result[key]
            return result
        if result not in (None, ""):
            return result

    if "content" in response:
        content = response["content"]
        return content[0] if isinstance(content, list) and content else content

    for key in ("data", "result", "output"):
        if key in response:
            value = response[key]
            if isinstance(value, list) and value:
                return value[0]
            if value not in (None, ""):
                return value

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if "content" in message:
            content = message["content"]
            return content[0] if isinstance(content, list) and content else content

    if "msg" in response and response.get("code") not in (200, None):
        return response["msg"]

    return response


def parse_model_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = min([index for index in (text.find("{"), text.find("[")) if index >= 0], default=-1)
    if start >= 0:
        for end in range(len(text) - 1, start, -1):
            if text[end] not in "}]":
                continue
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"模型输出不是有效JSON：{text[:200]}")


def call_agent_workflow(
    workflow: str,
    config: Dict[str, Any],
    store_image_url: str,
    sku_image_urls: Optional[List[str]] = None,
) -> Any:
    metadata = {
        "userId": config["user_id"],
        "envSystemName": config["user_system_name"],
        "userName": config["user_name"],
        "envSystemVersion": config["environment_version"],
        "unionId": config["union_id"],
        "apiVersion": config["api_version"],
    }

    if workflow == "quality":
        payload = {"target_sku_img": store_image_url, **metadata}
    elif workflow == "price":
        payload = {
            "image": store_image_url,
            "text": "请识别图片中坐标商品对应的价签。",
            "system_text": "你是一个专业的零售货架价签识别助手。你的任务是根据SKU信息识别对应的价签。",
            **metadata,
        }
    elif workflow == "sku":
        payload = {
            "system_text": "",
            "target_sku_img": sku_image_urls or [store_image_url],
            "store_img": store_image_url,
            "text": "",
            **metadata,
        }
    else:
        raise ValueError(f"未知 agent：{workflow}")

    url = config[f"{workflow}_url"]
    if not url:
        raise ValueError(f"config.json 中未配置 {workflow}_url")

    appid, secret = load_flow_credentials()
    response = http_post_json(
        url,
        payload,
        config["authorization"],
        config["timeout"],
        config["retries"],
        appid,
        secret,
    )
    if isinstance(response, dict) and "payload" in response and response.get("payload") is None:
        status = response.get("status")
        if status not in (200, "200", 0, "0", None):
            raise RuntimeError(f"agent接口返回异常 status={status}: {response.get('message', '')}")
    return parse_model_json(extract_model_content(response))


def get_first_dict(data: Any, preferred_keys: Iterable[str]) -> Optional[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in preferred_keys:
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def normalize_list(data: Any, preferred_keys: Iterable[str]) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        return []

    for key in preferred_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    list_values = [value for value in data.values() if isinstance(value, list)]
    if len(list_values) == 1:
        return [item for item in list_values[0] if isinstance(item, dict)]

    if any(key in data for key in ("sku", "price", "name", "price_tag")):
        return [data]
    return []


def normalize_sku_row(row: Dict[str, Any]) -> Dict[str, Any]:
    sku = row.get("sku") or row.get("sku_id") or row.get("sku_code") or row.get("code") or ""
    name = row.get("name") or row.get("sku_name") or row.get("product_name") or ""
    bbox = row.get("bbox") or row.get("box") or row.get("bounding_box") or row.get("coordinates") or []
    try:
        confidence = float(row.get("confidence") or row.get("score") or 0)
    except (TypeError, ValueError):
        confidence = 0
    return {"sku": str(sku).strip(), "name": str(name).strip(), "confidence": confidence, "bbox": bbox}


def normalize_price_row(row: Dict[str, Any]) -> Dict[str, Any]:
    sku = row.get("sku") or row.get("sku_id") or row.get("sku_code") or ""
    price = row.get("price") or row.get("price_value") or row.get("amount") or ""
    tag = row.get("price_tag") or row.get("tag_id") or row.get("label") or ""
    name = row.get("name") or row.get("sku_name") or row.get("product_name") or ""
    bbox = row.get("bbox") or row.get("box") or row.get("bounding_box") or row.get("coordinates") or []
    return {
        "sku": str(sku).strip(),
        "price": str(price).strip(),
        "price_tag": str(tag).strip(),
        "name": str(name).strip(),
        "bbox": bbox,
    }


def valid_bbox(bbox: Any) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        values = [float(item) for item in bbox]
    except (TypeError, ValueError):
        return False
    return values[0] < values[2] and values[1] < values[3]


def bbox_iou(left: Any, right: Any) -> float:
    if not valid_bbox(left) or not valid_bbox(right):
        return 0
    left_values = [float(item) for item in left]
    right_values = [float(item) for item in right]
    x1 = max(left_values[0], right_values[0])
    y1 = max(left_values[1], right_values[1])
    x2 = min(left_values[2], right_values[2])
    y2 = min(left_values[3], right_values[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    left_area = (left_values[2] - left_values[0]) * (left_values[3] - left_values[1])
    right_area = (right_values[2] - right_values[0]) * (right_values[3] - right_values[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0


def bbox_metrics(bbox: Any) -> Optional[Dict[str, float]]:
    if not valid_bbox(bbox):
        return None
    values = [float(item) for item in bbox]
    return {
        "x1": values[0],
        "y1": values[1],
        "x2": values[2],
        "y2": values[3],
        "cx": (values[0] + values[2]) / 2,
        "cy": (values[1] + values[3]) / 2,
        "width": abs(values[2] - values[0]),
        "height": abs(values[3] - values[1]),
    }


def normalize_bbox_scale(rows: List[Dict[str, Any]]) -> None:
    values: List[float] = []
    for row in rows:
        if valid_bbox(row.get("bbox")):
            values.extend(float(item) for item in row["bbox"])
    max_value = max(values, default=0)
    if max_value <= 1000:
        return
    scale = 1000 / max_value
    for row in rows:
        if valid_bbox(row.get("bbox")):
            row["bbox"] = [round(float(item) * scale, 3) for item in row["bbox"]]


def cluster_vertical_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid = [
        {"index": index, "metrics": bbox_metrics(row.get("bbox"))}
        for index, row in enumerate(rows)
        if bbox_metrics(row.get("bbox")) is not None
    ]
    if not valid:
        return []

    valid.sort(key=lambda item: item["metrics"]["cy"])
    heights = sorted(item["metrics"]["height"] for item in valid)
    median_height = heights[len(heights) // 2]
    threshold = max(60.0, median_height * 0.8)

    groups: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for item in valid:
        if not current or item["metrics"]["cy"] - current[-1]["metrics"]["cy"] <= threshold:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)

    result: List[Dict[str, Any]] = []
    for group in groups:
        centers = [item["metrics"]["cy"] for item in group]
        heights_in_group = [item["metrics"]["height"] for item in group]
        result.append(
            {
                "indices": [item["index"] for item in group],
                "center": sum(centers) / len(centers),
                "height": max(heights_in_group),
            }
        )
    return result


def normalize_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def name_similarity(left: str, right: str) -> float:
    left_normalized = normalize_name(left)
    right_normalized = normalize_name(right)
    if not left_normalized or not right_normalized:
        return 0
    if left_normalized == right_normalized:
        return 1
    shorter, longer = sorted((left_normalized, right_normalized), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return 0.92
    return difflib.SequenceMatcher(None, left_normalized, right_normalized).ratio()


def pick_nearest_by_horizontal(
    sku_row: Dict[str, Any],
    candidates: List[Tuple[int, Dict[str, Any]]],
) -> Optional[Tuple[int, Dict[str, Any], float]]:
    sku_metrics = bbox_metrics(sku_row.get("bbox"))
    if sku_metrics is None:
        return None

    scored: List[Tuple[float, int, Dict[str, Any]]] = []
    for index, price_row in candidates:
        price_metrics = bbox_metrics(price_row.get("bbox"))
        if price_metrics is None:
            continue
        distance = abs(sku_metrics["cx"] - price_metrics["cx"])
        scored.append((distance, index, price_row))
    if not scored:
        return None
    distance, index, price_row = min(scored)
    allowed_distance = max(
        180.0,
        (sku_metrics["width"] + price_metrics_width(price_row)) * 0.375,
    )
    if distance > allowed_distance:
        return None
    return index, price_row, distance


def price_metrics_width(price_row: Dict[str, Any]) -> float:
    metrics = bbox_metrics(price_row.get("bbox"))
    return metrics["width"] if metrics else 0


def match_sku_to_price(sku_rows: List[Dict[str, Any]], price_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    used_prices = set()

    normalize_bbox_scale(sku_rows)
    normalize_bbox_scale(price_rows)

    product_row_groups = cluster_vertical_rows(sku_rows)
    price_row_groups = cluster_vertical_rows(price_rows)
    aligned_price_group: Dict[int, Optional[int]] = {}
    for product_group_index, product_group in enumerate(product_row_groups):
        best_group: Optional[int] = None
        best_distance = float("inf")
        for price_group_index, price_group in enumerate(price_row_groups):
            distance = abs(product_group["center"] - price_group["center"])
            if distance < best_distance:
                best_distance = distance
                best_group = price_group_index
        aligned_price_group[product_group_index] = (
            best_group if best_distance <= max(180.0, product_group["height"] * 0.8) else None
        )

    for sku_position, sku_row in enumerate(sku_rows):
        matched: Optional[Tuple[int, Dict[str, Any], str, float, float]] = None

        if sku_row["sku"]:
            exact_sku = [
                (index, price_row)
                for index, price_row in enumerate(price_rows)
                if index not in used_prices and price_row["sku"] == sku_row["sku"]
            ]
            nearest = pick_nearest_by_horizontal(sku_row, exact_sku)
            if nearest:
                index, price_row, distance = nearest
                confidence = 1.0 if distance <= 180 else 0.92
                matched = (index, price_row, "exact_sku", confidence, distance)

        if matched is None and sku_row["name"] and price_rows:
            name_candidates = [
                (name_similarity(sku_row["name"], price_row["name"]), index, price_row)
                for index, price_row in enumerate(price_rows)
                if index not in used_prices and price_row["name"]
            ]
            name_candidates = [candidate for candidate in name_candidates if candidate[0] >= 0.82]
            if name_candidates:
                similarity, index, price_row = max(name_candidates)
                sku_metrics = bbox_metrics(sku_row.get("bbox"))
                price_metrics = bbox_metrics(price_row.get("bbox"))
                distance = (
                    ((sku_metrics["cx"] - price_metrics["cx"]) ** 2 + (sku_metrics["cy"] - price_metrics["cy"]) ** 2) ** 0.5
                    if sku_metrics and price_metrics
                    else 999999.0
                )
                confidence = 0.75 + similarity * 0.2
                matched = (index, price_row, "name_similarity", confidence, distance)

        if matched is None:
            product_group_index = next(
                (
                    group_index
                    for group_index, group in enumerate(product_row_groups)
                    if sku_position in group["indices"]
                ),
                None,
            )
            price_group_index = aligned_price_group.get(product_group_index) if product_group_index is not None else None
            if price_group_index is not None:
                same_row_candidates = [
                    (index, price_rows[index])
                    for index in price_row_groups[price_group_index]["indices"]
                    if index not in used_prices
                ]
                nearest = pick_nearest_by_horizontal(sku_row, same_row_candidates)
                if nearest:
                    index, price_row, distance = nearest
                    confidence = max(0.45, min(0.85, 1 - distance / 600))
                    matched = (index, price_row, "same_row_nearest", confidence, distance)

        if matched is None:
            sku_metrics = bbox_metrics(sku_row.get("bbox"))
            fallback_candidates: List[Tuple[int, float, float, int, Dict[str, Any]]] = []
            for index, price_row in enumerate(price_rows):
                if index in used_prices:
                    continue
                iou = bbox_iou(sku_row.get("bbox"), price_row.get("bbox"))
                sku_metrics_local = bbox_metrics(sku_row.get("bbox"))
                price_metrics = bbox_metrics(price_row.get("bbox"))
                if sku_metrics_local and price_metrics:
                    center_distance = (
                        (sku_metrics_local["cx"] - price_metrics["cx"]) ** 2
                        + (sku_metrics_local["cy"] - price_metrics["cy"]) ** 2
                    ) ** 0.5
                    vertical_distance = abs(sku_metrics_local["cy"] - price_metrics["cy"])
                else:
                    center_distance = 999999.0
                    vertical_distance = 999999.0
                if iou >= 0.05 or (center_distance <= 260 and vertical_distance <= 180):
                    fallback_candidates.append((0 if iou >= 0.05 else 1, -iou, center_distance, index, price_row))
            if fallback_candidates:
                _, _, center_distance, index, price_row = min(fallback_candidates)
                iou = bbox_iou(sku_row.get("bbox"), price_row.get("bbox"))
                if iou >= 0.05:
                    confidence = min(0.85, 0.55 + iou * 0.3)
                    match_type = "bbox_iou"
                else:
                    confidence = max(0.3, min(0.65, 1 - center_distance / 650))
                    match_type = "distance_fallback"
                matched = (index, price_row, match_type, confidence, center_distance)

        if matched is None:
            results.append(
                {
                    **sku_row,
                    "price": "",
                    "price_tag": "",
                    "price_bbox": [],
                    "match_type": "unmatched",
                    "match_status": "needs_human_review",
                    "match_confidence": 0,
                    "match_reason": "没有同SKU、相似名称或可信的同行价签",
                }
            )
            continue

        matched_price, price_row, match_type, confidence, distance = matched
        used_prices.add(matched_price)
        match_status = "matched" if match_type in ("exact_sku", "name_similarity") else "probable"
        results.append(
            {
                **sku_row,
                "price": price_row["price"],
                "price_tag": price_row["price_tag"],
                "price_bbox": price_row["bbox"],
                "match_type": match_type,
                "match_status": match_status,
                "match_confidence": round(confidence, 3),
                "match_reason": f"匹配距离={distance:.1f}",
            }
        )
    for index, price_row in enumerate(price_rows):
        if index not in used_prices:
            results.append(
                {
                    "sku": price_row["sku"],
                    "name": price_row["name"],
                    "confidence": 0,
                    "bbox": price_row["bbox"],
                    "price": price_row["price"],
                    "price_tag": price_row["price_tag"],
                    "price_bbox": price_row["bbox"],
                    "match_type": "price_only",
                    "match_status": "price_only",
                    "match_confidence": 0,
                    "match_reason": "识别到价签，但未找到可信关联商品",
                }
            )

    return results


def process_image(
    item: Dict[str, Any],
    config: Dict[str, Any],
    output_dir: Path,
    save_debug: bool,
    quality_only: bool = False,
) -> Dict[str, Any]:
    image_path = Path(item["image"])
    logging.info("开始处理：%s", image_path)
    source = image_source(image_path, config.get("image_base_url", ""))
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        quality = call_agent_workflow("quality", config, source)
        qc = get_first_dict(quality, ("qc_result",)) or {}
        is_valid = str(qc.get("is_valid", "")).lower() in ("true", "1", "yes")
        if not is_valid:
            logging.warning("质检未通过：%s - %s", image_path, qc.get("invalid_reason", ""))
            result = {"image": str(image_path), "status": "invalid", "qc_result": qc, "results": []}
            if save_debug:
                (output_dir / f"{image_path.stem}.quality.json").write_text(
                    json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            return result

        if quality_only:
            result = {
                "image": str(image_path),
                "status": "ok",
                "qc_result": qc,
                "results": [],
            }
            if save_debug:
                (output_dir / f"{image_path.stem}.quality.json").write_text(
                    json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            return result

        price_data = call_agent_workflow("price", config, source)
        price_rows = [
            normalize_price_row(row)
            for row in normalize_list(price_data, ("price_tags", "prices", "items", "data"))
        ]

        sku_refs = item.get("sku_refs") or []
        if isinstance(sku_refs, dict):
            ref_urls = []
            for ref_paths in sku_refs.values():
                ref_paths = [ref_paths] if isinstance(ref_paths, str) else ref_paths
                ref_urls.extend(image_source(Path(path), config.get("image_base_url", "")) for path in ref_paths)
        else:
            ref_urls = [image_source(Path(path), config.get("image_base_url", "")) for path in sku_refs]

        sku_data = call_agent_workflow("sku", config, source, ref_urls)
        sku_rows = [
            normalize_sku_row(row)
            for row in normalize_list(sku_data, ("skus", "products", "items", "data"))
        ]
        matched = match_sku_to_price(sku_rows, price_rows)

        result = {
            "image": str(image_path),
            "status": "ok",
            "qc_result": qc,
            "results": matched,
            "sku_count": len(sku_rows),
            "price_tag_count": len(price_rows),
        }
        if save_debug:
            (output_dir / f"{image_path.stem}.price.json").write_text(
                json.dumps(price_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_dir / f"{image_path.stem}.sku.json").write_text(
                json.dumps(sku_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return result
    except Exception as exc:
        logging.exception("处理失败：%s", image_path)
        return {"image": str(image_path), "status": "error", "error": str(exc), "results": []}


def collect_image_items(input_path: Path, sku_ref_dir: Optional[Path]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    if input_path.is_file() and input_path.suffix.lower() == ".json":
        manifest = load_json_file(input_path)
        if isinstance(manifest, dict):
            manifest = manifest.get("images", [])
        if not isinstance(manifest, list):
            raise ValueError("manifest 必须是 JSON 数组或包含 images 数组的对象")

        for raw_item in manifest:
            if isinstance(raw_item, str):
                item = {"image": raw_item}
            elif isinstance(raw_item, dict):
                item = dict(raw_item)
            else:
                continue

            if not Path(item["image"]).is_absolute():
                item["image"] = str(input_path.parent / item["image"])

            if item.get("sku_refs") and not isinstance(item["sku_refs"], dict):
                item["sku_refs"] = [
                    str(input_path.parent / path) if not Path(path).is_absolute() else path
                    for path in item["sku_refs"]
                ]
            items.append(item)
    elif input_path.is_dir():
        for path in sorted(input_path.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                items.append({"image": str(path)})
    elif input_path.is_file():
        items.append({"image": str(input_path)})
    else:
        raise FileNotFoundError(f"输入不存在：{input_path}")

    if sku_ref_dir:
        refs = [
            str(path)
            for path in sorted(sku_ref_dir.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        for item in items:
            item.setdefault("sku_refs", refs)

    return items


def write_outputs(final_results: List[Dict[str, Any]], output_dir: Path, json_path: Path, csv_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(final_results, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = [
        "image",
        "status",
        "sku",
        "name",
        "price",
        "price_tag",
        "confidence",
        "match_type",
        "match_status",
        "match_confidence",
        "match_reason",
        "bbox",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for image_result in final_results:
            rows = image_result.get("results") or []
            if not rows:
                writer.writerow({field: image_result.get(field, "") for field in ("image", "status")})
            for row in rows:
                writer.writerow(
                    {
                        "image": image_result.get("image", ""),
                        "status": image_result.get("status", ""),
                        "sku": row.get("sku", ""),
                        "name": row.get("name", ""),
                        "price": row.get("price", ""),
                        "price_tag": row.get("price_tag", ""),
                        "confidence": row.get("confidence", ""),
                        "match_type": row.get("match_type", ""),
                        "match_status": row.get("match_status", ""),
                        "match_confidence": row.get("match_confidence", ""),
                        "match_reason": row.get("match_reason", ""),
                        "bbox": json.dumps(row.get("bbox", []), ensure_ascii=False),
                    }
                )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量执行照片质检、价签提取和SKU识别")
    parser.add_argument("input", type=Path, help="图片文件、图片目录或manifest JSON")
    parser.add_argument("-c", "--config", type=Path, default=None, help="配置文件，默认使用当前目录的 config.json")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("output"), help="结果输出目录")
    parser.add_argument("--concurrency", type=int, default=1, help="并发图片数量")
    parser.add_argument("--sku-ref-dir", type=Path, default=None, help="SKU参考图目录，作用于所有图片")
    parser.add_argument("--save-debug", action="store_true", help="保存每个模型的原始输出")
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="只调用质量审核 API，不继续调用价签和 SKU API",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示待处理图片，不调用模型")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    setup_logging(args.verbose)

    config_path = default_config_path(str(args.config) if args.config else None)
    if not args.dry_run:
        if not config_path:
            logging.error("未找到配置文件。请先复制 config.example.json 为 config.json 并填写服务地址。")
            return 2
        config = merge_config(load_json_file(config_path))
    else:
        config = {}

    try:
        items = collect_image_items(args.input, args.sku_ref_dir)
    except Exception as exc:
        logging.error(str(exc))
        return 2

    if not items:
        logging.warning("没有找到可处理图片。")
        return 0

    logging.info("待处理图片：%d 张", len(items))
    for item in items:
        logging.info("%s", item["image"])
    if args.dry_run:
        return 0

    final_results: List[Dict[str, Any]] = []
    if args.concurrency <= 1:
        for item in items:
            final_results.append(
                process_image(item, config, args.output_dir, args.save_debug, args.quality_only)
            )
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    process_image,
                    item,
                    config,
                    args.output_dir,
                    args.save_debug,
                    args.quality_only,
                )
                for item in items
            ]
            for future in as_completed(futures):
                final_results.append(future.result())

    json_path = args.output_dir / "results.json"
    csv_path = args.output_dir / "results.csv"
    write_outputs(final_results, args.output_dir, json_path, csv_path)
    ok_count = sum(1 for result in final_results if result.get("status") == "ok")
    error_count = sum(1 for result in final_results if result.get("status") == "error")
    logging.info("完成：成功 %d，错误 %d", ok_count, error_count)
    logging.info("结果：%s", json_path)
    logging.info("表格：%s", csv_path)
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
