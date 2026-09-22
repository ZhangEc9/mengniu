from __future__ import annotations

from typing import Any

from app.models.entities import (
    ImportJob,
    PriceResult,
    PriceTagDetail,
    QcResult,
    RecognitionPhoto,
    RecognitionTask,
)


def isoformat(value) -> str | None:
    return value.isoformat() if value is not None else None


def task_summary(task: RecognitionTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_no": task.task_no,
        "source_type": task.source_type,
        "status": task.status.value,
        "total_photos": task.total_photos,
        "processed_photos": task.processed_photos,
        "succeeded_photos": task.succeeded_photos,
        "blocked_photos": task.blocked_photos,
        "failed_photos": task.failed_photos,
        "created_at": isoformat(task.created_at),
        "updated_at": isoformat(task.updated_at),
    }


def photo_summary(photo: RecognitionPhoto) -> dict[str, Any]:
    return {
        "id": photo.id,
        "task_id": photo.task_id,
        "status": photo.status.value,
        "outcome": photo.outcome.value,
        "current_stage": photo.current_stage.value,
        "stop_reason": photo.stop_reason,
        "retry_count": photo.retry_count,
        "image_name": photo.image_name,
        "image_url": photo.image_url,
        "source_row_id": photo.source_row_id,
        "source_photo_type": photo.source_photo_type,
        "last_error_type": photo.last_error_type,
        "last_error_message": photo.last_error_message,
    }


def qc_result(qc: QcResult) -> dict[str, Any]:
    return {
        "qc_status": qc.qc_status,
        "skipped": qc.qc_status == "SKIPPED",
        "is_valid": qc.is_valid,
        "should_continue": qc.should_continue,
        "quality_checks": {
            "图片模糊": qc.qc_blur,
            "过度曝光": qc.qc_over_exposure,
            "光线不足": qc.qc_low_light,
            "文件损坏": qc.qc_file_corrupted,
        },
        "invalid_reason": qc.invalid_reason,
        "invalid_reasons": qc.invalid_reasons,
        "scene_type": qc.scene_type,
        "scene_group": qc.scene_group,
        "has_price_tag": qc.has_price_tag,
        "is_quality_pass": qc.is_quality_pass,
        "is_target_scene": qc.is_target_scene,
        "can_proceed_to_price": qc.can_proceed_to_price,
        "rejection_reasons": qc.rejection_reasons,
        "model_name": qc.model_name,
        "prompt_version": qc.prompt_version,
        "model_cost_sec": qc.model_cost_sec,
    }


def price_tag_detail(detail: PriceTagDetail) -> dict[str, Any]:
    return {
        "id": detail.tag_id,
        "bbox": [detail.bbox_xmin, detail.bbox_ymin, detail.bbox_xmax, detail.bbox_ymax],
        "coordinate_scale": detail.coordinate_scale,
        "price": f"{detail.price:.2f}" if detail.price is not None else None,
        "raw_price_text": detail.raw_price_text,
        "unit": detail.unit,
    }


def price_result(
    photo: RecognitionPhoto,
    qc: QcResult | None,
    result: PriceResult,
    details: list[PriceTagDetail],
) -> dict[str, Any]:
    return {
        "image_name": photo.image_name,
        "image_url": photo.image_url,
        "scene_type": qc.scene_type if qc is not None else None,
        "price_tag_count": len(details),
        "price_tags": [price_tag_detail(detail) for detail in details],
    }


def import_job_result(job: ImportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "task_id": job.task_id,
        "total_rows": job.total_rows,
        "valid_rows": job.valid_rows,
        "created_photos": job.created_photos,
        "skipped_photos": job.skipped_photos,
        "error_message": job.error_message,
    }
