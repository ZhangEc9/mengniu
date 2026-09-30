# -*- coding: utf-8 -*-
"""检查未填tag_type的价签label、开发图新旧GT差异。"""
import json
import os

NEW = r"D:\Shixi\mengniu\密集价签专用测试\新数据"
OLD = r"D:\Shixi\mengniu\密集价签专用测试\ground_truth"

print("== 未填 tag_type 的框 label ==")
for fn in sorted(os.listdir(NEW)):
    if not fn.endswith(".json"):
        continue
    d = json.load(open(os.path.join(NEW, fn), encoding="utf-8"))
    miss = [s["label"] for s in d["shapes"]
            if s.get("shape_type") == "rectangle" and not (s.get("flags") or {}).get("tag_type")]
    print(fn[:8], miss)

print("\n== 旧 ground_truth 目录 ==")
for fn in sorted(os.listdir(OLD)):
    print(" ", fn)
