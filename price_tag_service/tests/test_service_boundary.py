from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_api.main import create_app as create_agent_app
from app.agent_api.service import AgentService
from app.business_api import models
from app.business_api.check_schema import compare_schema
from app.business_api.demo_fusion import demo_fusion_rows
from app.business_api.main import create_app as create_business_app
from app.business_api.schema import TaskCreate
from app.business_api.service import BusinessService
from app.business_api.verify_write import verify_rollback
from app.core.config import Settings
from app.clients.sku import SampleSkuClient
from app.contracts.agents import AgentOutput, BatchInput, PhotoInput, PhotoOutput
from app.processing.sku_match import match_sku_tags, normalize_sku


def test_agent_skips_quality_and_runs_both_branches():
    service = object.__new__(AgentService)
    service.sku = lambda url: AgentOutput(status="SUCCEEDED", parsed=[])
    service.price_tag = lambda url: AgentOutput(status="SUCCEEDED", parsed=[])
    service.quality = lambda url: (_ for _ in ()).throw(AssertionError("quality called"))
    request = PhotoInput(task_id="task", image_list_id="photo", image_id="image",
                         image_url="https://example.com/test.jpg", quality_check=False)
    result = service.process(request)
    assert result.quality.status == "SKIPPED"
    assert result.sku.status == result.price_tag.status == "SUCCEEDED"
    assert result.image_id == request.image_id


def test_image_url_limit_matches_database_field():
    long_url = "https://example.com/" + "a" * 500
    with pytest.raises(ValidationError):
        TaskCreate(business_code="store", business_unit="dairy",
                   photos=[{"image_url": long_url}])
    with pytest.raises(ValidationError):
        PhotoInput(task_id="task", image_list_id="photo", image_id="image", image_url=long_url)


def test_export_task_records_data_source_one(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'export.db'}", "https://agent", "key")
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        data_source=1, business_code="confirmed-code", business_unit="confirmed-unit",
        photos=[{"image_url": "https://example.com/export.jpeg"}],
    ))
    with service.sessions() as session:
        assert session.scalar(select(models.task.c.data_source).where(models.task.c.task_id == task_id)) == 1


def test_export_photo_synthetic_sku_fixture_reaches_match_branch():
    image_url = ("https://example.com/"
                 "49098981_7_first_normal_1788763058147_19960398.jpeg")
    sample_dir = Settings().sku_sample_dir
    items = normalize_sku(SampleSkuClient(sample_dir).recognize(image_url))["items"]
    assert len(items) == 2
    assert all(item["item_status"] == "OK" for item in items)
    pair = match_sku_tags(items, [{"id": 1, "bbox": [135, 804, 298, 846],
                                   "price": "12.90", "score": 0.8}])[0]
    assert pair["match_status"] == "MATCHED"
    assert pair["sku_code"] == "130200001752"


def test_demo_requires_explicit_server_switch_and_marker(tmp_path):
    request = dict(business_code="DEMO_FLOW_20260924_X7", business_unit="模拟事业部_仅联调",
                   demo_mode=True, photos=[{"image_url": "https://example.com/photo.jpg"}])
    disabled = BusinessService(f"sqlite:///{tmp_path / 'disabled.db'}", "http://agent", "key")
    with pytest.raises(ValueError, match="demo switch"):
        disabled.create_task(TaskCreate(**request))
    enabled = BusinessService(f"sqlite:///{tmp_path / 'enabled.db'}", "http://agent", "key",
                              demo_fusion=True)
    with pytest.raises(ValueError, match="DEMO_"):
        enabled.create_task(TaskCreate(**{**request, "business_code": "real_business"}))


