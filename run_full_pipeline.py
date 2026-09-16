# -*- coding: utf-8 -*-
"""
蒙牛智能巡店全流程一体化处理管线 (Pipeline)
已实现:
- 阶段一: 图像前置质量审核 (模糊/过曝/暗光/损坏/场景分类/价签判定)
- 阶段二: 纯全图高精价签检测与画框 (防自回归连续复读 + 长宽比纠偏)
- 阶段三: [预留标准接口] SKU 识别
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

# ==================== 0. 读取本地独立凭据配置 (解耦敏感信息) ====================
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
EXAMPLE_CONFIG = Path(__file__).resolve().parent / "config.example.json"

if CONFIG_FILE.exists():
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] 警告: 读取 config.json 失败 ({e})，尝试使用默认空模板")
        cfg = {}
else:
    print("[!] 未找到本地 config.json，请参照 config.example.json 创建并填入你的 API 凭证！")
    cfg = {}

QC_CFG = cfg.get("qc_config", {})
PRICE_CFG = cfg.get("price_tag_config", {})
OSS_CFG = cfg.get("oss_config", {})

QC_API_URL = QC_CFG.get("api_url", "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/fbe5e46fcd?version=dev")
QC_APPID = QC_CFG.get("appid", "")
QC_SECRET = QC_CFG.get("secret", "")

PRICE_API_URL = PRICE_CFG.get("api_url", "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version=dev")
PRICE_APPID = PRICE_CFG.get("appid", "")
PRICE_SECRET = PRICE_CFG.get("secret", "")

OSS_UPLOAD_URL = OSS_CFG.get("upload_url", "https://aism.mengniu.cn/brcapi/oss/api/oss/getFileUrl")
DEFAULT_BEARER_TOKEN = OSS_CFG.get("bearer_token", "")
CACHE_OSS_FILE = Path(__file__).resolve().parent / "价签识别" / "oss_image_map.json"

# ==================== 1. 质量审核智能体提示词 ====================
QC_PROMPT_FILE = Path(__file__).resolve().parent / "质量审核" / "prompt_text.txt"
if QC_PROMPT_FILE.exists():
    QC_SYSTEM_PROMPT = QC_PROMPT_FILE.read_text(encoding="utf-8")
else:
    QC_SYSTEM_PROMPT = "你是一个专业的零售巡店图片质检与场景识别专家，请审核图片质量、识别陈列场景并判断是否包含价签。"

QC_USER_PROMPT = "请严格审核该图片的质量、识别陈列场景并判断是否包含价签，直接输出标准 JSON。"

# ==================== 2. 价签识别智能体提示词 ====================
PRICE_SYSTEM_PROMPT = """你是一个专业的零售商品价签（Price Tag）视觉识别专家。你的任务是精准检测图片中每个商品独立的真实零售价签，提取其准确零售单价与空间坐标。本任务覆盖货架、地堆/堆头、冰箱冷柜等陈列。

### 扫描与识别原则：
1. 【只检测实体独立价签】：目标必须是对应具体某一商品的实体价签（含导轨价签条、电子墨水屏、单独贴于商品上的特价贴/爆炸签）。严禁识别宣传长横幅，严禁连续机械复读。
2. 【价格真实有效】：只输出能够识别出明确有效零售单价的价签，严禁输出 price 为 null/None 的占位条目。
3. 【层级自然分配】：shelf_layer 按照视觉从上到下的陈列层次自然标记（1, 2, 3...）。
4. 【翻转与倒立感知】：冷柜顶层或下层价签若存在 180 度倒插或倒悬，请翻正后读取其正向价格。
5. 【坐标精度规范】：每个价签的 bbox 必须紧密贴合外边缘，格式为 [xmin, ymin, xmax, ymax]（0~1000）。

