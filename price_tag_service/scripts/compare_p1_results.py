from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _amount(value: object) -> float | None:
    try:
        return round(float(str(value)), 2)
    except (TypeError, ValueError):
        return None


def _bbox(value: object) -> list[float]:
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, list) or len(value) != 4:
        return []
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def _iou(first: list[float], second: list[float]) -> float:
    if len(first) != 4 or len(second) != 4:
        return 0.0
    x_min = max(first[0], second[0])
    y_min = max(first[1], second[1])
    x_max = min(first[2], second[2])
    y_max = min(first[3], second[3])
    intersection = max(0.0, x_max - x_min) * max(0.0, y_max - y_min)
    if intersection <= 0:
        return 0.0
    area_first = (first[2] - first[0]) * (first[3] - first[1])
    area_second = (second[2] - second[0]) * (second[3] - second[1])
    union = area_first + area_second - intersection
    return intersection / union if union else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-dir", type=Path, required=True)
    parser.add_argument("--local-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iou", type=float, default=0.5)
    args = parser.parse_args()

    service_records = [
        _load_json(path)
        for path in sorted((args.service_dir / "service_results").glob("*.service.json"))
    ]
    local_records = {
        path.name: _load_json(path)
        for path in sorted((args.local_dir / "final_results").glob("*.pipeline.json"))
    }
    local_by_source_name = {
        re.sub(r"\.pipeline\.json$", "", re.sub(r"^\d+_", "", name)): record
        for name, record in local_records.items()
    }
    local_qc_records = {
        re.sub(r"\.qc\.json$", "", re.sub(r"^\d+_", "", name)): record
        for name, record in (
            (path.name, _load_json(path))
            for path in sorted((args.local_dir / "qc_results").glob("*.qc.json"))
        )
    }

    rows: list[dict] = []
    totals = {
        "images": 0,
        "outcome_agree": 0,
        "service_processed": 0,
        "local_processed": 0,
        "service_tags": 0,
        "local_tags": 0,
        "matched_bboxes": 0,
        "matched_bbox_and_price": 0,
        "service_unmatched": 0,
        "local_unmatched": 0,
    }
    for service_record in service_records:
        photo = service_record["photo"]
        source_name = photo["image_name"]
        local_record = local_by_source_name.get(Path(source_name).stem)
        if local_record is None:
            rows.append({"image_name": source_name, "error": "local record missing"})
            continue

        service_processed = photo["outcome"] == "PROCESSED"
        local_decision = local_qc_records.get(Path(source_name).stem, {}).get(
            "decision", {}
        )
        local_processed = bool(local_decision.get("can_proceed_to_price_tag"))
        service_tags = service_record.get("price", {}).get("price_tags", []) if service_processed else []
        local_tags = local_record.get("step2_price_tags", {}).get("tags", []) if local_processed else []

        unmatched_local = local_tags.copy()
        matched = 0
        matched_price = 0
        for service_tag in service_tags:
            candidates = [
                (_iou(_bbox(service_tag.get("bbox")), _bbox(local_tag.get("bbox"))), local_tag)
                for local_tag in unmatched_local
            ]
            if not candidates:
                continue
            iou, local_tag = max(candidates, key=lambda item: item[0])
            if iou < args.iou:
                continue
            unmatched_local.remove(local_tag)
            matched += 1
            if _amount(service_tag.get("price")) == _amount(local_tag.get("price")):
                matched_price += 1

        totals["images"] += 1
        totals["outcome_agree"] += int(service_processed == local_processed)
        totals["service_processed"] += int(service_processed)
        totals["local_processed"] += int(local_processed)
        totals["service_tags"] += len(service_tags)
        totals["local_tags"] += len(local_tags)
        totals["matched_bboxes"] += matched
        totals["matched_bbox_and_price"] += matched_price
        totals["service_unmatched"] += len(service_tags) - matched
        totals["local_unmatched"] += len(unmatched_local)
        rows.append(
            {
                "image_name": source_name,
                "service_outcome": photo["outcome"],
                "local_outcome": "PROCESSED" if local_processed else "BLOCKED",
                "outcome_agree": service_processed == local_processed,
                "service_tag_count": len(service_tags),
                "local_tag_count": len(local_tags),
                "matched_bboxes": matched,
                "matched_bbox_and_price": matched_price,
                "service_unmatched": len(service_tags) - matched,
                "local_unmatched": len(unmatched_local),
            }
        )

    totals["bbox_match_rate"] = (
        totals["matched_bboxes"] / totals["service_tags"] if totals["service_tags"] else None
    )
    totals["bbox_price_match_rate"] = (
        totals["matched_bbox_and_price"] / totals["service_tags"]
        if totals["service_tags"]
        else None
    )
    payload = {"iou_threshold": args.iou, "totals": totals, "images": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
