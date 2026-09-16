# -*- coding: utf-8 -*-
"""
极简纯净版价签识别与可视化脚本 (全图极速版: 最小伤害Prompt + 连续复读机智能熔断)
- 纯全图单次极速识别 (一张图 60 秒，无切片重影与卡顿)
- 综合伤害最小版本 Prompt
- 智能连续复读链截断算法 (保全正常并排同价商品，彻底斩断长条纸 8~12 个复读机)
- 空间几何 IoU 重叠抑制 + 物理长宽比自适应纠偏
"""

import os
import sys
import json
import time
import requests
import hashlib
import re
import cv2
import numpy as np
import argparse
import mimetypes
from pathlib import Path

APPID = "aism-8I4GX61"
SECRET = "3005609PIFGO2FAYJL0EASA5V829YK35W32EM5YR64JV35O7"
OSS_UPLOAD_URL = "https://aism.mengniu.cn/brcapi/oss/api/oss/getFileUrl"
CACHE_OSS_FILE = r"D:\Shixi\mengniu\价签识别\oss_image_map.json"
DEFAULT_BEARER_TOKEN = "Bearer eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiLmiJDotLrlhrI4MjciLCJhdXRoIjoiMjIyMiIsInJlYWxuYW1lIjoi5oiQ6LS65YayIiwidGlkIjoxLCJleHRlcm5hbFVzZXJJZCI6IjAwODIzOTEiLCJ1aWQiOjU5Miwib3JnSWQiOiI4YTcwOGQwMTkxNzNmMGU3MDE5MTc4ZjFlODY5MDAwNSIsInJpZCI6IjksMTMsMTYsMTgsMjYsMjkiLCJ0aW1lc3RhbXAiOjE3ODkzNTE2NzczODd9.C5FGFggREjKuz3hBxyrmBWSnEcgeBTDyltCpSE9P4iShx75-uOBCPbCkAAyK3v0DckH-O-zRQ5cmTqj-doG65Q"

# ================= 综合伤害最小版 Prompt + 防复读阻断 =================
SYSTEM_PROMPT = """你是一个专业的零售商品价签（Price Tag）视觉识别专家。你的任务是精准检测图片中每个商品独立的真实零售价签，提取其准确零售单价与空间坐标。本任务覆盖货架、地堆/堆头、冰箱冷柜等陈列。

### 扫描与识别原则：
1. 【只检测实体独立价签】：
   - 目标必须是对应具体某一商品的实体价签（含导轨价签条、电子墨水屏、单独贴于商品上的特价贴/爆炸签）。
   - 严禁把牛奶瓶盖、瓶口、包装常规图案文字当成价签。
   - 【严禁识别宣传横幅与自回归复读】：导轨上横跨多款商品的彩色长条宣传围条纸（如“第二件2元”、“买一赠一”宣传带）纯属背景宣传物料，严禁为其分配价签框！【同一排货架严禁对着宣传纸自回归连续复读输出同一种价格】！
2. 【价格真实有效】：只输出能够识别出明确有效零售单价的价签，严禁输出 price 为 null/None 的占位条目。
3. 【层级自然分配】：shelf_layer 按照视觉从上到下的陈列层次自然标记（1, 2, 3...）。依实际情况标记，有几层标记几层，无需强行凑满三层。
4. 【翻转与倒立感知】：冷柜顶层或下层价签若存在 180 度倒插或倒悬，请在视觉上旋转翻正后读取其正向真实价格（例如将倒立的 42.00 读为 42.00，避免误读为 00.24）。
5. 【坐标精度规范】：每个价签的 bbox 必须紧密贴合该实体价签外边缘，统一输出千分比归一化坐标 [xmin, ymin, xmax, ymax]（数值范围 0~1000）。

### 字段定义（标准 JSON 数组）：
- id: 序号（从上到下、从左到右递增）
- shelf_layer: 所在陈列层级（整数 1, 2...）
- bbox: 归一化坐标 [xmin, ymin, xmax, ymax]
- price: 价格数字字符串（如 "9.90"，纯数字保留小数点）
- raw_price_text: 包含单位或符号的原始文字（如 "9.90元"、"￥9.90"）

### 注意事项：
- 只返回标准 JSON 数组，严禁附带额外解释文字。
- 没有价签的区域绝不凭空捏造。
"""

