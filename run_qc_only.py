# -*- coding: utf-8 -*-

import argparse
import json
import time
from pathlib import Path

from run_full_pipeline import (
    load_oss_map,
    normalize_quality_checks,
    run_quality_check,
    save_oss_map,
    upload_to_mengniu_oss,
)


QC_QUALITY_KEYS = ["图片模糊", "过度曝光", "光线不足", "文件损坏"]
DEFAULT_IMAGE_DIRS = [
    Path(r"D:\Shixi\mengniu\标注数据集_AnyLabeling\有价签"),
    Path(r"D:\Shixi\mengniu\标注数据集_AnyLabeling\有价签但价签模糊"),
    Path(r"D:\Shixi\mengniu\标注数据集_AnyLabeling\无价签"),
]
DEFAULT_OUTPUT_DIR = Path(r"D:\Shixi\mengniu\质检一次_63张")


def qc_one_image(image_path: Path, oss_map: dict, output_dir: Path, force: bool = False) -> dict:
    output_file = output_dir / f"{image_path.stem}.qc.json"
    if output_file.is_file() and not force:
        return json.loads(output_file.read_text(encoding="utf-8"))

    image_url = oss_map.get(image_path.name, "")
    if not image_url:
        image_url = upload_to_mengniu_oss(image_path)
        if image_url:
            oss_map[image_path.name] = image_url
            save_oss_map(oss_map)

    if not image_url:
        record = {
            "image_name": image_path.name,
            "image_path": str(image_path),
            "status": "oss_upload_error",
        }
    else:
        try:
            started_at = time.time()
            qc_output = run_quality_check(image_url)
            elapsed = time.time() - started_at
            record = {
                "image_name": image_path.name,
                "image_path": str(image_path),
                "image_url": image_url,
                "status": "completed",
                "qc_result": qc_output.get("qc_result", {}),
                "content_info": qc_output.get("content_info", {}),
                "elapsed_sec": round(elapsed, 2),
            }
        except Exception as exc:
            record = {
                "image_name": image_path.name,
                "image_path": str(image_path),
                "image_url": image_url,
                "status": "qc_api_error",
                "error": str(exc),
            }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def summarize_record(record: dict) -> dict:
    qc_result = record.get("qc_result", {})
    content_info = record.get("content_info", {})
    quality_checks = normalize_quality_checks(qc_result.get("quality_checks", {}))
    quality_pass = all(
        str(quality_checks.get(key, "")).strip() == "合格" for key in QC_QUALITY_KEYS
    )
    has_price_tag = bool(content_info.get("has_price_tag", False))
    scene_type = str(content_info.get("scene_type", "")).strip()
    scene_valid = any(keyword in scene_type for keyword in ["货架", "冰箱", "堆头"])

    return {
        "quality_pass": quality_pass,
        "has_price_tag": has_price_tag,
        "scene_type": scene_type,
        "scene_valid": scene_valid,
    }


def run_directory(image_dir: Path, output_root: Path, oss_map: dict, force: bool = False) -> dict:
    output_dir = output_root / image_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = sorted(path for path in image_dir.iterdir() if path.suffix.lower() in valid_exts)

    print("\n" + "=" * 80)
    print(f"图片目录: {image_dir}")
    print(f"输出目录: {output_dir}")
    print(f"图片数量: {len(images)}")
    print("=" * 80)

    records = []
    for index, image_path in enumerate(images, 1):
        print(f"[{index}/{len(images)}] {image_path.name}")
        try:
            record = qc_one_image(image_path, oss_map, output_dir, force=force)
        except Exception as exc:
            record = {
                "image_name": image_path.name,
                "image_path": str(image_path),
                "status": "unhandled_exception",
                "error": str(exc),
            }
        records.append(record)

    results_file = output_dir / "qc_summary.json"
    failed_file = output_dir / "failed_images.json"
    failed_records = [record for record in records if record.get("status") != "completed"]

    summary = {
        "source_dir": str(image_dir),
        "total_images": len(records),
        "completed": len(records) - len(failed_records),
        "failed": len(failed_records),
        "quality_pass": sum(summarize_record(record)["quality_pass"] for record in records),
        "quality_fail": sum(not summarize_record(record)["quality_pass"] for record in records),
        "has_price_tag": sum(summarize_record(record)["has_price_tag"] for record in records),
        "no_price_tag": sum(not summarize_record(record)["has_price_tag"] for record in records),
        "scene_valid": sum(summarize_record(record)["scene_valid"] for record in records),
        "records": [summarize_record(record) | {"image_name": record.get("image_name"), "status": record.get("status")} for record in records],
    }

    results_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    failed_file.write_text(json.dumps(failed_records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"完成: {summary['completed']}/{summary['total_images']}；"
        f"质量通过 {summary['quality_pass']}；含价签 {summary['has_price_tag']}；"
        f"无价签 {summary['no_price_tag']}；失败 {summary['failed']}"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description="只运行蒙牛巡店图片质量审核")
    parser.add_argument("--img-dir", nargs="+", default=[str(path) for path in DEFAULT_IMAGE_DIRS])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--force", action="store_true", help="覆盖已有质检 JSON")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    oss_map = load_oss_map()
    summaries = [run_directory(Path(path), output_root, oss_map, force=args.force) for path in args.img_dir]

    total_summary = {
        "total_images": sum(item["total_images"] for item in summaries),
        "quality_pass": sum(item["quality_pass"] for item in summaries),
        "quality_fail": sum(item["quality_fail"] for item in summaries),
        "has_price_tag": sum(item["has_price_tag"] for item in summaries),
        "no_price_tag": sum(item["no_price_tag"] for item in summaries),
        "failed": sum(item["failed"] for item in summaries),
        "directories": summaries,
    }
    (output_root / "qc_total_summary.json").write_text(
        json.dumps(total_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print(f"全部质检完成: {total_summary['total_images']} 张")
    print(f"质量通过: {total_summary['quality_pass']}")
    print(f"质量不通过: {total_summary['quality_fail']}")
    print(f"判定有价签: {total_summary['has_price_tag']}")
    print(f"判定无价签: {total_summary['no_price_tag']}")
    print(f"失败: {total_summary['failed']}")
    print(f"汇总文件: {output_root / 'qc_total_summary.json'}")
    print("=" * 80)


if __name__ == "__main__":
    main()
