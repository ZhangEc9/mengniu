# -*- coding: utf-8 -*-
"""深挖：新旧裁块差异。保存新紧走廊裁块+掩膜、旧裁块+掩膜，并打印原始候选框。"""
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import rail_first_experiment as R
import top_rail_slot_experiment as T

stem = "45727462_6_first_normal_1782463362055_B04C0D8E"
image = R.read_image(HERE / "images" / f"{stem}.jpg")
out_dir = HERE / "导轨优先几何实验" / "debug_crops"
out_dir.mkdir(exist_ok=True)

# 旧方法
old_crop = image[93:160]
gray_old = cv2.cvtColor(old_crop, cv2.COLOR_BGR2GRAY)
mask_old = cv2.adaptiveThreshold(gray_old, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 21, 5)
mask_old = cv2.morphologyEx(mask_old, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
R.save_image(out_dir / "old_crop.png", old_crop)
R.save_image(out_dir / "old_mask.png", mask_old)
old_boxes, _ = T.contour_boxes(image, 93, 160, 21, 5)

# 新方法：取与顶排对应的段
edges = R.canny_edges(image)
entities = R.detect_entities(image)
rows = R.group_rows(entities, image.shape[1], edges)
target = None
for row in rows:
    row_mid = (row["x0"] + row["x1"]) / 2
    y_mid = row["a"] + row["b"] * row_mid
    if abs(y_mid - 130) < 30 and (row["x1"] - row["x0"]) > 1200:
        target = row
        break
assert target is not None, "没找到顶排"
rail = R.build_corridor(target, image.shape[1], image.shape[0], edges)
print(f"段: x[{rail['x_range'][0]:.0f},{rail['x_range'][1]:.0f}] ang={rail['angle_deg']:.3f} "
      f"inner_gap={rail['inner_gap']:.1f} n_ent={rail['n_entities']}")
crop, M_inv, ch = R.flatten_corridor(image, rail)
R.save_image(out_dir / "new_crop.png", crop)
gray_new = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
mask_new = cv2.adaptiveThreshold(gray_new, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 21, 5)
mask_new = cv2.morphologyEx(mask_new, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
R.save_image(out_dir / "new_mask.png", mask_new)

boxes_new, _ = R.contour_boxes_pass(crop, 21, 5, ch)
print(f"新主通道原始候选 {len(boxes_new)}: ")
for b in sorted(boxes_new)[:25]:
    print(f"  [{b[0]},{b[1]},{b[2]},{b[3]}] h={b[3]-b[1]} w={b[2]-b[0]}")
print(f"旧主通道候选 {len(old_boxes)}:")
for b in sorted(old_boxes)[:25]:
    print(f"  [{b[0]},{b[1]},{b[2]},{b[3]}] h={b[3]-b[1]} w={b[2]-b[0]}")
