from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.models.entities import RecognitionPhoto
from app.api.deps import require_api_key

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "price-tag-recognition", "version": "0.1.0"}


@router.get("/ready")
def ready() -> dict:
    return {"status": "ready"}


@router.get("/v1/queue/stats", dependencies=[Depends(require_api_key)])
def queue_stats(request: Request) -> dict:
    session_factory = request.app.state.db.session_factory
    with session_factory() as session:
        rows = session.execute(
            select(RecognitionPhoto.status, RecognitionPhoto.outcome, func.count())
            .group_by(RecognitionPhoto.status, RecognitionPhoto.outcome)
        ).all()
    return {
        "stats": [
            {"status": status.value, "outcome": outcome.value if outcome else None, "count": count}
            for status, outcome, count in rows
        ]
    }
