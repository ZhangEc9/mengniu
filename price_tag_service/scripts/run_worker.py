from __future__ import annotations

import logging

from app.core.config import load_settings
from app.db.session import Database
from app.worker.processor import WorkerRunner


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )
    settings = load_settings()
    database = Database(settings)
    database.create_all()
    runner = WorkerRunner(settings, database.session_factory)
    try:
        runner.start()
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    main()
