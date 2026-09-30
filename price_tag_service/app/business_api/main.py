from __future__ import annotations

import hmac
import logging
import os

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, status

from app.business_api.schema import TaskCreate
from app.business_api.service import BusinessService

logger = logging.getLogger(__name__)


def create_app(service: BusinessService | None = None) -> FastAPI:
    app = FastAPI(title="Mengniu Task and Results Service")
    if service is None:
        database_url = os.environ.get("MENGNIU_BUSINESS_DATABASE_URL")
        agent_url = os.environ.get("MENGNIU_AGENT_URL")
        agent_key = os.environ.get("MENGNIU_AGENT_API_KEY")
        if not database_url or not agent_url or not agent_key:
            raise RuntimeError("Business database URL, agent URL and agent API key are required")
        service = BusinessService(database_url, agent_url, agent_key,
                                  demo_fusion=os.environ.get("MENGNIU_ALLOW_DEMO_FUSION") == "1")
    app.state.service = service

    def run_background_task(task_id: str) -> None:
        try:
            app.state.service.run_claimed_task(task_id)
        except Exception as exc:
            logger.exception("Background task %s failed", task_id)
            app.state.service.fail_task(task_id, exc)

    def authorize(x_api_key: str | None = Header(default=None)) -> None:
        key = os.environ.get("MENGNIU_BUSINESS_API_KEY")
        if not key:
            raise HTTPException(status_code=503, detail="Business API key is not configured")
        if x_api_key is None or not hmac.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.post("/v1/tasks", dependencies=[Depends(authorize)])
    def create_task(request: TaskCreate) -> dict:
        try:
            return {"task_id": app.state.service.create_task(request)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/tasks/{task_id}/execute", dependencies=[Depends(authorize)],
              status_code=status.HTTP_202_ACCEPTED)
    def execute(task_id: str, background_tasks: BackgroundTasks) -> dict:
        try:
            app.state.service.queue_task(task_id)
            background_tasks.add_task(run_background_task, task_id)
            return {"task_id": task_id, "task_status": 1}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/tasks/{task_id}", dependencies=[Depends(authorize)])
    def get_task(task_id: str) -> dict:
        try:
            return app.state.service.get_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    return app


def app_factory() -> FastAPI:
    return create_app()
