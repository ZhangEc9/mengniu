# -*- coding: utf-8 -*-
"""阶段B画框验证：5 张图（除 45035165）的 API 导轨走廊内复用现有找框逻辑。
离线复用 rails.json 里已接受的走廊，不调 API。输出逐轨指标 + 带候选框复核图。"""
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rail_first_experiment as R
import api_rail_experiment as A

PASSES = [(21, 5), (31, 1), (41, 3)]


def projection_fill(crop, ch, contour_boxes):
    """投影切分补洞：轮廓检测漏掉的价签在逐列前景投影上是独立的峰。
    只返回与现有轮廓框横向重叠 <30% 的新框（只补洞，不覆盖，precision 不受损）。"""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = np.zeros(crop.shape[:2], np.uint8)
    for block, const in PASSES:
        m = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, block, const)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        mask |= m
    H, W = mask.shape[:2]
    # 行带收紧到价签实际所在区域：价签占裁块中央 ±0.35ch（裁块高 1.7ch）
    r0, r1 = int(0.32 * H), int(0.72 * H)
    # 投影只用主通道掩膜：(31,1)/(41,3) 在薄条带上是大块噪声源
    pm = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, PASSES[0][0], PASSES[0][1])
    pm = cv2.morphologyEx(pm, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    prof = pm[r0:r1].sum(axis=0) / 255.0
    k = max(3, int(0.02 * W)) | 1
    prof_s = np.convolve(prof, np.ones(k) / k, mode="same")

    h_est = 0.62 * ch
    thr = max(2.0, 0.10 * float(prof_s.max()))
    on = prof_s > thr
    segs, in_seg = [], False
    for x in range(W):
        if on[x] and not in_seg:
            start, in_seg = x, True
        elif not on[x] and in_seg:
            segs.append([start, x])
            in_seg = False
    if in_seg:
        segs.append([start, W])
    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < max(3, 0.12 * h_est):
            merged[-1][1] = s[1]
        else:
            merged.append(s)

    widths = [b - a for a, b in merged if b - a >= 0.6 * h_est]
    med_w = float(np.median(widths)) if widths else 2.0 * h_est

    def row_extent(a, b):
        col = mask[r0:r1, a:b]
        rows = np.where(col.sum(axis=1) >= 2)[0]
        if len(rows) == 0:
            return None
        top, bot = int(rows[0]) + r0, int(rows[-1]) + r0
        return [a, top, b, bot] if bot - top >= 0.30 * ch else None

    out = []
    dbg = {"segs": len(merged), "narrow": 0, "covered": 0, "wide_split": 0, "no_rows": 0, "added": 0}
    for a, b in merged:
        w = b - a
        if w < 0.6 * h_est:
            dbg["narrow"] += 1
            continue
        # 与轮廓框的重叠检查：段中心已被轮廓框覆盖 → 跳过
        cxa = (a + b) / 2
        if any(c[0] <= cxa <= c[2] for c in contour_boxes):
            dbg["covered"] += 1
            continue
        if w > 2.4 * med_w:
            dbg["wide_split"] += 1
            n = max(2, round(w / med_w))
            bounds = np.linspace(a, b, n + 1).astype(int)
            for i in range(n):
                box = row_extent(int(bounds[i]), int(bounds[i + 1]))
                if box:
                    out.append(box)
                    dbg["added"] += 1
                else:
                    dbg["no_rows"] += 1
        else:
            box = row_extent(a, b)
            if box:
                out.append(box)
                dbg["added"] += 1
            else:
                dbg["no_rows"] += 1
    if os.environ.get("PROJ_DEBUG"):
        dbg["thr"] = round(thr, 1)
        dbg["h_est"] = round(h_est, 1)
        dbg["med_w"] = round(med_w, 1)
        print("projection_fill:", dbg)
    return out

EXP = HERE / "API端点导轨实验"
OUT_REVIEW = EXP / "阶段B画框复核"
EXCLUDE = "45035165"


def rail_from_poly(poly, inner_gap):
    """rails.json 的走廊多边形 → 阶段B所需的 rail dict。"""
    p = np.asarray(poly, dtype=np.float64)
    (x0, top0), (x1, top1) = p[0], p[1]
    b_top = (top1 - top0) / max(1e-6, x1 - x0)
    a_top = top0 - b_top * x0
    bot0, bot1 = p[3][1], p[2][1]
    b_bot = (bot1 - bot0) / max(1e-6, x1 - x0)
    a_bot = bot0 - b_bot * x0
    return {"poly": p, "angle_deg": R.normalize_angle_deg(
                math.degrees(math.atan((b_top + b_bot) / 2))),
            "x_range": [float(x0), float(x1)],
            "gap": float(bot0 - top0 + (bot1 - top1)) / 2 * 1.0,
            "inner_gap": float(inner_gap),
            "sources": ["detected", "detected"],
            "top_line": {"a": a_top, "b": b_top},
            "bottom_line": {"a": a_bot, "b": b_bot}}


import math  # noqa: E402  (rail_from_poly 需要)

stems = [s for s in sorted(p.stem for p in (HERE / "新数据").glob("*.json"))
         if not s.startswith(EXCLUDE)]
summary = []
for stem in stems:
    rj = EXP / stem / "rails.json"
    if not rj.exists():
        continue
    d = json.loads(rj.read_text(encoding="utf-8"))
    img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                     if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
    image = R.read_image(img_path)
    H, W = image.shape[:2]
    user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")
    entities = R.detect_entities(image)
    rail_lines = R.detect_rail_lines(image)
    tilt = A.image_tilt(rail_lines)
    corridors = []
    for c in d["corridors"]:
        box_px = A.bbox_1000_to_pixels(c["api_box_1000"], W, H)
        rail = A.refine_corridor_from_railbox(box_px, entities, W, H, rail_lines, tilt)
        if rail is not None:
            corridors.append(rail)

    rail_boxes = {}
    proj_added = 0
    for ri, rail in enumerate(corridors):
        crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
        boxes_local, touches, n_main = R.detect_tags_in_corridor(crop, ch)
        extra = projection_fill(crop, ch, boxes_local)
        proj_added += len(extra)
        boxes_local = boxes_local + extra
        quads = [R.map_box_back(b, M_inv) for b in boxes_local]
        rail_boxes[ri] = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()]
                          for q in quads]

    # 逐轨评测（人工价签 = 中心落在人工导轨多边形内）
    out_dir = OUT_REVIEW / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    rail_rows = []
    for k, ur in enumerate(user_rails):
        pts = np.asarray(ur["poly"])
        inside = [t["bbox"] for t in user_tags
                  if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                      (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
        # 就近走廊：中心线 y 距人工带中心 < 0.8×带高 且 x 重叠
        e = R.user_rail_edges(ur["poly"])
        mid_x = (e["x0"] + e["x1"]) / 2
        uy = (R.y_on_edge(e["top"], mid_x) + R.y_on_edge(e["bottom"], mid_x)) / 2
        best_ri, best_gap = None, 1e9
        for ri, rail in enumerate(corridors):
            ry = (rail["top_line"]["a"] + rail["bottom_line"]["a"]) / 2 \
                + rail["top_line"]["b"] * mid_x
            ov = min(rail["x_range"][1], e["x1"]) - max(rail["x_range"][0], e["x0"])
            if ov < 0.3 * (e["x1"] - e["x0"]):
                continue
            dist = abs(ry - uy)
            if dist < best_gap:
                best_ri, best_gap = ri, dist
        preds = rail_boxes.get(best_ri, []) if best_ri is not None else []
        row = {"rail": k, "corridor": best_ri,
               "center_dist": round(best_gap, 1) if best_ri is not None else None,
               "user_tags": len(inside), "preds": len(preds)}
        for thr in (0.3, 0.5, 0.7):
            row[f"m{int(thr*100)}"] = len(R.match_greedy(preds, inside, thr))
        rail_rows.append(row)

        # 复核图
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        cx0, cy0 = int(max(0, x0 - 60)), int(max(0, y0 - 60))
        cx1, cy1 = int(min(W, x1 + 60)), int(min(H, y1 + 60))
        crop = image[cy0:cy1, cx0:cx1].copy()
        for t in user_tags:
            b = [int(round(v)) for v in t["bbox"]]
            cv2.rectangle(crop, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (255, 128, 0), 1)
        cv2.polylines(crop, [(pts - [cx0, cy0]).astype(np.int32)], True, (255, 0, 0), 2)
        if best_ri is not None:
            p = (corridors[best_ri]["poly"] - [cx0, cy0]).astype(np.int32)
            cv2.polylines(crop, [p[:2]], False, (0, 220, 0), 1)
            cv2.polylines(crop, [p[2:]], False, (0, 220, 0), 1)
        for b in preds:
            b = [int(round(v)) for v in b]
            cv2.rectangle(crop, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (0, 255, 0), 2)
        R.save_image(str(out_dir / f"rail{k}.jpg"), crop)

    all_preds = [b for boxes in rail_boxes.values() for b in boxes]
    all_gts = [t["bbox"] for t in user_tags]
    g = {f"m{int(t*100)}": len(R.match_greedy(all_preds, all_gts, t)) for t in (0.3, 0.5, 0.7)}
    summary.append({"image": stem[:8], "rails": len(user_rails),
                    "corridors": len(corridors),
                    "rail_rows": rail_rows,
                    "global": {"preds": len(all_preds), "gts": len(all_gts), **g}})

(OUT_REVIEW / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8")

lines = []
tot = {"tags": 0, "preds": 0, "m30": 0, "m50": 0, "m70": 0}
for s in summary:
    lines.append(f"== {s['image']} 走廊{s['corridors']}")
    for r in s["rail_rows"]:
        lines.append(f"  rail{r['rail']}(走廊{r['corridor']}, y距{r['center_dist']}): "
                     f"tags={r['user_tags']} preds={r['preds']} "
                     f"m30={r['m30']} m50={r['m50']} m70={r['m70']}")
        tot["tags"] += r["user_tags"]
    for k in ("preds", "m30", "m50", "m70"):
        tot[k] += s["global"][k]
    g = s["global"]
    lines.append(f"  全图: preds={g['preds']} gts={g['gts']} m30={g['m30']} m50={g['m50']} m70={g['m70']}")
lines.append(f"投影补洞新增框: {proj_added}")
lines.append(f"合计: 轨内tags={tot['tags']} preds={tot['preds']} "
             f"IoU0.3={tot['m30']} IoU0.5={tot['m50']} IoU0.7={tot['m70']}")
(OUT_REVIEW / "report.txt").write_text("\n".join(lines), encoding="utf-8")
print("done ->", OUT_REVIEW)
