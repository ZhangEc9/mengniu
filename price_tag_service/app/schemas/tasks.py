from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class PhotoCreate(BaseModel):
    image_url: HttpUrl
    source_row_id: str | None = None
    source_photo_type: str | None = None
    image_name: str | None = Field(default=None, max_length=512)
    source_metadata: dict = Field(default_factory=dict)


class TaskCreate(BaseModel):
    photos: list[PhotoCreate] = Field(min_length=1)
    photo_sources: list[str] | None = None
    min_price: float | None = None
    max_price: float | None = None
    created_by: str = Field(default="api", max_length=128)
    remark: str | None = None


class TaskSummary(BaseModel):
    id: str
    task_no: str
    source_type: str
    status: str
    total_photos: int
    processed_photos: int
    succeeded_photos: int
    blocked_photos: int
    failed_photos: int
    created_at: str | None = None
    updated_at: str | None = None


class PhotoSummary(BaseModel):
    id: str
    status: str
    outcome: str
    current_stage: str
    stop_reason: str | None
    retry_count: int
    image_name: str | None
    image_url: str | None
    source_row_id: str | None
    source_photo_type: str | None
    last_error_type: str | None
    last_error_message: str | None


class CreateTaskResponse(BaseModel):
    task_id: str
    task_no: str
    created_photos: int
