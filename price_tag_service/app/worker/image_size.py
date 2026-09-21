from __future__ import annotations

import io
from pathlib import Path

import httpx
from PIL import Image


def read_image_size(source: str | Path) -> tuple[int, int] | None:
    try:
        if isinstance(source, Path) or not str(source).startswith(("http://", "https://")):
            with Image.open(source) as image:
                return image.size
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(str(source))
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                return image.size
    except Exception:
        return None
