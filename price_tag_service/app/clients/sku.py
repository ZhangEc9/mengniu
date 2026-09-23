from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse


class SampleSkuClient:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    def recognize(self, image_url: str) -> dict:
        image_name = Path(unquote(urlparse(image_url).path)).stem
        fixture = self.fixture_dir / f"{image_name}.sku.json"
        if not fixture.is_file():
            raise FileNotFoundError(f"SKU sample missing for {image_name}: {fixture}")
        return json.loads(fixture.read_text(encoding="utf-8"))
