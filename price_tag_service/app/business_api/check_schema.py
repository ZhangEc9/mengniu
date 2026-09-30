from __future__ import annotations

import os
import sys
from getpass import getpass

from sqlalchemy import UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import URL

from app.business_api.models import metadata


def compare_schema(connection: Connection, schema: str | None = "public") -> list[str]:
    inspector = inspect(connection)
    issues: list[str] = []
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name, schema=schema):
            issues.append(f"{schema or 'default'}.{table.name}: missing table")
            continue
        actual_columns = {column["name"]: column for column in inspector.get_columns(table.name, schema=schema)}
        for expected in table.columns:
            actual = actual_columns.get(expected.name)
            if actual is None:
                issues.append(f"{table.name}.{expected.name}: missing column")
                continue
            expected_type = expected.type.dialect_impl(connection.dialect)
            actual_type = actual["type"]
            if expected_type._type_affinity != actual_type._type_affinity:
                issues.append(f"{table.name}.{expected.name}: expected {expected_type}, found {actual_type}")
            elif (getattr(expected_type, "length", None) and getattr(actual_type, "length", None)
                  and actual_type.length < expected_type.length):
                issues.append(f"{table.name}.{expected.name}: length {actual_type.length} < {expected_type.length}")
            if (getattr(expected_type, "precision", None) and getattr(actual_type, "precision", None)
                    and (expected_type.precision, expected_type.scale) != (actual_type.precision, actual_type.scale)):
                issues.append(f"{table.name}.{expected.name}: numeric precision/scale differs")
            if not expected.nullable and actual["nullable"] and not expected.primary_key:
                issues.append(f"{table.name}.{expected.name}: nullable in database; expected NOT NULL")
            if expected.nullable and not actual["nullable"] and not expected.primary_key:
                issues.append(f"{table.name}.{expected.name}: NOT NULL in database; application may send NULL")
        for name, actual in actual_columns.items():
            if name not in table.columns and not actual["nullable"] and actual.get("default") is None and not actual.get("identity"):
                issues.append(f"{table.name}.{name}: extra required column without default")
        actual_pk = inspector.get_pk_constraint(table.name, schema=schema).get("constrained_columns") or []
        expected_pk = [column.name for column in table.primary_key.columns]
        if set(actual_pk) != set(expected_pk):
            issues.append(f"{table.name}: primary key {actual_pk}, expected {expected_pk}")
        actual_uniques = {
            tuple(sorted(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table.name, schema=schema)
            if constraint.get("column_names")
        }
        actual_uniques.update(
            tuple(sorted(index["column_names"]))
            for index in inspector.get_indexes(table.name, schema=schema)
            if index.get("unique") and index.get("column_names") and all(index["column_names"])
        )
        if table.name in {"mn_image_quality_check", "mn_sku_recognition", "mn_price_tag_recognition"}:
            if ("image_id",) in actual_uniques:
                issues.append(f"{table.name}: database still enforces UNIQUE(image_id); repeated images may fail")
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                columns = tuple(sorted(column.name for column in constraint.columns))
                if columns not in actual_uniques and set(columns) != set(actual_pk):
                    issues.append(f"{table.name}: application expects unique {columns}, database does not enforce it")
    return issues


def database_url_from_prompt(description: str) -> str | URL:
    database_url = os.environ.get("MENGNIU_BUSINESS_DATABASE_URL")
    if not database_url:
        print(f"{description} Password will not be displayed or saved.")
        host = input("PG host/IP (from your saved database connection): ").strip()
        username = input("PG username: ").strip()
        port_text = input("PG port [5432]: ").strip() or "5432"
        database = input("Database [image_recognition]: ").strip() or "image_recognition"
        if not host or not username or not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
            raise ValueError("Host, username and a valid port are required")
        database_url = URL.create(
            "postgresql+psycopg", username=username, password=getpass("PG password: "),
            host=host, port=int(port_text), database=database,
        )
    return database_url


def main() -> int:
    try:
        database_url = database_url_from_prompt("Read-only PostgreSQL schema check.")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            issues = compare_schema(connection)
            inspector = inspect(connection)
            for table in metadata.sorted_tables:
                can_select, can_insert = connection.execute(text(
                    "SELECT has_table_privilege(current_user, :table_name, 'SELECT'), "
                    "has_table_privilege(current_user, :table_name, 'INSERT')"
                ), {"table_name": f"public.{table.name}"}).one() if inspector.has_table(table.name, schema="public") else (False, False)
                if not can_select or not can_insert:
                    issues.append(f"public.{table.name}: account SELECT={can_select}, INSERT={can_insert}")
            connection.rollback()
    finally:
        engine.dispose()
    for issue in issues:
        print(issue)
    if issues:
        return 1
    print("Six tables, mapped fields, primary keys and read/insert privileges verified (read-only check).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
