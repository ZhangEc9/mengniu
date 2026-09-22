from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.models.entities import RecognitionTask
from app.services.task_service import refresh_task_counters


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", help="Only refresh one task")
    args = parser.parse_args()

    database = Database(Settings())
    with database.session_factory() as session:
        query = select(RecognitionTask).order_by(RecognitionTask.created_at)
        if args.task_id:
            query = query.where(RecognitionTask.id == args.task_id)
        tasks = session.scalars(query).all()
        for task in tasks:
            refresh_task_counters(session, task.id)
            print(
                task.id,
                task.status.value,
                f"total={task.total_photos}",
                f"processed={task.processed_photos}",
                f"succeeded={task.succeeded_photos}",
                f"blocked={task.blocked_photos}",
                f"failed={task.failed_photos}",
            )
        session.commit()


if __name__ == "__main__":
    main()
