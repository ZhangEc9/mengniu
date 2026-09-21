from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


QUALITY_KEYS = ("图片模糊", "过度曝光", "光线不足", "文件损坏")
QUALITY_KEY_ALIASES = {"严重过曝": "过度曝光"}
QUALITY_VALUE_ALIASES = {"合": "合格"}
TARGET_SCENES = {"冰箱照", "货架照", "堆头照"}


@dataclass
class NormalizedQualityResult:
    is_valid: bool
    should_continue: bool | None
    quality_checks: dict[str, str]
    qc_blur: str | None
    qc_over_exposure: str | None
    qc_low_light: str | None
    qc_file_corrupted: str | None
    invalid_reason: str | None
    invalid_reasons: list[str]
    scene_type: str | None
    scene_group: str
    has_price_tag: bool
    is_quality_pass: bool
    is_target_scene: bool
    can_proceed_to_price: bool
    stop_reason: str | None
    rejection_reasons: list[str] = field(default_factory=list)


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "是", "有", "合格"}:
            return True
        if normalized in {"false", "0", "no", "n", "否", "无", "不合格"}:
            return False
    return default


def normalize_quality_checks(quality_checks: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(quality_checks, dict):
        return normalized
    for raw_key, raw_value in quality_checks.items():
        key = QUALITY_KEY_ALIASES.get(str(raw_key).strip(), str(raw_key).strip())
        value = str(raw_value).strip()
        value = QUALITY_VALUE_ALIASES.get(value, value)
        normalized.setdefault(key, value)
    return normalized


def normalize_scene(scene_type: Any) -> str:
    scene = str(scene_type or "").strip()
    if any(keyword in scene for keyword in ("冰箱", "冷柜")):
        return "冰箱照"
    if "货架" in scene:
        return "货架照"
    if any(keyword in scene for keyword in ("堆头", "地堆")):
        return "堆头照"
    return "其他"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_quality_payload(payload: dict[str, Any]) -> NormalizedQualityResult:
    qc_result = payload.get("qc_result") if isinstance(payload.get("qc_result"), dict) else {}
    content_info = payload.get("content_info") if isinstance(payload.get("content_info"), dict) else {}
    quality_checks = normalize_quality_checks(qc_result.get("quality_checks"))

    is_valid = coerce_bool(qc_result.get("is_valid"), False)
    checks_pass = all(quality_checks.get(key) == "合格" for key in QUALITY_KEYS)
    is_quality_pass = is_valid and checks_pass
    scene_type = str(content_info.get("scene_type") or "").strip() or None
    scene_group = normalize_scene(scene_type)
    is_target_scene = scene_group in TARGET_SCENES
    has_price_tag = coerce_bool(content_info.get("has_price_tag"), False)
    should_continue = (
        coerce_bool(qc_result.get("should_continue"), is_quality_pass)
        if qc_result.get("should_continue") is not None
        else None
    )
    can_proceed = is_quality_pass and is_target_scene and has_price_tag

    rejection_reasons: list[str] = []
    if not is_quality_pass:
        reason = str(qc_result.get("invalid_reason") or "").strip()
        rejection_reasons.append(f"质量不合格: {reason or '存在不合格检测项'}")
    if not is_target_scene:
        rejection_reasons.append(
            f"非目标巡店场景: {scene_type or '未识别'}（仅限冰箱照/货架照/堆头照）"
        )
    if not has_price_tag:
        rejection_reasons.append("画面内未检出有效商品价签")

    if not is_quality_pass:
        stop_reason = "QUALITY_REJECTED"
    elif not is_target_scene:
        stop_reason = "SCENE_NOT_TARGET"
    elif not has_price_tag:
        stop_reason = "NO_PRICE_TAG"
    else:
        stop_reason = None

    return NormalizedQualityResult(
        is_valid=is_valid,
        should_continue=should_continue,
        quality_checks=quality_checks,
        qc_blur=quality_checks.get("图片模糊"),
        qc_over_exposure=quality_checks.get("过度曝光"),
        qc_low_light=quality_checks.get("光线不足"),
        qc_file_corrupted=quality_checks.get("文件损坏"),
        invalid_reason=str(qc_result.get("invalid_reason") or "").strip() or None,
        invalid_reasons=_string_list(qc_result.get("invalid_reasons")),
        scene_type=scene_type,
        scene_group=scene_group,
        has_price_tag=has_price_tag,
        is_quality_pass=is_quality_pass,
        is_target_scene=is_target_scene,
        can_proceed_to_price=can_proceed,
        stop_reason=stop_reason,
        rejection_reasons=rejection_reasons,
    )
