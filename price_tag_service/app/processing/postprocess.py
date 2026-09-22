from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


POSTPROCESS_VERSION = "service-postprocess-v3-deliver-regular-only"
BUNDLE_PROMOTION_RE = re.compile(
    r"(\d+|两)\s*件\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|￥|RMB)?", re.IGNORECASE
)
SECOND_ITEM_PROMOTION_RE = re.compile(r"第\s*(?:二|2)\s*件")
SECOND_ITEM_PRICE_RE = re.compile(
    r"第\s*(?:二|2)\s*件\D{0,8}(\d+(?:\.\d{1,2})?)\s*(?:元|块|￥|RMB)?", re.IGNORECASE
)
ADD_PROMOTION_RE = re.compile(
    r"加\s*(?:\d+|[一两])\s*(?:元|块|￥)?\s*(?:多|换|购|送)", re.IGNORECASE
)


@dataclass
class FilterEvent:
    rule: str
    message: str
    tag_id: int | None = None
    price: str | None = None
    bbox: list[float] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "tag_id": self.tag_id,
            "price": self.price,
            "bbox": self.bbox,
        }


@dataclass
class PricePostprocessResult:
    price_tags: list[dict[str, Any]]
    promotion_tags: list[dict[str, Any]]
    filter_events: list[dict[str, Any]] = field(default_factory=list)
    postprocess_version: str = POSTPROCESS_VERSION
    image_width: int | None = None
    image_height: int | None = None


def parse_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return None
    matched = re.search(r"\d+(?:\.\d{1,2})?", text)
    if not matched:
        return None
    try:
        return Decimal(matched.group())
    except InvalidOperation:
        return None


def _promotion_text(tag: dict[str, Any]) -> str:
    return " ".join(
        str(tag.get(field_name, "")).strip()
        for field_name in (
            "raw_promotion_text",
            "promotion_text",
            "raw_price_text",
            "label_text",
            "text",
        )
    ).strip()


def _normalized_bbox(tag: dict[str, Any]) -> list[float] | None:
    raw_bbox = tag.get("bbox")
    if not isinstance(raw_bbox, list) or len(raw_bbox) < 4:
        return None
    try:
        values = [float(value) for value in raw_bbox[:4]]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) for value in values):
        return None
    if any(value < -1 or value > 1001 for value in values):
        return None
    xmin, xmax = sorted((values[0], values[2]))
    ymin, ymax = sorted((values[1], values[3]))
    if xmax - xmin <= 1 or ymax - ymin <= 1:
        return None
    return [xmin, ymin, xmax, ymax]


def resolve_bbox(
    bbox: list[float], image_width: int, image_height: int
) -> tuple[int, int, int, int] | None:
    if len(bbox) < 4:
        return None
    v0, v1, v2, v3 = bbox[:4]
    xmin_a, xmax_a = min(v0, v2), max(v0, v2)
    ymin_a, ymax_a = min(v1, v3), max(v1, v3)
    width_a = (xmax_a - xmin_a) / 1000 * image_width
    height_a = (ymax_a - ymin_a) / 1000 * image_height

    ymin_b, ymax_b = min(v0, v2), max(v0, v2)
    xmin_b, xmax_b = min(v1, v3), max(v1, v3)
    width_b = (xmax_b - xmin_b) / 1000 * image_width
    height_b = (ymax_b - ymin_b) / 1000 * image_height

    if height_a > image_height * 0.45 or (
        height_a > width_a * 2.5 and width_b > height_b * 0.5
    ):
        xmin, ymin, xmax, ymax = xmin_b, ymin_b, xmax_b, ymax_b
    else:
        xmin, ymin, xmax, ymax = xmin_a, ymin_a, xmax_a, ymax_a

    def clamp(value: float, upper: int) -> int:
        return max(0, min(upper - 1, int(value)))

    return (
        clamp(xmin / 1000 * image_width, image_width),
        clamp(ymin / 1000 * image_height, image_height),
        clamp(xmax / 1000 * image_width, image_width),
        clamp(ymax / 1000 * image_height, image_height),
    )


