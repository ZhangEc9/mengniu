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

# ==================== 1. 提示词加载接口 (提示词与代码完全分离) ====================
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

def load_prompt(filename: str, default: str = "") -> str:
    """从 prompts/ 目录动态读取外部提示词文件，支持用户随时直接在文本文件中修改并热更新。"""
    target = PROMPTS_DIR / filename
    if target.is_file():
        try:
            content = target.read_text(encoding="utf-8").strip()
            if content:
                return content
        except Exception as e:
            print(f"    [!] 读取提示词文件 {filename} 失败: {e}，使用默认兜底")
    return default.strip()

def get_quality_prompts() -> tuple[str, str]:
    sys_p = load_prompt("quality_system_prompt.txt", "你是一个专业的蒙牛巡店图片质检与场景识别专家，请对图片进行质检，识别陈列场景，判断是否包含价签。")
    usr_p = load_prompt("quality_user_prompt.txt", "请评估图片质量与场景，输出标准 JSON。")
    return sys_p, usr_p

def get_price_prompts() -> tuple[str, str]:
    sys_p = load_prompt("price_system_prompt.txt", "你是一个专业的零售商品价签（Price Tag）视觉识别专家。请识别图中所有真实独立的商品零售价签。")
    usr_p = load_prompt("price_user_prompt.txt", "请识别图中所有真实独立的商品零售价签，准确定位其 bbox 并识别价格。直接返回标准 JSON 数组。")
    return sys_p, usr_p

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
            is_array = (clean_text[start] == "[")
            for end in range(len(clean_text) - 1, start, -1):
                char = clean_text[end]
                if char in "}]":
                    candidates = [clean_text[start:end+1]]
                    if is_array and char == "}":
                        candidates.append(clean_text[start:end+1] + "\n]")
                    for cand in candidates:
                        try:
                            return json.loads(cand)
                        except Exception:
                            continue
    return clean_text

