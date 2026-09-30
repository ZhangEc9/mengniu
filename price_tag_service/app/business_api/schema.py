from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class PhotoCreate(BaseModel):
    image_url: HttpUrl = Field(max_length=512)
    image_id: str | None = Field(default=None, min_length=1, max_length=64)
    shop_id: str | None = Field(default=None, max_length=64)


class TaskCreate(BaseModel):
    data_source: Literal[1, 2] = 2
    demo_mode: bool = False
    business_code: str = Field(min_length=1, max_length=64)
    business_unit: str = Field(min_length=1, max_length=64)
    quality_check: bool = True
    photos: list[PhotoCreate] = Field(min_length=1)
