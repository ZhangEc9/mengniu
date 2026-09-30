from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException

from app.agent_api.service import AgentService
from app.contracts.agents import BatchInput, BatchOutput, PhotoInput, PhotoOutput
from app.core.config import load_settings


def create_app(service: AgentService | None = None) -> FastAPI:
    app = FastAPI(title="Mengniu Three-Agent Service")
    app.state.service = service or AgentService(load_settings())

    def authorize(x_api_key: str | None = Header(default=None)) -> None:
        key = os.environ.get("MENGNIU_AGENT_API_KEY")
        if not key:
            raise HTTPException(status_code=503, detail="Agent API key is not configured")
        if x_api_key is None or not hmac.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.post("/v1/process", response_model=PhotoOutput, dependencies=[Depends(authorize)])
    def process(photo: PhotoInput) -> PhotoOutput:
        return app.state.service.process(photo)

    @app.post("/v1/process-batch", response_model=BatchOutput, dependencies=[Depends(authorize)])
    def process_batch(batch: BatchInput) -> BatchOutput:
        return app.state.service.process_batch(batch)

    return app


app = create_app()
