# -*- coding: utf-8 -*-
import requests

for url in ("https://aism.mengniu.cn/", "https://aismapi.mengniu.cn/"):
    try:
        r = requests.get(url, timeout=8)
        print(url, "->", r.status_code)
    except Exception as e:
        print(url, "-> FAIL", type(e).__name__)
