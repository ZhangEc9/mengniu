# -*- coding: utf-8 -*-
"""API 端点法找导轨实验：API 找每条导轨首/尾 regular_price 价签 → 连线成走廊
→ 实体排验证 → 复用已验证多阈值找框 → 全量对照人工标注。

约定（与用户 2026-09-30 确认）：
- 端点框不要求精确落在人工最左/最右，大致范围即可；
- 端点只认 regular_price，促销牌不算；
- API 测试阶段放开调用；评测全部离线对照人工标注。
"""
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import run_full_pipeline as pipeline
import rail_first_experiment as R

NEW_DIR = HERE / "新数据"
OUT_DIR = HERE / "API端点导轨实验"

SYSTEM_PROMPT = "你是超市货架价签定位助手。只输出 JSON 数组，不输出解释、markdown 标记或其他文字。"
USER_PROMPT = (
    "图中是超市货架照片，货架上有一条或多条价格签条（导轨），每条上贴有一排价格签。\n"
    "任务：输出每一条价格签条的边界框，框住整条价签条（含其上全部价格签，上下留少量余量）。\n"
    "要求：\n"
    "1. 只算贴着普通白色价格标签的价签条；空导轨、只有促销牌/广告牌的条、商品包装上的"
    "文字行都不算。\n"
    "2. 一条价签条输出一个框；被照片边缘截断时框到可见部分为止。\n"
    "3. bbox 格式 [x1,y1,x2,y2]，为相对图像宽高的 0-1000 归一化整数坐标。\n"
    "4. 只输出 JSON 数组：[{\"rail_id\":1,\"rail_bbox\":[x1,y1,x2,y2]},...]\n"
    "5. 只输出真实可见的价签条；如果看不清或没有，返回 []，绝对不要按固定间隔编造。"
)

# 走廊几何（相对端点价签高 h）：紧走廊供检测（等价已验证贴签几何），宽走廊供评测
TIGHT_K = 0.65     # 半高 = h/2 + TIGHT_K*h
WIDE_K = 0.50
END_PAD_REL = 0.8  # 走廊 x 向两端各外扩 0.8×h
MAX_RAIL_ANGLE_DEG = 12.0
MIN_SPAN_PX = 300.0


def bbox_1000_to_pixels(bbox, width, height):
    x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return [x0 / 1000.0 * width, y0 / 1000.0 * height,
            x1 / 1000.0 * width, y1 / 1000.0 * height]


def call_api(image_path, out_dir):
    """上传图片并调用公司价签 API（自定义提示词），返回模型文本；网络抖动重试 3 次。"""
    url = pipeline.upload_to_mengniu_oss(image_path)
    if not url:
        raise RuntimeError("OSS upload failed")
    body = {"image": url, "system_text": SYSTEM_PROMPT, "text": USER_PROMPT,
            "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
            "unionId": "", "apiVersion": "0.0.2"}
    last_err = None
    for attempt in range(3):
        timestamp = str(int(time.time() * 1000))
        headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
                   "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
                   "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
        try:
            response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=180)
            (out_dir / f"response_attempt{attempt + 1}.txt").write_text(response.text, encoding="utf-8")
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("status") != 0:
                raise RuntimeError(f"API business status: {envelope.get('status')}")
            return ((envelope.get("payload") or {}).get("result") or {}).get("page_content", "")
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise last_err


def is_degenerate(rails_raw, width, height):
    """退化检测：大量记录共用几乎相同的端点 x（等距编造的特征）。"""
    if len(rails_raw) < 6:
        return False
    x0s = [min(r["first"][0], r["last"][0]) for r in rails_raw]
    x2s = [max(r["first"][2], r["last"][2]) for r in rails_raw]
    x0_med = float(np.median(x0s))
    x2_med = float(np.median(x2s))
    same_x0 = sum(1 for v in x0s if abs(v - x0_med) <= 12) / len(x0s)
    same_x2 = sum(1 for v in x2s if abs(v - x2_med) <= 12) / len(x2s)
    return same_x0 > 0.7 and same_x2 > 0.7


def parse_rails(content):
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("rails") or parsed.get("items")
    rails = []
    for item in parsed or []:
        if not isinstance(item, dict):
            continue
        box = item.get("rail_bbox")
        if box and len(box) >= 4:
            rails.append({"rail_id": item.get("rail_id"), "rail_bbox": list(box[:4])})
    return rails


