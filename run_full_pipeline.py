# -*- coding: utf-8 -*-
"""
蒙牛智能巡店全流程一体化处理管线 (Pipeline - 稳定克制版)
已精准调整:
1. 【彻底删去】'地毯式慢速逐格扫描'等过度发散形容词 (防止CT扫描式瓶身切片幻觉)
2. 【严格保留】'严禁合并整排导轨大框'核心铁律 (确保单品独立小框切分)
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

# ==================== 0. 读取本地独立凭据配置 ====================
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

if CONFIG_FILE.exists():
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] 警告: 读取 config.json 失败 ({e})")
        cfg = {}
else:
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

# ==================== 1. 质量审核提示词 ====================
QC_PROMPT_FILE = Path(__file__).resolve().parent / "质量审核" / "prompt_text.txt"
if QC_PROMPT_FILE.exists():
    QC_SYSTEM_PROMPT = QC_PROMPT_FILE.read_text(encoding="utf-8")
else:
    QC_SYSTEM_PROMPT = "你是一个专业的零售巡店图片质检与场景识别专家，请审核图片质量、识别陈列场景并判断是否包含价签。"

QC_USER_PROMPT = "请严格审核该图片的质量、识别陈列场景并判断是否包含价签，直接输出标准 JSON。"

# ==================== 2. 价签识别提示词 (克制稳定版: 保留严禁合并大框) ====================
PRICE_SYSTEM_PROMPT = """你是一个专业的零售商品价签（Price Tag）视觉识别专家。你的任务是精准检测图片中每个商品独立的真实零售价签，提取其准确零售单价与空间坐标。本任务覆盖货架、地堆/堆头、冰箱冷柜等陈列。

### 扫描与识别原则（严格执行）：
1. 【严禁合并大框，必须拆分单品独立小框】：
   - 即便一整排陈列的都是同款同价商品（如一整排 3.90 元），【严禁将整排导轨合并画成一个跨越全图的通栏大长框】！
   - 对已经确认有价格文字或电子价签屏的实体，必须按实际物理标签卡拆分；但不能仅凭商品排列、空白导轨或固定间距补造标签卡。
   - 每个 bbox 必须紧贴实际可见的价签实体和价格文字，不能框住商品包装。
2. 【只检测实体独立价签，严禁机械连续复读】：
   - 目标必须是对应具体商品的实体价签（含导轨价签条、电子墨水屏、单独贴于商品上的特价贴/爆炸签）。
   - 严禁把牛奶瓶盖、瓶口、包装常规图案文字当成价签。
   - 长横幅、空白导轨和普通宣传物料不能作为普通价签输出，也不能按横幅覆盖的每个商品重复输出。
3. 【价格真实有效】：只输出能够识别出明确有效零售单价的价签，严禁输出 price 为 null/None 的占位条目。
4. 【层级自然分配】：shelf_layer 按照视觉从上到下的陈列层次自然标记（1, 2, 3...）。
5. 【翻转与倒立感知】：冷柜顶层或下层价签若存在 180 度倒插或倒悬，请翻正后读取其正向价格（例如 42.00 严禁读成 00.24）。
6. 【坐标精度规范】：每个价签的 bbox 必须紧密贴合外边缘，格式统一为 [xmin, ymin, xmax, ymax]（数值范围 0~1000）。
7. 【第二件促销单独标记】：可见促销签明确写有“第二件X元”“第2件X元”等字样时，每张促销签只输出一条记录，`tag_type` 填 `second_item_promotion`，`second_item_price` 填 X；严禁按其覆盖的商品数重复输出。

### 字段定义（标准 JSON 数组）：
- id: 序号（1, 2, 3...）
- shelf_layer: 陈列层级（1, 2...）
- bbox: 归一化坐标 [xmin, ymin, xmax, ymax]
- price: 价格数字字符串（如 "9.90"，纯数字保留小数点；第二件促销时填第二件价格）
- raw_price_text: 包含单位或符号的原始文字（如 "9.90元"、"第二件2元"）
- tag_type: `regular_price`（默认）或 `second_item_promotion`
- second_item_price: 仅 `second_item_promotion` 填写第二件价格（如 "2.00"），普通价签省略