def test_demo_runs_six_tables_with_synthetic_price_and_null_issue(tmp_path):
    image_url = ("https://example.com/"
                 "49098981_7_first_normal_1788763058147_19960398.jpeg")
    sample_dir = Settings().sku_sample_dir
    raw_sku = SampleSkuClient(sample_dir).recognize(image_url)
    sku_items = normalize_sku(raw_sku)["items"]
    tags = [{"id": 1, "bbox": [135, 804, 298, 846], "price": "12.90", "score": 0.8}]

    def agent_handler(request: httpx.Request) -> httpx.Response:
        photo = PhotoInput.model_validate_json(request.content)
        output = PhotoOutput(
            **photo.model_dump(),
            quality=AgentOutput(status="SUCCEEDED", raw={"quality_checks": {}},
                                parsed={"score": 0.83, "scene_group": "冰箱照",
                                        "has_price_tag": True, "quality_checks": {}}, latency_ms=1),
            sku=AgentOutput(status="SUCCEEDED", raw=raw_sku, parsed=sku_items, latency_ms=1),
            price_tag=AgentOutput(status="SUCCEEDED", raw={"price_tags": tags},
                                  parsed=tags, latency_ms=1),
        )
        return httpx.Response(200, json=output.model_dump(mode="json"))

    service = BusinessService(f"sqlite:///{tmp_path / 'demo.db'}", "http://agent", "key",
                              agent_transport=httpx.MockTransport(agent_handler), demo_fusion=True)
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        data_source=1, demo_mode=True, business_code="DEMO_FLOW_20260924_X7",
        business_unit="模拟事业部_仅联调", photos=[{"image_url": image_url}],
    ))
    assert service.execute(task_id)["failed_images"] == []
    result = service.get_task(task_id)
    assert result["task_status"] == 2
    assert result["business_code"] == "DEMO_FLOW_20260924_X7"
    assert result["task_remark"].startswith("DEMO ONLY")
    assert result["matches"][0]["price_check_result"] == 0
    assert result["matches"][0]["sku_min_price"] == "10.00"
    with service.sessions() as session:
        for table in models.metadata.sorted_tables:
            assert session.scalar(select(func.count()).select_from(table)) == 1
        assert session.scalar(select(models.quality.c.quality_issue)) is None
        assert session.scalar(select(models.fusion.c.original_sku_bbox)) == sku_items[0]["bbox"]

    pair = match_sku_tags(sku_items, [{**tags[0], "price": "16.00"}])
    assert demo_fusion_rows(pair, sku_items, tags)[0]["price_check_result"] == 1


def test_schema_checker_reports_missing_table_without_creating_it(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'schema.db'}", "https://agent", "key")
    with service.engine.connect() as connection:
        assert any("missing table" in issue for issue in compare_schema(connection, schema=None))
    models.metadata.create_all(service.engine)
    with service.engine.connect() as connection:
        assert compare_schema(connection, schema=None) == []


def test_schema_checker_detects_unique_image_index(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'unique.db'}", "https://agent", "key")
    models.metadata.create_all(service.engine)
    with service.engine.begin() as connection:
        connection.exec_driver_sql("CREATE UNIQUE INDEX ux_quality_image ON mn_image_quality_check(image_id)")
    with service.engine.connect() as connection:
        assert any("mn_image_quality_check" in issue and "UNIQUE(image_id)" in issue
                   for issue in compare_schema(connection, schema=None))


def test_six_table_write_verification_rolls_back(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'rollback.db'}", "https://agent", "key")
    models.metadata.create_all(service.engine)
    with service.engine.connect() as connection:
        verify_rollback(connection, schema=None)
    with service.engine.connect() as connection:
        for table in models.metadata.sorted_tables:
            assert connection.scalar(select(func.count()).select_from(table)) == 0


def test_agent_blocks_both_branches():
    service = object.__new__(AgentService)
    service.quality = lambda url: AgentOutput(status="BLOCKED")
    service.sku = service.price_tag = lambda url: (_ for _ in ()).throw(AssertionError("recognition called"))
    request = PhotoInput(task_id="task", image_list_id="photo", image_id="image",
                         image_url="https://example.com/test.jpg")
    result = service.process(request)
    assert result.sku.status == result.price_tag.status == "SKIPPED"


