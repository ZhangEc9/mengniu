# -*- coding: utf-8 -*-
"""数值验证 flatten_corridor 变换与目标排实体分布。"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rail_first_experiment as R

stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
image = R.read_image(HERE / "images" / f"{stem}.jpg")
edges = R.canny_edges(image)
entities = R.detect_entities(image)
rows = R.group_rows(entities, image.shape[1], edges)

lines = []
target = None
for i, row in enumerate(rows):
    row_mid = (row["x0"] + row["x1"]) / 2
    y_mid = row["a"] + row["b"] * row_mid
    if abs(y_mid - 130) < 30 and (row["x1"] - row["x0"]) > 1200:
        target = row
        lines.append(f"命中排#{i}: a={row['a']:.2f} b={row['b']:.5f} x[{row['x0']:.0f},{row['x1']:.0f}] "
                     f"n={row['n']} ang={row['angle']:.3f}")
        ys = sorted((round(m['bbox'][1]), round(m['bbox'][3])) for m in row['members'])
        lines.append(f"  实体 top/bottom 前10: {ys[:10]}")
        hs = [m['h'] for m in row['members']]
        lines.append(f"  h: p50={np.median(hs):.0f} p90={np.percentile(hs, 90):.0f} min={min(hs)} max={max(hs)}")
        break

assert target is not None
rail = R.build_corridor(target, image.shape[1], image.shape[0], edges)
top, bottom = rail["top_line"], rail["bottom_line"]
cx = (rail["x_range"][0] + rail["x_range"][1]) / 2
center_a = (top["a"] + bottom["a"]) / 2
lines.append(f"rail: center_a={center_a:.2f} b={top['b']:.5f} cy@cx={center_a + top['b'] * cx:.2f} "
             f"cx={cx:.0f} inner_gap={rail['inner_gap']:.1f} wide_gap={rail['gap']:.1f}")
# 变换采样：crop 上几点对应原图坐标
crop, M_inv, ch = R.flatten_corridor(image, rail)
for (u, v) in [(0, ch/2), (crop.shape[1]/2, ch/2), (crop.shape[1]-1, ch/2), (0, 0), (crop.shape[1]-1, ch-1)]:
    p = np.array([u, v, 1.0]) @ M_inv.T
    lines.append(f"  crop({u:.0f},{v:.0f}) -> orig({p[0]:.0f},{p[1]:.0f})")

text = "\n".join(lines)
print(text)
(HERE / "导轨优先几何实验" / "debug_transform.txt").write_text(text, encoding="utf-8")
