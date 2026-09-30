from __future__ import annotations

import os

from sqlalchemy import create_engine

from app.business_api.models import metadata


def main() -> None:
    database_url = os.environ["MENGNIU_BUSINESS_DATABASE_URL"]
    engine = create_engine(database_url)
    metadata.create_all(engine)


if __name__ == "__main__":
    main()