def test_batch_preserves_order_and_isolates_image_failure(monkeypatch):
    service = object.__new__(AgentService)
    def process(photo):
        if photo.image_id == "broken":
            raise RuntimeError("sample failure")
        return PhotoOutput(**photo.model_dump(), quality=AgentOutput(status="SKIPPED"),
                           sku=AgentOutput(status="SUCCEEDED", parsed=[]),
                           price_tag=AgentOutput(status="SUCCEEDED", parsed=[]))
    monkeypatch.setattr(service, "process", process)
    batch = BatchInput(request_id="req", task_id="task", quality_check=False, images=[
        {"image_list_id": "first", "image_id": "broken", "image_url": "https://example.com/1.jpg"},
        {"image_list_id": "second", "image_id": "ok", "image_url": "https://example.com/2.jpg"},
    ])
    result = service.process_batch(batch)
    assert result.request_id == "req"
    assert [item.image_id for item in result.results] == ["broken", "ok"]
    assert result.results[0].quality.status == "FAILED"
    assert result.results[1].sku.status == "SUCCEEDED"
    with pytest.raises(ValidationError, match="Duplicate image_id"):
        BatchInput(request_id="req", task_id="task", images=[
            {"image_list_id": "first", "image_id": "same", "image_url": "https://example.com/1.jpg"},
            {"image_list_id": "second", "image_id": "same", "image_url": "https://example.com/2.jpg"},
        ])


def test_batch_route_requires_auth_and_preserves_request_id(monkeypatch):
    service = object.__new__(AgentService)
    service.process = lambda photo: PhotoOutput(
        **photo.model_dump(), quality=AgentOutput(status="SKIPPED"),
        sku=AgentOutput(status="SUCCEEDED", parsed=[]),
        price_tag=AgentOutput(status="SUCCEEDED", parsed=[]))
    monkeypatch.setenv("MENGNIU_AGENT_API_KEY", "agent-key")
    client = TestClient(create_agent_app(service))
    body = {"request_id": "req-1", "task_id": "task", "quality_check": False, "images": [
        {"image_list_id": "photo", "image_id": "image", "image_url": "https://example.com/test.jpg"}]}
    assert client.post("/v1/process-batch", json=body).status_code == 401
    response = client.post("/v1/process-batch", json=body, headers={"X-API-Key": "agent-key"})
    assert response.status_code == 200
    assert response.json()["request_id"] == "req-1"
    assert response.json()["results"][0]["image_id"] == "image"


def test_business_persists_once_and_rejects_wrong_identity(tmp_path, monkeypatch):
    monkeypatch.setattr("app.business_api.service.current_sync_date", lambda: datetime(2026, 9, 24))
    service = BusinessService(f"sqlite:///{tmp_path / 'business.db'}", "http://agent", "key")
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        business_code="store", business_unit="dairy", quality_check=False,
        photos=[{"image_url": "https://example.com/test.jpg", "image_id": "stable-id"}],
    ))
    with service.sessions() as session:
        photo = session.execute(select(models.photo)).mappings().one()
    identity = dict(task_id=task_id, image_list_id=photo["image_list_id"], image_id="stable-id",
                    image_url="https://example.com/test.jpg", quality_check=False)
    sku_items = [
        {"idx": 1, "bbox": [100, 100, 200, 300], "sku_code": "milk", "sku_name": "Milk",
         "score": 0.9, "item_status": "OK"},
        {"idx": 2, "bbox": [200, 100, 300, 300], "sku_code": "water", "sku_name": "Water",
         "score": 0.8, "item_status": "OK"},
    ]
    result = PhotoOutput(**identity, quality=AgentOutput(status="SKIPPED"),
                         sku=AgentOutput(status="SUCCEEDED", raw={"Data": []}, parsed=sku_items, latency_ms=3),
                         price_tag=AgentOutput(status="SUCCEEDED", raw={"price_tags": []}, parsed=[
                             {"id": 1, "bbox": [105, 320, 195, 350], "price": "8.90", "score": 0.8}], latency_ms=9))
    assert service.save_result(result) is True
    assert service.save_result(result) is True
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(models.quality)) == 0
        for table in (models.sku, models.price_tag):
            assert session.scalar(select(func.count()).select_from(table)) == 1
        assert session.scalar(select(func.count()).select_from(models.fusion)) == 0
        assert session.scalar(select(models.photo.c.sync_date)) == datetime(2026, 9, 24)
        assert session.scalar(select(models.task.c.sync_date)) == datetime(2026, 9, 24)
        assert session.scalar(select(models.task.c.batch_no)) == 1
    assert service.get_task(task_id)["photos"][0]["remark"].startswith("Fusion pending:")
    try:
        service.save_result(result.model_copy(update={"image_id": "wrong"}))
    except ValueError:
        pass
    else:
        raise AssertionError("Wrong image identity was accepted")


