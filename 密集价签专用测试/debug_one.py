# -*- coding: utf-8 -*-
"""调试：45566458 自动导轨与人工导轨的逐项几何对照。"""
import json
import math
import sys
from pathlib import Path

HERE = Path(r"D:\Shixi\mengniu\密集价签专用测试")
sys.path.insert(0, str(HERE))

import rail_first_experiment as R

stem = "45566458_6_first_normal_1782006172632_15944804"
image = R.read_image(HERE / "新数据" / f"{stem}.jpeg")
segments, edges = R.detect_line_segments(image)
print(f"segments={len(segments)}")
lines = R.merge_segments(segments)
print(f"lines={len(lines)}")
pairs = R.pair_rail_lines(lines)
auto_rails = [r for r in (R.rail_record(p, image.shape[1], image.shape[0]) for p in pairs) if r]
user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")

out = ["", "=== 用户导轨 ==="]
for ui, ur in enumerate(user_rails):
    e = R.user_rail_edges(ur["poly"])
    out.append(f"U{ui}: top=({e['top'][0][0]:.0f},{e['top'][0][1]:.0f})->({e['top'][1][0]:.0f},{e['top'][1][1]:.0f}) "
               f"bot=({e['bottom'][0][0]:.0f},{e['bottom'][0][1]:.0f})->({e['bottom'][1][0]:.0f},{e['bottom'][1][1]:.0f})")
    inside = [t["bbox"] for t in user_tags
              if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                  (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
    out.append(f"     tags={len(inside)} first={[round(v) for v in inside[0]] if inside else None}")
    mid_x = (e["x0"] + e["x1"]) / 2
    u_center = ((e["top"][0][1] + e["top"][1][1]) + (e["bottom"][0][1] + e["bottom"][1][1])) / 4
    for ai, ar in enumerate(auto_rails):
        a_center = ((ar["top_line"]["a"] + ar["top_line"]["b"] * mid_x)
                    + (ar["bottom_line"]["a"] + ar["bottom_line"]["b"] * mid_x)) / 2
        cont = (sum(1 for b in inside if R.corridor_contains(ar, b, R.TRUNCATION_MARGIN)) / len(inside)) if inside else 0
        out.append(f"     vs A{ai}: center_diff={a_center - u_center:+7.1f} cont={cont:.2f} "
                   f"topY@mid={ar['top_line']['a'] + ar['top_line']['b'] * mid_x:7.1f} "
                   f"botY@mid={ar['bottom_line']['a'] + ar['bottom_line']['b'] * mid_x:7.1f}")
(HERE / "导轨优先几何实验" / "debug_45566458.txt").write_text("\n".join(out), encoding="utf-8")
print("debug written")