USER_PROMPT = """请全面识别图片中所有真实独立的商品零售价签，准确定位其 bbox 并识别价格。严禁识别跨商品长条宣传横幅纸，严禁机械连续复读同一种价格，对倒置价签翻正后读取，直接返回标准 JSON 数组。"""
# =====================================================================

def load_oss_map() -> dict:
    p = Path(CACHE_OSS_FILE)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_oss_map(mapping: dict):
    p = Path(CACHE_OSS_FILE)
    p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

def upload_to_mengniu_oss(image_path: Path, token: str = DEFAULT_BEARER_TOKEN, retries: int = 3) -> str:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    headers = {"Authorization": token}
    data = {"type": 0}
    for attempt in range(1, retries + 1):
        try:
            with open(image_path, "rb") as f:
                files = {"file": (image_path.name, f, mime_type)}
                resp = requests.post(OSS_UPLOAD_URL, headers=headers, data=data, files=files, timeout=30)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("success") and res.get("data", {}).get("fileUrl"):
                    return res["data"]["fileUrl"]
        except Exception as e:
            print(f"    [OSS上传重试 {attempt}/{retries}] 异常: {e}")
        time.sleep(2)
    return ""

def parse_model_json(text: str):
    if not isinstance(text, str):
        return text
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    try:
        return json.loads(clean_text)
    except Exception:
        start = min([idx for idx in (clean_text.find("{"), clean_text.find("[")) if idx >= 0], default=-1)
        if start >= 0:
            for end in range(len(clean_text) - 1, start, -1):
                if clean_text[end] in "}]":
                    try:
                        return json.loads(clean_text[start:end+1])
                    except Exception:
                        continue
    return clean_text

def cv_imread_utf8(path: str):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None

def cv_imwrite_utf8(path: str, img):
    try:
        ext = os.path.splitext(path)[1]
        _, buf = cv2.imencode(ext, img)
        buf.tofile(path)
        return True
    except Exception as e:
        print(f"[!] 写入文件失败: {e}")
        return False

def resolve_tag_bbox(bbox: list, img_w: int, img_h: int):
    """自适应物理长宽比纠偏 (确保横向长方形，消除电线杆立柱)"""
    if len(bbox) < 4:
        return None
    v0, v1, v2, v3 = bbox[:4]
    
    xmin_A, xmax_A = min(v0, v2), max(v0, v2)
    ymin_A, ymax_A = min(v1, v3), max(v1, v3)
    w_px_A = (xmax_A - xmin_A) / 1000.0 * img_w
    h_px_A = (ymax_A - ymin_A) / 1000.0 * img_h
    
    ymin_B, ymax_B = min(v0, v2), max(v0, v2)
    xmin_B, xmax_B = min(v1, v3), max(v1, v3)
    w_px_B = (xmax_B - xmin_B) / 1000.0 * img_w
    h_px_B = (ymax_B - ymin_B) / 1000.0 * img_h

    if h_px_A > img_h * 0.45 or (h_px_A > w_px_A * 2.5 and w_px_B > h_px_B * 0.5):
        xmin = int(xmin_B / 1000.0 * img_w)
        ymin = int(ymin_B / 1000.0 * img_h)
        xmax = int(xmax_B / 1000.0 * img_w)
        ymax = int(ymax_B / 1000.0 * img_h)
    else:
        xmin = int(xmin_A / 1000.0 * img_w)
        ymin = int(ymin_A / 1000.0 * img_h)
        xmax = int(xmax_A / 1000.0 * img_w)
        ymax = int(ymax_A / 1000.0 * img_h)
        
    ymin = max(0, min(img_h - 1, ymin))
    ymax = max(0, min(img_h - 1, ymax))
    xmin = max(0, min(img_w - 1, xmin))
    xmax = max(0, min(img_w - 1, xmax))
    return xmin, ymin, xmax, ymax

def _compute_iou(b1, b2):
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0

