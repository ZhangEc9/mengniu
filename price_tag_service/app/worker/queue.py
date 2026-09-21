from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import PhotoStatus, RecognitionPhoto
from app.services.task_service import utc_now


def recover_stale_photos(session: Session, lease_sec: int) -> int:
    cutoff = utc_now()
    stale_photos = session.scalars(
        select(RecognitionPhoto).where(
            RecognitionPhoto.status == PhotoStatus.RUNNING,
            RecognitionPhoto.locked_until.is_not(None),
            RecognitionPhoto.locked_until < cutoff,
        )
    ).all()
    for photo in stale_photos:
        photo.status = PhotoStatus.QUEUED
        photo.locked_by = None
        photo.locked_until = None
        photo.updated_at = utc_now()
    return len(stale_photos)


def claim_next_photo(
    session: Session,
    *,
    worker_id: str,
    lease_sec: int,
    use_select_for_update: bool,
) -> RecognitionPhoto | None:
    now = utc_now()
    query = (
        select(RecognitionPhoto)
        .where(
            RecognitionPhoto.status == PhotoStatus.QUEUED,
            RecognitionPhoto.next_run_at <= now,
        )
        .order_by(RecognitionPhoto.next_run_at, RecognitionPhoto.id)
        .limit(1)
    )
    if use_select_for_update:
        query = query.with_for_update(skip_locked=True)
    photo = session.scalar(query)
    if photo is None:
        return None
    photo.status = PhotoStatus.RUNNING
    photo.locked_by = worker_id
    photo.locked_until = now + timedelta(seconds=lease_sec)
    photo.updated_at = now
    return photo
