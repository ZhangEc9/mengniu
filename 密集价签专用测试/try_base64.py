# -*- coding: utf-8 -*-
"""试探：OSS 不可用时，flowApi 是否接受 base64 图片。"""
import base64
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import run_full_pipeline as pipeline
import center_point_experiment as C

strip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if strip_path is None or not strip_path.exists():
    print("usage: py try_base64.py <strip.jpg>")
    sys.exit(1)

b64 = base64.b64encode(strip_path.read_bytes()).decode()
body = {"image": "data:image/jpeg;base64," + b64,
        "system_text": C.SYSTEM_PROMPT, "text": C.USER_PROMPT,
        "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
        "unionId": "", "apiVersion": "0.0.2"}
timestamp = str(int(time.time() * 1000))
headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
           "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
           "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
r = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=180)
out = {"http": r.status_code, "body": r.text[:1500]}
(HERE / "中心点读价实验" / "base64_probe.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("http", r.status_code)