### 字段定义（标准 JSON 数组）：
- id: 序号（1, 2, 3...）
- shelf_layer: 陈列层级（1, 2...）
- bbox: 归一化坐标 [xmin, ymin, xmax, ymax]
- price: 价格数字字符串（如 "9.90"）
- raw_price_text: 原始文字
"""

PRICE_USER_PROMPT = """请识别图中所有真实有效的商品零售价签，准确定位 bbox 并识别价格。严禁机械连续复读同一种价格，无有效价格区域不输出，直接返回标准 JSON 数组。"""

# ==================== 3. 基础通用工具 ====================
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
    if not token:
        print("    [!] 未配置 OSS bearer_token，请在 config.json 中配置！")
        return ""
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

def make_mn_sign(body: dict, secret: str, timestamp: str) -> str:
    sign_str = json.dumps(body) + secret + timestamp
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest()

def parse_robust_json(text: str):
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

# ==================== 4. 阶段一：质量审核执行器 ====================
def run_quality_check(image_url: str, timeout: int = 60) -> dict:
    body = {
        "target_sku_img": image_url,
        "image": image_url,
        "system_text": QC_SYSTEM_PROMPT,
        "text": QC_USER_PROMPT,
        "userId": "",
        "envSystemName": "",
        "userName": "",
        "envSystemVersion": "",
        "unionId": "",
        "apiVersion": "0.0.2"
    }
    timestamp = str(int(time.time() * 1000))
    headers = {
        "X-MN-APP-ID": QC_APPID,
        "X-MN-SIGN": make_mn_sign(body, QC_SECRET, timestamp),
        "X-MN-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }
    t0 = time.time()
    resp = requests.post(QC_API_URL, headers=headers, json=body, timeout=timeout)
    elapsed = time.time() - t0
    if resp.status_code != 200:
        raise RuntimeError(f"质量审核 HTTP 异常: {resp.status_code}")
    res_data = resp.json()
    if res_data.get("status") != 0:
        raise RuntimeError(f"质量审核业务错误: {res_data.get('message')}")
        
    page_content = res_data.get("payload", {}).get("result", {}).get("page_content", "")
    parsed = parse_robust_json(page_content)
    if not isinstance(parsed, dict):
        parsed = {"raw": page_content}
    parsed["elapsed_sec"] = round(elapsed, 2)
    return parsed

# ==================== 5. 阶段二：价签识别执行器 ====================
def resolve_tag_bbox(bbox: list, img_w: int, img_h: int):
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
    if not tags:
        return []
    layer_groups = {}
    for t in tags:
        l = t.get("shelf_layer", 1)
        layer_groups.setdefault(l, []).append(t)

    cleaned_tags = []
    for layer, ltags in layer_groups.items():
        distinct_p = {str(t.get("price", "")).strip() for t in ltags}
        if len(ltags) >= 6 and len(distinct_p) == 1:
            print(f"        [!] 触发整行自回归虚构熔断: 层级 {layer} 纯同价 {distinct_p} 数量达 {len(ltags)} 个，整行剔除！")
            continue

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
            if consecutive_count <= 3:
                filtered_layer.append(t)
        cleaned_tags.extend(filtered_layer)

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

def run_price_tag_detection(image_url: str, timeout: int = 180):
    body = {
        "image": image_url,
        "system_text": PRICE_SYSTEM_PROMPT,
        "text": PRICE_USER_PROMPT,
        "userId": "",
        "envSystemName": "",
        "userName": "",
        "envSystemVersion": "",
        "unionId": "",
        "apiVersion": "0.0.2"
    }
    timestamp = str(int(time.time() * 1000))
    headers = {
        "X-MN-APP-ID": PRICE_APPID,
        "X-MN-SIGN": make_mn_sign(body, PRICE_SECRET, timestamp),
        "X-MN-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }
    t0 = time.time()
    resp = requests.post(PRICE_API_URL, headers=headers, json=body, timeout=timeout)
    elapsed = time.time() - t0
    if resp.status_code != 200:
        raise RuntimeError(f"价签识别 HTTP 异常: {resp.status_code}")
    res_data = resp.json()
    if res_data.get("status") != 0:
        raise RuntimeError(f"价签识别业务错误: {res_data.get('message')}")
    payload = res_data.get("payload", {})
    page_content = payload.get("result", {}).get("page_content", "")
    parsed = parse_robust_json(page_content)
    raw_tags = parsed if isinstance(parsed, list) else parsed.get("price_tags", [])
    clean_tags = [t for t in raw_tags if t.get("price") and str(t["price"]).strip().lower() not in ["none", "null", ""]]
    final_tags = smart_consecutive_repetition_filter(clean_tags)
    return final_tags, elapsed

def draw_visual_tags_simple(image_path: str, tags: list, output_path: str):
    image = cv_imread_utf8(image_path)
    if image is None:
        return
    h, w = image.shape[:2]
    colors = {1: (0, 255, 0), 2: (255, 191, 0), 3: (0, 165, 255), 4: (255, 0, 255)}
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

# ==================== 6. 阶段三：[预留扩展接口] SKU 识别 ====================
def run_sku_recognition(image_url: str, price_tags: list = None, **kwargs) -> dict:
    """
    【预留标准扩展接口】SKU 商品品类与陈列识别
    入参:
        - image_url: 图片在 OSS 上的公网链接
        - price_tags: 已识别出的价签列表 (后续可用于将价格与SKU空间位置精准对齐匹配)
    """
    return {
        "status": "pending_implementation",
        "message": "SKU 识别接口已预留，待后端模型就绪后随时无缝接入",
        "sku_items": []
    }

# ==================== 7. 全流程总控调度 (Main Pipeline) ====================
def process_single_image(img_path: Path, oss_map: dict, output_base_dir: Path, force: bool = False):
    img_name = img_path.name
    qc_dir = output_base_dir / "qc_results"
    vis_dir = output_base_dir / "vis_images"
    final_json_dir = output_base_dir / "final_results"
    
    for d in [qc_dir, vis_dir, final_json_dir]:
        d.mkdir(parents=True, exist_ok=True)

    final_report_file = final_json_dir / f"{img_path.stem}.pipeline.json"
    if not force and final_report_file.exists():
        print(f"    -> [跳过] 已存在完整全流程报告: {final_report_file.name}")
        return

    # 1. 确保 OSS 链接有效
    image_url = oss_map.get(img_name)
    if not image_url:
        print("    -> 本地映射缺失，正在上传至蒙牛内部 OSS ...")
        image_url = upload_to_mengniu_oss(img_path)
        if image_url:
            oss_map[img_name] = image_url
            save_oss_map(oss_map)
            print(f"    -> 上传成功: {image_url}")
        else:
            print("    [!] 上传失败，跳过该图")
            return
    else:
        print(f"    -> 复用已有 OSS: {image_url}")

    pipeline_record = {
        "image_name": img_name,
        "image_url": image_url,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step1_qc": None,
        "step2_price_tags": None,
        "step3_sku": None
    }

    # ================= 步骤 1: 质量审核预检 =================
    print("    -> [Step 1] 正在进行图片质量预检与场景识别 ...")
    try:
        qc_output = run_quality_check(image_url)
        pipeline_record["step1_qc"] = qc_output
        
        qc_res = qc_output.get("qc_result", {})
        is_valid = qc_res.get("is_valid", False)
        invalid_reason = qc_res.get("invalid_reason", "")
        
        content_info = qc_output.get("content_info", {})
        has_price_tag = content_info.get("has_price_tag", True)
        scene_type = content_info.get("scene_type", "未识别")
        
        print(f"       质检状态: {'通过' if is_valid else '未通过'} | 场景类型: {scene_type} | 包含价签: {has_price_tag}")
        if not is_valid:
            print(f"       不合格原因: {invalid_reason}")
            
    except Exception as e:
        print(f"    [!] 质量审核执行异常: {e}")
        pipeline_record["step1_qc"] = {"error": str(e)}
        is_valid, has_price_tag = False, False

    # ================= 步骤 2: 准入判定与条件分流 =================
    if not is_valid:
        print("    -> [拦截] 图片质量不达标，提前终止后续流程，节省模型算力。")
    elif not has_price_tag:
        print("    -> [跳过] 图片质量合格，但判定画面内无商品价签，无需执行价签识别。")
    else:
        # ================= 步骤 3: 价签检测识别 =================
        print("    -> [Step 2] 质检通过且包含价签，正在执行高精价签检测 ...")
        try:
            tags, elapsed = run_price_tag_detection(image_url)
            pipeline_record["step2_price_tags"] = {
                "total_tags": len(tags),
                "elapsed_sec": round(elapsed, 2),
                "tags": tags
            }
            print(f"       价签识别成功: 共识别到 {len(tags)} 个有效价签 (耗时 {elapsed:.2f}s)")
            
            vis_path = vis_dir / f"{img_path.stem}.vis.jpg"
            draw_visual_tags_simple(str(img_path), tags, str(vis_path))
            print(f"       已生成可视化标注图: {vis_path.name}")
        except Exception as e:
            print(f"    [!] 价签识别异常: {e}")
            pipeline_record["step2_price_tags"] = {"error": str(e)}

    # ================= 步骤 4: [预留] SKU 商品识别 =================
    pipeline_record["step3_sku"] = run_sku_recognition(
        image_url=image_url, 
        price_tags=pipeline_record.get("step2_price_tags", {}).get("tags", [])
    )

    with open(final_report_file, "w", encoding="utf-8") as f:
        json.dump(pipeline_record, f, ensure_ascii=False, indent=2)
    print(f"    -> [完成] 全流程报告已沉淀: {final_report_file.name}")

def main():
    parser = argparse.ArgumentParser(description="蒙牛智能巡店全流程一体化管线 (质量预检 -> 价签识别 -> 预留SKU)")
    parser.add_argument("--offset", type=int, default=0, help="起始图片索引")
    parser.add_argument("--limit", type=int, default=12, help="批量处理数量")
    parser.add_argument("--target-image", default="", help="指定单张图片精确跑")
    parser.add_argument("--force", action="store_true", help="强制重新执行，覆盖已有全流程报告")
    args = parser.parse_args()

    img_dir = Path(r"D:\Shixi\mengniu\蒙牛 poc0805_images")
    output_dir = Path(r"D:\Shixi\mengniu\全流程运行结果")
    output_dir.mkdir(parents=True, exist_ok=True)

    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    all_images = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in valid_exts])

    if args.target_image:
        target_images = [p for p in all_images if args.target_image.lower() in p.name.lower()]
    else:
        target_images = all_images[args.offset : args.offset + args.limit]

    print("=" * 80)
    print("=== 蒙牛巡店智能全流程 Pipeline（质量审核 -> 价签识别 -> 预留SKU）===")
    print(f"图片目录: {img_dir}")
    print(f"输出目录: {output_dir}")
    print(f"待跑图片数: {len(target_images)} 张 (偏移: {args.offset}, 数量: {args.limit})")
    print("=" * 80)

    oss_map = load_oss_map()

    for idx, img_path in enumerate(target_images, 1):
        print(f"\n[{idx}/{len(target_images)}] 处理图片: {img_path.name}")
        process_single_image(img_path, oss_map, output_dir, force=args.force)

    print("\n" + "=" * 80)
    print("🎉 全流程批量处理圆满结束！结果已全部保存至: " + str(output_dir))
    print("=" * 80)

if __name__ == "__main__":
    main()
