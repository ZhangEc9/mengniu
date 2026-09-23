from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.clients.models import AismCallError, AismCallResult, CallLog
from app.core.config import AismEndpointConfig
from app.core.jsonutil import parse_robust_json


class AismClient:
    def __init__(self, config: AismEndpointConfig, prompt_dir: Path):
        self.config = config.model_copy(deep=True)
        self.prompt_dir = prompt_dir

    def _load_prompt(self, filename: str) -> str:
        path = self.prompt_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"Prompt file is empty: {path}")
        return content

    @staticmethod
    def _make_sign(request_body: str, secret: str, timestamp: str) -> str:
        sign_str = request_body + secret + timestamp
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize_body(body: dict[str, Any]) -> str:
        return json.dumps(body)

    @staticmethod
    def _build_body(image_url: str, system_text: str, user_text: str) -> dict[str, Any]:
        return {
            "image": image_url,
            "system_text": system_text,
            "text": user_text,
            "userId": "",
            "envSystemName": "",
            "userName": "",
            "envSystemVersion": "",
            "unionId": "",
            "apiVersion": "0.0.2",
        }

    def quality_check(self, image_url: str) -> AismCallResult:
        system_text = self._load_prompt("quality_system_prompt.txt")
        user_text = self._load_prompt("quality_user_prompt.txt")
        prompt_version = self._prompt_version(system_text, user_text)
        body = self._build_body(
            image_url,
            system_text,
            user_text,
        )
        body["target_sku_img"] = image_url
        return self._invoke("QUALITY_CHECK", body, prompt_version)

    def price_tag_detect(self, image_url: str) -> AismCallResult:
        system_text = self._load_prompt("price_system_prompt_final.txt")
        user_text = self._load_prompt("price_user_prompt_final.txt")
        prompt_version = self._prompt_version(system_text, user_text)
        body = self._build_body(
            image_url,
            system_text,
            user_text,
        )
        return self._invoke("PRICE_TAG_DETECT", body, prompt_version)

    @staticmethod
    def _prompt_version(system_text: str, user_text: str) -> str:
        digest = hashlib.sha256((system_text + "\n" + user_text).encode("utf-8")).hexdigest()
        return f"sha256:{digest[:12]}"

    @staticmethod
    def _normalize_parsed_payload(ability: str, parsed: Any) -> dict[str, Any]:
        if ability == "PRICE_TAG_DETECT" and isinstance(parsed, list):
            return {"price_tags": parsed}
        if not isinstance(parsed, dict):
            raise ValueError("page_content is not a JSON object")
        return parsed

    def _invoke(self, ability: str, body: dict[str, Any], prompt_version: str) -> AismCallResult:
        self.config.prompt_version = prompt_version
        if not self.config.api_url or not self.config.appid or not self.config.secret.get_secret_value():
            raise AismCallError(f"{ability} endpoint is not configured", error_type="CONFIG_ERROR")

        logs: list[CallLog] = []
        last_error: AismCallError | None = None
        with httpx.Client(timeout=self.config.timeout_sec) as client:
            for attempt in range(1, self.config.retries + 1):
                started = time.perf_counter()
                timestamp = str(int(time.time() * 1000))
                try:
                    request_body = self._serialize_body(body)
                    headers = {
                        "X-MN-APP-ID": self.config.appid,
                        "X-MN-SIGN": self._make_sign(
                            request_body, self.config.secret.get_secret_value(), timestamp
                        ),
                        "X-MN-TIMESTAMP": timestamp,
                        "Content-Type": "application/json",
                    }
                    response = client.post(
                        self.config.api_url,
                        headers=headers,
                        content=request_body.encode("utf-8"),
                    )
                    cost_sec = round(time.perf_counter() - started, 3)
                    try:
                        raw_response = response.json()
                    except ValueError as exc:
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                http_status=response.status_code,
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type="RESPONSE_INVALID",
                                error_message="HTTP response is not JSON",
                                cost_sec=cost_sec,
                            )
                        )
                        raise AismCallError(
                            f"{ability} returned non-JSON body",
                            error_type="RESPONSE_INVALID",
                            call_logs=logs,
                        ) from exc
                    if not isinstance(raw_response, dict):
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                response_payload={"value": raw_response},
                                http_status=response.status_code,
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type="RESPONSE_INVALID",
                                error_message="HTTP response is not an object",
                                cost_sec=cost_sec,
                            )
                        )
                        raise AismCallError(
                            f"{ability} HTTP response is not an object",
                            error_type="RESPONSE_INVALID",
                            call_logs=logs,
                        )

                    if response.status_code >= 500 or response.status_code == 429:
                        last_error = AismCallError(
                            f"{ability} HTTP {response.status_code}",
                            error_type="HTTP_RETRYABLE",
                            call_logs=logs,
                        )
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                response_payload=raw_response,
                                http_status=response.status_code,
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type=last_error.error_type,
                                error_message=str(last_error),
                                cost_sec=cost_sec,
                            )
                        )
                        continue
                    if response.status_code >= 400:
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                response_payload=raw_response,
                                http_status=response.status_code,
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type="HTTP_NON_RETRYABLE",
                                error_message=f"{ability} HTTP {response.status_code}",
                                cost_sec=cost_sec,
                            )
                        )
                        raise AismCallError(
                            f"{ability} HTTP {response.status_code}",
                            error_type="HTTP_NON_RETRYABLE",
                            call_logs=logs,
                        )
                    if raw_response.get("status") != 0:
                        last_error = AismCallError(
                            f"{ability} business error: {raw_response.get('message')}",
                            error_type="BUSINESS_RETRYABLE",
                            call_logs=logs,
                        )
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                response_payload=raw_response,
                                http_status=response.status_code,
                                business_status=str(raw_response.get("status")),
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type=last_error.error_type,
                                error_message=str(last_error),
                                cost_sec=cost_sec,
                            )
                        )
                        continue

                    page_content = raw_response.get("payload", {}).get("result", {}).get(
                        "page_content", ""
                    )
                    parsed = parse_robust_json(page_content)
                    try:
                        parsed = self._normalize_parsed_payload(ability, parsed)
                    except ValueError:
                        logs.append(
                            CallLog(
                                ability=ability,
                                attempt=attempt,
                                success=False,
                                request_payload=body,
                                response_payload=raw_response,
                                http_status=response.status_code,
                                business_status="0",
                                model_name=self.config.model_name,
                                prompt_version=self.config.prompt_version,
                                error_type="RESPONSE_INVALID",
                                error_message="page_content is not a JSON object",
                                cost_sec=cost_sec,
                            )
                        )
                        raise AismCallError(
                            f"{ability} page_content is not a JSON object",
                            error_type="RESPONSE_INVALID",
                            call_logs=logs,
                        )

                    logs.append(
                        CallLog(
                            ability=ability,
                            attempt=attempt,
                            success=True,
                            request_payload=body,
                            response_payload=raw_response,
                            http_status=response.status_code,
                            business_status="0",
                            model_name=self.config.model_name,
                            prompt_version=self.config.prompt_version,
                            cost_sec=cost_sec,
                        )
                    )
                    return AismCallResult(
                        parsed_payload=parsed,
                        raw_response=raw_response,
                        page_content=page_content,
                        model_name=self.config.model_name,
                        prompt_version=self.config.prompt_version,
                        cost_sec=cost_sec,
                        call_logs=logs,
                    )
                except httpx.TimeoutException as exc:
                    self._append_transport_log(
                        logs, ability, attempt, body, "TIMEOUT", exc, started, self.config
                    )
                    last_error = AismCallError(
                        f"{ability} timeout", error_type="TIMEOUT", call_logs=logs
                    )
                except httpx.TransportError as exc:
                    self._append_transport_log(
                        logs, ability, attempt, body, "TRANSPORT", exc, started, self.config
                    )
                    last_error = AismCallError(
                        f"{ability} transport error", error_type="TRANSPORT", call_logs=logs
                    )

        if last_error:
            last_error.call_logs = logs
            raise last_error
        raise AismCallError(f"{ability} failed", error_type="UNKNOWN_ERROR", call_logs=logs)

    @staticmethod
    def _append_transport_log(
        logs: list[CallLog],
        ability: str,
        attempt: int,
        body: dict[str, Any],
        error_type: str,
        exc: Exception,
        started: float,
        config: AismEndpointConfig,
    ) -> None:
        logs.append(
            CallLog(
                ability=ability,
                attempt=attempt,
                success=False,
                request_payload=body,
                model_name=config.model_name,
                prompt_version=config.prompt_version,
                error_type=error_type,
                error_message=str(exc),
                cost_sec=round(time.perf_counter() - started, 3),
            )
        )
