from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import imports, photos, system, tasks
from app.core.config import Settings, load_settings
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    if app_settings.require_api_key and not app_settings.api_key.get_secret_value():
        raise RuntimeError("PRICE_SERVICE_REQUIRE_API_KEY=true but PRICE_SERVICE_API_KEY is empty")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.db = Database(app_settings)
        app.state.db.create_all()
        yield

    app = FastAPI(
        title="Mengniu Price Tag Recognition Service",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(system.router)
    app.include_router(tasks.router)
    app.include_router(imports.router)
    app.include_router(photos.router)
    return app


app = create_app()
