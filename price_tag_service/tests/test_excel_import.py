from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.importers.excel import extract_photos


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