def is_degenerate(rails_raw, width, height):
    """退化检测：大量记录共用几乎相同的 x 范围（等距编造的特征）。"""
    if len(rails_raw) < 6:
        return False
    x0s = [r["rail_bbox"][0] for r in rails_raw]
    x2s = [r["rail_bbox"][2] for r in rails_raw]
    x0_med = float(np.median(x0s))
    x2_med = float(np.median(x2s))
    same_x0 = sum(1 for v in x0s if abs(v - x0_med) <= 12) / len(x0s)
    same_x2 = sum(1 for v in x2s if abs(v - x2_med) <= 12) / len(x2s)
    return same_x0 > 0.7 and same_x2 > 0.7


def build_corridor_from_railbox(box_px, width, height):
    """API 导轨框 → 探针（仅用于匹配实体排的语义位置，不直接当走廊）。"""
    x0, y0, x1, y1 = box_px
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    span = x1 - x0
    if span < MIN_SPAN_PX:
        return None
    h = y1 - y0
    if not 12.0 <= h <= 0.25 * height:
        return None
    cy = (y0 + y1) / 2
    return {"x_range": [max(0.0, x0), min(float(width), x1)],
            "top_line": {"a": cy - 1.0, "b": 0.0},
            "bottom_line": {"a": cy + 1.0, "b": 0.0}}


def verify_with_entity_rows(rail, rows, h_med):
    """实体排硬验证：走廊中心线 0.6×h_med 内须有贴合实体排，且排高与端点价签高一致，
    覆盖 ≥60% 线长。返回 (cover, aligned_row_xspan)；无则 (0, 0)。"""
    mid = (rail["x_range"][0] + rail["x_range"][1]) / 2
    line_y = (rail["top_line"]["a"] + rail["bottom_line"]["a"]) / 2 + rail["top_line"]["b"] * mid
    best = (0.0, 0.0)
    for row in rows:
        row_y = row["a"] + row["b"] * mid
        if abs(row_y - line_y) > 0.6 * h_med:
            continue
        if not (0.5 <= row["h_med"] / max(1e-6, rail["tag_h"]) <= 1.6):
            continue
        ov = min(row["x1"], rail["x_range"][1]) - max(row["x0"], rail["x_range"][0])
        cover = ov / max(1e-6, rail["x_range"][1] - rail["x_range"][0])
        if cover > best[0]:
            best = (cover, row["x1"] - row["x0"])
    return round(best[0], 2), round(best[1], 0)


