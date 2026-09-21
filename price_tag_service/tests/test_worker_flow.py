from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clients.models import AismCallResult
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app
from app.models.entities import PhotoOutcome, PhotoStatus, PriceTagDetail, QcResult, RecognitionPhoto
from app.worker.processor import PhotoProcessor
from app.worker.queue import claim_next_photo


class FakeQualityClient:
    def quality_check(self, image_url: str):
        return AismCallResult(
            parsed_payload={
                "qc_result": {
                    "is_valid": True,
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
                    }
                ],
                "promotion_tags": [],
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

    with database.session_factory() as session:
        photo = session.get(RecognitionPhoto, photo_id)
        assert photo.status == PhotoStatus.COMPLETED
        assert photo.outcome == PhotoOutcome.PROCESSED
        assert photo.current_stage.value == "DONE"
        qc = session.scalar(select(QcResult).where(QcResult.photo_id == photo_id))
        detail = session.scalar(select(PriceTagDetail).where(PriceTagDetail.photo_id == photo_id))
        assert qc is not None and qc.can_proceed_to_price is True
        assert detail is not None and float(detail.price) == 8.90
