from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class PhotoInput(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    image_list_id: str = Field(min_length=1, max_length=64)
    image_id: str = Field(min_length=1, max_length=64)
    image_url: HttpUrl = Field(max_length=512)
    shop_id: str | None = Field(default=None, max_length=64)
    quality_check: bool = True


class AgentOutput(BaseModel):
    status: Literal["SUCCEEDED", "FAILED", "SKIPPED", "BLOCKED"]
    raw: dict[str, Any] | None = None
    parsed: dict[str, Any] | list[dict[str, Any]] | None = None
    latency_ms: int | None = None
    error: str | None = None


class PhotoOutput(PhotoInput):
    quality: AgentOutput
    sku: AgentOutput
    price_tag: AgentOutput


class ImageInput(BaseModel):
    image_list_id: str = Field(min_length=1, max_length=64)
    image_id: str = Field(min_length=1, max_length=64)
    image_url: HttpUrl = Field(max_length=512)
    shop_id: str | None = Field(default=None, max_length=64)


class BatchInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=64)
    quality_check: bool = True
    images: list[ImageInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_images(self) -> "BatchInput":
        for key in ("image_list_id", "image_id"):
            identifiers = [getattr(image, key) for image in self.images]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Duplicate {key} in batch")
        return self


class BatchOutput(BaseModel):
    request_id: str
    task_id: str
    results: list[PhotoOutput]
