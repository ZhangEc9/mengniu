# -*- coding: utf-8 -*-
"""中心点读价 A/B 实验（用户 2026-09-30 提案）。

同一条轨、同一次 API 返回，对比两种分配策略：
  A（本地先行）：组件↔中心点匹配，配上的用组件 bbox；中心没配上→种子扩张；
              组件没配上中心、中心扩张失败 → 一律删除（用户口径）。
  B（直接扩张）：跳过本地检测，每个中心点直接自适应扩张；失败即删。

提示词继承生产最终版规则：宁缺毋滥/严禁 null、X折忽略、两件X元与第二件X元单独
标注、严禁等差脑补、严禁合并、倒立翻正、置信度字段。
走廊复用 rails.json（离线重建，不调导轨 API）。"""
import json
import math
import os
import re
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
import api_rail_experiment as A

EXP = HERE / "API端点导轨实验"
OUT = HERE / "中心点读价实验"
EXCLUDE = "45035165"

SYSTEM_PROMPT = (
    "你是专业的零售商品价签视觉识别专家。最高准则：宁缺毋滥，只有价格数字 100% "
    "清晰可辨才输出，price 严禁为 null；严禁凭空脑补等差数列价格（如 4.90,5.90,6.90）；"
    "严禁把相邻价签合并；严禁把瓶盖、瓶口、包装图案文字当成价签；冷柜倒立价签请翻正读取。"
    "只输出标准 JSON 数组，不输出任何解释或 markdown 标记。"
)
USER_PROMPT = (
    "图中是一条价格签条（导轨）的特写，上面贴有一排价签。请找出图中每个真实独立、"
    "价格清晰可辨的零售价签，输出其价格与中心点。\n"
    "规则：\n"
    "1. 仅标有“X折/打折”而无折后单价的宣传牌直接忽略；会员价、积分兑换牌忽略。\n"
    "2. “两件X元/X件Y元”输出 tag_type:\"bundle_promotion\"，price 为组合总价；"
    "“第二件X元”输出 tag_type:\"second_item_promotion\"；普通价签 tag_type:\"regular_price\"。\n"
    "3. center 为该价签卡片的几何中心点 [x,y]，0-1000 归一化整数坐标（相对本图宽高）。\n"
    "4. 每个价签一条记录，按从左到右排序；price 为清晰有效的金额字符串（如 \"13.90\"），"
    "看不清的价签直接跳过不输出。\n"
    "5. 只输出 JSON 数组："
    '[{"price":"13.90","center":[x,y],"tag_type":"regular_price",'
    '"confidence":0.95,"price_confidence":0.95},...]'
)

STRIP_SCALE = 2          # 条带放大倍数（与 rail_numbered_read 一致）
CROP_MARGIN_Y = 0.35     # 裁条上下余量（与阶段B一致）
PASSES = [(21, 5), (31, 1), (41, 3)]   # 多阈值通道（同阶段B）


def norm_price(s):
    if s is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(s))
    return f"{float(m.group()):.2f}" if m else None


def call_strip_api(image_path, out_dir, tag):
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
            (out_dir / f"response_{tag}_a{attempt + 1}.txt").write_text(response.text, encoding="utf-8")
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("status") != 0:
                raise RuntimeError(f"API business status: {envelope.get('status')}")
            return ((envelope.get("payload") or {}).get("result") or {}).get("page_content", "")
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise last_err


def parse_readings(content):
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("tags")
    items = []
    for it in parsed or []:
        if not isinstance(it, dict):
            continue
        c = it.get("center")
        if c and len(c) >= 2:
            items.append({"price": it.get("price"),
                          "price_norm": norm_price(it.get("price")),
                          "tag_type": it.get("tag_type", "regular_price"),
                          "center": [float(c[0]), float(c[1])]})
    return items


