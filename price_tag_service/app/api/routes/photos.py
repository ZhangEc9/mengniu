from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DbDep, Protected
from app.api.serializers import photo_summary, price_result, qc_result
from app.models.entities import (
    PhotoOutcome,
    PhotoStatus,
    PriceResult,
    PriceTagDetail,
    ProcessStage,
    QcResult,
    RecognitionPhoto,
)
from app.services.task_service import refresh_task_counters, reset_photo_for_retry

router = APIRouter(prefix="/v1/photos", dependencies=[Protected])


class RetryRequest(BaseModel):
    from_stage: ProcessStage | None = None


def _get_photo(db, photo_id: str) -> RecognitionPhoto:
    photo = db.get(RecognitionPhoto, photo_id)
    if photo is None:
        raise HTTPException(404, "Photo not found")
    return photo


def _latest_qc(db, photo_id: str) -> QcResult | None:
    return db.scalar(
        select(QcResult)
        .where(QcResult.photo_id == photo_id)
        .order_by(QcResult.created_at.desc())
        .limit(1)
    )


def _latest_price(db, photo_id: str) -> PriceResult | None:
    return db.scalar(
        select(PriceResult)
        .where(PriceResult.photo_id == photo_id)
        .order_by(PriceResult.created_at.desc())
        .limit(1)
    )


@router.get("/{photo_id}")
def get_photo(photo_id: str, db: DbDep):
    photo = _get_photo(db, photo_id)
    qc = _latest_qc(db, photo_id)
    price = _latest_price(db, photo_id)
    details = (
        db.scalars(
            select(PriceTagDetail)
            .where(PriceTagDetail.price_result_id == price.id)
            .order_by(PriceTagDetail.tag_id)
        ).all()
        if price is not None
        else []
    )
    return {
        **photo_summary(photo),
        "qc": qc_result(qc) if qc is not None else None,
        "price": (
            price_result(photo, qc, price, details) if price is not None else None
        ),
        "sku": {"status": "NOT_CONFIGURED", "message": "SKU识别服务暂未接入", "sku_items": []},
    }


@router.get("/{photo_id}/qc")
def get_photo_qc(photo_id: str, db: DbDep):
    photo = _get_photo(db, photo_id)
    qc = _latest_qc(db, photo_id)
    if qc is None:
        raise HTTPException(404, "QC result not found")
    return {"photo_id": photo.id, **qc_result(qc)}


@router.get("/{photo_id}/tags")
def get_photo_tags(photo_id: str, db: DbDep):
    photo = _get_photo(db, photo_id)
    price = _latest_price(db, photo_id)
    if price is None:
        raise HTTPException(404, "Price result not found")
    details = db.scalars(
        select(PriceTagDetail)
        .where(PriceTagDetail.price_result_id == price.id)
        .order_by(PriceTagDetail.tag_id)
    ).all()
    return price_result(photo, _latest_qc(db, photo_id), price, details)


@router.get("/{photo_id}/sku")
def get_photo_sku(photo_id: str, db: DbDep):
    _get_photo(db, photo_id)
    return {"status": "NOT_CONFIGURED", "message": "SKU识别服务暂未接入", "sku_items": []}


@router.post("/{photo_id}/retry")
def retry_photo(photo_id: str, payload: RetryRequest, db: DbDep):
    photo = _get_photo(db, photo_id)
    if photo.status not in {PhotoStatus.COMPLETED, PhotoStatus.FAILED}:
        raise HTTPException(409, "Only terminal photos can be retried")
    reset_photo_for_retry(db, photo, from_stage=payload.from_stage)
    refresh_task_counters(db, photo.task_id)
    return photo_summary(photo)
