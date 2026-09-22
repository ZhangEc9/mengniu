from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TaskStatus(str, enum.Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PhotoStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PhotoOutcome(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessStage(str, enum.Enum):
    UPLOAD = "UPLOAD"
    QUALITY_CHECK = "QUALITY_CHECK"
    PRICE_RECOGNITION = "PRICE_RECOGNITION"
    POSTPROCESS = "POSTPROCESS"
    DONE = "DONE"


class RecognitionTask(Base):
    __tablename__ = "recognition_task"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    task_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="MANUAL")
    status: Mapped[str] = mapped_column(Enum(TaskStatus), default=TaskStatus.CREATED, index=True)
    total_photos: Mapped[int] = mapped_column(Integer, default=0)
    processed_photos: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_photos: Mapped[int] = mapped_column(Integer, default=0)
    blocked_photos: Mapped[int] = mapped_column(Integer, default=0)
    failed_photos: Mapped[int] = mapped_column(Integer, default=0)
    task_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), default="api")
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    photos: Mapped[list[RecognitionPhoto]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class RecognitionPhoto(Base):
    __tablename__ = "recognition_photo"
    __table_args__ = (
        UniqueConstraint("task_id", "dedupe_key", name="uq_photo_task_dedupe"),
        Index("ix_photo_queue", "status", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("recognition_task.id"), index=True)
    source_row_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_photo_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    image_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    oss_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    url_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[PhotoStatus] = mapped_column(
        Enum(PhotoStatus), default=PhotoStatus.QUEUED, index=True
    )
    outcome: Mapped[PhotoOutcome] = mapped_column(Enum(PhotoOutcome), default=PhotoOutcome.PENDING)
    current_stage: Mapped[ProcessStage] = mapped_column(
        Enum(ProcessStage), default=ProcessStage.UPLOAD
    )
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    task: Mapped[RecognitionTask] = relationship(back_populates="photos")
    runs: Mapped[list[RecognitionRun]] = relationship(
        back_populates="photo", cascade="all, delete-orphan"
    )


class RecognitionRun(Base):
    __tablename__ = "recognition_run"
    __table_args__ = (UniqueConstraint("photo_id", "run_no", name="uq_photo_run_no"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    photo_id: Mapped[str] = mapped_column(ForeignKey("recognition_photo.id"), index=True)
    run_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    outcome: Mapped[PhotoOutcome] = mapped_column(Enum(PhotoOutcome), default=PhotoOutcome.PENDING)
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    photo: Mapped[RecognitionPhoto] = relationship(back_populates="runs")


class QcResult(Base):
    __tablename__ = "qc_result"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    photo_id: Mapped[str] = mapped_column(ForeignKey("recognition_photo.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("recognition_run.id"), index=True)
    qc_status: Mapped[str] = mapped_column(String(16), nullable=False, default="SKIPPED", server_default="SKIPPED")
    is_valid: Mapped[bool] = mapped_column(Boolean)
    should_continue: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qc_blur: Mapped[str | None] = mapped_column(String(32), nullable=True)
    qc_over_exposure: Mapped[str | None] = mapped_column(String(32), nullable=True)
    qc_low_light: Mapped[str | None] = mapped_column(String(32), nullable=True)
    qc_file_corrupted: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invalid_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalid_reasons: Mapped[list] = mapped_column(JSON, default=list)
    scene_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene_group: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_price_tag: Mapped[bool] = mapped_column(Boolean)
    is_quality_pass: Mapped[bool] = mapped_column(Boolean)
    is_target_scene: Mapped[bool] = mapped_column(Boolean)
    can_proceed_to_price: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_cost_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceResult(Base):
    __tablename__ = "price_result"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    photo_id: Mapped[str] = mapped_column(ForeignKey("recognition_photo.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("recognition_run.id"), index=True)
    total_raw_tags: Mapped[int] = mapped_column(Integer, default=0)
    total_tags: Mapped[int] = mapped_column(Integer, default=0)
    total_promotion_tags: Mapped[int] = mapped_column(Integer, default=0)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recognition_mode: Mapped[str] = mapped_column(String(32), default="WHOLE_IMAGE_VLM")
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postprocess_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_cost_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    filter_events: Mapped[list] = mapped_column(JSON, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    details: Mapped[list[PriceTagDetail]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class PriceTagDetail(Base):
    __tablename__ = "price_tag_detail"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    price_result_id: Mapped[str] = mapped_column(ForeignKey("price_result.id"), index=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("recognition_photo.id"), index=True)
    tag_id: Mapped[int] = mapped_column(Integer)
    shelf_layer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_xmin: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_ymin: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_xmax: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_ymax: Mapped[float | None] = mapped_column(Float, nullable=True)
    coordinate_scale: Mapped[int] = mapped_column(Integer, default=1000)
    price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    raw_price_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promotion_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundle_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    second_item_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_promotion: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    result: Mapped[PriceResult] = relationship(back_populates="details")


class OssUploadCache(Base):
    __tablename__ = "oss_upload_cache"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    oss_url: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AICallLog(Base):
    __tablename__ = "ai_call_log"
    __table_args__ = (Index("ix_ai_call_photo_ability", "photo_id", "ability"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    photo_id: Mapped[str | None] = mapped_column(ForeignKey("recognition_photo.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("recognition_run.id"), index=True)
    ability: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int]
    success: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    business_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_payload: Mapped[dict] = mapped_column(JSON)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportJob(Base):
    __tablename__ = "import_job"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    source_file: Mapped[str] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("recognition_task.id"), index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_photos: Mapped[int] = mapped_column(Integer, default=0)
    skipped_photos: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
