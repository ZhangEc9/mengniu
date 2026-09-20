# -*- coding: utf-8 -*-
"""
读取 Excel 表格中前 100 张图片 URL 进行两阶段全流程流水线执行脚本
"""

import os
import sys
import json
import time
import requests
import pandas as pd
from pathlib import Path
from PIL import Image

from run_full_pipeline import (
    run_quality_check,
    run_price_tag_detection,
    coerce_bool,
    draw_visual_tags_simple
)

EXCEL_PATH = Path(r"D:/Shixi/mengniu/2026-09-18-14-38-17_EXPORT_XLSX_27962701_827/2026-09-18-14-38-17_EXPORT_XLSX_27962701_917_0.xlsx")
OUTPUT_BASE = Path(r"D:/Shixi/mengniu/Excel_前100张识别结果")

REJECTED_DIR = OUTPUT_BASE / "1_质量不合格"
ACCEPTED_DIR = OUTPUT_BASE / "2_质量合格_价签识别"

REJECTED_DIR.mkdir(parents=True, exist_ok=True)
ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)

(ACCEPTED_DIR / "qc_results").mkdir(parents=True, exist_ok=True)
(ACCEPTED_DIR / "final_results").mkdir(parents=True, exist_ok=True)
(ACCEPTED_DIR / "vis_images").mkdir(parents=True, exist_ok=True)
(ACCEPTED_DIR / "images").mkdir(parents=True, exist_ok=True)
(REJECTED_DIR / "images").mkdir(parents=True, exist_ok=True)

def extract_first_n_urls(excel_path: Path, max_count: int = 100):
    print(f"正在从 Excel 读取前 {max_count} 张图片 URL ...")
    df = pd.read_excel(excel_path, nrows=500)
    photo_priority = ["priceTagPhotos", "productCloseupPhotos", "freshMilkDisplayShelfPhotos"]
    items = []
    seen_urls = set()
    for _, row in df.iterrows():
        for col in photo_priority:
            val = row.get(col)
            if pd.notna(val) and str(val).strip():
                for u in str(val).split(","):
                    u = u.strip()
                    if u.startswith("http") and u not in seen_urls:
                        seen_urls.add(u)
                        fname = u.split("/")[-1].split("?")[0]
                        items.append({
                            "id": row.get("id"),
                            "storeCode": str(row.get("storeCode", "")),
                            "storeName": str(row.get("storeName", "")),
                            "photo_type": col,
                            "filename": fname,
                            "url": u
                        })
                        if len(items) >= max_count:
                            return items
    return items

