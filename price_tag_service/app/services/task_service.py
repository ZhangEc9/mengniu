from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    PhotoOutcome,
    PhotoStatus,
    ProcessStage,
    RecognitionPhoto,
    RecognitionTask,
    TaskStatus,
)
from app.schemas.tasks import PhotoCreate


class LocalPhoto:
    def __init__(
        self,
        *,
        file_name: str,
        local_path: str,
        file_sha256: str,
        source_metadata: dict | None = None,
    ):
        self.file_name = file_name
        self.local_path = local_path
        self.file_sha256 = file_sha256
        self.source_metadata = source_metadata or {}


def utc_now() -> datetime:
    return datetime.now(UTC)


def url_hash(image_url: str) -> str:
    return hashlib.sha256(image_url.encode("utf-8")).hexdigest()


def _dedupe_key(photo: PhotoCreate) -> str:
    source_row = photo.source_row_id or "none"
    source_type = photo.source_photo_type or "none"
    return hashlib.sha256(
        f"{source_row}|{source_type}|{photo.image_url.unicode_string()}".encode("utf-8")
    ).hexdigest()


def create_task(
    session: Session,
    *,
    source_type: str,
    photos: Sequence[PhotoCreate],
    task_config: dict | None = None,
    created_by: str = "api",
    remark: str | None = None,
) -> tuple[RecognitionTask, int]:
    now = utc_now()
    task = RecognitionTask(
        task_no=f"T{now.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}",
        source_type=source_type,
        status=TaskStatus.CREATED,
        task_config=task_config or {},
        created_by=created_by,
        remark=remark,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()

    existing_keys = set(
        session.scalars(
            select(RecognitionPhoto.dedupe_key).where(RecognitionPhoto.task_id == task.id)
        )
    )
    created = 0
    for photo in photos:
        key = _dedupe_key(photo)
        if key in existing_keys:
            continue
        image_url = photo.image_url.unicode_string()
        entity = RecognitionPhoto(
            task_id=task.id,
            source_row_id=photo.source_row_id,
            source_photo_type=photo.source_photo_type,
            source_metadata=photo.source_metadata,
            image_name=photo.image_name,
            image_url=image_url,
            url_hash=url_hash(image_url),
            dedupe_key=key,
            status=PhotoStatus.QUEUED,
            outcome=PhotoOutcome.PENDING,
            current_stage=ProcessStage.UPLOAD,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(entity)
        existing_keys.add(key)
        created += 1

    task.total_photos = created
    session.flush()
    return task, created


def create_local_task(
    session: Session,
    *,
    files: Sequence[LocalPhoto],
    created_by: str = "api",
    remark: str | None = None,
) -> tuple[RecognitionTask, int]:
    now = utc_now()
    task = RecognitionTask(
        task_no=f"U{now.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}",
        source_type="LOCAL_UPLOAD",
        status=TaskStatus.CREATED,
        task_config={},
        created_by=created_by,
        remark=remark,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    seen_hashes: set[str] = set()
    created = 0
    for uploaded in files:
        if uploaded.file_sha256 in seen_hashes:
            continue
        seen_hashes.add(uploaded.file_sha256)
        session.add(
            RecognitionPhoto(
                task_id=task.id,
                source_photo_type="LOCAL_UPLOAD",
                source_metadata=uploaded.source_metadata,
                image_name=uploaded.file_name,
                local_path=uploaded.local_path,
                file_sha256=uploaded.file_sha256,
                dedupe_key=uploaded.file_sha256,
                status=PhotoStatus.QUEUED,
                outcome=PhotoOutcome.PENDING,
                current_stage=ProcessStage.UPLOAD,
                next_run_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    task.total_photos = created
    session.flush()
    return task, created


def refresh_task_counters(session: Session, task_id: str) -> None:
    task = session.get(RecognitionTask, task_id)
    if task is None:
        return
    rows = session.execute(
        select(RecognitionPhoto.outcome, func.count())
        .where(RecognitionPhoto.task_id == task_id)
        .group_by(RecognitionPhoto.outcome)
    ).all()
    counts = {row[0]: int(row[1]) for row in rows}
    task.succeeded_photos = counts.get(PhotoOutcome.PROCESSED, 0)
    task.blocked_photos = counts.get(PhotoOutcome.BLOCKED, 0)
    task.failed_photos = counts.get(PhotoOutcome.FAILED, 0)
    task.processed_photos = sum(counts.values())
    pending = session.scalar(
        select(func.count())
        .select_from(RecognitionPhoto)
        .where(
            RecognitionPhoto.task_id == task_id,
            RecognitionPhoto.status.in_([PhotoStatus.QUEUED, PhotoStatus.RUNNING]),
        )
    )
    if not pending:
        task.status = TaskStatus.COMPLETED
    else:
        task.status = TaskStatus.RUNNING
    task.updated_at = utc_now()


def reset_photo_for_retry(
    session: Session,
    photo: RecognitionPhoto,
    *,
    from_stage: ProcessStage | None = None,
) -> None:
    photo.status = PhotoStatus.QUEUED
    photo.outcome = PhotoOutcome.PENDING
    photo.stop_reason = None
    photo.last_error_type = None
    photo.last_error_message = None
    photo.retry_count = 0
    photo.current_stage = from_stage or ProcessStage.QUALITY_CHECK
    photo.next_run_at = utc_now()
    photo.locked_by = None
    photo.locked_until = None
    photo.updated_at = utc_now()


def iter_photos(session: Session, task_id: str) -> Iterable[RecognitionPhoto]:
    yield from session.scalars(
        select(RecognitionPhoto)
        .where(RecognitionPhoto.task_id == task_id)
        .order_by(RecognitionPhoto.id)
    )
