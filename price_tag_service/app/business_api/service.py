from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import logging
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.business_api import models
from app.business_api.demo_fusion import DEMO_TASK_REMARK, demo_fusion_rows
from app.business_api.schema import TaskCreate
from app.business_api.status import TaskStatus
from app.contracts.agents import PhotoInput, PhotoOutput
from app.processing.sku_match import match_sku_tags

logger = logging.getLogger(__name__)


def new_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def current_sync_date() -> datetime:
    china_date = datetime.now(timezone(timedelta(hours=8))).date()
    return datetime.combine(china_date, time.min)


class BusinessService:
    def __init__(self, database_url: str, agent_url: str, agent_key: str,
                 agent_transport: httpx.BaseTransport | None = None, demo_fusion: bool = False):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.agent_url = agent_url.rstrip("/")
        self.agent_key = agent_key
        self.agent_transport = agent_transport
        self.demo_fusion = demo_fusion

    def create_task(self, request: TaskCreate) -> str:
        if request.demo_mode and (not self.demo_fusion or not request.business_code.startswith("DEMO_")):
            raise ValueError("Demo tasks require the B service demo switch and a DEMO_ business_code")
        task_id = new_id()
        sync_date = current_sync_date()
        image_ids = [item.image_id for item in request.photos if item.image_id is not None]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("Duplicate image_id in task")
        with self.sessions.begin() as session:
            session.execute(models.task.insert().values(
                task_id=task_id, data_source=request.data_source, quality_check=int(request.quality_check),
                auto_run=0, business_code=request.business_code, business_unit=request.business_unit,
                task_status=TaskStatus.PENDING, sync_date=sync_date, batch_no=1,
                remark=DEMO_TASK_REMARK if request.demo_mode else "",
            ))
            for item in request.photos:
                session.execute(models.photo.insert().values(
                    image_list_id=new_id(), task_id=task_id, image_id=item.image_id or new_id(),
                    image_url=str(item.image_url), shop_id=item.shop_id, sync_date=sync_date,
                ))
        return task_id

    def execute(self, task_id: str) -> dict:
        self.queue_task(task_id)
        return self.run_claimed_task(task_id)

    def queue_task(self, task_id: str) -> None:
        with self.sessions.begin() as session:
            exists = session.scalar(select(models.task.c.task_id).where(models.task.c.task_id == task_id))
            if exists is None:
                raise KeyError(task_id)
            claimed = session.execute(update(models.task).where(
                models.task.c.task_id == task_id, models.task.c.task_status == TaskStatus.PENDING,
            ).values(task_status=TaskStatus.RUNNING, update_time=now()))
            if claimed.rowcount != 1:
                raise ValueError("Task already started; concurrent execution or rerun is not supported")

    def run_claimed_task(self, task_id: str) -> dict:
        with self.sessions() as session:
            task = session.execute(select(models.task).where(models.task.c.task_id == task_id)).mappings().first()
            if task is None or task["task_status"] != TaskStatus.RUNNING:
                raise ValueError("Task is not running")
            photos = session.execute(select(models.photo).where(models.photo.c.task_id == task_id)).mappings().all()
        failures = []
        for image in photos:
            with self.sessions() as session:
                already = session.execute(select(models.sku.c.sku_recognition_id).where(
                    models.sku.c.image_list_id == image["image_list_id"])).first()
            if already:
                continue
            request = PhotoInput(task_id=task_id, image_list_id=image["image_list_id"],
                                 image_id=image["image_id"], image_url=image["image_url"],
                                 shop_id=image["shop_id"], quality_check=bool(task["quality_check"]))
            try:
                with httpx.Client(timeout=420, transport=self.agent_transport) as client:
                    response = client.post(f"{self.agent_url}/v1/process", json=request.model_dump(mode="json"),
                                           headers={"X-API-Key": self.agent_key})
                    response.raise_for_status()
                result = PhotoOutput.model_validate(response.json())
                fusion_pending = self.save_result(result)
                if fusion_pending:
                    failures.append(image["image_id"])
                if any(output.status == "FAILED" for output in (result.quality, result.sku, result.price_tag)):
                    failures.append(image["image_id"])
            except Exception as exc:
                logger.exception("Failed to process image %s in task %s", image["image_id"], task_id)
                with self.sessions.begin() as session:
                    session.execute(update(models.photo).where(
                        models.photo.c.image_list_id == image["image_list_id"],
                    ).values(remark=str(exc)[:500], update_time=now()))
                failures.append(image["image_id"])
        with self.sessions.begin() as session:
            session.execute(update(models.task).where(models.task.c.task_id == task_id).values(
                task_status=TaskStatus.ERROR if failures else TaskStatus.COMPLETED, update_time=now()))
        return {"task_id": task_id, "failed_images": failures}

    def fail_task(self, task_id: str, error: Exception) -> None:
        with self.sessions.begin() as session:
            session.execute(update(models.task).where(
                models.task.c.task_id == task_id, models.task.c.task_status == TaskStatus.RUNNING,
            ).values(task_status=TaskStatus.ERROR, remark=str(error)[:500], update_time=now()))

    def save_result(self, result: PhotoOutput) -> bool:
        with self.sessions.begin() as session:
            image = session.execute(select(models.photo).where(
                models.photo.c.image_list_id == result.image_list_id)).mappings().first()
            if image is None or image["task_id"] != result.task_id or image["image_id"] != result.image_id or image["image_url"] != str(result.image_url):
                raise ValueError("Agent result does not match a registered photo")
            demo_task = self.demo_fusion and session.scalar(select(models.task.c.remark).where(
                models.task.c.task_id == result.task_id)) == DEMO_TASK_REMARK
            if any(session.execute(select(table.c[primary_key]).where(
                    table.c.image_list_id == result.image_list_id)).first() for table, primary_key in (
                        (models.quality, "quality_check_id"),
                        (models.sku, "sku_recognition_id"),
                        (models.price_tag, "price_tag_recognition_id"),
                    )):
                return bool(image["remark"] and image["remark"].startswith("Fusion pending:"))
            identity = dict(task_id=result.task_id, image_list_id=result.image_list_id,
                            image_id=result.image_id, image_url=str(result.image_url))
            quality = result.quality
            parsed_quality = quality.parsed if isinstance(quality.parsed, dict) else {}
            if quality.status in ("SUCCEEDED", "BLOCKED"):
                if quality.raw is None or parsed_quality.get("score") is None or quality.latency_ms is None:
                    raise ValueError("Quality result lacks mandatory score/raw/latency")
                scene = {"堆头照": 1, "货架照": 2, "冰箱照": 3}.get(parsed_quality.get("scene_group"), 0)
                issues = parsed_quality.get("quality_checks") or {}
                issue_codes = [index for index, key in enumerate(("图片模糊", "过度曝光", "光线不足", "文件损坏"), 1)
                               if issues.get(key) and issues[key] != "合格"]
                session.execute(models.quality.insert().values(
                    quality_check_id=new_id(), **identity, scene_type=scene,
                    quality_issue=",".join(map(str, issue_codes)) if issue_codes else None,
                    has_price_tag=int(bool(parsed_quality.get("has_price_tag"))),
                    quality_check_result=1 if quality.status == "SUCCEEDED" else 0,
                    score=parsed_quality["score"], model_latency=quality.latency_ms,
                    agent_recognition_result=quality.raw,
                    remark=("; ".join(parsed_quality.get("rejection_reasons") or []) or quality.status)[:500],
                ))
            for table, key, output, parse_field in (
                (models.sku, "sku_recognition_id", result.sku, "sku_parse_json"),
                (models.price_tag, "price_tag_recognition_id", result.price_tag, "price_parse_json"),
            ):
                if output.status != "SUCCEEDED":
                    continue
                if output.raw is None or not isinstance(output.parsed, list) or output.latency_ms is None:
                    raise ValueError(f"{table.name} result lacks mandatory raw/parsed/latency")
                session.execute(table.insert().values(
                    **{key: new_id(), **identity, "shop_id": image["shop_id"],
                       "agent_recognition_result": output.raw,
                       parse_field: output.parsed, "model_latency": output.latency_ms,
                       "recognition_time": now(), "remark": output.status},
                ))
            errors = [f"{name}: {output.error or output.status}" for name, output in (
                ("quality", result.quality), ("sku", result.sku), ("price_tag", result.price_tag),
            ) if output.status == "FAILED"]
            if errors:
                session.execute(update(models.photo).where(
                    models.photo.c.image_list_id == result.image_list_id,
                ).values(remark="; ".join(errors)[:500], update_time=now()))
            if result.sku.status == "SUCCEEDED" and result.price_tag.status == "SUCCEEDED":
                sku_items = result.sku.parsed if isinstance(result.sku.parsed, list) else []
                tags = result.price_tag.parsed if isinstance(result.price_tag.parsed, list) else []
                pairs = match_sku_tags(sku_items, tags)
                if demo_task:
                    for row in demo_fusion_rows(pairs, sku_items, tags):
                        session.execute(models.fusion.insert().values(
                            image_recognition_result_id=new_id(), **identity, **row,
                        ))
                elif any(pair.get("match_status") == "MATCHED" and pair.get("price") for pair in pairs):
                    session.execute(update(models.photo).where(
                        models.photo.c.image_list_id == result.image_list_id,
                    ).values(remark="Fusion pending: SKU reference price range and price-check rule required",
                             update_time=now()))
                    return True
        return False

    def get_task(self, task_id: str) -> dict:
        with self.sessions() as session:
            task = session.execute(select(models.task).where(models.task.c.task_id == task_id)).mappings().first()
            if task is None:
                raise KeyError(task_id)
            photos = session.execute(select(models.photo).where(models.photo.c.task_id == task_id)).mappings().all()
            qc_rows = session.execute(select(models.quality).where(models.quality.c.task_id == task_id)).mappings().all()
            sku_rows = session.execute(select(models.sku).where(models.sku.c.task_id == task_id)).mappings().all()
            price_rows = session.execute(select(models.price_tag).where(models.price_tag.c.task_id == task_id)).mappings().all()
            matches = session.execute(select(models.fusion).where(models.fusion.c.task_id == task_id)).mappings().all()
        quality_by_photo = {row["image_list_id"]: row for row in qc_rows}
        sku_by_photo = {row["image_list_id"]: row for row in sku_rows}
        price_by_photo = {row["image_list_id"]: row for row in price_rows}
        return {"task_id": task_id, "task_status": task["task_status"],
                "business_code": task["business_code"], "business_unit": task["business_unit"],
                "task_remark": task["remark"],
                "photos": [
                    {"image_list_id": row["image_list_id"], "image_id": row["image_id"],
                     "quality_result": quality_by_photo[row["image_list_id"]]["quality_check_result"]
                     if row["image_list_id"] in quality_by_photo else None,
                     "quality_remark": quality_by_photo[row["image_list_id"]]["remark"]
                     if row["image_list_id"] in quality_by_photo else None,
                     "sku_status": "SUCCEEDED" if row["image_list_id"] in sku_by_photo else None,
                     "sku_items": sku_by_photo[row["image_list_id"]]["sku_parse_json"]
                     if row["image_list_id"] in sku_by_photo else None,
                     "price_status": "SUCCEEDED" if row["image_list_id"] in price_by_photo else None,
                     "price_tags": price_by_photo[row["image_list_id"]]["price_parse_json"]
                     if row["image_list_id"] in price_by_photo else None,
                     "remark": row["remark"]}
                    for row in photos],
                "matches": [{"image_id": row["image_id"], "sku_code": row["sku_code"],
                             "sku_name": row["sku_name"], "price": str(row["price"]),
                             "sku_min_price": str(row["sku_min_price"]),
                             "sku_max_price": str(row["sku_max_price"]),
                             "price_check_result": row["price_check_result"], "remark": row["remark"]}
                            for row in matches]}
