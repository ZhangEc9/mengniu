import argparse
import csv
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="合并底部条带预测与顶部物理槽位/局部读价实验")
    parser.add_argument("--probe-dir", type=Path, action="append")
    parser.add_argument("--slots-csv", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--baseline-pipeline", type=Path)
    parser.add_argument("--image-height", type=int, default=1500)
    parser.add_argument("--rail-top", type=int)
    parser.add_argument("--rail-bottom", type=int)
    parser.add_argument("--dry-run-gate", action="store_true")
    args = parser.parse_args()
    test_dir = Path(__file__).resolve().parent
    baseline_path = args.baseline_pipeline or test_dir / "密集条带放大复检结果" / "dense_result.pipeline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_tags = list(baseline["step2_price_tags"]["tags"])
    if args.dry_run_gate:
        if args.rail_top is None or args.rail_bottom is None:
            parser.error("--dry-run-gate 需要 --rail-top 和 --rail-bottom")
        existing = [tag for tag in baseline_tags if args.rail_top <=
                    (float(tag["bbox"][1]) + float(tag["bbox"][3])) / 2000 * args.image_height < args.rail_bottom]
        print(json.dumps({"baseline_pipeline": str(baseline_path), "rail": [args.rail_top, args.rail_bottom],
                          "existing_tags": len(existing),
                          "action": "preserve_baseline" if existing else "allow_experimental_candidates"}, ensure_ascii=False))
        return
    if not args.probe_dir:
        parser.error("缺少 --probe-dir")
    expected_image = "45727462_6_first_normal_1782463362055_B04C0D8E.jpg"
    if baseline.get("image_name") != expected_image:
        raise ValueError("此组合实验只适用于密集样图；其它图片仅可运行 --dry-run-gate")
    slots_path = args.slots_csv or test_dir / "顶部导轨物理找框实验" / "slots.csv"
    with slots_path.open(encoding="utf-8-sig", newline="") as handle:
        slots = list(csv.DictReader(handle))
    probes_by_box = {}
    for probe_dir in args.probe_dir:
        probes = json.loads((probe_dir / "summary.json").read_text(encoding="utf-8"))
        for probe in probes:
            key = tuple(probe["slot_bbox"])
            if key in probes_by_box:
                raise ValueError(f"同一槽位重复读价，需要明确选用哪次：{key}")
            probes_by_box[key] = probe
    slot_boxes = [tuple(int(row[key]) for key in ("x0", "y0", "x1", "y1")) for row in slots]
    if any(box not in probes_by_box for box in slot_boxes):
        raise ValueError("必须覆盖全部槽位，不能只挑选成功样例")
    image_width, image_height = 2000, 1500
    tags = baseline_tags
    rail_top = min(box[1] for box in slot_boxes)
    rail_bottom = max(box[3] for box in slot_boxes)
    if any(rail_top <= (float(tag["bbox"][1]) + float(tag["bbox"][3])) / 2000 * image_height < rail_bottom
           for tag in tags):
        raise ValueError("该导轨已有 API 候选，禁止实验补框覆盖或重复")
    for row, box in zip(slots, slot_boxes):
        probe = probes_by_box[box]
        left, top, right, bottom = box
        price = probe.get("price")
        readable = probe.get("readable") is True
        valid_price = bool(readable and isinstance(price, str) and re.fullmatch(r"\d+\.\d{2}", price))
        tag = {
            "id": len(tags) + 1, "bbox": [round(left / image_width * 1000),
                                            round(top / image_height * 1000),
                                            round(right / image_width * 1000),
                                            round(bottom / image_height * 1000)],
            "pixel_bbox": [left, top, right, bottom],
            "tag_type": "price", "price": price if valid_price else "",
            "raw_price_text": probe.get("page_content", ""),
            "need_review": True,
            "review_reason": ["single_crop_read_no_consensus"] if valid_price else ["unreadable_or_invalid_price"],
            "source": "top_rail_contour_slot_and_single_crop_api",
            "source_slot_index": int(row["index"]),
        }
        if isinstance(probe.get("price_confidence"), (int, float)):
            tag["price_confidence"] = probe["price_confidence"]
        tags.append(tag)
    output_dir = args.output_dir or test_dir / "密集型组合实验"
    predictions_dir = output_dir / "standard_pred"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    baseline["step2_price_tags"]["tags"] = tags
    baseline["step2_price_tags"]["total_tags"] = len(tags)
    baseline["experiment"] = {
        "baseline": str(baseline_path), "top_probes": [str(path / "summary.json") for path in args.probe_dir],
        "slots_csv": str(slots_path),
        "note": "离线组合评测。顶部价格仅单次读取，全部标为 need_review；非生产可接受结果。",
    }
    path = predictions_dir / (Path(baseline["image_name"]).stem + ".pipeline.json")
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"输出 {len(tags)} 个候选（基线 80 + 顶部 {len(slots)}），其中顶部完整读价格式 {sum(bool(tag.get('price')) for tag in tags[80:])} 个：{path}")


if __name__ == "__main__":
    main()
