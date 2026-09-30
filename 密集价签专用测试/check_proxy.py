# -*- coding: utf-8 -*-
"""诊断 python requests 连不上而 curl 能连上的原因。"""
import os
import urllib.request

import requests

out = []
out.append(f"env HTTP_PROXY={os.environ.get('HTTP_PROXY')}")
out.append(f"env HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}")
out.append(f"env http_proxy={os.environ.get('http_proxy')}")
out.append(f"env https_proxy={os.environ.get('https_proxy')}")
out.append(f"env NO_PROXY={os.environ.get('NO_PROXY')}")
out.append(f"urllib getproxies: {urllib.request.getproxies()}")

try:
    r = requests.get("https://aismapi.mengniu.cn/", timeout=10,
                     proxies={"http": None, "https": None})
    out.append(f"requests直连(禁代理): {r.status_code}")
except Exception as e:
    out.append(f"requests直连(禁代理)失败: {type(e).__name__}: {e}")

try:
    r = requests.get("https://aismapi.mengniu.cn/", timeout=10)
    out.append(f"requests默认: {r.status_code}")
except Exception as e:
    out.append(f"requests默认失败: {type(e).__name__}: {str(e)[:200]}")

print("\n".join(out))
