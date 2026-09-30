# -*- coding: utf-8 -*-
"""对照：已验证 detect_slots(93,160) vs 新阶段B框 vs 人工价签框（开发图顶排）。"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import rail_first_experiment as R
import top_rail_slot_experiment as T

stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
image = R.read_image(HERE / "images" / f"{stem}.jpg")

# 旧验证方法（人工 rail 93-160 + augment）
old_boxes, _ = T.detect_slots(image, 93, 160, True)

# 新阶段 B
edges = R.canny_edges(image)
entities = R.detect_entities(image)
rows = R.group_rows(entities, image.shape[1], edges)
user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")
u0 = R.user_rail_edges(user_rails[0]["poly"])
u0_tags = [t["bbox"] for t in user_tags
           if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                               (t["bbox"][1] + t["bbox"][3]) / 2), user_rails[0]["poly"])]

out = [f"旧验证框 {len(old_boxes)} 个; 人工顶排价签 {len(u0_tags)} 个",
       "人工价签 x0,y0,x1,y1 / 高:"]
for b in sorted(u0_tags):
    out.append(f"  GT [{b[0]:6.1f},{b[1]:6.1f},{b[2]:6.1f},{b[3]:6.1f}] h={b[3]-b[1]:.0f} w={b[2]-b[0]:.0f}")
out.append("旧验证框 x0,y0,x1,y1:")
for b in old_boxes:
    out.append(f"  OLD [{b[0]},{b[1]},{b[2]},{b[3]}] h={b[3]-b[1]} w={b[2]-b[0]}")

# 新排中选与人工 rail0 重叠的一段，走阶段 B
for ri, row in enumerate(rows):
    row_mid = (row["x0"] + row["x1"]) / 2
    row_y = row["a"] + row["b"] * row_mid
    uy = (R.y_on_edge(u0["top"], row_mid) + R.y_on_edge(u0["bottom"], row_mid)) / 2
    ov = min(row["x1"], u0["x1"]) - max(row["x0"], u0["x0"])
    if abs(row_y - uy) < 80 and ov > 0.5 * (row["x1"] - row["x0"]):
        rail = R.build_corridor(row, image.shape[1], image.shape[0], edges)
        if rail is None:
            continue
        crop, M_inv, ch = R.flatten_corridor(image, rail)
        boxes_local, touches, n_main = R.detect_tags_in_corridor(crop, ch)
        quads = [R.map_box_back(b, M_inv) for b in boxes_local]
        new_boxes = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads]
        out.append(f"新排R{ri}: x[{rail['x_range'][0]:.0f},{rail['x_range'][1]:.0f}] "
                   f"inner_gap={rail['inner_gap']:.1f} 框 {len(new_boxes)} 个 (触边 {touches})")
        for b in new_boxes:
            out.append(f"  NEW [{b[0]:6.1f},{b[1]:6.1f},{b[2]:6.1f},{b[3]:6.1f}] "
                       f"h={b[3]-b[1]:.0f} w={b[2]-b[0]:.0f}")
        m05 = R.match_greedy(new_boxes, u0_tags, 0.5)
        m03 = R.match_greedy(new_boxes, u0_tags, 0.3)
        out.append(f"  vs GT: m03={len(m03)} m05={len(m05)}")
        # 旧框 vs GT 对照
        mo05 = R.match_greedy(old_boxes, u0_tags, 0.5)
        out.append(f"旧框 vs GT: m05={len(mo05)}")

(HERE / "导轨优先几何实验" / "debug_stageB.txt").write_text("\n".join(out), encoding="utf-8")
print("written")