def download_image_temp(url: str, target_path: Path, timeout: int = 20) -> bool:
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            with open(target_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False

def main():
    print("=" * 80)
    print("启动【Excel 前 100 张巡店图片质量审核与价签识别管线】")
    print(f"Excel 路径: {EXCEL_PATH}")
    print(f"总输出目录: {OUTPUT_BASE}")
    print(f"  - 质检未通过归档目录: {REJECTED_DIR}")
    print(f"  - 质检通过与识别目录: {ACCEPTED_DIR}")
    print("=" * 80)

    url_items = extract_first_n_urls(EXCEL_PATH, max_count=100)
    print(f"成功提取 {len(url_items)} 个有效图片 URL。开始逐一执行...\n")

    summary = {
        "total": len(url_items),
        "passed_qc": 0,
        "rejected_qc": 0,
        "reasons": {}
    }

    for idx, item in enumerate(url_items, 1):
        fname = item["filename"]
        url = item["url"]
        stem = Path(fname).stem
        print(f"[{idx:03d}/{len(url_items):03d}] {item['storeName']} | {item['photo_type']} | {fname}")

        # ---------------- 阶段 1: 质量审核与场景准入 ----------------
        try:
            qc_output = run_quality_check(url)
            qc_res = qc_output.get("qc_result", {})
            quality_checks = qc_res.get("quality_checks", {})
            required_keys = ["图片模糊", "过度曝光", "光线不足", "文件损坏"]
            is_quality_pass = all(
                str(quality_checks.get(k, "")).strip() == "合格" for k in required_keys
            ) and coerce_bool(qc_res.get("is_valid"), False)
            invalid_reason = qc_res.get("invalid_reason", "")

            content_info = qc_output.get("content_info", {})
            has_price_tag = coerce_bool(
                content_info.get("has_price_tag") if isinstance(content_info, dict) else None,
                False,
            )
            scene_type = str(content_info.get("scene_type", "未识别")).strip()
            is_scene_valid = any(kw in scene_type for kw in ["货架", "冰箱", "堆头", "冷柜", "地堆"])

            can_proceed = is_quality_pass and is_scene_valid and has_price_tag
            rejections = []
            if not is_quality_pass:
                rejections.append(f"质量不合格: {invalid_reason or '存在不合格检测项'}")
            if not is_scene_valid:
                rejections.append(f"非目标巡店场景: {scene_type} (仅支持货架/冰箱/堆头)")
            if not has_price_tag:
                rejections.append("画面内未检出有效商品价签")

            qc_record = {
                "image_info": item,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "qc_result": qc_res,
                "content_info": content_info,
                "decision": {
                    "is_quality_pass": is_quality_pass,
                    "is_scene_valid": is_scene_valid,
                    "scene_type": scene_type,
                    "has_price_tag": has_price_tag,
                    "can_proceed_to_price_tag": can_proceed,
                    "rejection_reasons": rejections
                }
            }
        except Exception as e:
            can_proceed = False
            rejections = [f"质检调用异常: {str(e)}"]
            qc_record = {
                "image_info": item,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e),
                "decision": {"can_proceed_to_price_tag": False, "rejection_reasons": rejections}
            }

        # ---------------- 图片回执下载（无论质检是否合格，都保存原图） ----------------
        local_img_dir = ACCEPTED_DIR / "images" if can_proceed else REJECTED_DIR / "images"
        local_img_dir.mkdir(parents=True, exist_ok=True)
        local_img_path = local_img_dir / fname
        if not local_img_path.exists():
            if download_image_temp(url, local_img_path):
                print(f"    -> 原图已保存: {local_img_dir.name}/{fname}")
            else:
                print(f"    [!] 原图下载失败: {url}")

        # ---------------- 分流归档 ----------------
        if not can_proceed:
            summary["rejected_qc"] += 1
            main_reason = rejections[0] if rejections else "未知原因"
            summary["reasons"][main_reason] = summary["reasons"].get(main_reason, 0) + 1
            out_file = REJECTED_DIR / f"{stem}.qc.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(qc_record, f, ensure_ascii=False, indent=2)
            print(f"    -> [未通过] 原因: {main_reason} -> 归档至: 1_质量不合格/{out_file.name}")
            continue

        # ---------------- 阶段 2: 质量通过，进入价签识别 ----------------
        summary["passed_qc"] += 1
        qc_pass_file = ACCEPTED_DIR / "qc_results" / f"{stem}.qc.json"
        with open(qc_pass_file, "w", encoding="utf-8") as f:
            json.dump(qc_record, f, ensure_ascii=False, indent=2)
        print(f"    -> [质检通过] 场景: {scene_type} | 包含价签: 是 -> 进入价签识别 ...")

        img_w, img_h = 1000, 1000
        if local_img_path.exists():
            try:
                with Image.open(local_img_path) as im:
                    img_w, img_h = im.size
            except Exception:
                pass

        try:
            detection_res, elapsed = run_price_tag_detection(url, img_w=img_w, img_h=img_h)
            tags = detection_res.get("price_tags", [])
            promotion_tags = detection_res.get("promotion_tags", [])
            print(f"       识别成功: 检出 {len(tags)} 个有效单品价签 (耗时 {elapsed:.2f}s)")

            final_record = {
                "image_info": item,
                "step1_qc": qc_record,
                "step2_price_tags": {
                    "total_tags": len(tags),
                    "total_promotion_tags": len(promotion_tags),
                    "elapsed_sec": round(elapsed, 2),
                    "tags": tags,
                    "promotion_tags": promotion_tags
                }
            }
            final_json_file = ACCEPTED_DIR / "final_results" / f"{stem}.pipeline.json"
            with open(final_json_file, "w", encoding="utf-8") as f:
                json.dump(final_record, f, ensure_ascii=False, indent=2)

            if local_img_path.exists():
                vis_path = ACCEPTED_DIR / "vis_images" / f"{stem}.vis.jpg"
                draw_visual_tags_simple(str(local_img_path), tags, str(vis_path), promotion_tags)
        except Exception as e:
            print(f"    [!] 价签识别异常: {e}")

    print("\n" + "=" * 80)
    print("前 100 张图片全流程运行完毕！统计汇总:")
    print(f"  - 总处理图片数: {summary['total']}")
    print(f"  - 质检通过进入识别数: {summary['passed_qc']}")
    print(f"  - 质检拦截数: {summary['rejected_qc']}")
    for r, c in summary["reasons"].items():
        print(f"      * {r}: {c} 张")
    print(f"\n未通过质检结果保存在: {REJECTED_DIR}")
    print(f"通过质检与价签识别保存在: {ACCEPTED_DIR}")
    print("=" * 80)

if __name__ == "__main__":
    main()