### 注意事项：
- 只返回标准 JSON 数组，严禁附带额外解释文字。
- 价格和第二件价格均只保留数字与小数点；无法看清文字或金额时不要猜测、不要输出。
- 没有价签的区域绝不凭空捏造。
"""

PRICE_USER_PROMPT = """请识别图中所有真实独立的商品零售价签，准确定位其 bbox 并识别价格。先确认标签实体和可见价格文字，禁止根据商品排列、空白导轨或固定间隔补造价签；真实相邻标签仍须拆分，严禁合并通栏大框。明确写有“第二件X元”或“第2件X元”的可见促销签每张只输出一次，tag_type 标为 second_item_promotion 并填写 second_item_price。直接返回标准 JSON 数组。"""

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
        print("    [!] 未配置 OSS bearer_token！")
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

    # 过滤过大的通栏大长框 (代码级硬防御: 宽度超过全图 60% 判定为错误合并大框，丢弃)
    valid_size_tags = []
    for t in cleaned_tags:
        b = t.get("bbox", [])
        if len(b) >= 4:
            span_x = abs(b[2] - b[0]) if abs(b[2] - b[0]) > abs(b[3] - b[1]) else abs(b[3] - b[1])
            if span_x > 600:
                print(f"        [!] 拦截异常通栏超宽大框: bbox={b}, 价格={t.get('price')}")
                continue
        valid_size_tags.append(t)

    final_list = []
    for t in valid_size_tags:
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


SECOND_ITEM_PROMOTION_RE = re.compile(r"第\s*(?:二|2)\s*件")
SECOND_ITEM_PRICE_RE = re.compile(
    r"第\s*(?:二|2)\s*件\D{0,8}(\d+(?:\.\d{1,2})?)\s*(?:元|块|￥|RMB)?",
    re.IGNORECASE,
)


def _promotion_text(tag: dict) -> str:
    fields = (
        "raw_promotion_text",
        "promotion_text",
        "raw_price_text",
        "label_text",
        "text",
    )
    return " ".join(str(tag.get(field, "")).strip() for field in fields).strip()


def is_second_item_promotion(tag: dict) -> bool:
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").strip().lower()
    return tag_type in ("second_item_price", "second_item_promotion") or bool(SECOND_ITEM_PROMOTION_RE.search(_promotion_text(tag)))


def normalize_second_item_promotion(tag: dict) -> dict:
    """Normalize a model promotion record into a stable post-processing contract."""
    normalized = dict(tag)
    raw_text = _promotion_text(normalized)
    price = str(normalized.get("second_item_price") or "").strip()
    if not price:
        matched = SECOND_ITEM_PRICE_RE.search(raw_text)
        price = matched.group(1) if matched else str(normalized.get("price") or "").strip()
    normalized["promotion_type"] = "second_item_price"
    normalized["second_item_price"] = price
    normalized["raw_promotion_text"] = raw_text
    normalized.pop("price", None)
    normalized.pop("raw_price_text", None)
    return normalized


def split_price_and_promotion_tags(parsed) -> tuple[list, list]:
    """Accept the legacy array format while separating second-item promotions."""
    if isinstance(parsed, list):
        raw_price_tags, raw_promotion_tags = parsed, []
    elif isinstance(parsed, dict):
        raw_price_tags = parsed.get("price_tags", [])
        raw_promotion_tags = parsed.get("promotion_tags", [])
    else:
        raw_price_tags, raw_promotion_tags = [], []

    price_tags = []
    promotion_tags = []
    for tag in raw_price_tags if isinstance(raw_price_tags, list) else []:
        if not isinstance(tag, dict):
            continue
        if is_second_item_promotion(tag):
            promotion_tags.append(normalize_second_item_promotion(tag))
        else:
            price_tags.append(tag)
    for tag in raw_promotion_tags if isinstance(raw_promotion_tags, list) else []:
        if isinstance(tag, dict):
            promotion_tags.append(normalize_second_item_promotion(tag))
    return price_tags, promotion_tags


def valid_promotion_tags(tags: list) -> list:
    result = []
    for tag in tags:
        bbox = tag.get("bbox", [])
        price = str(tag.get("second_item_price", "")).strip().lower()
        if len(bbox) >= 4 and price not in ("", "none", "null"):
            result.append(tag)
    for idx, tag in enumerate(result, 1):
        tag["id"] = idx
    return result

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
    raw_tags, raw_promotion_tags = split_price_and_promotion_tags(parsed)
    clean_tags = [t for t in raw_tags if t.get("price") and str(t["price"]).strip().lower() not in ["none", "null", ""]]
    final_tags = smart_consecutive_repetition_filter(clean_tags)
    promotion_tags = valid_promotion_tags(raw_promotion_tags)
    return {"price_tags": final_tags, "promotion_tags": promotion_tags}, elapsed

def draw_visual_tags_simple(image_path: str, tags: list, output_path: str, promotion_tags: list = None):
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
    for tag in promotion_tags or []:
        bbox = tag.get("bbox", [])
        coords = resolve_tag_bbox(bbox, w, h)
        if not coords:
            continue
        xmin, ymin, xmax, ymax = coords
        color = (255, 0, 255)
        label = f"P{tag.get('id', 0)} SECOND {tag.get('second_item_price', '')}"
        text_y = max(ymin - 8, 25)
        cv2.rectangle(image, (xmin, ymin), (xmax, ymax), color, 3)
        cv2.putText(image, label, (xmin, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(image, label, (xmin, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv_imwrite_utf8(output_path, image)

def run_sku_recognition(image_url: str, price_tags: list = None, **kwargs) -> dict:
    return {
        "status": "pending_implementation",
        "message": "SKU 识别接口已预留，待后端模型就绪后随时无缝接入",
        "sku_items": []
    }

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

    if not is_valid:
        print("    -> [拦截] 图片质量不达标，提前终止后续流程。")
    elif not has_price_tag:
        print("    -> [跳过] 画面内判定无商品价签，无需执行价签识别。")
    else:
        print("    -> [Step 2] 质检通过且包含价签，正在执行价签单品检测 ...")
        try:
            detection_result, elapsed = run_price_tag_detection(image_url)
            tags = detection_result["price_tags"]
            promotion_tags = detection_result["promotion_tags"]
            pipeline_record["step2_price_tags"] = {
                "total_tags": len(tags),
                "total_promotion_tags": len(promotion_tags),
                "elapsed_sec": round(elapsed, 2),
                "tags": tags,
                "promotion_tags": promotion_tags,
            }
            print(f"       价签识别成功: 共识别到 {len(tags)} 个有效单品价签 (耗时 {elapsed:.2f}s)")
            if promotion_tags:
                print(f"       已单独标记 {len(promotion_tags)} 个第二件价格促销签")
            vis_path = vis_dir / f"{img_path.stem}.vis.jpg"
            draw_visual_tags_simple(str(img_path), tags, str(vis_path), promotion_tags)
            print(f"       已生成高质量标注图: {vis_path.name}")
        except Exception as e:
            print(f"    [!] 价签识别异常: {e}")
            pipeline_record["step2_price_tags"] = {"error": str(e)}

    pipeline_record["step3_sku"] = run_sku_recognition(
        image_url=image_url, 
        price_tags=pipeline_record.get("step2_price_tags", {}).get("tags", [])
    )

    with open(final_report_file, "w", encoding="utf-8") as f:
        json.dump(pipeline_record, f, ensure_ascii=False, indent=2)
    print(f"    -> [完成] 全流程报告已沉淀: {final_report_file.name}")

def main():
    parser = argparse.ArgumentParser(description="蒙牛智能巡店全流程一体化管线 (严禁大框版)")
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
    print("=== 蒙牛巡店全流程管线 (严禁合并大框 + 彻底剔除过度扫描) ===")
    print(f"待跑图片数: {len(target_images)} 张")
    print("=" * 80)

    oss_map = load_oss_map()

    for idx, img_path in enumerate(target_images, 1):
        print(f"\n[{idx}/{len(target_images)}] 处理图片: {img_path.name}")
        process_single_image(img_path, oss_map, output_dir, force=args.force)

    print("\n" + "=" * 80)
    print("🎉 全流程处理圆满结束！结果保存至: " + str(output_dir))
    print("=" * 80)

if __name__ == "__main__":
    main()
