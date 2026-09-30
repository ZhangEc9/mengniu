from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import time

from app.clients.aism import AismClient
from app.clients.sku import SampleSkuClient
from app.contracts.agents import AgentOutput, BatchInput, BatchOutput, PhotoInput, PhotoOutput
from app.core.config import Settings
from app.processing.postprocess import postprocess_price_payload
from app.processing.quality import normalize_quality_payload
from app.processing.sku_match import normalize_sku
from app.processing.image_size import read_image_size


class AgentService:
    def __init__(self, settings: Settings):
        self.quality_client = AismClient(settings.qc_config, settings.prompt_dir)
        self.price_client = AismClient(settings.price_tag_config, settings.prompt_dir)
        self.sku_client = SampleSkuClient(settings.sku_sample_dir)
        self.min_price = settings.min_price
        self.max_price = settings.max_price

    def quality(self, image_url: str) -> AgentOutput:
        try:
            result = self.quality_client.quality_check(image_url)
            parsed = normalize_quality_payload(result.parsed_payload)
            return AgentOutput(
                status="SUCCEEDED" if parsed.can_proceed_to_price else "BLOCKED",
                raw=result.raw_response,
                parsed={"score": parsed.confidence, "scene_group": parsed.scene_group,
                        "has_price_tag": parsed.has_price_tag, "quality_checks": parsed.quality_checks,
                        "rejection_reasons": parsed.rejection_reasons},
                latency_ms=round(result.cost_sec * 1000),
            )
        except Exception as exc:
            return AgentOutput(status="FAILED", error=str(exc))

    def sku(self, image_url: str) -> AgentOutput:
        started = time.perf_counter()
        try:
            raw = self.sku_client.recognize(image_url)
            return AgentOutput(status="SUCCEEDED", raw=raw, parsed=normalize_sku(raw)["items"],
                               latency_ms=round((time.perf_counter() - started) * 1000))
        except Exception as exc:
            return AgentOutput(status="FAILED", error=str(exc))

    def price_tag(self, image_url: str) -> AgentOutput:
        try:
            result = self.price_client.price_tag_detect(image_url)
            size = read_image_size(image_url)
            processed = postprocess_price_payload(
                result.parsed_payload,
                image_width=size[0] if size else None,
                image_height=size[1] if size else None,
                min_price=self.min_price,
                max_price=self.max_price,
            )
            tags = []
            for tag in processed.price_tags:
                confidence = []
                for key in ("confidence", "price_confidence"):
                    try:
                        value = float(tag[key])
                        if math.isfinite(value) and 0 <= value <= 1:
                            confidence.append(value)
                    except (KeyError, TypeError, ValueError):
                        pass
                tags.append({"id": tag["id"], "bbox": tag.get("bbox"), "price": tag.get("price"),
                             "score": min(confidence) if confidence else None,
                             "price_text": tag.get("raw_price_text")})
            return AgentOutput(status="SUCCEEDED", raw=result.raw_response,
                               parsed=tags, latency_ms=round(result.cost_sec * 1000))
        except Exception as exc:
            return AgentOutput(status="FAILED", error=str(exc))

    def process(self, photo: PhotoInput) -> PhotoOutput:
        image_url = str(photo.image_url)
        quality = self.quality(image_url) if photo.quality_check else AgentOutput(status="SKIPPED")
        if quality.status in ("BLOCKED", "FAILED"):
            sku = price_tag = AgentOutput(status="SKIPPED")
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                sku_future = pool.submit(self.sku, image_url)
                price_future = pool.submit(self.price_tag, image_url)
                sku, price_tag = sku_future.result(), price_future.result()
        return PhotoOutput(**photo.model_dump(), quality=quality, sku=sku, price_tag=price_tag)

    def process_batch(self, batch: BatchInput) -> BatchOutput:
        results = []
        for image in batch.images:
            photo = PhotoInput(task_id=batch.task_id, quality_check=batch.quality_check,
                               **image.model_dump())
            try:
                results.append(self.process(photo))
            except Exception as exc:
                results.append(PhotoOutput(
                    **photo.model_dump(), quality=AgentOutput(status="FAILED", error=str(exc)),
                    sku=AgentOutput(status="SKIPPED"), price_tag=AgentOutput(status="SKIPPED"),
                ))
        return BatchOutput(request_id=batch.request_id, task_id=batch.task_id, results=results)
