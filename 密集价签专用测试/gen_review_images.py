# -*- coding: utf-8 -*-
"""生成人工复核图：每条人工导轨一张局部图。
蓝=人工标注（粗框=导轨，细框=价签），绿=API接受的导轨走廊，橙=API返回但被拒的框。
输出：API端点导轨实验/人工复核/<stem>/rail<k>.jpg
"""
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rail_first_experiment as R

EXP = HERE / "API端点导轨实验"
OUT = EXP / "人工复核"

for stem in sorted(os.listdir(EXP)):
    rj = EXP / stem / "rails.json"
    if not os.path.isfile(rj):
        continue
    d = json.loads(rj.read_text(encoding="utf-8"))
    img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                     if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
    image = R.read_image(img_path)
    user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")
    out_dir = OUT / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 重建走廊（确定性，无需调 API）
    entities = R.detect_entities(image)
    H, W = image.shape[:2]
    import math
    import api_rail_experiment as A
    rail_lines = R.detect_rail_lines(image)
    corridors = []
    for c in d["corridors"]:
        box_px = A.bbox_1000_to_pixels(c["api_box_1000"], W, H)
        rail = A.refine_corridor_from_railbox(box_px, entities, W, H, rail_lines)
        if rail is not None:
            corridors.append(rail)

    for k, ur in enumerate(user_rails):
        pts = np.asarray(ur["poly"])
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        mx, my = 60, 60
        cx0, cy0 = int(max(0, x0 - mx)), int(max(0, y0 - my))
        cx1, cy1 = int(min(W, x1 + mx)), int(min(H, y1 + my))
        crop = image[cy0:cy1, cx0:cx1].copy()
        # 用户价签（细蓝）
        for t in user_tags:
            b = [int(round(v)) for v in t["bbox"]]
            if b[2] < cx0 or b[0] > cx1 or b[3] < cy0 or b[1] > cy1:
                continue
            cv2.rectangle(crop, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (255, 128, 0), 1)
        # 用户导轨（粗蓝）
        cv2.polylines(crop, [(pts - [cx0, cy0]).astype(np.int32)], True, (255, 0, 0), 2)
        # API 走廊（绿）
        for rail in corridors:
            p = (rail["poly"] - [cx0, cy0]).astype(np.int32)
            cv2.polylines(crop, [p[:2]], False, (0, 220, 0), 2)
            cv2.polylines(crop, [p[2:]], False, (0, 220, 0), 2)
        R.save_image(str(out_dir / f"rail{k}.jpg"), crop)
    print(stem[:8], "rails:", len(user_rails), "->", out_dir)