def smart_consecutive_repetition_filter(tags: list) -> list:
    """
    智能连续度熔断算法 (只针对长条促销纸复读机，绝不误杀正常并排同价):
    1. 允许真实同价商品并排摆放 (连续 <= 3 个完全保留)
    2. 只有当在同一层中，完全相同的价格像打字机一样连续紧挨着出现 >= 4 次，
       才判定进入了复读机死循环，自动熔断截断第 4 个及以后的复读假框！
    """
    if not tags:
        return []

    # 按层级分组检查
    layer_groups = {}
    for t in tags:
        l = t.get("shelf_layer", 1)
        layer_groups.setdefault(l, []).append(t)

    cleaned_tags = []
    for layer, ltags in layer_groups.items():
        # 如果整层所有价格 100% 单一且数量 >= 6 (例如凭空画在瓶身上的整层 5.50)，整行虚构熔断抛弃
        distinct_p = {str(t.get("price", "")).strip() for t in ltags}
        if len(ltags) >= 6 and len(distinct_p) == 1:
            print(f"    [!] 触发整行自回归虚构熔断: 层级 {layer} 纯同价 {distinct_p} 数量达 {len(ltags)} 个，整行剔除！")
            continue

        # 连续复读链检测
        filtered_layer = []
        last_price = None
        consecutive_count = 0

        for t in ltags:
            curr_price = str(t.get("price", "")).strip()
            if curr_price == last_price:
                consecutive_count += 1
            else:
                last_price = curr_price
                consecutive_count = 1

            # 阈值：最多允许连续 3 个并排同价商品；第 4 个及以上认定为打字机复读机，予以拦截
            if consecutive_count <= 3:
                filtered_layer.append(t)
            else:
                pass # 拦截多余的复读机
                
        cleaned_tags.extend(filtered_layer)

    # 空间几何 IoU 去重 (过滤物理重叠 > 0.65)
    final_list = []
    for t in cleaned_tags:
        b = t.get("bbox", [])
        coords = resolve_tag_bbox(b, 1000, 1000)
        if not coords:
            continue
        is_dup = False
        for _, existing_coords in final_list:
            if _compute_iou(coords, existing_coords) > 0.65:
                is_dup = True
                break
        if not is_dup:
            final_list.append((t, coords))

    res = [item[0] for item in final_list]
    for idx, t in enumerate(res, 1):
        t["id"] = idx
    return res

