from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def test_baseline_migration_upgrades_empty_database(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("PRICE_SERVICE_DATABASE_URL", database_url)

    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