def expand_center_to_box(center, crop, ch, masks_cache):
    """中心点 → 自适应边界：多阈值掩膜上取包含/最近中心且尺寸合理的连通块。"""
    ccx, ccy = center
    win = 1.2 * ch                      # 横向搜索窗口半宽
    h_lo, h_hi = 0.30 * ch, 1.10 * ch
    best = None
    for mask in masks_cache:
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        lab = labels[min(int(ccy), mask.shape[0] - 1), min(int(ccx), mask.shape[1] - 1)]
        cands = []
        if lab > 0:
            cands.append(lab)
        for lb in range(1, n):
            x, y, w, h, area = stats[lb]
            if h < h_lo or h > h_hi or w < 0.8 * h or w > 1.8 * ch:
                continue
            ecx, ecy = centroids[lb]
            if abs(ecx - ccx) > win or abs(ecy - ccy) > 1.0 * ch:
                continue
            if lb not in cands:
                cands.append(lb)
        for lb in cands:
            x, y, w, h, area = stats[lb]
            contains = (x <= ccx <= x + w and y <= ccy <= y + h)
            dist = math.hypot(centroids[lb][0] - ccx, centroids[lb][1] - ccy)
            score = (0 if contains else 1, dist)
            if best is None or score < best[0]:
                best = (score, [x, y, x + w, y + h])
    return best[1] if best else None


def plan_a(components, centers, ch):
    """本地先行：组件↔中心匹配；未配中心→扩张；未配组件/扩张失败→删。"""
    med_w = float(np.median([c[2] - c[0] for c in components])) if components else 0.0
    pair_thr = 0.6 * med_w if med_w > 0 else 0.0
    pairs = []
    for ci, comp in enumerate(components):
        ccx = (comp[0] + comp[2]) / 2
        ccy = (comp[1] + comp[3]) / 2
        for pi, it in enumerate(centers):
            d = math.hypot(it["center"][0] - ccx, it["center"][1] - ccy)
            if d <= pair_thr:
                pairs.append((d, ci, pi))
    pairs.sort()
    used_c, used_p, out = set(), set(), []
    for d, ci, pi in pairs:
        if ci in used_c or pi in used_p:
            continue
        used_c.add(ci)
        used_p.add(pi)
        out.append({"bbox": components[ci], "src": "component", **centers[pi]})
    for pi, it in enumerate(centers):
        if pi in used_p:
            continue
        out.append({"bbox": None, "src": "expand_failed", **it})
    return out, len(components) - len(used_c)


def plan_b(centers, expand_fn):
    """直接扩张：每个中心点自适应扩展，失败即删。"""
    out = []
    for it in centers:
        bbox = expand_fn(it["center"])
        out.append({"bbox": bbox, "src": "expanded" if bbox else "expand_failed", **it})
    return out


def back_map(entries, M_inv):
    for e in entries:
        if e["bbox"] is None:
            e["bbox_orig"] = None
            continue
        x0, y0, x1, y1 = e["bbox"]
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
        mapped = np.append(corners, np.ones((4, 1)), axis=1) @ M_inv.T
        e["bbox_orig"] = [mapped[:, 0].min(), mapped[:, 1].min(),
                          mapped[:, 0].max(), mapped[:, 1].max()]
    return entries


