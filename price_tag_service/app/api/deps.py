from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session


def get_settings(request: Request):
    return request.app.state.settings


def get_db(request: Request):
    session = request.app.state.db.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    settings = request.app.state.settings
    if not settings.require_api_key:
        return
    configured = settings.api_key.get_secret_value()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key is required but not configured",
        )
    if x_api_key is None or not hmac.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


SettingsDep = Annotated[object, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]
Protected = Depends(require_api_key)
