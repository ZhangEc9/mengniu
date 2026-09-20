# -*- coding: utf-8 -*-

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
PROMPTS_DIR = ROOT / "prompts"


DEFAULT_URL = (
    "http://sjimgpub.slicejobs.com/algapp/order/type_4/100/7594/46949011/"
    "46949011_6_first_normal_1784719128344_12614631.jpeg"
)
DEFAULT_STEM = "46949011_6_first_normal_1784719128344_12614631"


def load_config():
    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(f"未找到配置文件: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def load_prompt(filename):
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()


def parse_robust_json(text):
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    try:
        return json.loads(clean_text)
    except Exception:
        start = min(
            (idx for idx in (clean_text.find("{"), clean_text.find("[")) if idx >= 0),
            default=-1,
        )
        if start >= 0:
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
                    except Exception:
                        continue
        return clean_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--stem", default=DEFAULT_STEM)
    parser.add_argument("--out-dir", default="output/rerun_raw")
    args = parser.parse_args()

    cfg = load_config()
    price_cfg = cfg.get("price_tag_config", {})
    api_url = price_cfg.get(
        "api_url",
        "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version=dev",
    )
    appid = price_cfg.get("appid", "")
    secret = price_cfg.get("secret", "")
    price_sys = load_prompt("price_system_prompt.txt")
    price_usr = load_prompt("price_user_prompt.txt")
    body = {
        "image": args.url,
        "system_text": price_sys,
        "text": price_usr,
        "userId": "",
        "envSystemName": "",
        "userName": "",
        "envSystemVersion": "",
        "unionId": "",
        "apiVersion": "0.0.2",
    }
    timestamp = str(int(time.time() * 1000))
    sign = hashlib.md5((json.dumps(body) + secret + timestamp).encode()).hexdigest()
    headers = {
        "X-MN-APP-ID": appid,
        "X-MN-SIGN": sign,
        "X-MN-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }

    print(f"请求模型: {args.url}")
    started = time.time()
    response = requests.post(api_url, headers=headers, json=body, timeout=360)
    elapsed = time.time() - started
    print(f"HTTP {response.status_code}，耗时 {elapsed:.2f}s")
    response.raise_for_status()

    response_data = response.json()
    if response_data.get("status") != 0:
        raise RuntimeError(f"业务错误: {response_data.get('message')}")

    page_content = (
        response_data.get("payload", {})
        .get("result", {})
        .get("page_content", "")
    )
    parsed_model_output = parse_robust_json(page_content)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_api_path = out_dir / f"{args.stem}.api_response.json"
    raw_text_path = out_dir / f"{args.stem}.model_page_content.txt"
    parsed_path = out_dir / f"{args.stem}.parsed_model_output.json"

    raw_api_path.write_text(
        json.dumps(response_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    raw_text_path.write_text(page_content, encoding="utf-8")
    parsed_path.write_text(
        json.dumps(parsed_model_output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"原始 HTTP 响应: {raw_api_path}")
    print(f"原始模型文本:  {raw_text_path}")
    print(f"解析后模型输出: {parsed_path}")


if __name__ == "__main__":
    main()