def eval_entries(entries, tag_dicts):
    gts = [t["bbox"] for t in tag_dicts]
    preds = [e["bbox_orig"] for e in entries if e["bbox_orig"] is not None]
    kept = [e for e in entries if e["bbox_orig"] is not None]
    res = {f"m{int(t*100)}": len(R.match_greedy(preds, gts, t)) for t in (0.3, 0.5, 0.7)}
    matches = R.match_greedy(preds, gts, 0.5)
    price_ok = price_cmp = 0
    for m in matches:
        e = kept[m["pred"]]
        gt_price = norm_price(tag_dicts[m["gt"]].get("label"))
        if e["price_norm"] and gt_price:
            price_cmp += 1
            price_ok += (e["price_norm"] == gt_price)
    res["price_cmp"] = price_cmp
    res["price_ok"] = price_ok
    return res


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 999
    stems = [s for s in sorted(p.stem for p in (HERE / "新数据").glob("*.json"))
             if not s.startswith(EXCLUDE)]
    results = []
    n_calls = 0
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

        out_dir = OUT / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        rail_rows = []
        for k, ur in enumerate(user_rails):
            if n_calls >= limit:
                break
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
                if abs(ry - uy) < best_gap:
                    best_ri, best_gap = ri, abs(ry - uy)
            if best_ri is None:
                continue
            rail = corridors[best_ri]
            crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=CROP_MARGIN_Y)
            strip = cv2.resize(crop, None, fx=STRIP_SCALE, fy=STRIP_SCALE,
                               interpolation=cv2.INTER_CUBIC)
            strip_path = out_dir / f"strip_rail{k}.jpg"
            R.save_image(str(strip_path), strip)

            content = call_strip_api(strip_path, out_dir, f"rail{k}")
            n_calls += 1
            (out_dir / f"content_rail{k}.txt").write_text(content, encoding="utf-8")
            readings = parse_readings(content)
            centers = [{"price": it["price"], "price_norm": it["price_norm"],
                        "tag_type": it["tag_type"],
                        "center": [it["center"][0] / 1000.0 * strip.shape[1] / STRIP_SCALE,
                                   it["center"][1] / 1000.0 * strip.shape[0] / STRIP_SCALE]}
                       for it in readings]

            # 组件与掩膜（A 用组件，A/B 共用扩张掩膜）
            masks = []
            comp_all = []
            for block, const in PASSES:
                boxes_p, mask = R.contour_boxes_pass(crop, block, const, ch)
                masks.append(mask)
                comp_all += boxes_p
            components = R.dedup_iou(comp_all)

            def expand_fn(center):
                return expand_center_to_box(center, crop, ch, masks)

            entries_a, n_del_comp = plan_a(components, centers, ch)
            entries_b = plan_b(centers, expand_fn)
            entries_a = back_map(entries_a, M_inv)
            entries_b = back_map(entries_b, M_inv)

            inside = [t for t in user_tags
                      if R.point_in_poly(((t["bbox"][0] + t["bbox"][2]) / 2,
                                          (t["bbox"][1] + t["bbox"][3]) / 2), ur["poly"])]
            gts = [t["bbox"] for t in inside]
            ev_a = eval_entries(entries_a, inside)
            ev_b = eval_entries(entries_b, inside)
            row = {
                "rail": k, "corridor": best_ri, "strip_wh": [strip.shape[1], strip.shape[0]],
                "centers": len(centers), "components": len(components),
                "a": {"kept": sum(1 for x in entries_a if x["bbox_orig"]),
                      "deleted_components": n_del_comp,
                      "expand_failed": sum(1 for x in entries_a if x["bbox_orig"] is None),
                      **ev_a},
                "b": {"kept": sum(1 for x in entries_b if x["bbox_orig"]),
                      "expand_failed": sum(1 for x in entries_b if x["bbox_orig"] is None),
                      **ev_b},
                "user_tags": len(inside),
            }
            rail_rows.append(row)

            # 复核图
            pts = np.asarray(ur["poly"])
            x0u, y0u = pts[:, 0].min(), pts[:, 1].min()
            x1u, y1u = pts[:, 0].max(), pts[:, 1].max()
            cx0, cy0 = int(max(0, x0u - 60)), int(max(0, y0u - 60))
            cx1, cy1 = int(min(W, x1u + 60)), int(min(H, y1u + 60))
            vis = image[cy0:cy1, cx0:cx1].copy()
            for t in user_tags:
                b = [int(round(v)) for v in t["bbox"]]
                cv2.rectangle(vis, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (255, 128, 0), 1)
            cv2.polylines(vis, [(pts - [cx0, cy0]).astype(np.int32)], True, (255, 0, 0), 2)
            for e_ in entries_a:
                if e_["bbox_orig"] is None:
                    continue
                b = [int(round(v)) for v in e_["bbox_orig"]]
                cv2.rectangle(vis, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (0, 220, 0), 2)
                if e_["price_norm"]:
                    cv2.putText(vis, e_["price_norm"], (b[0] - cx0, max(12, b[1] - cy0 - 4)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 0), 1)
            for e_ in entries_b:
                if e_["bbox_orig"] is None:
                    continue
                b = [int(round(v)) for v in e_["bbox_orig"]]
                cv2.rectangle(vis, (b[0] - cx0, b[1] - cy0), (b[2] - cx0, b[3] - cy0), (0, 140, 255), 1)
            R.save_image(str(out_dir / f"rail{k}.jpg"), vis)

        results.append({"image": stem[:8], "rails": rail_rows})
        print(f"{stem[:8]}: {len(rail_rows)} rails done")

    (OUT / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("calls:", n_calls, "->", OUT)


if __name__ == "__main__":
    main()
