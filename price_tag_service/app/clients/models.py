from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallLog:
    ability: str
    attempt: int
    success: bool
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None = None
    http_status: int | None = None
    business_status: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    cost_sec: float | None = None


@dataclass
class AismCallResult:
    parsed_payload: dict[str, Any]
    raw_response: dict[str, Any]
    page_content: str
    model_name: str
    prompt_version: str
    cost_sec: float
    call_logs: list[CallLog] = field(default_factory=list)


class AismCallError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        call_logs: list[CallLog] | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.call_logs = call_logs or []