def coerce_bool(value, default: bool = False) -> bool:
    """将模型可能返回的布尔值/字符串安全转换为 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "是", "有", "合格"}:
            return True
        if normalized in {"false", "0", "no", "n", "否", "无", "不合格"}:
            return False
    return default

# 质量四项：名称与 prompts/quality_system_prompt.txt 严格保持一致
QC_QUALITY_KEYS = ["图片模糊", "过度曝光", "光线不足", "文件损坏"]

# 兼容模型返回的历史别名（模型偶尔输出旧版字段名）
QC_QUALITY_KEY_ALIASES = {"严重过曝": "过度曝光"}

def normalize_quality_checks(quality_checks) -> dict:
    """将模型返回的质量项键名归一化为 prompts/quality_system_prompt.txt 中的标准名称。"""
    normalized = {}
    if not isinstance(quality_checks, dict):
        return normalized
    for raw_key, raw_val in quality_checks.items():
        key = str(raw_key).strip()
        key = QC_QUALITY_KEY_ALIASES.get(key, key)
        if key not in normalized:
            normalized[key] = raw_val
    return normalized

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
    qc_sys, qc_usr = get_quality_prompts()
    body = {
        "target_sku_img": image_url,
        "image": image_url,
        "system_text": qc_sys,
        "text": qc_usr,
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
from price_postprocess import (
    resolve_tag_bbox,
    split_price_and_promotion_tags,
    smart_consecutive_repetition_filter,
    valid_promotion_tags,
)

def run_price_tag_detection(image_url: str, img_w: int = 1000, img_h: int = 1000, timeout: int = 360):
    price_sys, price_usr = get_price_prompts()
    body = {
        "image": image_url,
        "system_text": price_sys,
        "text": price_usr,
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
    resp = None
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(PRICE_API_URL, headers=headers, json=body, timeout=timeout)
            if resp.status_code == 200:
                break
            print(f"    [!] 价签识别第 {attempt} 次请求返回状态码 {resp.status_code}，准备重试...")
        except Exception as err:
            if attempt == max_retries:
                raise RuntimeError(f"价签识别网络异常（重试已达上限）: {err}")
            print(f"    [!] 价签识别第 {attempt} 次网络超时/抖动，正在重试: {err}")
            time.sleep(2)
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
    final_tags = smart_consecutive_repetition_filter(clean_tags, img_w=img_w, img_h=img_h)
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
        if tag.get("promotion_type") == "bundle_promotion":
            label = f"P{tag.get('id', 0)} {tag.get('bundle_quantity', 2)}PCS {tag.get('bundle_price', '')}"
        else:
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
    qc_file = qc_dir / f"{img_path.stem}.qc.json"
    try:
        qc_output = run_quality_check(image_url)
        pipeline_record["step1_qc"] = qc_output
        qc_res = qc_output.get("qc_result", {})
        
        # 1. 质量四项严格校验（必须全部合格）
        quality_checks = normalize_quality_checks(qc_res.get("quality_checks", {}))
        required_keys = QC_QUALITY_KEYS
        is_quality_pass = all(
            str(quality_checks.get(k, "")).strip() == "合格" for k in required_keys
        ) and coerce_bool(qc_res.get("is_valid"), False)
        invalid_reason = qc_res.get("invalid_reason", "")

        # 2. 场景类型与价签判定
        content_info = qc_output.get("content_info", {})
        has_price_tag = coerce_bool(
            content_info.get("has_price_tag") if isinstance(content_info, dict) else None,
            False,
        )
        scene_type = str(content_info.get("scene_type", "未识别")).strip()
        valid_scene_keywords = ["货架", "冰箱", "堆头", "冷柜", "地堆"]
        is_scene_valid = any(kw in scene_type for kw in valid_scene_keywords)

        # 综合准入判断：质量合格 AND 识别出价签 AND 场景满足
        can_proceed_to_price = is_quality_pass and is_scene_valid and has_price_tag

        # 3. 输出独立质检 JSON 文件
        rejection_reasons = []
        if not is_quality_pass:
            reason_desc = invalid_reason if invalid_reason else "存在不合格检测项"
            rejection_reasons.append(f"质量不合格: {reason_desc}")
        if not is_scene_valid:
            rejection_reasons.append(f"非目标巡店场景: {scene_type} (仅限货架照/冰箱照/堆头照)")
        if not has_price_tag:
            rejection_reasons.append("画面内未检出有效商品价签")

        qc_record = {
            "image_name": img_name,
            "image_url": image_url,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "qc_result": qc_res,
            "content_info": content_info,
            "decision": {
                "is_quality_pass": is_quality_pass,
                "is_scene_valid": is_scene_valid,
                "scene_type": scene_type,
                "has_price_tag": has_price_tag,
                "can_proceed_to_price_tag": can_proceed_to_price,
                "rejection_reasons": rejection_reasons
            }
        }

        with open(qc_file, "w", encoding="utf-8") as qf:
            json.dump(qc_record, qf, ensure_ascii=False, indent=2)
        print(f"       [质检单出] 独立质检报告已沉淀: {qc_file.name}")
        status_str = "通过" if is_quality_pass else "未通过"
        scene_str = "合规" if is_scene_valid else "非目标场景"
        print(f"       质检状态: {status_str} | 场景: {scene_type} ({scene_str}) | 包含价签: {has_price_tag}")
        if not is_quality_pass:
            print(f"       不合格原因: {invalid_reason}")
    except Exception as e:
        print(f"    [!] 质量审核执行异常: {e}")
        pipeline_record["step1_qc"] = {"error": str(e)}
        is_quality_pass, has_price_tag, is_scene_valid = False, False, False
        qc_record = {
            "image_name": img_name,
            "image_url": image_url,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e),
            "decision": {"can_proceed_to_price_tag": False, "rejection_reasons": [str(e)]}
        }
        with open(qc_file, "w", encoding="utf-8") as qf:
            json.dump(qc_record, qf, ensure_ascii=False, indent=2)

    # 4. 严格三道门槛拦截流转
    if not is_quality_pass:
        print("    -> [拦截并跳过] 图片质量不达标，跳过此图，继续下一张。")
        pipeline_record["status"] = "skipped_invalid_quality"
        with open(final_report_file, "w", encoding="utf-8") as f:
            json.dump(pipeline_record, f, ensure_ascii=False, indent=2)
        print(f"    -> [标记完成] 全流程报告已沉淀: {final_report_file.name}")
        return

    if not is_scene_valid:
        print(f"    -> [拦截并跳过] 场景类型不符合要求 ({scene_type})，仅支持货架/冰箱/堆头，跳过此图。")
        pipeline_record["status"] = "skipped_invalid_scene"
        with open(final_report_file, "w", encoding="utf-8") as f:
            json.dump(pipeline_record, f, ensure_ascii=False, indent=2)
        print(f"    -> [标记完成] 全流程报告已沉淀: {final_report_file.name}")
        return

    if not has_price_tag:
        print("    -> [跳过] 画面内判定无有效商品价签，跳过此图，继续下一张。")
        pipeline_record["status"] = "skipped_no_price_tag"
        with open(final_report_file, "w", encoding="utf-8") as f:
            json.dump(pipeline_record, f, ensure_ascii=False, indent=2)
        print(f"    -> [标记完成] 全流程报告已沉淀: {final_report_file.name}")
        return
    
    img_cv = cv_imread_utf8(str(img_path))
    img_h, img_w = img_cv.shape[:2] if img_cv is not None else (1000, 1000)

    print("    -> [Step 2] 质检通过且包含价签，正在执行价签单品检测 ...")
    try:
        detection_result, elapsed = run_price_tag_detection(image_url, img_w=img_w, img_h=img_h)
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
        price_tags=(pipeline_record.get("step2_price_tags") or {}).get("tags", [])
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
    parser.add_argument("--img-dir", default="", help="specify image dir")
    args = parser.parse_args()

    img_dir = Path(args.img_dir) if args.img_dir else Path(r"D:\Shixi\mengniu\蒙牛 poc0805_images")
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
        try:
            process_single_image(img_path, oss_map, output_dir, force=args.force)
        except Exception as e:
            print(f"    [!] 单图处理异常已捕获，跳过继续下一张: {e}")

    print("\n" + "=" * 80)
    print("全流程处理完成，结果保存至: " + str(output_dir))
    print("=" * 80)

if __name__ == "__main__":
    main()
