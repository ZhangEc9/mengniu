from __future__ import annotations

from app.core.config import load_settings
from app.db.session import Database


def main() -> None:
    settings = load_settings()
    database = Database(settings)
    database.create_all()
    print(f"Database initialized: {settings.database_url}")


if __name__ == "__main__":
    main()