def test_quality_score_uses_actual_schema_and_blocked_skips_recognition(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'quality.db'}", "https://agent", "key")
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        business_code="store", business_unit="dairy",
        photos=[{"image_url": "https://example.com/blocked.jpg"}],
    ))
    with service.sessions() as session:
        image = session.execute(select(models.photo)).mappings().one()
    result = PhotoOutput(task_id=task_id, image_list_id=image["image_list_id"],
                         image_id=image["image_id"], image_url=image["image_url"],
                         quality=AgentOutput(status="BLOCKED", raw={"qc_result": {}},
                                             parsed={"score": 0.83, "quality_checks": {"图片模糊": "不合格"}},
                                             latency_ms=50),
                         sku=AgentOutput(status="SKIPPED"), price_tag=AgentOutput(status="SKIPPED"))
    assert service.save_result(result) is False
    assert service.save_result(result) is False
    with service.sessions() as session:
        quality = session.execute(select(models.quality)).mappings().one()
        assert quality["quality_check_result"] == 0
        assert float(quality["score"]) == 0.83
        assert quality["quality_issue"] == "1"
        assert session.scalar(select(func.count()).select_from(models.sku)) == 0
        assert session.scalar(select(func.count()).select_from(models.price_tag)) == 0


def test_quality_issue_keeps_multiple_codes_and_null_for_pass(tmp_path):
    service = BusinessService(f"sqlite:///{tmp_path / 'quality_codes.db'}", "https://agent", "key")
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        business_code="store", business_unit="dairy", photos=[
            {"image_url": "https://example.com/multi.jpg"},
            {"image_url": "https://example.com/pass.jpg"},
        ],
    ))
    with service.sessions() as session:
        images = session.execute(select(models.photo).order_by(models.photo.c.image_url)).mappings().all()
    for image in images:
        multiple = image["image_url"].endswith("multi.jpg")
        quality = AgentOutput(
            status="BLOCKED" if multiple else "SUCCEEDED", raw={"qc_result": {}},
            parsed={"score": 0.75, "quality_checks":
                    {"图片模糊": "不合格", "过度曝光": "不合格"} if multiple else {}},
            latency_ms=10,
        )
        result = PhotoOutput(task_id=task_id, image_list_id=image["image_list_id"],
                             image_id=image["image_id"], image_url=image["image_url"],
                             quality=quality, sku=AgentOutput(status="SKIPPED"),
                             price_tag=AgentOutput(status="SKIPPED"))
        service.save_result(result)
    with service.sessions() as session:
        issues = session.execute(select(models.quality.c.quality_issue).order_by(models.quality.c.image_url)).scalars().all()
    assert issues == ["1,2", None]


