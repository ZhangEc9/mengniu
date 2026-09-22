from __future__ import annotations

import hashlib
import json

import httpx

from app.clients.aism import AismClient


def test_sign_uses_exact_request_body():
    body = {
        "image": "https://example.com/a.jpg",
        "system_text": "提示词",
        "text": "用户提示",
        "apiVersion": "0.0.2",
    }
    request_body = AismClient._serialize_body(body)
    request = httpx.Request(
        "POST",
        "https://example.com/api",
        headers={"Content-Type": "application/json"},
        content=request_body.encode("utf-8"),
    )
    expected_sign = hashlib.md5(
        (request_body + "secret" + "123").encode("utf-8")
    ).hexdigest()

    assert AismClient._make_sign(request_body, "secret", "123") == expected_sign
    assert request.content == request_body.encode("utf-8")
    assert json.loads(request.content) == body


def test_price_array_payload_is_normalized():
    parsed = [{"id": 1, "price": "9.90"}]
    normalized = AismClient._normalize_parsed_payload("PRICE_TAG_DETECT", parsed)
    assert normalized == {"price_tags": parsed}
