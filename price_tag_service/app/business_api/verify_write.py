from __future__ import annotations

import sys

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Connection

from app.business_api import models
from app.business_api.check_schema import compare_schema, database_url_from_prompt
from app.business_api.service import current_sync_date, new_id, now


def verify_rollback(connection: Connection, schema: str | None = "public") -> None:
    transaction = connection.begin()
    try:
        issues = compare_schema(connection, schema=schema)
        if issues:
            raise ValueError("Schema mismatch: " + "; ".join(issues))
        task_id, image_list_id, image_id = new_id(), new_id(), new_id()
        identity = dict(task_id=task_id, image_list_id=image_list_id, image_id=image_id,
                        image_url="https://example.invalid/rollback-only.jpg")
        connection.execute(models.task.insert().values(
            task_id=task_id, data_source=2, quality_check=1, auto_run=0,
            business_code="rollback_test", business_unit="rollback_test",
            task_status=0, sync_date=current_sync_date(), batch_no=1,
        ))
        connection.execute(models.photo.insert().values(
            **identity, sync_date=current_sync_date(),
        ))
        connection.execute(models.quality.insert().values(
            quality_check_id=new_id(), **identity, scene_type=0, quality_issue=None,
            has_price_tag=1, quality_check_result=1, score=0.95, model_latency=1,
            agent_recognition_result={"verification": "rollback_only"},
        ))
        connection.execute(models.sku.insert().values(
            sku_recognition_id=new_id(), **identity, agent_recognition_result={},
            sku_parse_json=[], recognition_time=now(), model_latency=1,
        ))
        connection.execute(models.price_tag.insert().values(
            price_tag_recognition_id=new_id(), **identity, agent_recognition_result={},
            price_parse_json=[], recognition_time=now(), model_latency=1,
        ))
        connection.execute(models.fusion.insert().values(
            image_recognition_result_id=new_id(), **identity, sku_code="rollback_test",
            sku_name="rollback_test", price=1, sku_min_price=1, sku_max_price=1,
            price_check_result=0,
        ))
        for table in models.metadata.sorted_tables:
            key = table.c.image_list_id if "image_list_id" in table.c else table.c.task_id
            value = image_list_id if "image_list_id" in table.c else task_id
            if connection.execute(select(key).select_from(table).where(key == value)).first() is None:
                raise ValueError(f"Insert not visible in transaction: {table.name}")
    finally:
        transaction.rollback()


def main() -> int:
    try:
        database_url = database_url_from_prompt("Transactional write verification (all rows rolled back).")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection:
            verify_rollback(connection)
    finally:
        engine.dispose()
    print("Six table inserts succeeded and were rolled back; no test rows committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
