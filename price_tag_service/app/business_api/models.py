from __future__ import annotations

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Integer, MetaData, Numeric, SmallInteger, String, Table, func
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
json_column = JSON().with_variant(JSONB(), "postgresql")


def audit_columns() -> list[Column]:
    return [
        Column("create_time", DateTime, nullable=False, server_default=func.current_timestamp()),
        Column("update_time", DateTime, nullable=False, server_default=func.current_timestamp()),
        Column("create_by", String(32), nullable=False, server_default=""),
        Column("update_by", String(32), nullable=False, server_default=""),
        Column("deleted", SmallInteger, nullable=False, server_default="0"),
    ]


task = Table(
    "mn_task_list", metadata,
    Column("task_id", String(64), primary_key=True),
    Column("data_source", SmallInteger, nullable=False),
    Column("quality_check", SmallInteger, nullable=False),
    Column("auto_run", SmallInteger, nullable=False),
    Column("business_code", String(64), nullable=False),
    Column("business_unit", String(64), nullable=False),
    Column("district_code", String(64)), Column("district_name", String(64)),
    Column("province_code", String(64)), Column("province_name", String(64)),
    Column("capture_date_start", Date), Column("capture_date_end", Date),
    Column("task_status", SmallInteger, nullable=False),
    Column("sync_date", DateTime, nullable=False),
    Column("batch_no", Integer, nullable=False),
    Column("remark", String(500)), *audit_columns(),
)

photo = Table(
    "mn_task_photo_list", metadata,
    Column("image_list_id", String(64), primary_key=True),
    Column("task_id", String(64), ForeignKey("mn_task_list.task_id"), nullable=False, index=True),
    Column("image_id", String(64), nullable=False, index=True),
    Column("image_url", String(512), nullable=False),
    Column("shop_id", String(64)), Column("sync_date", DateTime, nullable=False),
    Column("remark", String(500)), *audit_columns(),
)


def image_columns() -> list[Column]:
    return [
        Column("task_id", String(64), ForeignKey("mn_task_list.task_id"), nullable=False, index=True),
        Column("image_list_id", String(64), ForeignKey("mn_task_photo_list.image_list_id"), nullable=False),
        Column("image_id", String(64), nullable=False, index=True),
        Column("image_url", String(512), nullable=False),
    ]


quality = Table(
    "mn_image_quality_check", metadata,
    Column("quality_check_id", String(64), primary_key=True), *image_columns(),
    Column("scene_type", SmallInteger, nullable=False),
    Column("quality_issue", String(16)),
    Column("has_price_tag", SmallInteger, nullable=False),
    Column("quality_check_result", SmallInteger, nullable=False),
    Column("score", Numeric(5, 2), nullable=False), Column("model_latency", Integer, nullable=False),
    Column("agent_recognition_result", json_column, nullable=False),
    Column("remark", String(500)), *audit_columns(),
)

sku = Table(
    "mn_sku_recognition", metadata,
    Column("sku_recognition_id", String(64), primary_key=True), *image_columns(),
    Column("shop_id", String(64)),
    Column("agent_recognition_result", json_column, nullable=False),
    Column("sku_parse_json", json_column, nullable=False),
    Column("recognition_time", DateTime, nullable=False), Column("model_latency", Integer, nullable=False),
    Column("remark", String(500)), *audit_columns(),
)

price_tag = Table(
    "mn_price_tag_recognition", metadata,
    Column("price_tag_recognition_id", String(64), primary_key=True), *image_columns(),
    Column("shop_id", String(64)),
    Column("agent_recognition_result", json_column, nullable=False),
    Column("price_parse_json", json_column, nullable=False),
    Column("model_latency", Integer, nullable=False), Column("recognition_time", DateTime, nullable=False),
    Column("remark", String(500)), *audit_columns(),
)

fusion = Table(
    "mn_image_recognition_result", metadata,
    Column("image_recognition_result_id", String(64), primary_key=True), *image_columns(),
    Column("sku_code", String(64), nullable=False), Column("sku_name", String(255), nullable=False),
    Column("price", Numeric(18, 2), nullable=False), Column("company", String(255)), Column("brand", String(255)),
    Column("sku_min_price", Numeric(18, 2), nullable=False), Column("sku_max_price", Numeric(18, 2), nullable=False),
    Column("price_check_result", SmallInteger, nullable=False),
    Column("original_sku_bbox", json_column), Column("original_price_bbox", json_column),
    Column("remark", String(500)), *audit_columns(),
)