def _iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (first[2] - first[0]) * (first[3] - first[1])
    area2 = (second[2] - second[0]) * (second[3] - second[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0


def is_pure_discount_tag(tag: dict[str, Any]) -> bool:
    raw_text = _promotion_text(tag)
    if re.search(r"^(?:[打]?\s*\d+(?:\.\d+)?\s*折|全场\s*\d+(?:\.\d+)?\s*折)$", raw_text):
        return True
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").lower()
    return tag_type in {"discount", "discount_promotion"}


def is_invalid_misread_price(tag: dict[str, Any]) -> bool:
    raw_text = _promotion_text(tag)
    price = parse_amount(tag.get("price"))
    if ADD_PROMOTION_RE.search(raw_text):
        return True
    specification = re.search(r"pro\s*(\d(?:\.\d+)?)", raw_text, re.IGNORECASE)
    if specification and price is not None and price == Decimal(specification.group(1)):
        return True
    if re.search(r"\d+\s*(?:ml|毫升|g|克|l|升)", raw_text, re.IGNORECASE):
        has_price_symbol = bool(re.search(r"[元块￥]|rmb", raw_text, re.IGNORECASE))
        has_product_word = any(
            keyword in raw_text for keyword in ("牛奶", "鲜奶", "乳", "饮品", "每日鲜语", "光明")
        )
        if has_product_word and not has_price_symbol:
            return True
    return False


def _is_hallucinated_sequence(tags: list[dict[str, Any]]) -> bool:
    if len(tags) < 5:
        return False
    bboxes = [tag["_normalized_bbox"] for tag in tags if tag.get("_normalized_bbox")]
    if len(bboxes) >= 5:
        widths = [bbox[2] - bbox[0] for bbox in bboxes]
        if max(widths) - min(widths) <= 3:
            steps = [bboxes[index + 1][0] - bboxes[index][0] for index in range(len(bboxes) - 1)]
            if max(steps) - min(steps) <= 3:
                return True
    amounts = [parse_amount(tag.get("price")) for tag in tags]
    amounts = [amount for amount in amounts if amount is not None]
    if len(amounts) >= 5:
        diffs = [round(float(amounts[index + 1] - amounts[index]), 2) for index in range(len(amounts) - 1)]
        if len(set(diffs)) == 1 and diffs[0] in {0.5, 1.0, 2.0}:
            return True
    return False


def _split_price_and_promotion(
    parsed: Any, events: list[FilterEvent]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if isinstance(parsed, list):
        raw_price_tags, raw_promotion_tags = parsed, []
    elif isinstance(parsed, dict):
        raw_price_tags = parsed.get("price_tags", [])
        raw_promotion_tags = parsed.get("promotion_tags", [])
    else:
        raw_price_tags, raw_promotion_tags = [], []

    total_raw_tags = len(raw_price_tags) + len(raw_promotion_tags)
    price_tags: list[dict[str, Any]] = []
    promotion_tags: list[dict[str, Any]] = []

    for raw_tag in raw_price_tags if isinstance(raw_price_tags, list) else []:
        if not isinstance(raw_tag, dict):
            events.append(FilterEvent("INVALID_TYPE", "价签输出不是对象"))
            continue
        tag = dict(raw_tag)
        if is_pure_discount_tag(tag):
            events.append(FilterEvent("PURE_DISCOUNT", "打折宣传不是零售价签", tag.get("id"), str(tag.get("price", ""))))
            continue
        if is_invalid_misread_price(tag):
            events.append(
                FilterEvent(
                    "MISREAD_MARKETING_OR_SPEC",
                    "营销语或商品规格被误读为价格",
                    tag.get("id"),
                    str(tag.get("price", "")),
                )
            )
            continue
        if is_second_item_promotion(tag):
            normalized_promotion = normalize_second_item_promotion(tag)
            promotion_tags.append(normalized_promotion)
            events.append(
                FilterEvent(
                    "SECOND_ITEM_PROMOTION_EXCLUDED",
                    "第二件促销不进入交付结果",
                    tag.get("id"),
                    str(normalized_promotion.get("second_item_price", "")),
                )
            )
        elif is_bundle_promotion(tag):
            normalized_bundle = normalize_bundle_promotion(tag)
            promotion_tags.append(normalized_bundle)
            events.append(
                FilterEvent(
                    "BUNDLE_PROMOTION_EXCLUDED",
                    "组合促销不进入交付结果",
                    tag.get("id"),
                    str(normalized_bundle.get("bundle_price", "")),
                )
            )
        else:
            price_tags.append(tag)

    for raw_tag in raw_promotion_tags if isinstance(raw_promotion_tags, list) else []:
        if not isinstance(raw_tag, dict):
            events.append(FilterEvent("INVALID_TYPE", "促销签输出不是对象"))
            continue
        tag = dict(raw_tag)
        if is_pure_discount_tag(tag) or is_invalid_misread_price(tag):
            events.append(FilterEvent("INVALID_PROMOTION", "无效促销宣传", tag.get("id")))
            continue
        if is_bundle_promotion(tag):
            normalized_bundle = normalize_bundle_promotion(tag)
            promotion_tags.append(normalized_bundle)
            events.append(
                FilterEvent(
                    "BUNDLE_PROMOTION_EXCLUDED",
                    "组合促销不进入交付结果",
                    tag.get("id"),
                    str(normalized_bundle.get("bundle_price", "")),
                )
            )
        else:
            normalized_promotion = normalize_second_item_promotion(tag)
            promotion_tags.append(normalized_promotion)
            events.append(
                FilterEvent(
                    "SECOND_ITEM_PROMOTION_EXCLUDED",
                    "第二件促销不进入交付结果",
                    tag.get("id"),
                    str(normalized_promotion.get("second_item_price", "")),
                )
            )

    return price_tags, promotion_tags, total_raw_tags


def _price_range_out_of_bound(
    tag: dict[str, Any], min_price: float | None, max_price: float | None
) -> bool:
    tag_types = {
        str(tag.get("tag_type") or "").lower(),
        str(tag.get("promotion_type") or "").lower(),
    }
    is_second_item = bool(tag_types & {"second_item_price", "second_item_promotion"})
    min_amounts = [] if is_second_item else [parse_amount(tag.get("price"))]
    if tag.get("bundle_price") is not None:
        min_amounts.append(parse_amount(tag.get("bundle_price")))
    max_amounts = list(min_amounts)
    if tag.get("second_item_price") is not None:
        max_amounts.append(parse_amount(tag.get("second_item_price")))

    valid_min = [amount for amount in min_amounts if amount is not None]
    valid_max = [amount for amount in max_amounts if amount is not None]
    below_min = min_price is not None and any(amount <= Decimal(str(min_price)) for amount in valid_min)
    above_max = max_price is not None and any(amount > Decimal(str(max_price)) for amount in valid_max)
    return below_min or above_max


def _prepare_bbox(
    tags: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    events: list[FilterEvent],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for tag in tags:
        bbox = _normalized_bbox(tag)
        if bbox is None:
            events.append(
                FilterEvent(
                    "INVALID_BBOX",
                    "bbox 缺失、非法、过小或越界",
                    tag.get("id"),
                    str(tag.get("price", "")),
                )
            )
            continue
        tag["_normalized_bbox"] = bbox
        tag["bbox"] = bbox
        prepared.append(tag)
    return prepared


def _clean_price_tags(
    tags: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    min_price: float | None,
    max_price: float | None,
    events: list[FilterEvent],
) -> list[dict[str, Any]]:
    priced_tags: list[dict[str, Any]] = []
    for tag in tags:
        amount = parse_amount(tag.get("price"))
        if amount is None:
            events.append(
                FilterEvent(
                    "INVALID_PRICE",
                    "价格缺失或无法解析",
                    tag.get("id"),
                    str(tag.get("price", "")),
                    tag.get("bbox"),
                )
            )
            continue
        tag["price"] = f"{amount:.2f}"
        priced_tags.append(tag)

    layer_groups: dict[Any, list[dict[str, Any]]] = {}
    for tag in priced_tags:
        layer_groups.setdefault(tag.get("shelf_layer", 1), []).append(tag)

    layer_cleaned: list[dict[str, Any]] = []
    for layer, layer_tags in layer_groups.items():
        if _is_hallucinated_sequence(layer_tags):
            events.append(
                FilterEvent(
                    "HALLUCINATED_GRID",
                    f"层级 {layer} 疑似等距网格或等差价格脑补，整行剔除",
                    price=str(layer_tags[0].get("price", "")),
                )
            )
            continue
        distinct_prices = {str(tag.get("price", "")) for tag in layer_tags}
        if len(layer_tags) >= 6 and len(distinct_prices) == 1:
            events.append(
                FilterEvent(
                    "SAME_PRICE_RUN",
                    f"层级 {layer} 连续 {len(layer_tags)} 个同价疑似自回归虚构，整行剔除",
                    price=next(iter(distinct_prices)),
                )
            )
            continue

        kept_tags: list[dict[str, Any]] = []
        last_price: str | None = None
        consecutive_count = 0
        for tag in layer_tags:
            current_price = str(tag.get("price", "")).strip()
            if current_price == last_price:
                consecutive_count += 1
            else:
                last_price = current_price
                consecutive_count = 1
            if consecutive_count <= 3:
                kept_tags.append(tag)
            else:
                events.append(
                    FilterEvent(
                        "CONSECUTIVE_DUPLICATE",
                        "连续同价数量超过 3 个",
                        tag.get("id"),
                        current_price,
                        tag.get("bbox"),
                    )
                )
        layer_cleaned.extend(kept_tags)

    size_cleaned: list[dict[str, Any]] = []
    physical_size_available = image_width > 0 and image_height > 0
    for tag in layer_cleaned:
        bbox = tag["_normalized_bbox"]
        normalized_width = bbox[2] - bbox[0]
        if normalized_width > 600:
            events.append(
                FilterEvent("OVER_WIDE_BBOX", "bbox 宽度超过全图 60%", tag.get("id"), str(tag.get("price")), bbox)
            )
            continue
        if physical_size_available:
            coords = resolve_bbox(bbox, image_width, image_height)
            width_px, height_px = coords[2] - coords[0], coords[3] - coords[1]
            if width_px > image_width * 0.60:
                events.append(
                    FilterEvent("OVER_WIDE_BBOX", "物理 bbox 宽度超过全图 60%", tag.get("id"), str(tag.get("price")), bbox)
                )
                continue
            if height_px < 12 or width_px < 20:
                events.append(
                    FilterEvent("TOO_SMALL_BBOX", f"物理尺寸过小：{width_px}x{height_px}px", tag.get("id"), str(tag.get("price")), bbox)
                )
                continue
        size_cleaned.append(tag)

    deduplicated: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
    final_tags: list[dict[str, Any]] = []
    for tag in size_cleaned:
        coords = resolve_bbox(tag["_normalized_bbox"], max(image_width, 1), max(image_height, 1))
        if any(_iou(coords, existing_coords) > 0.65 for _, existing_coords in deduplicated):
            events.append(
                FilterEvent("IOU_DUPLICATE", "IoU 大于 0.65 的重复框", tag.get("id"), str(tag.get("price")), tag.get("bbox"))
            )
            continue
        deduplicated.append((tag, coords))
        final_tags.append(tag)

    range_filtered: list[dict[str, Any]] = []
    for tag in final_tags:
        if _price_range_out_of_bound(tag, min_price, max_price):
            events.append(
                FilterEvent(
                    "PRICE_OUT_OF_RANGE",
                    f"价格不在保留区间 ({min_price}, {max_price}]",
                    tag.get("id"),
                    str(tag.get("price")),
                    tag.get("bbox"),
                )
            )
            continue
        range_filtered.append(tag)
    return range_filtered


def _clean_promotion_tags(
    tags: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    min_price: float | None,
    max_price: float | None,
    events: list[FilterEvent],
) -> list[dict[str, Any]]:
    valid_tags: list[dict[str, Any]] = []
    for tag in tags:
        price = tag.get("bundle_price") if tag.get("promotion_type") == "bundle_promotion" else tag.get("second_item_price")
        if parse_amount(price) is None:
            events.append(FilterEvent("INVALID_PROMOTION_PRICE", "促销价格缺失或无法解析", tag.get("id")))
            continue
        if _price_range_out_of_bound(tag, min_price, max_price):
            events.append(
                FilterEvent("PROMOTION_PRICE_OUT_OF_RANGE", "促销价格不在保留区间", tag.get("id"), str(price))
            )
            continue
        valid_tags.append(tag)

    deduplicated: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
    result: list[dict[str, Any]] = []
    for tag in valid_tags:
        coords = resolve_bbox(tag["_normalized_bbox"], max(image_width, 1), max(image_height, 1))
        if any(_iou(coords, existing_coords) > 0.65 for _, existing_coords in deduplicated):
            events.append(FilterEvent("PROMOTION_IOU_DUPLICATE", "促销签重复框", tag.get("id")))
            continue
        deduplicated.append((tag, coords))
        result.append(tag)
    return result


def postprocess_price_payload(
    parsed: Any,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> PricePostprocessResult:
    events: list[FilterEvent] = []
    raw_price_tags, raw_promotion_tags, total_raw_tags = _split_price_and_promotion(parsed, events)
    width = image_width or 1000
    height = image_height or 1000

    prepared_price_tags = _prepare_bbox(raw_price_tags, width, height, events)
    prepared_promotion_tags = _prepare_bbox(raw_promotion_tags, width, height, events)
    final_price_tags = _clean_price_tags(
        prepared_price_tags, width, height, min_price, max_price, events
    )
    final_promotion_tags = _clean_promotion_tags(
        prepared_promotion_tags, width, height, min_price, max_price, events
    )

    for index, tag in enumerate(final_price_tags, 1):
        tag["id"] = index
        tag.pop("_normalized_bbox", None)
        amount = parse_amount(tag.get("price"))
        tag["price"] = f"{amount:.2f}" if amount is not None else str(tag.get("price") or "")
        tag["unit"] = str(tag.get("unit") or "元").strip() or "元"
    for index, tag in enumerate(final_promotion_tags, 1):
        tag["id"] = index
        tag.pop("_normalized_bbox", None)

    return PricePostprocessResult(
        price_tags=final_price_tags,
        promotion_tags=final_promotion_tags,
        filter_events=[event.as_dict() for event in events],
        image_width=image_width,
        image_height=image_height,
    )


def is_bundle_promotion(tag: dict[str, Any]) -> bool:
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").lower()
    if tag_type in {"bundle_promotion", "multi_item_promotion"}:
        return True
    return bool(BUNDLE_PROMOTION_RE.search(_promotion_text(tag)))


def normalize_bundle_promotion(tag: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tag)
    raw_text = _promotion_text(normalized)
    quantity = normalized.get("bundle_quantity")
    bundle_price = normalized.get("bundle_price")
    match = BUNDLE_PROMOTION_RE.search(raw_text)
    if (not quantity or not bundle_price) and match:
        quantity = 2 if match.group(1) == "两" else int(match.group(1))
        bundle_price = match.group(2)
    normalized["tag_type"] = "bundle_promotion"
    normalized["promotion_type"] = "bundle_promotion"
    normalized["bundle_quantity"] = int(quantity or 2)
    amount = parse_amount(bundle_price or normalized.get("price"))
    normalized["bundle_price"] = f"{amount:.2f}" if amount is not None else str(bundle_price or "")
    if not normalized.get("price"):
        normalized["price"] = normalized["bundle_price"]
    normalized["raw_promotion_text"] = raw_text
    return normalized


def is_second_item_promotion(tag: dict[str, Any]) -> bool:
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").lower()
    return tag_type in {"second_item_price", "second_item_promotion"} or bool(
        SECOND_ITEM_PROMOTION_RE.search(_promotion_text(tag))
    )


def normalize_second_item_promotion(tag: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(tag)
    raw_text = _promotion_text(normalized)
    price = str(normalized.get("second_item_price") or "").strip()
    if not price:
        match = SECOND_ITEM_PRICE_RE.search(raw_text)
        price = match.group(1) if match else str(normalized.get("price") or "").strip()
    normalized["promotion_type"] = "second_item_price"
    normalized["second_item_price"] = price
    normalized["raw_promotion_text"] = raw_text
    normalized.pop("price", None)
    normalized.pop("raw_price_text", None)
    return normalized