def refine_corridor_from_railbox(box_px, entities, width, height, lines=None, global_tilt=None):
    """API 导轨框即走廊：位置/高度/x范围用框，实体在框内验证并参与定中心线。
    角度：框内实体 Theil–Sen 拟合；若内点率 <50%（被商品文字污染）且全图倾角
    可用，则回退全图倾角（货架整体微倾时全图线段中位角比框内拟合稳）。"""
    x0, y0, x1, y1 = box_px
    x0, x1 = max(0.0, x0), min(float(width), x1)
    y0, y1 = max(0.0, y0), min(float(height), y1)
    if x1 - x0 < MIN_SPAN_PX:
        return None
    inside = [e for e in entities if x0 - 20 <= e["cx"] <= x1 + 20
              and y0 - 15 <= e["cy"] <= y1 + 15]
    if len(inside) < 4:
        return None
    cx = np.array([e["cx"] for e in inside])
    cy = np.array([e["cy"] for e in inside])
    med_h = float(np.median([e["h"] for e in inside]))

    # Theil–Sen 中位斜率：框内混入的水平商品文字实体曾把 2.3° 斜排拟合拉平，
    # 中位斜率抗少数派污染；中心线在"框中心线"与"实体拟合线"间按内点数二选一。
    slopes = []
    dx_min = max(2.0 * med_h, 0.05 * (x1 - x0))
    n = len(inside)
    for i in range(n):
        for j in range(i + 1, n):
            dx = cx[j] - cx[i]
            if abs(dx) >= dx_min:
                slopes.append((cy[j] - cy[i]) / dx)
    b = float(np.median(slopes)) if slopes else float(np.polyfit(cx, cy, 1)[0])

    def inlier_count(a_):
        return int((np.abs(cy - (a_ + b * cx)) <= 0.6 * med_h).sum())

    a_entity = float(np.median(cy - b * cx))
    for _ in range(2):
        resid = np.abs(cy - (a_entity + b * cx))
        inl_e = resid <= 0.6 * med_h
        if inl_e.sum() >= 4:
            a_entity = float(np.median(cy[inl_e] - b * cx[inl_e]))
        else:
            break
    a_box = (y0 + y1) / 2 - b * ((x0 + x1) / 2)
    if inlier_count(a_box) > inlier_count(a_entity):
        a, anchor = a_box, "box_center"
    else:
        a, anchor = a_entity, "entity_fit"
    inl = np.abs(cy - (a + b * cx)) <= 0.6 * med_h
    # 内点率过低 → 实体拟合被污染，回退全图倾角（货架整体一致微倾）
    if global_tilt is not None and inl.sum() < 0.5 * len(inside) \
            and abs(global_tilt) <= MAX_RAIL_ANGLE_DEG \
            and abs(global_tilt - math.degrees(math.atan(b))) > 0.8:
        b = math.tan(math.radians(global_tilt))
        a = float(np.median(cy - b * cx))
        anchor = "global_tilt"
    resid = np.abs(cy - (a + b * cx))
    inl = resid <= 0.6 * med_h
    if inl.sum() >= 4:
        p90 = float(np.percentile([e["h"] for e, g in zip(inside, inl) if g], 90))
        n_use = int(inl.sum())
    else:
        p90 = float(np.percentile([e["h"] for e in inside], 90))
        n_use = len(inside)
    signed = cy - (a + b * cx)
    inlier_resid = signed[inl] if inl.sum() else np.array([0.0])
    # x 范围：API 框 ∪ 内点实体范围（能不漏尽量不漏）
    if inl.sum() >= 1:
        ex0 = min(e["bbox"][0] for e, g in zip(inside, inl) if g)
        ex1 = max(e["bbox"][2] for e, g in zip(inside, inl) if g)
        x0 = float(max(0.0, min(x0, ex0 - 0.3 * med_h)))
        x1 = float(min(width, max(x1, ex1 + 0.3 * med_h)))
    angle = R.normalize_angle_deg(math.degrees(math.atan(b)))
    if abs(angle) > MAX_RAIL_ANGLE_DEG:
        return None
    h_box = y1 - y0
    h = min(max(h_box, 1.0 * p90), 3.0 * p90)
    half = h / 2.0
    center_y = a + b * ((x0 + x1) / 2)
    xs = np.array([x0, x1])

    def band_poly():
        top_y = (center_y - half) + b * xs
        bot_y = (center_y + half) + b * xs
        return np.asarray([[x0, top_y[0]], [x1, top_y[1]], [x1, bot_y[1]], [x0, bot_y[0]]])

    return {
        "poly": band_poly(), "angle_deg": angle,
        "x_range": [x0, x1], "gap": 2.0 * half, "inner_gap": 2.0 * half,
        "sources": ["detected", "detected"],
        "n_entities": n_use, "anchor": anchor,
        "inlier_ratio": round(float(inl.sum()) / max(1, len(inside)), 2),
        "inlier_resid_std": round(float(np.std(inlier_resid)), 1),
        "inlier_med_h": round(med_h, 1),
        "top_line": {"a": center_y - half, "b": b},
        "bottom_line": {"a": center_y + half, "b": b},
    }


def image_tilt(lines, top_k=15):
    """全图倾角：支持度最高的 top_k 条线的角度中位数（货架整体一致微倾）。"""
    if not lines:
        return None
    top = sorted(lines, key=lambda l: -l["support"])[:top_k]
    return float(np.median([l["angle"] for l in top]))


