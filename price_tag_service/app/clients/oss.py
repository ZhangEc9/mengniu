from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

from app.clients.models import AismCallError
from app.core.config import OssConfig


class OssClient:
    def __init__(self, config: OssConfig):
        self.config = config

    def upload(self, file_path: Path) -> str:
        if not self.config.upload_url or not self.config.bearer_token.get_secret_value():
            raise AismCallError("OSS endpoint is not configured", error_type="CONFIG_ERROR")
        if not file_path.is_file():
            raise AismCallError(f"Upload file not found: {file_path}", error_type="FILE_NOT_FOUND")

        mime_type = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
        last_error: Exception | None = None
        with httpx.Client(timeout=self.config.timeout_sec) as client:
            for _ in range(self.config.retries):
                try:
                    with file_path.open("rb") as file_handle:
                        response = client.post(
                            self.config.upload_url,
                            headers={"Authorization": self.config.bearer_token.get_secret_value()},
                            data={"type": "0"},
                            files={"file": (file_path.name, file_handle, mime_type)},
                        )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("success") and payload.get("data", {}).get("fileUrl"):
                        return payload["data"]["fileUrl"]
                    last_error = RuntimeError(payload.get("message") or "OSS business error")
                except Exception as exc:
                    last_error = exc
        raise AismCallError(f"OSS upload failed: {last_error}", error_type="OSS_UPLOAD_ERROR")
