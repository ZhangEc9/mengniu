import argparse
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from run_full_pipeline import (
    cv_imwrite_utf8,
    run_price_tag_detection,
    upload_to_mengniu_oss,
)


def read_image_unicode(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取图片: {path}")
    return image


def build_strips(image_height, image_width, strip_height, strip_overlap):
    if strip_height <= 0 or strip_height > image_height or strip_overlap < 0:
        raise ValueError("条带高度或重叠参数无效")
    if strip_overlap >= strip_height:
        raise ValueError("重叠必须小于条带高度")

    strips = []
    top = 0
    step = strip_height - strip_overlap
    while True:
        bottom = min(top + strip_height, image_height)
        strips.append({"index": len(strips), "y0": top, "y1": bottom})
        if bottom >= image_height:
            break
        top += step
    return strips


def bbox_to_pixels(value, width, height):
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = [float(item) for item in value[:4]]
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (
        max(0, min(width, int(round(x0 / 1000.0 * width)))),
        max(0, min(height, int(round(y0 / 1000.0 * height)))),
        max(0, min(width, int(round(x1 / 1000.0 * width)))),
        max(0, min(height, int(round(y1 / 1000.0 * height)))),
    )


def mapped_bbox_to_1000(pixel_bbox, crop_y0, image_width, image_height):
    x0, y0, x1, y1 = pixel_bbox
    absolute_y0 = max(0, min(image_height, crop_y0 + y0))
    absolute_y1 = max(0, min(image_height, crop_y0 + y1))
    return [
        int(round(x0 / image_width * 1000)),
        int(round(absolute_y0 / image_height * 1000)),
        int(round(x1 / image_width * 1000)),
        int(round(absolute_y1 / image_height * 1000)),
    ]


def iou(first, second):
    ix0, iy0 = max(first[0], second[0]), max(first[1], second[1])
    ix1, iy1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if intersection <= 0:
        return 0.0
    area_first = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    area_second = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    return intersection / float(area_first + area_second - intersection)


def price_number(value):
    text = str(value or "").strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def same_price(first, second, tolerance):
    left, right = price_number(first), price_number(second)
    if left is None or right is None:
        return str(first or "").strip() == str(second or "").strip()
    return abs(left - right) <= max(tolerance, abs(left) * tolerance)


def candidate_key(tag, tag_type):
    return (tag_type, str(tag.get("price", "")).strip(), tuple(tag.get("bbox", [])))


def extract_candidates(api_result, crop, crop_width, crop_height, image_width, image_height, edge_margin):
    candidates = []
    crop_w, crop_h = crop_width, crop_height
    y0, y1 = crop["y0"], crop["y1"]
    for tag_type in ("price_tags", "promotion_tags"):
        for tag in api_result.get(tag_type) or []:
            if not isinstance(tag, dict):
                continue
            local_bbox = bbox_to_pixels(tag.get("bbox"), crop_w, crop_h)
            if not local_bbox:
                continue
            box_y0, box_y1 = local_bbox[1], local_bbox[3]
            partial = box_y0 < crop_h * edge_margin or box_y1 > crop_h * (1.0 - edge_margin)
            candidate = dict(tag)
            candidate["bbox"] = mapped_bbox_to_1000(local_bbox, y0, image_width, image_height)
            candidate["pixel_bbox"] = (local_bbox[0], y0 + local_bbox[1], local_bbox[2], y0 + local_bbox[3])
            candidate["tag_type"] = tag_type[:-5] if tag_type.endswith("_tags") else tag_type
            candidate["source_strip"] = crop["index"]
            candidate["partial"] = partial
            candidate["consensus_count"] = 1
            candidate["source_strips"] = [crop["index"]]
            candidates.append(candidate)
    return candidates


def merge_candidates(candidates, merge_iou, price_tolerance):
    merged = []
    for candidate in candidates:
        best = None
        best_iou = 0.0
        for existing in merged:
            if existing["tag_type"] != candidate["tag_type"]:
                continue
            overlap = iou(existing["pixel_bbox"], candidate["pixel_bbox"])
            if overlap > best_iou and same_price(existing.get("price"), candidate.get("price"), price_tolerance):
                best = existing
                best_iou = overlap

        if best is None or best_iou < merge_iou:
            merged.append(candidate)
            continue

        best["consensus_count"] += 1
        best["source_strips"].append(candidate["source_strip"])
        best["source_strips"] = sorted(set(best["source_strips"]))
        best["source_strip"] = best["source_strips"][0]
        best["confidence"] = max(float(best.get("confidence") or 0), float(candidate.get("confidence") or 0))
        best["price_confidence"] = max(float(best.get("price_confidence") or 0), float(candidate.get("price_confidence") or 0))
        best["max_iou"] = max(float(best.get("max_iou") or 0), best_iou)
        best["partial"] = bool(best.get("partial")) and bool(candidate.get("partial"))

        old_area = max(0, best["pixel_bbox"][2] - best["pixel_bbox"][0]) * max(0, best["pixel_bbox"][3] - best["pixel_bbox"][1])
        new_area = max(0, candidate["pixel_bbox"][2] - candidate["pixel_bbox"][0]) * max(0, candidate["pixel_bbox"][3] - candidate["pixel_bbox"][1])
        if new_area > old_area and not candidate.get("partial"):
            best["pixel_bbox"] = candidate["pixel_bbox"]
            best["bbox"] = candidate["bbox"]
    return merged


def renumber_tags(tags):
    for index, tag in enumerate(tags, 1):
        tag["id"] = index
        tag["shelf_layer"] = int(tag.get("shelf_layer") or (tag["pixel_bbox"][1] // 250) + 1)
        tag["need_review"] = bool(
            tag.get("partial")
            or tag.get("consensus_count", 1) < 2
            or float(tag.get("confidence") or 0) < 0.70
            or float(tag.get("price_confidence") or 0) < 0.70
        )
        tag["review_reason"] = []
        if tag.get("partial"):
            tag["review_reason"].append("strip_edge_partial")
        if tag.get("consensus_count", 1) < 2:
            tag["review_reason"].append("single_strip_candidate")
        if float(tag.get("confidence") or 0) < 0.70:
            tag["review_reason"].append("low_confidence")
        if float(tag.get("price_confidence") or 0) < 0.70:
            tag["review_reason"].append("low_price_confidence")
    return tags


def draw_result(image, tags, output_path):
    rendered = image.copy()
    height, width = rendered.shape[:2]
    for tag in tags:
        pixel_bbox = tag.get("pixel_bbox")
        if not pixel_bbox:
            pixel_bbox = bbox_to_pixels(tag.get("bbox"), width, height)
        if not pixel_bbox:
            continue
        color = (0, 255, 0) if not tag.get("need_review") else (0, 165, 255)
        cv2.rectangle(rendered, pixel_bbox[:2], pixel_bbox[2:], color, 3)
        label = f"#{tag.get('id')} {tag.get('price', '')} c{tag.get('consensus_count', 1)}"
        label_y = max(pixel_bbox[1] - 8, 24)
        cv2.putText(rendered, label, pixel_bbox[:2], cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(rendered, label, pixel_bbox[:2], cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    cv_imwrite_utf8(str(output_path), rendered)


def main():
    parser = argparse.ArgumentParser(description="密集价签横向条带裁剪复检实验")
    parser.add_argument("--image", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prompt-suffix", default="_final")
    parser.add_argument("--strip-height", type=int, default=500)
    parser.add_argument("--strip-overlap", type=int, default=150)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--merge-iou", type=float, default=0.30)
    parser.add_argument("--price-tolerance", type=float, default=0.005)
    parser.add_argument("--edge-margin", type=float, default=0.01)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    os.environ["PRICE_PROMPT_SUFFIX"] = args.prompt_suffix
    importlib.reload(sys.modules["run_full_pipeline"])

    image_path = Path(args.image)
    output_dir = Path(args.output_dir)
    crops_dir = output_dir / "crops"
    strip_results_dir = output_dir / "strip_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    strip_results_dir.mkdir(parents=True, exist_ok=True)

    image = read_image_unicode(image_path)
    image_height, image_width = image.shape[:2]
    strips = build_strips(image_height, image_width, args.strip_height, args.strip_overlap)

    candidates = []
    failures = []
    for strip in strips:
        crop_path = crops_dir / f"{image_path.stem}_strip_{strip['index']:02d}.jpg"
        if args.force or not crop_path.exists():
            crop = image[strip["y0"]:strip["y1"], 0:image_width].copy()
            cv_imwrite_utf8(str(crop_path), crop)
        else:
            crop = read_image_unicode(crop_path)

        upload_path = crop_path
        if args.scale != 1.0:
            upload_path = crops_dir / f"{image_path.stem}_strip_{strip['index']:02d}_x{args.scale:g}.jpg"
            if args.force or not upload_path.exists():
                scaled_crop = cv2.resize(
                    crop,
                    None,
                    fx=args.scale,
                    fy=args.scale,
                    interpolation=cv2.INTER_CUBIC,
                )
                cv_imwrite_utf8(str(upload_path), scaled_crop)
            else:
                crop = read_image_unicode(upload_path)
        strip["image"] = crop

        result_path = strip_results_dir / f"{crop_path.stem}.api.json"
        if result_path.exists() and not args.force:
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            if "error" in saved:
                failures.append({"strip": strip["index"], "error": saved["error"]})
                continue
            api_result, elapsed = saved["api_result"], saved["elapsed_sec"]
        else:
            print(f"[Strip {strip['index'] + 1}/{len(strips)}] y={strip['y0']}..{strip['y1']} 上传并识别")
            try:
                image_url = upload_to_mengniu_oss(upload_path)
                if not image_url:
                    raise RuntimeError("OSS 上传失败")
                api_result, elapsed = run_price_tag_detection(
                    image_url,
                    img_w=int(image_width * args.scale),
                    img_h=int((strip["y1"] - strip["y0"]) * args.scale),
                    timeout=args.timeout,
                )
                result_path.write_text(
                    json.dumps({"api_result": api_result, "elapsed_sec": elapsed}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                failures.append({"strip": strip["index"], "error": str(exc)})
                result_path.write_text(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
                continue

        strip_candidates = extract_candidates(
            api_result,
            strip,
            image_width,
            strip["y1"] - strip["y0"],
            image_width,
            image_height,
            args.edge_margin,
        )
        strip["candidate_count"] = len(strip_candidates)
        print(f"    -> {len(strip_candidates)} candidates")
        candidates.extend(strip_candidates)

    merged = merge_candidates(candidates, args.merge_iou, args.price_tolerance)
    regular = renumber_tags([tag for tag in merged if tag.get("tag_type") == "price"])
    promotion = renumber_tags([tag for tag in merged if tag.get("tag_type") == "promotion"])

    report = {
        "image_name": image_path.name,
        "method": "horizontal_strip_recheck",
        "image_width": image_width,
        "image_height": image_height,
        "prompt_suffix": args.prompt_suffix,
        "strip_height": args.strip_height,
        "strip_overlap": args.strip_overlap,
        "scale": args.scale,
        "merge_iou": args.merge_iou,
        "price_tolerance": args.price_tolerance,
        "total_tags": len(regular),
        "total_promotion_tags": len(promotion),
        "need_review_tags": sum(1 for tag in regular if tag.get("need_review")),
        "raw_candidate_count": len(candidates),
        "failed_strips": failures,
        "step2_price_tags": {"total_tags": len(regular), "total_promotion_tags": len(promotion), "tags": regular, "promotion_tags": promotion},
    }
    output_path = output_dir / "dense_result.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    pipeline_path = output_dir / "dense_result.pipeline.json"
    pipeline_path.write_text(
        json.dumps({
            "image_name": image_path.name,
            "step2_price_tags": {
                "total_tags": len(regular),
                "total_promotion_tags": len(promotion),
                "tags": regular,
                "promotion_tags": promotion,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    draw_result(image, regular, output_dir / "dense_result_vis.jpg")
    print(json.dumps({key: report[key] for key in ("total_tags", "total_promotion_tags", "need_review_tags", "raw_candidate_count", "failed_strips")}, ensure_ascii=False))


if __name__ == "__main__":
    import importlib
    import sys

    main()
