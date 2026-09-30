# -*- coding: utf-8 -*-
"""按排质量路由 + 双视图仲裁 最小闭环。

流程（每图）：
  ① 全图原 API（生产最终版提示词）→ 框+价          [view 1]
  ② API 导轨框 → 实体验证 → 走廊                    [已有，离线重建]
  ③ 逐走廊质量门禁（GT-free 特征）：
       通过 → 本地画框 + 编号渲染 + 按ID读价 API     [view 2]
       不通过 → 本地不出场
  ④ 仲裁：通过排以 view2 替换 view1（框里没价格不输出）；
         其余区域保留 view1。
  ⑤ 对照人工 GT 评测最终结果与仲裁正确率。

用法：
  py arbitration_loop.py features   # 只打印每走廊门禁特征（0 API）
  py arbitration_loop.py run        # 全流程（全图+编号读价）
"""
import json
import math
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
OUT = HERE / "仲裁闭环实验"
PROMPTS_DIR = ROOT / "prompts"

# ---- 冻结门禁阈值（在开发图上标定后对 6 图统一冻结）----
GATE = {
    "min_inliers": 6,          # 中心线内点实体数
    "inlier_ratio": 0.45,      # 内点 / 框内实体（低=被商品文字污染）
    "inlier_resid_ratio": 0.35,# 内点残差 std / 内点中位实体高
    "min_preds": 4,            # 阶段B候选数
}
# 读后保护：编号读价可读数不足的排不替换全图结果（错误放行的轨自动无害）
MIN_READABLE = 3
MIN_READABLE_RATIO = 0.5

# rail_numbered_read 的读价提示词（原样复用）
NR_SYSTEM = "你是零售价签读数助手。图上方的编号是人工画在图片外的索引，不是价签上的价格。只转录各彩色框内实体价签的实际零售价格，不猜测。"
NR_USER = (
    "图中是一整排价签，每个绿色框上方有唯一 ID。请按 ID 分别读绿色框内的实际商品价签，"
    "不能按等距顺序推断，不能把邻框价格挪入本框；若框包含多个价签、没有完整价签或数字模糊，"
    "该 ID 返回 price:null、readable:false。必须返回每个 ID，且只输出 JSON 数组："
    '[{"id":1,"price":"8.90","readable":true},...]。不要输出或修改 bbox。'
)


def norm_price(s):
    if s is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(s))
    return f"{float(m.group()):.2f}" if m else None


def call_api(image_path, system_text, user_text, out_dir, tag):
    url = pipeline.upload_to_mengniu_oss(image_path)
    if not url:
        raise RuntimeError("OSS upload failed")
    body = {"image": url, "system_text": system_text, "text": user_text,
            "userId": "", "envSystemName": "", "userName": "", "envSystemVersion": "",
            "unionId": "", "apiVersion": "0.0.2"}
    last_err = None
    for attempt in range(4):
        timestamp = str(int(time.time() * 1000))
        headers = {"X-MN-APP-ID": pipeline.PRICE_APPID,
                   "X-MN-SIGN": pipeline.make_mn_sign(body, pipeline.PRICE_SECRET, timestamp),
                   "X-MN-TIMESTAMP": timestamp, "Content-Type": "application/json"}
        try:
            response = requests.post(pipeline.PRICE_API_URL, headers=headers, json=body, timeout=240)
            (out_dir / f"response_{tag}_a{attempt + 1}.txt").write_text(response.text, encoding="utf-8")
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("status") != 0:
                raise RuntimeError(f"API business status: {envelope.get('status')}")
            return ((envelope.get("payload") or {}).get("result") or {}).get("page_content", "")
        except (requests.RequestException, RuntimeError, ValueError) as e:
            last_err = e
            time.sleep(8 * (attempt + 1))
    raise last_err


def corridor_bot_sup(edges, rail, width, height):
    """走廊底边线下方 0-12px 的 Canny 命中率（走廊版 bottom_edge_support）。"""
    h_img, w_img = edges.shape
    bot = rail["bottom_line"]
    x0, x1 = rail["x_range"]
    xs = np.linspace(x0, x1, 40)
    hits = 0
    for x in xs:
        xi = min(int(round(x)), w_img - 1)
        yi = int(round(bot["a"] + bot["b"] * x))
        yi0, yi1 = max(0, yi - 2), min(h_img, yi + 13)
        if yi0 < yi1 and edges[yi0:yi1, xi].max() > 0:
            hits += 1
    return hits / 40.0


def build_corridors(stem, image):
    H, W = image.shape[:2]
    d = json.loads((EXP / stem / "rails.json").read_text(encoding="utf-8"))
    entities = R.detect_entities(image)
    rail_lines = R.detect_rail_lines(image)
    tilt = A.image_tilt(rail_lines)
    corridors = []
    for c in d["corridors"]:
        box_px = A.bbox_1000_to_pixels(c["api_box_1000"], W, H)
        rail = A.refine_corridor_from_railbox(box_px, entities, W, H, rail_lines, tilt)
        if rail is not None:
            corridors.append(rail)
    return corridors, entities


