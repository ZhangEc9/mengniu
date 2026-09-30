# -*- coding: utf-8 -*-
"""检查可用 API 配置：config.json 结构、环境变量、price_tag_service 配置。"""
import json
import os
from pathlib import Path

ROOT = Path(r"D:\Shixi\mengniu")
out = []

cfg_path = ROOT / "config.json"
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    out.append(f"config.json top keys: {list(cfg.keys())}")
    for name in ("price_tag_config", "qc_config"):
        sec = cfg.get(name, {})
        out.append(f"{name}: keys={list(sec.keys())}")
        for k, v in sec.items():
            s = str(v)
            if any(t in k.lower() for t in ("secret", "key", "token", "appid")):
                s = s[:6] + "..." + f"(len={len(str(v))})"
            out.append(f"  {k} = {s}")
else:
    out.append("config.json 不存在")

out.append(f"DASHSCOPE_API_KEY env: {bool(os.environ.get('DASHSCOPE_API_KEY'))}")
out.append(f"OPENAI_API_KEY env: {bool(os.environ.get('OPENAI_API_KEY'))}")
out.append(f"QWEN env: {[k for k in os.environ if 'QWEN' in k.upper() or 'DASH' in k.upper()]}")

svc = ROOT / "price_tag_service" / ".env"
out.append(f"price_tag_service/.env exists: {svc.exists()}")

(ROOT / "密集价签专用测试" / "导轨优先几何实验" / "api_config_check.txt").write_text("\n".join(out), encoding="utf-8")
print("ok")
