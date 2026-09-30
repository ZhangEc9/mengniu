# -*- coding: utf-8 -*-
"""汇总新数据人工标注：每图尺寸、价签框数、导轨框数与几何信息。"""
import json
import math
import os

DIR = r"D:\Shixi\mengniu\密集价签专用测试\新数据"

for fn in sorted(os.listdir(DIR)):
    if not fn.endswith(".json"):
        continue
    with open(os.path.join(DIR, fn), encoding="utf-8") as f:
        d = json.load(f)
    tags, rails, others = [], [], []
    for s in d["shapes"]:
        if s.get("label") == "框" or s.get("shape_type") == "rotation":
            rails.append(s)
        elif s.get("shape_type") == "rectangle":
            tags.append(s)
        else:
            others.append(s)
    print("=" * 70)
    print(fn, f'{d["imageWidth"]}x{d["imageHeight"]}', f'tags={len(tags)} rails={len(rails)} others={len(others)}')
    for i, r in enumerate(rails):
        pts = r["points"]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if len(pts) == 4:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        else:
            ang = 0.0
        print(f'  rail{i}: bbox=({min(xs):.0f},{min(ys):.0f})-({max(xs):.0f},{max(ys):.0f}) w={w:.0f} h={h:.0f} top_edge_angle={ang:.2f}deg direction={r.get("direction")}')
    tt = {}
    noflag = 0
    for t in tags:
        f_ = t.get("flags") or {}
        k = f_.get("tag_type", "<missing>")
        tt[k] = tt.get(k, 0) + 1
        if not f_:
            noflag += 1
    print("  tag_type:", tt, f"无flags={noflag}")
    rows = []
    for t in tags:
        ys_ = [p[1] for p in t["points"]]
        c = sum(ys_) / len(ys_)
        placed = False
        for row in rows:
            if abs(row[0] - c) < 40:
                row[0] = (row[0] * row[1] + c) / (row[1] + 1)
                row[1] += 1
                placed = True
                break
        if not placed:
            rows.append([c, 1])
    rows.sort()
    print("  每排价签数(粗略y聚类):", [int(r[1]) for r in rows])
