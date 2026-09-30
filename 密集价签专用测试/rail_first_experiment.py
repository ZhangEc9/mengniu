# -*- coding: utf-8 -*-
"""导轨优先离线几何实验（文档下一步第 2、3 步）。

设计原则：走廊内找框完全复用 top_rail_slot_experiment.py 已验证的多阈值轮廓方法
（尺寸过滤改为相对走廊高度）；本实验只新增"自动找走廊"这一块。

阶段 A（新增，泛化目标）：全图多阈值自适应阈值找"价签样实体"（宽尺寸范围、高召回），
        按 y 中心与方向相干性聚成排（排内实体数、横向覆盖即文档的密集判据），
        由排内实体中心拟合方向角与走廊上下界；走廊边再用 Canny 梯度支持标注
        detected/inferred。
阶段 B（复用已验证方法）：走廊仿射拉平，多阈值轮廓找框，相对尺寸过滤。
评测：人工标注（LabelMe：rectangle=价签，rotation"框"=导轨）只在此阶段加载，
      标注不参与候选生成与参数选择。参数对全部 6 张图冻结同参运行。

经验依据：实测人工导轨实体边（低对比深色导轨）常无强线支持，而价签排自身
上下沿是最强线（45566458 调试：导轨边 y=362/493 无强线，价签沿 support 3106/2133），
故走廊以"实体排"为基准而非线段配对。单图结果不证明泛化。
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "新数据"
OUT_DIR = HERE / "导轨优先几何实验"

# ---- 冻结参数（全部相对图像/排尺度，6 图同参）----
CANNY = (60, 160)
ENTITY_PASSES = [(21, 5), (31, 1), (41, 3)]   # 与已验证找框同一组阈值通道
ENTITY_H_REL = (0.015, 0.10)                  # 实体高 / 图高（宽网只要召回）
ENTITY_W_OVER_H = (0.7, 6.0)                  # 实体宽高比
ENTITY_MIN_W_PX = 18.0
ROW_MIN_ENTITIES = 5                          # 排内最少实体数（文档口径约 6，取 5 容忍漏检）
ROW_Y_TOL_REL = 0.5                           # 排聚类容差 = 0.5×实体中位高
ROW_RESIDUAL_REL = 0.5                        # 排内中心拟合残差上限（同上）
ROW_X_SPAN_REL = 0.25                         # 排横向覆盖 ≥ 25% 图宽
ROW_ANGLE_MAX_DEG = 12.0
ROW_MERGE_CENTER_REL = 0.8                    # 排合并：中心距 = 0.8×中位高
ROW_MERGE_ANGLE_DEG = 2.5                     # 排合并：角度差上限
TILT_VOTE_RANGE_DEG = 10.0                    # 全局倾角投票范围/步长
TILT_VOTE_STEP_DEG = 0.5
TAG_W_MAX_REL_GAP = 1.6                       # 阶段 B：单框宽 ≤ 1.6×走廊高（滤合框）
CORRIDOR_EXPAND_FACTOR = 1.25                 # 触边外扩系数（只扩一次）
CORRIDOR_EXPAND_TOUCH_FRAC = 0.3              # 触边框占比超过则外扩重检
CORRIDOR_MARGIN_REL = 0.50                    # 宽走廊（评测/叠图）：每侧 0.5×实体中位高
INNER_MARGIN_REL = 0.15                       # 紧走廊（阶段 B 裁块）：重现已验证贴签几何
EDGE_SUPPORT_MIN = 0.5                        # 走廊边 Canny 支持率≥0.5 记 detected
EDGE_SUPPORT_WINDOW = 3                       # 采样 ±3px
EDGE_SAMPLES = 60
DENSE_BAND_MIN = 0.05                         # 走廊带内边缘密度下限（阶段 B 门控）
TAG_H_REL = (0.40, 1.00)                      # 阶段 B：价签高 / 走廊高（贴边价签允许触边）
TAG_W_OVER_H = (1.0, 3.0)                     # 阶段 B：价签宽 / 高（实测 1.3-2.6）
ROW_BOT_EDGE_SUP_MIN = 0.45                   # 排门槛：底边 Canny 支持率（真排 p10≈0.43）

# ---- 导轨证据门槛：只在有实体导轨线的排里找框（用户口径"只在导轨区域找价签"）----
HOUGH = dict(rho=1, theta=np.pi / 180, threshold=70, minLineLength=90, maxLineGap=18)
LINE_MAX_ANGLE_DEG = 12.0
LINE_MERGE_ANGLE_DEG = 1.5
LINE_MERGE_PERP_PX = 6.0
RAIL_LINE_SPAN_RATIO = 0.60                   # 线的横向覆盖 ≥ 排宽的 60%
RAIL_LINE_MIN_SUPPORT = 600.0                 # 线的最小累计长度
RAIL_BELOW_WINDOW = (-8.0, 55.0)              # 线相对排底边中位的距离窗口
RAIL_BOTTOM_RESID_MAX = 12.0                  # 排内各实体底边到线的残差 std 上限
AUGMENT_XOVERLAP = 0.2                        # 补充通道与已有框最小横向重叠（去重，同已验证方法）
TRUNCATION_MARGIN = 6.0
ROW_CENTER_TOL = 80.0                         # 自动/人工走廊带中心允许偏差
RAIL_MATCH_CONTAINMENT = 0.8                  # 匹配判据：人工价签被走廊完整包含比例
CROP_PAD_X = 12                               # 拉平裁块左右留白


# ---------- 基础 IO ----------

def read_image(path):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图像：{path}")
    return image


def save_image(path, image):
    ok, encoded = cv2.imencode(Path(path).suffix or ".jpg", image)
    if not ok:
        raise RuntimeError(f"无法编码图像：{path}")
    encoded.tofile(str(path))


def load_annotations(json_path):
    """LabelMe JSON -> (tags, rails)。tags: bbox+label+tag_type；rails: 4 点多边形。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    tags, rails = [], []
    for shape in data["shapes"]:
        pts = np.asarray(shape["points"], dtype=np.float64)
        if shape.get("label") == "框" or shape.get("shape_type") == "rotation":
            rails.append({"poly": pts, "direction": shape.get("direction")})
        elif shape.get("shape_type") == "rectangle":
            flags = shape.get("flags") or {}
            tags.append({
                "bbox": [pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()],
                "label": shape.get("label", ""),
                "tag_type": flags.get("tag_type", "<missing>"),
            })
    return tags, rails, (data["imageWidth"], data["imageHeight"])


