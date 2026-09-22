from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clients.models import AismCallResult
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.models.entities import (
    PhotoOutcome,
    PhotoStatus,
    PriceResult,
    PriceTagDetail,
    QcResult,
    ProcessStage,
    RecognitionPhoto,
    RecognitionTask,
    RecognitionRun,
    TaskStatus,
)
from app.services.task_service import reset_photo_for_retry
from app.worker.processor import PhotoProcessor
from app.worker.queue import claim_next_photo


class FakeQualityClient:
    def __init__(self):
        self.call_count = 0

    def quality_check(self, image_url: str):
        self.call_count += 1
        return AismCallResult(
            parsed_payload={
                "qc_result": {
                    "is_valid": True,
                    "confidence": 0.93,
                    "quality_checks": {
                        "图片模糊": "合格",
                        "过度曝光": "合格",
                        "光线不足": "合格",
                        "文件损坏": "合格",
                    },
                },
                "content_info": {"has_price_tag": True, "scene_type": "货架照"},
            },
            raw_response={"status": 0},
            page_content="{}",
            model_name="fake-qc",
            prompt_version="test",
            cost_sec=0.1,
            call_logs=[],
        )


class FakePriceClient:
    def price_tag_detect(self, image_url: str):
        return AismCallResult(
            parsed_payload={
                "price_tags": [
                    {
                        "id": 1,
                        "bbox": [100, 100, 180, 130],
                        "price": "8.90",
                        "raw_price_text": "8.90元",
                        "tag_type": "regular_price",
                        "confidence": 0.9,
                        "price_confidence": 0.88,
                        "unit": "元",
                    },
                    {
                        "id": 2,
                        "bbox": [200, 100, 280, 130],
                        "price": "19.90",
                        "raw_price_text": "两件19.90元",
                        "tag_type": "bundle_promotion",
                        "bundle_quantity": 2,
                        "bundle_price": "19.90",
                        "confidence": 0.9,
                    }
                ],
                "promotion_tags": [
                    {
                        "id": 1,
                        "bbox": [300, 100, 380, 130],
                        "price": "1",
                        "raw_price_text": "第二件1元",
                        "tag_type": "second_item_promotion",
                    }
                ],
            },
            raw_response={"status": 0},
            page_content="{}",
            model_name="fake-price",
            prompt_version="test",
            cost_sec=0.2,
            call_logs=[],
        )


def make_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        min_price=2,
        max_price=99,
        require_api_key=False,
    )


def test_task_api_creates_and_queries_task(tmp_path: Path):
    settings = make_settings(f"sqlite:///{tmp_path/'tasks.db'}")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            json={"photos": [{"image_url": "https://example.com/a.jpg"}], "min_price": 2},
        )
        assert response.status_code == 200
        task_id = response.json()["task_id"]
        assert client.get(f"/v1/tasks/{task_id}").status_code == 200
        photos_response = client.get(f"/v1/tasks/{task_id}/photos")
        assert photos_response.status_code == 200
        assert photos_response.json()["items"][0]["status"] == "QUEUED"


def test_worker_completes_fake_flow(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path/'worker.db'}"
    settings = make_settings(database_url)
    database = Database(settings)
    database.create_all()
    from app.schemas.tasks import PhotoCreate
    from app.services.task_service import create_task

    with database.session_factory() as session:
        task, _ = create_task(
            session,
            source_type="TEST",
            photos=[PhotoCreate(image_url="https://example.com/image.jpg")],
            task_config={"min_price": 2, "max_price": 99},
        )
        task_id = task.id
        photo = session.scalars(
            select(RecognitionPhoto).where(RecognitionPhoto.task_id == task_id)
        ).one()
        photo_id = photo.id
        session.commit()

    with database.session_factory() as session:
        claimed = claim_next_photo(
            session, worker_id="test", lease_sec=60, use_select_for_update=False
        )
        assert claimed is not None and claimed.id == photo_id
        session.commit()

    processor = PhotoProcessor(settings, database.session_factory)
    processor.qc_client = FakeQualityClient()
    processor.price_client = FakePriceClient()
    monkeypatch.setattr(
        "app.worker.processor.read_image_size", lambda source: None
    )
    processor.process_photo_id(photo_id)

    app = create_app(settings)

    with database.session_factory() as session:
        photo = session.get(RecognitionPhoto, photo_id)
        task = session.get(RecognitionTask, task_id)
        assert photo.status == PhotoStatus.COMPLETED
        assert photo.outcome == PhotoOutcome.PROCESSED
        assert photo.current_stage.value == "DONE"
        assert task.status == TaskStatus.COMPLETED
        assert task.total_photos == 1
        assert task.processed_photos == 1
        assert task.succeeded_photos == 1
        assert task.blocked_photos == 0
        assert task.failed_photos == 0
        qc = session.scalar(select(QcResult).where(QcResult.photo_id == photo_id))
        detail = session.scalar(select(PriceTagDetail).where(PriceTagDetail.photo_id == photo_id))
        assert qc is not None and qc.can_proceed_to_price is True
        assert qc.qc_status == "PASSED"
        assert qc.confidence == 0.93
        assert detail is not None and float(detail.price) == 8.90
        assert detail.tag_type == "regular_price"
        assert detail.bundle_quantity is None
        assert detail.bundle_price is None
        assert detail.second_item_price is None
        assert detail.is_promotion is False
        price_result = session.scalar(
            select(PriceResult).where(PriceResult.photo_id == photo_id)
        )
        assert price_result is not None
        assert price_result.total_tags == 1
        assert price_result.total_promotion_tags == 2
        exclusion_rules = {event["rule"] for event in price_result.filter_events}
        assert "BUNDLE_PROMOTION_EXCLUDED" in exclusion_rules
        assert "SECOND_ITEM_PROMOTION_EXCLUDED" in exclusion_rules

    with TestClient(app) as client:
        qc_response = client.get(f"/v1/photos/{photo_id}/qc")
        assert qc_response.status_code == 200
        assert qc_response.json()["confidence"] == 0.93

        tags_response = client.get(f"/v1/photos/{photo_id}/tags")
        assert tags_response.status_code == 200
        tags_payload = tags_response.json()
        assert tags_payload["price_tag_count"] == 1
        assert tags_payload["price_tags"] == [
            {
                "id": 1,
                "bbox": [100, 100, 180, 130],
                "coordinate_scale": 1000,
                "price": "8.90",
                "confidence": 0.88,
                "raw_price_text": "8.90元",
                "unit": "元",
            }
        ]


