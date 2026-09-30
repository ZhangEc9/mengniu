# -*- coding: utf-8 -*-
"""对比 API 导轨框像素高度与用户轨高、实体 p90 高。"""
import json
import os

OUT = r"D:\Shixi\mengniu\密集价签专用测试\API端点导轨实验"
lines = []
sizes = {"45035165": (2000, 844), "45524464": (1920, 864), "45544469": (2000, 1500),
         "45566458": (2000, 1500), "45727462": (2000, 1500), "45789553": (2000, 1125)}
for stem in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, stem, "rails.json")
    if not os.path.isfile(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    W, H = sizes[stem[:8]]
    lines.append(f"== {stem[:8]} ({W}x{H})")
    for c in d["corridors"]:
        b = c["api_box_1000"]
        h_px = (b[3] - b[1]) / 1000.0 * H
        w_px = (b[2] - b[0]) / 1000.0 * W
        lines.append(f"  rail{c['rail_id']}: API框 h={h_px:.0f}px w={w_px:.0f}px "
                     f"-> 重建走廊 gap={c['inner_gap']:.0f}")
text = "\n".join(lines)
open(r"D:\Shixi\mengniu\密集价签专用测试\API端点导轨实验\diag_boxheight.txt", "w", encoding="utf-8").write(text)
print("ok")