def corridor_features(image, edges, rail):
    crop, M_inv, ch = R.flatten_corridor(image, rail, crop_margin_y=0.35)
    boxes_local, touches, n_main = R.detect_tags_in_corridor(crop, ch)
    quads = [R.map_box_back(b, M_inv) for b in boxes_local]
    bboxes = [[q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()] for q in quads]
    feats = {
        "n_inliers": rail["n_entities"],
        "inlier_ratio": rail["inlier_ratio"],
        "resid_ratio": round(rail["inlier_resid_std"] / max(1e-6, rail["inlier_med_h"]), 2),
        "bot_sup": round(corridor_bot_sup(edges, rail, image.shape[1], image.shape[0]), 2),
        "preds": len(bboxes),
    }
    return feats, bboxes, (crop, M_inv, ch)


def render_numbered(crop, boxes_local):
    header = 40
    canvas = np.full((header + crop.shape[0], crop.shape[1], 3), 255, dtype=np.uint8)
    canvas[header:] = crop
    for i, (x0, y0, x1, y1) in enumerate(boxes_local, 1):
        cv2.rectangle(canvas, (x0, y0 + header), (x1, y1 + header), (0, 180, 0), 2)
        cv2.putText(canvas, f"ID{i:02d}", (x0, header - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.line(canvas, ((x0 + x1) // 2, header - 6), ((x0 + x1) // 2, y0 + header - 2),
                 (0, 180, 0), 1)
    return cv2.resize(canvas, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)


def parse_numbered(content, count):
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("tags")
    readings = {}
    for it in parsed or []:
        if isinstance(it, dict) and "id" in it:
            try:
                idx = int(it["id"])
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= count and idx not in readings:
                readings[idx] = it
    return readings


def parse_full_image(content, width, height):
    parsed = pipeline.parse_robust_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("tags")
    out = []
    for it in parsed or []:
        if not isinstance(it, dict):
            continue
        bbox = it.get("bbox")
        if not bbox or len(bbox) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
        px = [x0 / 1000.0 * width, y0 / 1000.0 * height,
              x1 / 1000.0 * width, y1 / 1000.0 * height]
        price = norm_price(it.get("price"))
        out.append({"bbox": px, "price": price,
                    "tag_type": it.get("tag_type", "regular_price"),
                    "raw": it.get("raw_price_text", "")})
    return out


def center_in(box, rail):
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    top = rail["top_line"]["a"] + rail["top_line"]["b"] * cx
    bot = rail["bottom_line"]["a"] + rail["bottom_line"]["b"] * cx
    return top <= cy <= bot and rail["x_range"][0] - 20 <= cx <= rail["x_range"][1] + 20


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "features"
    stems = sorted(p.stem for p in (HERE / "新数据").glob("*.json"))
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = []

    for stem in stems:
        img_path = next((HERE / "新数据" / f"{stem}{ext}" for ext in (".jpg", ".jpeg")
                         if (HERE / "新数据" / f"{stem}{ext}").exists()), None)
        image = R.read_image(img_path)
        H, W = image.shape[:2]
        edges = R.canny_edges(image)
        global entities_global
        entities_global = R.detect_entities(image)
        med_h_global = float(np.median([e["h"] for e in entities_global])) if entities_global else 30.0
        corridors, _ = build_corridors(stem, image)
        user_tags, user_rails, _ = R.load_annotations(HERE / "新数据" / f"{stem}.json")

        img_out = OUT / stem
        img_out.mkdir(parents=True, exist_ok=True)

        # 每走廊特征
        feats_list, boxes_list, crop_info = [], [], []
        for rail in corridors:
            feats, bboxes, cinfo = corridor_features(image, edges, rail)
            feats_list.append(feats)
            boxes_list.append(bboxes)
            crop_info.append(cinfo)

        # 门禁
        gate = []
        for f in feats_list:
            ok = (f["n_inliers"] >= GATE["min_inliers"]
                  and f["inlier_ratio"] >= GATE["inlier_ratio"]
                  and f["resid_ratio"] <= GATE["inlier_resid_ratio"]
                  and f["preds"] >= GATE["min_preds"])
            gate.append(ok)

        print(f"== {stem[:8]} corridors={len(corridors)}")
        for i, (f, g) in enumerate(zip(feats_list, gate)):
            print(f"  C{i}: {f} gate={'PASS' if g else 'REJECT'}")

        if mode != "run":
            continue

        # ① 全图原 API（生产提示词）
        sys_p = (PROMPTS_DIR / "price_system_prompt_final.txt").read_text(encoding="utf-8")
        usr_p = (PROMPTS_DIR / "price_user_prompt_final.txt").read_text(encoding="utf-8")
        full_path = img_out / "full_image.jpg"
        R.save_image(str(full_path), image)
        content = call_api(full_path, sys_p, usr_p, img_out, "full")
        (img_out / "full_content.txt").write_text(content, encoding="utf-8")
        view1 = parse_full_image(content, W, H)

        # ②/③ 通过门禁的走廊：编号读价（读后保护：可读数不足则该排保留 view1）
        view2 = []   # [{"bbox","price","rail"}]
        gate_eff = list(gate)
        guard_rejected = []
        for ri, ok in enumerate(gate):
            if not ok or not boxes_list[ri]:
                continue
            crop, M_inv, ch = crop_info[ri]
            boxes_crop, _, _ = R.detect_tags_in_corridor(crop, ch)
            if not boxes_crop:
                gate_eff[ri] = False
                continue
            strip = render_numbered(crop, boxes_crop)
            strip_path = img_out / f"numbered_rail{ri}.jpg"
            R.save_image(str(strip_path), strip)
            content_nr = call_api(strip_path, NR_SYSTEM, NR_USER, img_out, f"num{ri}")
            (img_out / f"num{ri}_content.txt").write_text(content_nr, encoding="utf-8")
            readings = parse_numbered(content_nr, len(boxes_crop))
            readable = [i for i, r in readings.items()
                        if r.get("readable") is True and norm_price(r.get("price"))]
            if len(readable) < max(MIN_READABLE,
                                   math.ceil(MIN_READABLE_RATIO * len(boxes_crop))):
                guard_rejected.append(ri)
                gate_eff[ri] = False
                continue
            for idx, box_crop in enumerate(boxes_crop, 1):
                r = readings.get(idx, {})
                price = norm_price(r.get("price")) if r.get("readable") is True else None
                if not price:
                    continue   # 框里没价格不输出
                corners = np.array([[box_crop[0], box_crop[1]], [box_crop[2], box_crop[1]],
                                    [box_crop[2], box_crop[3]], [box_crop[0], box_crop[3]]],
                                   dtype=np.float64)
                mapped = np.append(corners, np.ones((4, 1)), axis=1) @ M_inv.T
                bbox = [mapped[:, 0].min(), mapped[:, 1].min(),
                        mapped[:, 0].max(), mapped[:, 1].max()]
                view2.append({"bbox": bbox, "price": price, "rail": ri})

        # ④ 仲裁合并 v4（去重并集，教训：像素证据剔除会误杀弱本地图上的正确框；
        #   模型读数可信、定位不可信 → 凡有价格者基本保留，只按重叠去重）：
        #   - 通过排：view2（本地框+可读价）先入列
        #   - view1 框价格非空且不与任何 view2 框重叠 → 保留
        final = []
        for it in view2:
            final.append({"bbox": it["bbox"], "price": it["price"], "src": "view2"})
        v1_dup = 0
        for it in view1:
            if it["price"] is None:
                continue
            if any(R.iou(it["bbox"], v["bbox"]) >= 0.3 for v in view2):
                v1_dup += 1
                continue
            final.append({"bbox": it["bbox"], "price": it["price"], "src": "view1"})

        # ⑤ 评测
        def evaluate(entries):
            preds = [e["bbox"] for e in entries]
            gts = [t["bbox"] for t in user_tags]
            matches = R.match_greedy(preds, gts, 0.5)
            price_ok = 0
            for m in matches:
                if norm_price(entries[m["pred"]]["price"]) == norm_price(user_tags[m["gt"]]["label"]):
                    price_ok += 1
            return {"preds": len(preds), "gts": len(gts), "m30": len(R.match_greedy(preds, gts, 0.3)),
                    "m50": len(matches), "price_ok": price_ok}

        base_ev = evaluate([{"bbox": it["bbox"], "price": it["price"]} for it in view1])
        final_ev = evaluate(final)
        row = {"image": stem[:8], "corridors": len(corridors),
               "gate_pass": sum(gate), "guard_rejected": guard_rejected,
               "v1": base_ev, "v1_dropped_by_local": v1_dropped,
               "view2": len(view2), "final": final_ev,
               "gate": gate, "gate_eff": gate_eff, "features": feats_list}
        all_rows.append(row)
        print(f"  全图①: {base_ev}")
        print(f"  仲裁后: {final_ev} (view2贡献{len(view2)}, 替换①{v1_dropped}, "
              f"读后保护拦下{len(guard_rejected)}排)")

    if mode == "run":
        (OUT / "summary.json").write_text(
            json.dumps(all_rows, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
        tb = {"preds": 0, "gts": 0, "m30": 0, "m50": 0, "price_ok": 0}
        tf = dict(tb)
        for r in all_rows:
            for k in tb:
                tb[k] += r["v1"][k]
                tf[k] += r["final"][k]
        print(f"合计 ①全图: {tb}")
        print(f"合计 仲裁后: {tf}")


if __name__ == "__main__":
    main()