def test_worker_skips_quality_check_when_disabled(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{tmp_path/'quality-check-disabled.db'}"
    settings = make_settings(database_url)
    database = Database(settings)
    database.create_all()
    from app.schemas.tasks import PhotoCreate
    from app.services.task_service import create_task

    with database.session_factory() as session:
        task, _ = create_task(
            session,
            source_type="TEST",
            photos=[PhotoCreate(image_url="https://example.com/image.jpg")],
            task_config={
                "min_price": 2,
                "max_price": 99,
                "agent_config": {"quality_check": {"enabled": False}},
            },
        )
        task_id = task.id
        photo = session.scalars(
            select(RecognitionPhoto).where(RecognitionPhoto.task_id == task_id)
        ).one()
        photo_id = photo.id
        session.commit()

    with database.session_factory() as session:
        claimed = claim_next_photo(
            session, worker_id="test", lease_sec=60, use_select_for_update=False
        )
        assert claimed is not None and claimed.id == photo_id
        session.commit()

    processor = PhotoProcessor(settings, database.session_factory)
    quality_client = FakeQualityClient()
    processor.qc_client = quality_client
    processor.price_client = FakePriceClient()
    monkeypatch.setattr("app.worker.processor.read_image_size", lambda source: None)
    processor.process_photo_id(photo_id)

    with database.session_factory() as session:
        photo = session.get(RecognitionPhoto, photo_id)
        task = session.get(RecognitionTask, task_id)
        run = session.scalar(
            select(RecognitionRun).where(RecognitionRun.photo_id == photo_id)
        )
        qc = session.scalar(select(QcResult).where(QcResult.photo_id == photo_id))
        price_result = session.scalar(
            select(PriceResult).where(PriceResult.photo_id == photo_id)
        )
        detail = session.scalar(select(PriceTagDetail).where(PriceTagDetail.photo_id == photo_id))

        assert photo is not None
        assert photo.status == PhotoStatus.COMPLETED
        assert photo.outcome == PhotoOutcome.PROCESSED
        assert photo.current_stage.value == "DONE"
        assert task is not None and task.status == TaskStatus.COMPLETED
        assert task.blocked_photos == 0
        assert run is not None and run.config_snapshot["quality_check_enabled"] is False
        assert quality_client.call_count == 0
        assert qc is not None
        assert qc.qc_status == "SKIPPED"
        assert qc.can_proceed_to_price is True
        assert qc.confidence is None
        assert qc.raw_payload == {"status": "SKIPPED", "reason": "quality_check_disabled"}
        assert price_result is not None
        assert detail is not None and float(detail.price) == 8.90


def test_manual_retry_resets_retry_count(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path/'retry.db'}"
    settings = make_settings(database_url)
    database = Database(settings)
    database.create_all()
    from app.schemas.tasks import PhotoCreate
    from app.services.task_service import create_task

    with database.session_factory() as session:
        task, _ = create_task(
            session,
            source_type="TEST",
            photos=[PhotoCreate(image_url="https://example.com/retry.jpg")],
        )
        photo = session.scalars(
            select(RecognitionPhoto).where(RecognitionPhoto.task_id == task.id)
        ).one()
        photo.retry_count = 3
        photo.status = PhotoStatus.FAILED
        photo.outcome = PhotoOutcome.FAILED
        session.commit()

        reset_photo_for_retry(session, photo, from_stage=ProcessStage.PRICE_RECOGNITION)
        assert photo.retry_count == 0
        assert photo.status == PhotoStatus.QUEUED
        assert photo.outcome == PhotoOutcome.PENDING