def test_business_executes_via_agent_contract(tmp_path):
    def agent_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "secret"
        incoming = PhotoInput.model_validate_json(request.content)
        return httpx.Response(200, json=PhotoOutput(
            **incoming.model_dump(), quality=AgentOutput(status="SKIPPED"),
            sku=AgentOutput(status="SUCCEEDED", raw={"Data": []}, parsed=[], latency_ms=1),
            price_tag=AgentOutput(status="SUCCEEDED", raw={"price_tags": []}, parsed=[], latency_ms=2),
        ).model_dump(mode="json"))

    service = BusinessService(f"sqlite:///{tmp_path / 'exec.db'}", "https://agent", "secret",
                              agent_transport=httpx.MockTransport(agent_handler))
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(
        business_code="store", business_unit="dairy", quality_check=False,
        photos=[{"image_url": "https://example.com/photo.jpg"}],
    ))
    assert service.execute(task_id) == {"task_id": task_id, "failed_images": []}
    data = service.get_task(task_id)
    assert data["task_status"] == 2
    assert data["photos"][0]["sku_status"] == "SUCCEEDED"
    assert data["photos"][0]["price_status"] == "SUCCEEDED"
    assert data["matches"] == []
    with pytest.raises(ValueError, match="already started"):
        service.execute(task_id)


def test_api_keys_and_task_routes(tmp_path, monkeypatch):
    service = BusinessService(f"sqlite:///{tmp_path / 'api.db'}", "https://agent", "agent-key")
    models.metadata.create_all(service.engine)
    monkeypatch.setenv("MENGNIU_BUSINESS_API_KEY", "business-key")
    client = TestClient(create_business_app(service))
    payload = {"business_code": "store", "business_unit": "dairy", "photos": [
        {"image_url": "https://example.com/photo.jpg"}]}
    assert client.post("/v1/tasks", json=payload).status_code == 401
    response = client.post("/v1/tasks", json=payload, headers={"X-API-Key": "business-key"})
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": "business-key"}).status_code == 200
    agent = object.__new__(AgentService)
    agent.process = lambda photo: PhotoOutput(
        **photo.model_dump(), quality=AgentOutput(status="SKIPPED"),
        sku=AgentOutput(status="SKIPPED"), price_tag=AgentOutput(status="SKIPPED"))
    monkeypatch.setenv("MENGNIU_AGENT_API_KEY", "agent-key")
    agent_client = TestClient(create_agent_app(agent))
    with service.sessions() as session:
        image = session.execute(select(models.photo)).mappings().one()
    request = PhotoInput(task_id=task_id, image_list_id=image["image_list_id"],
                         image_id=image["image_id"], image_url=image["image_url"])
    assert agent_client.post("/v1/process", json=request.model_dump(mode="json")).status_code == 401
    assert agent_client.post("/v1/process", json=request.model_dump(mode="json"),
                             headers={"X-API-Key": "agent-key"}).status_code == 200


def test_execute_endpoint_returns_accepted_and_records_background_error(tmp_path, monkeypatch):
    service = BusinessService(f"sqlite:///{tmp_path / 'background.db'}", "https://agent", "agent-key")
    models.metadata.create_all(service.engine)
    task_id = service.create_task(TaskCreate(business_code="store", business_unit="dairy",
                                              photos=[{"image_url": "https://example.com/photo.jpg"}]))
    monkeypatch.setenv("MENGNIU_BUSINESS_API_KEY", "business-key")
    monkeypatch.setattr(service, "run_claimed_task", lambda task_id: (_ for _ in ()).throw(RuntimeError("worker failed")))
    client = TestClient(create_business_app(service))
    endpoint = f"/v1/tasks/{task_id}/execute"
    headers = {"X-API-Key": "business-key"}
    response = client.post(endpoint, headers=headers)
    assert response.status_code == 202
    assert response.json() == {"task_id": task_id, "task_status": 1}
    assert service.get_task(task_id)["task_status"] == 3
    assert client.post(endpoint, headers=headers).status_code == 409
