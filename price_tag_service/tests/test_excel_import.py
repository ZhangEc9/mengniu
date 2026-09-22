from __future__ import annotations

import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.importers.excel import extract_photos
from app.db.session import Database
from app.main import create_app
from app.models.entities import ImportJob
from app.core.config import Settings


def test_extract_photos_splits_urls_and_filters_deleted(tmp_path: Path):
    path = tmp_path / "export.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "id", "storeCode", "storeName", "storeRegion", "firstChannel", "secondChannel",
            "xlOrderId", "xlMarketId", "visitStartTime", "visitFinishTime", "deletedAt",
            "priceTagPhotos", "productCloseupPhotos",
        ]
    )
    worksheet.append([1, "S1", "门店", "华东", "A", "B", "O1", "M1", None, None, None, "https://a/1.jpg, https://a/2.jpg", ""])
    worksheet.append([2, "S2", "门店", "华东", "A", "B", "O2", "M2", None, None, "2026-01-01", "https://a/3.jpg", ""])
    worksheet.append([3, "S3", "门店", "华东", "A", "B", "O3", "M3", None, None, None, "", "https://a/4.jpg"])
    workbook.save(path)

    photos, total_rows, valid_rows = extract_photos(
        path, photo_sources=["priceTagPhotos", "productCloseupPhotos"], max_photos=10
    )
    assert total_rows == 3
    assert valid_rows == 2
    assert [photo.source_row_id for photo in photos] == ["1", "1", "3"]
    assert [photo.source_photo_type for photo in photos] == [
        "priceTagPhotos", "priceTagPhotos", "productCloseupPhotos"
    ]


def test_extract_photos_ignores_invalid_worksheet_dimension(tmp_path: Path):
    path = tmp_path / "export.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "id", "storeCode", "storeName", "storeRegion", "firstChannel", "secondChannel",
            "xlOrderId", "xlMarketId", "visitStartTime", "visitFinishTime", "deletedAt",
            "priceTagPhotos", "productCloseupPhotos",
        ]
    )
    worksheet.append([1, "S1", "门店", "华东", "A", "B", "O1", "M1", None, None, None, "https://a/1.jpg", ""])
    workbook.save(path)

    malformed_path = tmp_path / "malformed.xlsx"
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(malformed_path, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet"):
                data = re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1"/>', data)
            target.writestr(item, data)

    photos, total_rows, valid_rows = extract_photos(
        malformed_path, photo_sources=["priceTagPhotos", "productCloseupPhotos"], max_photos=10
    )
    assert total_rows == 1
    assert valid_rows == 1
    assert str(photos[0].image_url) == "https://a/1.jpg"


def test_import_endpoint_commits_job_before_background_task(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'import.db'}", require_api_key=False)
    database = Database(settings)
    database.create_all()

    def fake_run_import_job(*, session_factory, import_job_id, **_kwargs):
        with session_factory() as session:
            job = session.get(ImportJob, import_job_id)
            assert job is not None
            job.status = "COMPLETED"
            session.commit()

    monkeypatch.setattr("app.api.routes.tasks.run_import_job", fake_run_import_job)

    path = tmp_path / "export.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "id", "storeCode", "storeName", "storeRegion", "firstChannel", "secondChannel",
            "xlOrderId", "xlMarketId", "visitStartTime", "visitFinishTime", "deletedAt",
            "priceTagPhotos", "productCloseupPhotos",
        ]
    )
    worksheet.append([1, "S1", "门店", "华东", "A", "B", "O1", "M1", None, None, None, "https://a/1.jpg", ""])
    workbook.save(path)

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks/import-excel?max_photos=1",
            files={"file": ("export.xlsx", path.read_bytes())},
        )
        assert response.status_code == 200
        import_job_id = response.json()["id"]

    with database.session_factory() as session:
        job = session.scalar(select(ImportJob).where(ImportJob.id == import_job_id))
        assert job is not None
        assert job.status == "COMPLETED"
