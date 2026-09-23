from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.clients.sku import SampleSkuClient
from app.processing.sku_match import match_sku_tags, normalize_sku
from app.schemas.tasks import AgentConfig


def test_sku_response_shape_uses_top1():
    fixture = Path(__file__).parent / "fixtures" / "sku_response.json"
    normalized = normalize_sku(json.loads(fixture.read_text(encoding="utf-8")))
    assert len(normalized["items"]) == 2
    assert normalized["items"][0]["sku_code"] == "君乐宝悦鲜活A2牛奶450ML"
    assert normalized["items"][0]["score"] == pytest.approx(0.57485133)
    assert normalized["items"][0]["bbox"] == [1.0, 687.0, 83.0, 800.0]
    assert normalized["items"][1]["item_status"] == "INVALID"


def test_invalid_item_and_empty_success():
    payload = {"Code": "200", "Success": True, "Data": {"BoxCount": 2, "Data": [
        {"Idx": 1, "Error": "embedding failed", "Bbox": [1], "Top1": None},
        {"Idx": 2, "Bbox": [1, 1, 2, 2], "Top1": {"SkuId": "a", "Score": 0.7}},
    ]}}
    items = normalize_sku(payload)["items"]
    assert [item["item_status"] for item in items] == ["INVALID", "OK"]
    assert normalize_sku({"Code": "200", "Success": True, "Data": {"Data": []}})["items"] == []
    with pytest.raises(ValueError):
        normalize_sku({"Code": "500", "Success": False, "Data": {"Data": []}})
    with pytest.raises(ValueError):
        AgentConfig.model_validate({"sku_price_match": {"enabled": True}})


def test_sample_client_requires_same_image(tmp_path):
    client = SampleSkuClient(tmp_path)
    (tmp_path / "photo.sku.json").write_text('{"Success": true}', encoding="utf-8")
    assert client.recognize("https://example.com/photo.jpg")["Success"]
    with pytest.raises(FileNotFoundError):
        client.recognize("https://example.com/other.jpg")


def test_matching_keeps_unmatched_and_evidence():
    sku = [{"idx": 0, "bbox": [100, 100, 200, 300], "sku_code": "A",
            "sku_name": "product", "score": 0.9, "item_status": "OK"}]
    tags = [{"id": 1, "bbox": [105, 320, 195, 350], "price": "8.90", "score": 0.8},
            {"id": 2, "bbox": [800, 800, 900, 850], "price": "9.90"}]
    rows = match_sku_tags(sku, tags)
    assert rows[0]["sku_code"] == "A"
    assert rows[0]["cost"] < 0.65
    assert rows[0]["second_cost"] is None
    assert rows[0]["match_status"] == "AMBIGUOUS"
    assert rows[1]["match_status"] == "UNMATCHED"