def draw_visual_tags_simple(image_path: str, tags: list, output_path: str):
    image = cv_imread_utf8(image_path)
    if image is None:
        print(f"    [!] 无法读取本地图片: {image_path}")
        return
    
    h, w = image.shape[:2]
    
    colors = {
        1: (0, 255, 0),      # Green
        2: (255, 191, 0),    # Deep Sky Blue
        3: (0, 165, 255),    # Orange/Yellow
        4: (255, 0, 255)     # Magenta
    }
    
    for tag in tags:
        tid = tag.get("id", 0)
        layer = tag.get("shelf_layer", 1)
        price = str(tag.get("price", ""))
        bbox = tag.get("bbox", [])
        
        coords = resolve_tag_bbox(bbox, w, h)
        if not coords:
            continue
        xmin, ymin, xmax, ymax = coords
        
        color = colors.get(layer, (0, 255, 0))
        cv2.rectangle(image, (xmin, ymin), (xmax, ymax), color, 3)
        
        label = f"#{tid} {price}"
        text_y = max(ymin - 8, 25)
        cv2.putText(image, label, (xmin, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(image, label, (xmin, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv_imwrite_utf8(output_path, image)

def call_api(image_url: str, api_url: str, timeout: int = 180):
    body = {
        "image": image_url,
        "system_text": SYSTEM_PROMPT,
        "text": USER_PROMPT,
        "userId": "",
        "envSystemName": "",
        "userName": "",
        "envSystemVersion": "",
        "unionId": "",
        "apiVersion": "0.0.2"
    }
    timestamp = str(int(time.time() * 1000))
    sign_str = json.dumps(body) + SECRET + timestamp
    sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()
    headers = {
        "X-MN-APP-ID": APPID,
        "X-MN-SIGN": sign,
        "X-MN-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }
    t0 = time.time()
    resp = requests.post(api_url, headers=headers, json=body, timeout=timeout)
    elapsed = time.time() - t0
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    res_data = resp.json()
    if res_data.get("status") != 0:
        raise RuntimeError(f"{res_data.get('message')}")
    payload = res_data.get("payload", {})
    page_content = payload.get("result", {}).get("page_content", "")
    parsed = parse_model_json(page_content)
    raw_tags = parsed if isinstance(parsed, list) else parsed.get("price_tags", [])
    
    clean_tags = []
    for t in raw_tags:
        p = t.get("price")
        if p is not None and str(p).strip().lower() not in ["none", "null", ""]:
            clean_tags.append(t)
            
    final_tags = smart_consecutive_repetition_filter(clean_tags)
    return final_tags, elapsed

def main():
    parser = argparse.ArgumentParser(description="全图极速高精价签识别 (最小伤害Prompt + 智能连续复读熔断)")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev", help="API环境: dev 或 prod")
    parser.add_argument("--offset", type=int, default=0, help="起始图片索引 (默认0)")
    parser.add_argument("--limit", type=int, default=12, help="处理的图片数量 (默认12张)")
    parser.add_argument("--target-image", default="", help="指定单张图片文件名")
    parser.add_argument("--force", action="store_true", help="强制重新识别，覆盖已有结果")
    parser.add_argument("--redraw-only", action="store_true", help="仅根据已有原生 JSON 重跑过滤与重绘")
    args = parser.parse_args()

    api_url = f"https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version={args.env}"
    img_dir = Path(r"D:\Shixi\mengniu\蒙牛 poc0805_images")
    output_dir = Path(r"D:\Shixi\mengniu\价签识别\output_price_tags")
    vis_dir = output_dir / "vis_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    vis_dir.mkdir(parents=True, exist_ok=True)

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    all_images = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in valid_exts])

    if args.target_image:
        target_images = [p for p in all_images if args.target_image.lower() in p.name.lower()]
    else:
        target_images = all_images[args.offset : args.offset + args.limit]

    print("=" * 80)
    print("=== 开始全图极速高精价签识别（彻底摆脱切片卡顿 + 智能斩断长纸复读机）===")
    print(f"待处理总图数: {len(target_images)} (起始偏移: {args.offset}, 处理数量: {args.limit})")
    print(f"接口地址: {api_url}")
    print("=" * 80)

    oss_map = load_oss_map()

    for idx, img_path in enumerate(target_images, 1):
        img_name = img_path.name
        json_path = output_dir / f"{img_path.stem}.price.json"
        vis_path = vis_dir / f"{img_path.stem}.vis.jpg"

        print(f"\n[{idx}/{len(target_images)}] 处理: {img_name}")

        if args.redraw_only and json_path.exists():
            with open(json_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw_tags = d.get("price_tags", [])
            filtered_tags = smart_consecutive_repetition_filter(raw_tags)
            draw_visual_tags_simple(str(img_path), filtered_tags, str(vis_path))
            print(f"    -> [智能熔断重绘完成]: {vis_path.name} (保留高可信度价签: {len(filtered_tags)} 个)")
            continue

        if not args.force and json_path.exists() and vis_path.exists():
            print(f"    -> [跳过] 已存在结果文件，跳过处理 (如需重跑请加 --force)")
            continue

        image_url = oss_map.get(img_name)
        if not image_url:
            print("    -> 本地映射未找到，正在上传至蒙牛内部 OSS ...")
            image_url = upload_to_mengniu_oss(img_path)
            if image_url:
                oss_map[img_name] = image_url
                save_oss_map(oss_map)
                print(f"    -> 上传成功: {image_url}")
            else:
                print("    [!] OSS 上传失败，跳过该图片")
                continue
        else:
            print(f"    -> 复用已有 OSS 链接: {image_url}")

        try:
            tags, elapsed = call_api(image_url, api_url)
            print(f"    -> 识别成功: 检出有效真实价签 {len(tags)} 个 (耗时 {elapsed:.2f}s)")
            
            draw_visual_tags_simple(str(img_path), tags, str(vis_path))
            print(f"    -> 已生成高质量标注图: {vis_path.name}")
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"image_name": img_name, "total": len(tags), "price_tags": tags}, f, ensure_ascii=False, indent=2)
            print(f"    -> 结果数据已保存: {json_path.name}")
        except Exception as e:
            print(f"    [!] 识别失败: {e}")

    print("\n" + "=" * 80)
    print("全量批量处理圆满完成！")
    print("=" * 80)

if __name__ == "__main__":
    main()