def process_image(stem, image_path):
    out_dir = OUT_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    image = R.read_image(image_path)
    height, width = image.shape[:2]

    content = call_api(image_path, out_dir)
    (out_dir / "content.txt").write_text(content, encoding="utf-8")
    rails_raw = parse_rails(content)
    degenerated = is_degenerate(rails_raw, width, height)
    if degenerated:
        # 退化输出（等距编造）：换一次调用再试
        content2 = call_api(image_path, out_dir)
        (out_dir / "content_retry.txt").write_text(content2, encoding="utf-8")
        rails_raw2 = parse_rails(content2)
        if not is_degenerate(rails_raw2, width, height):
            rails_raw = rails_raw2
            degenerated = False

    # API 框即走廊；实体框内验证 + 拟合角度；同框重复轨去重
    edges = R.canny_edges(image)
    entities = R.detect_entities(image)
    rows = R.group_rows(entities, width, edges)
    h_med = float(np.median([e["h"] for e in entities])) if entities else 30.0
    rail_lines = R.detect_rail_lines(image)
    tilt = image_tilt(rail_lines)

    corridors, rejected, used_rows = [], [], set()
    for item in rails_raw:
        box_px = bbox_1000_to_pixels(item["rail_bbox"], width, height)
        rail = refine_corridor_from_railbox(box_px, entities, width, height, rail_lines, tilt)
        if rail is None:
            rejected.append({"rail_id": item.get("rail_id"), "reason": "sanity_or_no_entity"})
            continue
        mid = (rail["x_range"][0] + rail["x_range"][1]) / 2
        y = (rail["top_line"]["a"] + rail["bottom_line"]["a"]) / 2 + rail["top_line"]["b"] * mid
        dup = any(abs(y - ((d["top_line"]["a"] + d["bottom_line"]["a"]) / 2
                           + d["top_line"]["b"] * mid)) <= 0.6 * (rail["gap"]) for d in corridors)
        if dup:
            rejected.append({"rail_id": item.get("rail_id"), "reason": "duplicate_row"})
            continue
        rail["rail_id"] = item.get("rail_id")
        rail["api_box_1000"] = item["rail_bbox"]
        corridors.append(rail)

    rail_boxes, details = {}, {}
    for ri, rail in enumerate(corridors):
        crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
        boxes_local, touches, n_main = R.detect_tags_in_corridor(crop, ch)
        quads = [R.map_box_back(b, M_inv) for b in boxes_local]
        rail_boxes[ri] = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads]
        details[ri] = {"candidates": len(boxes_local), "touching": touches,
                       "entities_in_box": rail["n_entities"]}

    # 评测：排匹配按"位置大致吻合"（用户口径），containment 仅作诊断
    user_tags, user_rails, _ = R.load_annotations(NEW_DIR / f"{stem}.json")
    rail_evals = []
    for ur in user_rails:
        edges_u = R.user_rail_edges(ur["poly"])
        (xa0, ya0), (xa1, ya1) = edges_u["top"]
        uang = R.normalize_angle_deg(math.degrees(math.atan2(ya1 - ya0, xa1 - xa0)))
        u_h = abs(R.y_on_edge(edges_u["bottom"], (edges_u["x0"] + edges_u["x1"]) / 2)
                  - R.y_on_edge(edges_u["top"], (edges_u["x0"] + edges_u["x1"]) / 2))
        inside_tags = [t["bbox"] for t in user_tags
                       if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                           (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
        rec = {"user_x": [round(edges_u["x0"], 1), round(edges_u["x1"], 1)],
               "user_angle": round(uang, 2), "tags_in_rail": len(inside_tags),
               "matched": False, "containment": 0.0}
        mid_x = (edges_u["x0"] + edges_u["x1"]) / 2
        u_center = (R.y_on_edge(edges_u["top"], mid_x) + R.y_on_edge(edges_u["bottom"], mid_x)) / 2
        best = None
        for ai, ar in enumerate(corridors):
            a_center = (ar["top_line"]["a"] + ar["bottom_line"]["a"]) / 2 \
                + ar["top_line"]["b"] * mid_x
            ov = min(ar["x_range"][1], edges_u["x1"]) - max(ar["x_range"][0], edges_u["x0"])
            if ov < 0.3 * (edges_u["x1"] - edges_u["x0"]):
                continue
            cont = 0
            if inside_tags:
                hits = 0
                for b_ in inside_tags:
                    cxm = (b_[0] + b_[2]) / 2
                    ym = (b_[1] + b_[3]) / 2
                    top_y = ar["top_line"]["a"] + ar["top_line"]["b"] * cxm
                    bot_y = ar["bottom_line"]["a"] + ar["bottom_line"]["b"] * cxm
                    if top_y <= ym <= bot_y:
                        hits += 1
                cont = hits / len(inside_tags)
            if best is None or cont > best[0]:
                best = (cont, ai)
        # 用户口径：走廊允许漏掉一两个价签
        allow = max(0, len(inside_tags) - 2) / len(inside_tags) if inside_tags else 0
        if best is not None and len(inside_tags) >= 2 and best[0] >= allow:
            rec["matched"] = True
            rec["containment"] = round(best[0], 3)
            rec["corridor"] = best[1]
        rail_evals.append(rec)

    stage_b_eval = []
    for ri, rec in enumerate(rail_evals):
        if not rec["matched"]:
            continue
        preds = rail_boxes.get(rec["corridor"], [])
        ur = user_rails[ri]
        inside = [t["bbox"] for t in user_tags
                  if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                      (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
        entry = {"rail": ri, "corridor": rec["corridor"], "containment": rec["containment"],
                 "user_tags": len(inside), "preds": len(preds)}
        for thr in (0.3, 0.5, 0.7):
            entry[f"matched_{thr}"] = len(R.match_greedy(preds, inside, thr))
        merged = sum(1 for p_ in preds if sum(1 for g in inside if R.iou(p_, g) > 0) >= 2)
        entry["preds_covering_2plus_tags"] = merged
        stage_b_eval.append(entry)

    # 全量口径（用户统计口径）：全部候选 vs 全部人工价签
    all_preds = [b for boxes in rail_boxes.values() for b in boxes]
    all_gts = [t["bbox"] for t in user_tags]
    global_eval = {f"m{int(t * 100)}": len(R.match_greedy(all_preds, all_gts, t))
                   for t in (0.3, 0.5)}

    R.draw_overlay(image, corridors, user_rails, user_tags, rail_boxes, str(out_dir / "overlay.jpg"))
    result = {
        "image": stem, "rails_returned": len(rails_raw), "rails_accepted": len(corridors),
        "rejected": rejected, "details": details,
        "rail_eval": rail_evals, "stage_b_eval": stage_b_eval,
        "global": {"preds": len(all_preds), "gts": len(all_gts), **global_eval},
        "corridors": [{"rail_id": c["rail_id"], "x_range": [round(v) for v in c["x_range"]],
                       "angle_deg": round(c["angle_deg"], 2),
                       "inner_gap": round(c["inner_gap"], 1),
                       "n_entities": c["n_entities"],
                       "poly": [[round(p[0]), round(p[1])] for p in c["poly"]],
                       "api_box_1000": c["api_box_1000"]} for c in corridors],
        "prompts": {"system": SYSTEM_PROMPT, "user": USER_PROMPT},
        "note": "端点由 API 语义识别（只要 regular_price）；几何只做走廊与找框；标注仅用于评测。",
    }
    (out_dir / "rails.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    return result


def main():
    stems = sorted(p.stem for p in NEW_DIR.glob("*.json"))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = []
    for stem in stems:
        if only and only not in stem:
            continue
        image_path = next((NEW_DIR / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                           if (NEW_DIR / f"{stem}{ext}").exists()), None)
        if image_path is None:
            continue
        result = process_image(stem, image_path)
        g = result["global"]
        n_matched = sum(1 for r in result["rail_eval"] if r["matched"])
        prec05 = g["m50"] / g["preds"] if g["preds"] else 0
        print(f"{stem[:8]}: API轨 {result['rails_returned']}→收 {result['rails_accepted']} "
              f"排匹配 {n_matched}/{len(result['rail_eval'])} | 全量 preds={g['preds']} "
              f"gts={g['gts']} m30={g['m30']} m50={g['m50']} prec={prec05:.3f}")
        results.append(result)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    tp = sum(r["global"]["m50"] for r in results)
    preds = sum(r["global"]["preds"] for r in results)
    gts = sum(r["global"]["gts"] for r in results)
    print(f"合计: preds={preds} gts={gts} IoU0.5命中={tp} "
          f"recall={tp / max(1, gts):.3f} precision={tp / max(1, preds):.3f}")


if __name__ == "__main__":
    main()