def normalize_angle_deg(ang):
    while ang >= 90.0:
        ang -= 180.0
    while ang < -90.0:
        ang += 180.0
    return ang


def canny_edges(image):
    return cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), *CANNY)


# ---------- 导轨线证据（Hough 长线，排锚定单向查线）----------

def detect_rail_lines(image):
    """Canny+Hough 提取近水平长线段，按角度+垂直距离聚类成线（跨缺口回收）。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, *CANNY)
    raw = cv2.HoughLinesP(edges, **HOUGH)
    segments = []
    if raw is not None:
        for x0, y0, x1, y1 in np.asarray(raw).reshape(-1, 4):
            ang = normalize_angle_deg(math.degrees(math.atan2(y1 - y0, x1 - x0)))
            if abs(ang) > LINE_MAX_ANGLE_DEG:
                continue
            segments.append({"seg": (float(x0), float(y0), float(x1), float(y1)),
                             "angle": ang, "length": float(math.hypot(x1 - x0, y1 - y0))})

    def vdist(seed_seg, seg):
        x0, y0, x1, y1 = seed_seg
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1.0
        px, py = (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2
        return abs(dy * (px - x0) - dx * (py - y0)) / norm

    pool = sorted(segments, key=lambda m: -m["length"])
    lines = []
    while pool:
        seed = pool.pop(0)
        members = [m for m in pool
                   if abs(m["angle"] - seed["angle"]) <= LINE_MERGE_ANGLE_DEG
                   and vdist(seed["seg"], m["seg"]) <= LINE_MERGE_PERP_PX + 4]
        taken = {id(seed)} | {id(m) for m in members}
        pool = [m for m in pool if id(m) not in taken]
        xs, ys = [], []
        for m in [seed] + members:
            xs += [m["seg"][0], m["seg"][2]]
            ys += [m["seg"][1], m["seg"][3]]
        extra = []
        if len(xs) >= 2:
            b, a = np.polyfit(xs, ys, 1)
            fit_ang = normalize_angle_deg(math.degrees(math.atan(b)))
            for m in pool:
                if abs(m["angle"] - fit_ang) > LINE_MERGE_ANGLE_DEG:
                    continue
                mx, my = (m["seg"][0] + m["seg"][2]) / 2, (m["seg"][1] + m["seg"][3]) / 2
                if abs(a + b * mx - my) <= LINE_MERGE_PERP_PX:
                    extra.append(m)
            taken |= {id(m) for m in extra}
            pool = [m for m in pool if id(m) not in taken]
            for m in extra:
                xs += [m["seg"][0], m["seg"][2]]
                ys += [m["seg"][1], m["seg"][3]]
            b, a = np.polyfit(xs, ys, 1)
        lines.append({"a": float(a), "b": float(b),
                      "angle": normalize_angle_deg(math.degrees(math.atan(float(b)))),
                      "support": sum(m["length"] for m in [seed] + members + extra),
                      "x0": float(min(xs)), "x1": float(max(xs))})
    return lines


def rail_line_below(row, lines):
    """排底边下方是否有"单条、长、强、底边对齐"的导轨线。
    返回 (found, info)：info 含线参数与底边残差 std。"""
    members = row["members"]
    bot_med = float(np.median([m["bbox"][3] for m in members]))
    span = row["x1"] - row["x0"]
    best = None
    for ln in lines:
        lo = max(ln["x0"], row["x0"])
        hi = min(ln["x1"], row["x1"])
        if hi - lo < RAIL_LINE_SPAN_RATIO * span:
            continue
        mid = (lo + hi) / 2
        d = (ln["a"] + ln["b"] * mid) - bot_med
        if not (RAIL_BELOW_WINDOW[0] <= d <= RAIL_BELOW_WINDOW[1]):
            continue
        ds = [m["bbox"][3] - (ln["a"] + ln["b"] * m["cx"]) for m in members]
        resid = float(np.std(ds))
        score = (resid, -ln["support"])
        if best is None or score < best[0]:
            best = (score, ln, resid, d)
    if best is None or best[2] > RAIL_BOTTOM_RESID_MAX or best[1]["support"] < RAIL_LINE_MIN_SUPPORT:
        return False, None
    ln, resid, d = best[1], best[2], best[3]
    return True, {"line_a": round(ln["a"], 2), "line_b": round(ln["b"], 5),
                  "support": round(ln["support"]), "dist_med": round(d, 1),
                  "bottom_resid": round(resid, 1)}


# ---------- 阶段 A：实体检测 -> 排聚类 -> 走廊 ----------

def iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def dedup_iou(boxes, thr=0.5):
    boxes = sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    kept = []
    for box in boxes:
        if all(iou(box, other) < thr for other in kept):
            kept.append(box)
    return kept


def detect_entities(image):
    """全图多阈值轮廓找价签样实体。宽尺寸范围（相对图高），只求召回。"""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height = image.shape[0]
    h_lo, h_hi = ENTITY_H_REL[0] * height, ENTITY_H_REL[1] * height
    boxes = []
    for block, const in ENTITY_PASSES:
        if block >= gray.shape[1]:
            continue
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, block, const)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (h_lo <= h <= h_hi):
                continue
            if w < ENTITY_MIN_W_PX or not (ENTITY_W_OVER_H[0] * h <= w <= ENTITY_W_OVER_H[1] * h):
                continue
            boxes.append([x, y, x + w, y + h])
    boxes = dedup_iou(boxes)
    return [{"bbox": b, "w": b[2] - b[0], "h": b[3] - b[1],
             "cx": (b[0] + b[2]) / 2.0, "cy": (b[1] + b[3]) / 2.0}
            for b in sorted(boxes, key=lambda b: (b[1], b[0]))]


def fit_row(members, h_med):
    """排内实体中心最小二乘拟合方向线，剔除残差离群后重拟合一次。"""
    members = list(members)
    for _ in range(2):
        if len(members) < 2:
            return None
        cx = np.array([m["cx"] for m in members])
        cy = np.array([m["cy"] for m in members])
        b, a = np.polyfit(cx, cy, 1)
        resid = np.abs(cy - (a + b * cx))
        good = resid <= ROW_RESIDUAL_REL * h_med
        if good.sum() < 2:
            return None
        if good.all():
            return {"members": members, "a": float(a), "b": float(b),
                    "resid_max": float(resid.max()), "h_med": h_med}
        members = [m for m, g in zip(members, good) if g]
    return None


def cluster_by_y(entities, tol):
    clusters = []
    for e in sorted(entities, key=lambda e: e["cy"]):
        host = None
        for row in clusters:
            if abs(e["cy"] - row["mean_cy"]) <= tol and (
                    host is None or abs(e["cy"] - row["mean_cy"]) < abs(e["cy"] - host["mean_cy"])):
                host = row
        if host is None:
            clusters.append({"members": [e], "mean_cy": e["cy"]})
        else:
            host["members"].append(e)
            host["mean_cy"] = sum(m["cy"] for m in host["members"]) / len(host["members"])
    return clusters


def adjacent_gap_ratio(members):
    """排内相邻实体净间隔中位数 / 实体宽中位数（文档的密集判据统计，仅输出供标定）。"""
    ordered = sorted(members, key=lambda m: m["bbox"][0])
    gaps = []
    for cur, nxt in zip(ordered, ordered[1:]):
        gap = nxt["bbox"][0] - cur["bbox"][2]
        if 0 <= gap < 3 * cur["h"]:
            gaps.append(gap)
    if not gaps:
        return None
    w_med = float(np.median([m["w"] for m in members]))
    return round(float(np.median(gaps)) / max(1e-6, w_med), 2)


def rows_compatible(r1, r2, h_med):
    if abs(r1["angle"] - r2["angle"]) > ROW_MERGE_ANGLE_DEG:
        return False
    lo = max(r1["x0"], r2["x0"])
    hi = min(r1["x1"], r2["x1"])
    if hi >= lo:
        xm = (lo + hi) / 2.0
    else:
        xm = (max(r1["x0"], r2["x0"]) + min(r1["x1"], r2["x1"])) / 2.0
    d = abs((r1["a"] + r1["b"] * xm) - (r2["a"] + r2["b"] * xm))
    return d <= ROW_MERGE_CENTER_REL * h_med


def group_rows(entities, width, edges=None):
    """y 聚类 → 每段拟合（不设门槛）→ 链式合并相容段（解倾斜拆段）→ 统一过滤。"""
    if not entities:
        return []
    h_med = float(np.median([e["h"] for e in entities]))
    tol = ROW_Y_TOL_REL * h_med
    clusters = cluster_by_y(entities, tol)

    pieces = []
    for cluster in clusters:
        row = fit_row(cluster["members"], h_med)
        if row is None:
            continue
        members = row["members"]
        row.update({"angle": normalize_angle_deg(math.degrees(math.atan(row["b"]))),
                    "x0": min(m["bbox"][0] for m in members),
                    "x1": max(m["bbox"][2] for m in members),
                    "n": len(members)})
        pieces.append(row)

    # 单实体吸收：倾斜把排拆成小段时，只有 1 个实体的段会被 fit_row 丢弃、
    # 断开合并链；把落在各段延长线附近的散点并入，弥合断链后再合并。
    groups = [p["members"] for p in pieces]
    grouped_ids = {id(m) for g in groups for m in g}
    singles = [e for e in entities if id(e) not in grouped_ids]
    pitch = float(np.median([e["w"] for e in entities]))
    for group in groups:
        grew = True
        while grew:
            grew = False
            cx = np.array([m["cx"] for m in group])
            cy = np.array([m["cy"] for m in group])
            b_, a_ = np.polyfit(cx, cy, 1)
            x_lo = min(m["bbox"][0] for m in group) - 2 * pitch
            x_hi = max(m["bbox"][2] for m in group) + 2 * pitch
            for s in list(singles):
                if not (x_lo <= s["cx"] <= x_hi):
                    continue
                if abs(s["cy"] - (a_ + b_ * s["cx"])) > tol:
                    continue
                test = fit_row(group + [s], h_med)
                if test is None:
                    continue
                group[:] = test["members"]
                singles.remove(s)
                grew = True
                break

    pieces = []
    for group in groups:
        row = fit_row(group, h_med)
        if row is None:
            continue
        members = row["members"]
        row.update({"angle": normalize_angle_deg(math.degrees(math.atan(row["b"]))),
                    "x0": min(m["bbox"][0] for m in members),
                    "x1": max(m["bbox"][2] for m in members),
                    "n": len(members)})
        pieces.append(row)

    # 链式合并：同一条排被倾斜/聚类容差拆成数段时，按角度+拼接点位置相容逐步合并
    merged = True
    while merged and len(pieces) > 1:
        merged = False
        pieces.sort(key=lambda r: (r["x0"], r["a"]))
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                if not rows_compatible(pieces[i], pieces[j], h_med):
                    continue
                comb = fit_row(pieces[i]["members"] + pieces[j]["members"], h_med)
                if comb is None:
                    continue
                angle = normalize_angle_deg(math.degrees(math.atan(comb["b"])))
                if abs(angle) > ROW_ANGLE_MAX_DEG:
                    continue
                comb.update({"angle": angle,
                             "x0": min(pieces[i]["x0"], pieces[j]["x0"]),
                             "x1": max(pieces[i]["x1"], pieces[j]["x1"]),
                             "n": len(comb["members"])})
                pieces[i] = comb
                del pieces[j]
                merged = True
                break
            if merged:
                break

    rows = []
    for row in pieces:
        if abs(row["angle"]) > ROW_ANGLE_MAX_DEG:
            continue
        if row["n"] < ROW_MIN_ENTITIES:
            continue
        if (row["x1"] - row["x0"]) < ROW_X_SPAN_REL * width:
            continue
        row["gap_ratio"] = adjacent_gap_ratio(row["members"])
        if edges is not None:
            row["bot_edge_sup"] = bottom_edge_support(row, edges)
        rows.append(row)
    rows.sort(key=lambda r: r["a"])
    return rows


def line_edge_support(edges, a, b, x0, x1, width, height):
    xs = np.linspace(x0, x1, EDGE_SAMPLES)
    hits = 0
    for x in xs:
        xi = min(int(round(x)), width - 1)
        yi = int(round(a + b * x))
        if not (0 <= yi < height):
            continue
        y0 = max(0, yi - EDGE_SUPPORT_WINDOW)
        y1 = min(height, yi + EDGE_SUPPORT_WINDOW + 1)
        if edges[y0:y1, xi].max() > 0:
            hits += 1
    return hits / max(1, len(xs))


def bottom_edge_support(row, edges):
    """沿排实体底边线下方 0-12px 采样 Canny 命中率：价签挂同一导轨 -> 高。"""
    h_img, w_img = edges.shape
    members = row["members"]
    cxs = np.array([m["cx"] for m in members], float)
    bots = np.array([m["bbox"][3] for m in members], float)
    a_, b_ = row["a"], row["b"]
    bot_med = float(np.median(bots))
    xs = np.linspace(row["x0"], row["x1"], 40)
    hits = 0
    for x in xs:
        xi = min(int(round(x)), w_img - 1)
        yi = int(round(a_ + b_ * x + (bot_med - (a_ + b_ * float(np.median(cxs))))))
        yi0, yi1 = max(0, yi - 2), min(h_img, yi + 13)
        if yi0 < yi1 and edges[yi0:yi1, xi].max() > 0:
            hits += 1
    return hits / 40.0


def build_corridor(row, width, height, edges):
    """由排拟合线与实体高度分布构造走廊。宽走廊（含余量）用于评测与叠图；
    紧走廊（贴实体）用于阶段 B 拉平裁块，几何等价已验证的人工 rail_top/bottom。"""
    h_med = row["h_med"]
    a, b = row["a"], row["b"]
    p90_h = float(np.percentile([m["h"] for m in row["members"]], 90))
    half_wide = p90_h / 2.0 + CORRIDOR_MARGIN_REL * h_med
    half_tight = p90_h / 2.0 + INNER_MARGIN_REL * h_med
    x0 = max(0.0, float(row["x0"]))
    x1 = min(float(width), float(row["x1"]))
    if x1 - x0 < 80:
        return None
    center_a = a
    sup_top = line_edge_support(edges, a - half_wide, b, x0, x1, width, height)
    sup_bot = line_edge_support(edges, a + half_wide, b, x0, x1, width, height)
    sources = ["detected" if sup_top >= EDGE_SUPPORT_MIN else "inferred",
               "detected" if sup_bot >= EDGE_SUPPORT_MIN else "inferred"]
    xs = np.array([x0, x1])

    def band_poly(half):
        top_y = (center_a - half) + b * xs
        bot_y = (center_a + half) + b * xs
        return np.asarray([[x0, top_y[0]], [x1, top_y[1]], [x1, bot_y[1]], [x0, bot_y[0]]])

    return {"poly": band_poly(half_wide), "angle_deg": normalize_angle_deg(math.degrees(math.atan(b))),
            "x_range": [x0, x1], "gap": 2.0 * half_wide, "sources": sources,
            "score": float(row["n"]), "n_entities": row["n"],
            "gap_ratio": row.get("gap_ratio"),
            "bot_edge_sup": round(row.get("bot_edge_sup", -1.0), 2),
            "edge_support": [round(sup_top, 2), round(sup_bot, 2)],
            "resid_max_px": round(row["resid_max"], 1),
            "inner_half_h": half_tight, "inner_gap": 2.0 * half_tight,
            "top_line": {"a": center_a - half_wide, "b": b},
            "bottom_line": {"a": center_a + half_wide, "b": b}}


def expand_corridor(rail, factor):
    """紧走廊按系数外扩（阶段 B 触边重检用），宽走廊不变。"""
    rail["inner_half_h"] *= factor
    rail["inner_gap"] = 2.0 * rail["inner_half_h"]
    return rail


# ---------- 阶段 B：走廊内找框（复用已验证方法）----------

def flatten_corridor(image, rail, crop_margin_y=0.0):
    """把紧走廊（贴价签）拉平成横条，供阶段 B 检测。返回 (crop, M_inv, ch)。

    cv2.warpAffine 默认把 M 当"前向映射"(src->dst) 并内部求逆采样；
    getRotationMatrix2D 返回的正是前向映射。故：旋转后把走廊中心平移到
    crop 内的 (u_c, v_c) 即可，u_c = cx-(x0-CROP_PAD_X)，v_c = ch/2。
    此前把 M 当 dst->src 构造/平移，被二次求逆后采样错位。
    crop_margin_y：裁块上下各多留 ch×margin 的余量（尺寸过滤仍用 ch），
    防 API 紧框把价签上下沿裁掉。"""
    top, bottom = rail["top_line"], rail["bottom_line"]
    x0, x1 = rail["x_range"]
    angle = rail["angle_deg"]
    center_a = (top["a"] + bottom["a"]) / 2.0
    b = top["b"]
    ch = float(rail["inner_gap"])
    cx = (x0 + x1) / 2
    cy = center_a + b * cx
    w = int(round(x1 - x0)) + 2 * CROP_PAD_X
    h = int(round(ch * (1.0 + 2.0 * crop_margin_y)))
    u_c = cx - (x0 - CROP_PAD_X)
    v_c = h / 2.0
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    M[0, 2] += u_c - cx
    M[1, 2] += v_c - cy
    crop = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
    M_inv = cv2.invertAffineTransform(M)
    return crop, M_inv, ch


def map_box_back(box, M_inv):
    x0, y0, x1, y1 = box
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
    ones = np.append(corners, np.ones((4, 1)), axis=1)
    return ones @ M_inv.T


def contour_boxes_pass(crop, block_size, constant, ch):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block_size, constant)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h_min, h_max = TAG_H_REL[0] * ch, TAG_H_REL[1] * ch
    w_max = TAG_W_MAX_REL_GAP * ch
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (h_min <= h <= h_max):
            continue
        if not (TAG_W_OVER_H[0] * h <= w <= min(TAG_W_OVER_H[1] * h, w_max)):
            continue
        boxes.append([x, y, x + w, y + h])
    return boxes, mask


def detect_tags_in_corridor(crop, ch):
    """多阈值通道找框：主通道 + 补充通道（横向基本不重叠才收）。"""
    boxes, _ = contour_boxes_pass(crop, 21, 5, ch)
    main_boxes = list(boxes)
    for block_size, constant in ((31, 1), (41, 3)):
        extra, _ = contour_boxes_pass(crop, block_size, constant, ch)
        for cand in extra:
            w = cand[2] - cand[0]
            if all(max(0.0, min(cand[2], b[2]) - max(cand[0], b[0])) < AUGMENT_XOVERLAP * w
                   for b in main_boxes):
                boxes.append(cand)
    boxes = dedup_iou(boxes)
    touches = sum(1 for b in boxes if b[1] <= 2 or b[3] >= crop.shape[0] - 2)
    return boxes, touches, len(main_boxes)


# ---------- 评测 ----------

def match_greedy(preds, gts, thr):
    pairs = []
    for pi, p in enumerate(preds):
        for gi, g in enumerate(gts):
            v = iou(p, g)
            if v >= thr:
                pairs.append((v, pi, gi))
    pairs.sort(key=lambda t: -t[0])
    used_p, used_g, matches = set(), set(), []
    for v, pi, gi in pairs:
        if pi in used_p or gi in used_g:
            continue
        used_p.add(pi)
        used_g.add(gi)
        matches.append({"pred": pi, "gt": gi, "iou": v})
    return matches


def raster_iou(poly_a, poly_b, width, height):
    ma = np.zeros((height, width), np.uint8)
    mb = np.zeros((height, width), np.uint8)
    cv2.fillPoly(ma, [np.asarray(poly_a, dtype=np.int32)], 1)
    cv2.fillPoly(mb, [np.asarray(poly_b, dtype=np.int32)], 1)
    inter = np.logical_and(ma, mb).sum()
    union = np.logical_or(ma, mb).sum()
    return inter / union if union else 0.0


def user_rail_edges(poly):
    """人工导轨框上下边：枚举 4 点两两配对的三种分法，取两边都最水平的分法。"""
    pts = [(float(p[0]), float(p[1])) for p in poly]
    best = None
    for a, b in ((0, 1), (0, 2), (0, 3)):
        rest = [i for i in range(4) if i not in (a, b)]
        e1 = (pts[a], pts[b])
        e2 = (pts[rest[0]], pts[rest[1]])
        ang1 = abs(normalize_angle_deg(math.degrees(math.atan2(e1[1][1] - e1[0][1], e1[1][0] - e1[0][0]))))
        ang2 = abs(normalize_angle_deg(math.degrees(math.atan2(e2[1][1] - e2[0][1], e2[1][0] - e2[0][0]))))
        if best is None or max(ang1, ang2) < best[0]:
            top, bottom = sorted([e1, e2], key=lambda e: (e[0][1] + e[1][1]) / 2)
            best = (max(ang1, ang2), top, bottom)
    _, top, bottom = best
    xs = [p[0] for p in pts]
    return {"top": top, "bottom": bottom, "x0": min(xs), "x1": max(xs)}


def y_on_edge(edge, x):
    (xa, ya), (xb, yb) = edge
    if xb == xa:
        return (ya + yb) / 2
    t = min(1.0, max(0.0, (x - xa) / (xb - xa)))
    return ya + (yb - ya) * t


def point_in_poly(pt, poly):
    return cv2.pointPolygonTest(np.asarray(poly, dtype=np.float32),
                                (float(pt[0]), float(pt[1])), False) >= 0


def corridor_contains(rail, bbox, margin):
    """价签完整落在走廊内（允许超出走廊边至多 margin 像素）。"""
    x0, y0, x1, y1 = bbox
    top = rail["top_line"]
    bottom = rail["bottom_line"]
    ok_top = all(top["a"] + top["b"] * x <= y0 + margin for x in (x0, x1))
    ok_bot = all(bottom["a"] + bottom["b"] * x >= y1 - margin for x in (x0, x1))
    return ok_top and ok_bot


def evaluate_rails(auto_rails, user_rails, user_tags, width, height):
    """段集评估：人工排附近的所有自动走廊段合并度量。判据是人工价签被段集
    完整包含的比例（目的是价签框位置，不要求单条完整长导轨）。"""
    results = []
    used_auto = set()
    for ur in user_rails:
        edges = user_rail_edges(ur["poly"])
        (xa0, ya0), (xa1, ya1) = edges["top"]
        uang = normalize_angle_deg(math.degrees(math.atan2(ya1 - ya0, xa1 - xa0)))
        inside_tags = [t["bbox"] for t in user_tags
                       if point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                         (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]

        def u_line(x):
            return (y_on_edge(edges["top"], x) + y_on_edge(edges["bottom"], x)) / 2.0

        segset = []
        for ai, ar in enumerate(auto_rails):
            lo = max(ar["x_range"][0], edges["x0"])
            hi = min(ar["x_range"][1], edges["x1"])
            if hi - lo <= 0:
                continue
            # 沿重叠区采样：段中心线须整段落在人工带内（防邻近排杂段混入）
            in_band = True
            for fx in (0.1, 0.3, 0.5, 0.7, 0.9):
                x = lo + fx * (hi - lo)
                seg_y = ar["top_line"]["a"] + ar["top_line"]["b"] * x
                band_top = y_on_edge(edges["top"], x)
                band_bot = y_on_edge(edges["bottom"], x)
                margin = 0.25 * (band_bot - band_top)
                if not (band_top - margin <= seg_y <= band_bot + margin):
                    in_band = False
                    break
            if not in_band:
                continue
            segset.append(ai)

        rec = {"user_x": [round(edges["x0"], 1), round(edges["x1"], 1)],
               "user_angle": round(uang, 2), "tags_in_rail": len(inside_tags),
               "segments": segset, "matched": False,
               "containment": 0.0, "union_x_coverage": 0.0}
        if inside_tags and segset:
            cont = sum(1 for b in inside_tags
                       if any(corridor_contains(auto_rails[ai], b, TRUNCATION_MARGIN)
                              for ai in segset)) / len(inside_tags)
            ivals = sorted((max(auto_rails[ai]["x_range"][0], edges["x0"]),
                            min(auto_rails[ai]["x_range"][1], edges["x1"])) for ai in segset)
            merged_len, cur_lo, cur_hi = 0.0, ivals[0][0], ivals[0][1]
            for lo, hi in ivals[1:]:
                if lo <= cur_hi:
                    cur_hi = max(cur_hi, hi)
                else:
                    merged_len += cur_hi - cur_lo
                    cur_lo, cur_hi = lo, hi
            merged_len += cur_hi - cur_lo
            rec["union_x_coverage"] = round(merged_len / max(1e-6, edges["x1"] - edges["x0"]), 3)
            rec["containment"] = round(cont, 3)
            rec["matched"] = cont >= RAIL_MATCH_CONTAINMENT
            if rec["matched"]:
                used_auto.update(segset)
            wsum = sum(auto_rails[ai]["x_range"][1] - auto_rails[ai]["x_range"][0] for ai in segset)
            if wsum > 0:
                rec["angle_err_deg"] = round(
                    sum(auto_rails[ai]["angle_deg"] * (auto_rails[ai]["x_range"][1] - auto_rails[ai]["x_range"][0])
                        for ai in segset) / wsum - uang, 2)
        results.append(rec)
    false_rails = [i for i in range(len(auto_rails)) if i not in used_auto]
    return results, false_rails


# ---------- 叠图 ----------

def draw_overlay(image, auto_rails, user_rails, user_tags, rail_boxes, path):
    vis = image.copy()
    for t in user_tags:
        b = [int(round(v)) for v in t["bbox"]]
        cv2.rectangle(vis, (b[0], b[1]), (b[2], b[3]), (255, 128, 0), 1)
    for ur in user_rails:
        cv2.polylines(vis, [np.asarray(ur["poly"], dtype=np.int32)], True, (255, 0, 0), 2)
    colors = {"detected": (0, 255, 0), "inferred": (0, 165, 255)}
    for ri, ar in enumerate(auto_rails):
        p = ar["poly"].astype(int)
        cv2.polylines(vis, [p[:2]], False, colors[ar["sources"][0]], 2)
        cv2.polylines(vis, [p[2:]], False, colors[ar["sources"][1]], 2)
        cv2.putText(vis, f"R{ri}", (int(ar["x_range"][0]) + 4, int(p[0][1]) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        for bi, box in enumerate(rail_boxes.get(ri, []), 1):
            b = [int(round(v)) for v in box]
            cv2.rectangle(vis, (b[0], b[1]), (b[2], b[3]), (0, 255, 0), 1)
    save_image(path, vis)


# ---------- 主流程 ----------

def process_image(stem, image_path):
    image = read_image(image_path)
    height, width = image.shape[:2]
    edges = canny_edges(image)
    entities = detect_entities(image)
    rows = group_rows(entities, width, edges)
    auto_rails = [r for r in (build_corridor(row, width, height, edges) for row in rows) if r]

    rail_boxes = {}
    corridor_details = {}
    for ri, ar in enumerate(auto_rails):
        band_mask = np.zeros(edges.shape, np.uint8)
        cv2.fillPoly(band_mask, [ar["poly"].astype(np.int32)], 1)
        ar["band_edge_density"] = float(edges[band_mask > 0].mean() / 255.0)
        skip = None
        if ar["bot_edge_sup"] < ROW_BOT_EDGE_SUP_MIN:
            skip = "skipped_row_no_rail_edge"
        elif ar["band_edge_density"] < DENSE_BAND_MIN:
            skip = "skipped_not_dense"
        if skip:
            corridor_details[ri] = {"stage_b": skip,
                                    "bot_edge_sup": ar["bot_edge_sup"],
                                    "band_edge_density": round(ar["band_edge_density"], 4)}
            continue
        crop, M_inv, ch = flatten_corridor(image, ar)
        boxes_local, touches, n_main = detect_tags_in_corridor(crop, ch)
        expanded = False
        if boxes_local and touches > CORRIDOR_EXPAND_TOUCH_FRAC * len(boxes_local):
            # 价签候选触及走廊上下界：扩大余量重检一次（文档规定行为）
            expand_corridor(ar, CORRIDOR_EXPAND_FACTOR)
            crop, M_inv, ch = flatten_corridor(image, ar)
            boxes_local, touches, n_main = detect_tags_in_corridor(crop, ch)
            expanded = True
        quads = [map_box_back(b, M_inv) for b in boxes_local]
        bboxes = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads]
        rail_boxes[ri] = bboxes
        corridor_details[ri] = {
            "stage_b": "ran", "candidates": len(boxes_local),
            "main_pass": n_main, "touching_corridor_edge": touches,
            "expanded": expanded, "flattened_h": round(ch, 1),
            "band_edge_density": round(ar["band_edge_density"], 4),
        }

    user_tags, user_rails, _ = load_annotations(NEW_DIR / f"{stem}.json")
    rail_evals, false_rails = evaluate_rails(auto_rails, user_rails, user_tags, width, height)

    stage_b_eval = []
    for ri, rec in enumerate(rail_evals):
        if not rec["matched"]:
            continue
        preds = [b for ai in rec["segments"] for b in rail_boxes.get(ai, [])]
        ur = user_rails[ri]
        inside = [t["bbox"] for t in user_tags
                  if point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                    (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
        entry = {"rail": ri, "segments": len(rec["segments"]),
                 "containment": rec["containment"],
                 "user_tags": len(inside), "preds": len(preds)}
        for thr in (0.3, 0.5, 0.7):
            entry[f"matched_{thr}"] = len(match_greedy(preds, inside, thr))
        merged = sum(1 for p in preds if sum(1 for g in inside if iou(p, g) > 0) >= 2)
        entry["preds_covering_2plus_tags"] = merged
        stage_b_eval.append(entry)

    out_dir = OUT_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    draw_overlay(image, auto_rails, user_rails, user_tags, rail_boxes, str(out_dir / "overlay.jpg"))

    rails_out = [{
        "index": ri, "angle_deg": round(ar["angle_deg"], 3),
        "x_range": [round(v, 1) for v in ar["x_range"]],
        "gap": round(ar["gap"], 1), "inner_gap": round(ar["inner_gap"], 1),
        "sources": ar["sources"],
        "n_entities": ar["n_entities"], "gap_ratio": ar["gap_ratio"],
        "bot_edge_sup": ar["bot_edge_sup"],
        "edge_support": ar["edge_support"],
        "resid_max_px": ar["resid_max_px"],
        "band_edge_density": round(ar["band_edge_density"], 4),
        "poly": ar["poly"].tolist(), "detail": corridor_details.get(ri, {}),
    } for ri, ar in enumerate(auto_rails)]

    return {
        "image": stem, "size": [width, height],
        "entities_total": len(entities), "rows_total": len(rows),
        "auto_rails": rails_out, "rail_eval": rail_evals,
        "false_auto_rails": false_rails, "stage_b_eval": stage_b_eval,
        "params": {"entity_passes": ENTITY_PASSES, "entity_h_rel": ENTITY_H_REL,
                   "row_min_entities": ROW_MIN_ENTITIES, "row_x_span_rel": ROW_X_SPAN_REL,
                   "corridor_margin_rel": CORRIDOR_MARGIN_REL,
                   "tag_h_rel": TAG_H_REL, "tag_w_over_h": TAG_W_OVER_H},
        "note": "走廊由实体排拟合（非导轨实体边配对）；阶段B复用已验证多阈值找框；"
                "标注仅用于评测；参数冻结；单图结果不证明泛化。",
    }


def main():
    stems = sorted(p.stem for p in NEW_DIR.glob("*.json"))
    all_results = []
    for stem in stems:
        for ext in (".jpg", ".jpeg"):
            image_path = NEW_DIR / f"{stem}{ext}"
            if image_path.exists():
                break
        else:
            print(f"跳过 {stem}：无图像文件")
            continue
        result = process_image(stem, image_path)
        n_matched = sum(1 for r in result["rail_eval"] if r["matched"])
        b_tags = sum(e["user_tags"] for e in result["stage_b_eval"])
        b_preds = sum(e["preds"] for e in result["stage_b_eval"])
        b_m03 = sum(e["matched_0.3"] for e in result["stage_b_eval"])
        b_m05 = sum(e["matched_0.5"] for e in result["stage_b_eval"])
        print(f"{stem[:8]}: 排 {len(result['auto_rails'])} 人工 {len(result['rail_eval'])} "
              f"匹配 {n_matched} | 阶段B: tags={b_tags} preds={b_preds} m03={b_m03} m05={b_m05}")
        all_results.append(result)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    tot = {"rails": sum(len(r["rail_eval"]) for r in all_results),
           "matched": sum(1 for r in all_results for e in r["rail_eval"] if e["matched"]),
           "tags": sum(e["user_tags"] for r in all_results for e in r["stage_b_eval"]),
           "preds": sum(e["preds"] for r in all_results for e in r["stage_b_eval"]),
           "m03": sum(e["matched_0.3"] for r in all_results for e in r["stage_b_eval"]),
           "m05": sum(e["matched_0.5"] for r in all_results for e in r["stage_b_eval"]),
           "m07": sum(e["matched_0.7"] for r in all_results for e in r["stage_b_eval"])}
    print(f"合计: 人工排 {tot['rails']} 匹配 {tot['matched']} | 阶段B tags={tot['tags']} "
          f"preds={tot['preds']} IoU0.3={tot['m03']} IoU0.5={tot['m05']} IoU0.7={tot['m07']}")
    print("完成 ->", OUT_DIR)


if __name__ == "__main__":
    main()
