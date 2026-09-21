from __future__ import annotations

import json
from typing import Any


def parse_robust_json(text: str) -> Any:
    if not isinstance(text, str):
        return text

    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    try:
        return json.loads(clean_text)
    except json.JSONDecodeError:
        pass

    starts = [index for index in (clean_text.find("{"), clean_text.find("[")) if index >= 0]
    if not starts:
        return text
    start = min(starts)
    is_array = clean_text[start] == "["
    for end in range(len(clean_text) - 1, start, -1):
        if clean_text[end] not in "}]":
            continue
        candidates = [clean_text[start : end + 1]]
        if is_array and clean_text[end] == "}":
            candidates.append(clean_text[start : end + 1] + "\n]")
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return text


def ensure_json_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {"value": value}
