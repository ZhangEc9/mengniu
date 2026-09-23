r"""Offline dry-run: price tags + sample SKU results -> SKU/tag matching -> JSON/CSV.

No service code is touched and nothing is written to a database. Price tags come
from the committed service delivery JSON (P1_fixed10). SKU results are SAMPLE ONLY:
the real SKU agent API has not been provided yet, so boxes are derived from the
price-tag geometry with jitter, plus distractor and invalid entries. This validates
plumbing and the matching rules, not accuracy.

Run:
  D:\Anaconda\python.exe run_sku_match_offline.py
"""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SERVICE_RESULTS = ROOT / "P1_fixed10" / "service_results"
IMAGES = ROOT / "P1_fixed10" / "images"
SKU_CSV = ROOT / "标准sku图-鲜奶.csv"
OUT = ROOT / "sku_match_offline"

MATCH_METHOD_VERSION = "SPATIAL_RULE_V0"
COORD_SCALE = 1000
SEED = 20260923

# Weights follow 价签识别经验与优化方向.md section 5.4.
W = {
    "horizontal_gap": 0.25,
    "center_x_distance": 0.25,
    "vertical_edge_gap": 0.30,
    "horizontal_overlap": 0.08,
    "center_distance": 0.04,
    "direction": 0.15,
    "row": 0.10,
    "size": 0.05,
    "semantic": 0.10,
}
HIGH_COST, HIGH_MARGIN = 0.65, 0.12
MEDIUM_COST, MEDIUM_MARGIN = 1.10, 0.04
MAX_VERTICAL_GAP_ROWS = 3.0


@dataclass
class Box:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def cx(self) -> float:
        return (self.xmin + self.xmax) / 2.0

    @property
    def cy(self) -> float:
        return (self.ymin + self.ymax) / 2.0

    @property
    def width(self) -> float:
        return max(self.xmax - self.xmin, 1.0)

    @property
    def height(self) -> float:
        return max(self.ymax - self.ymin, 1.0)


@dataclass
class SkuItem:
    idx: int
    bbox: Box
    sku_code: str | None
    sku_name: str | None
    score: float | None
    product_role: str
    item_status: str
    error_message: str | None = None
    topk: list = field(default_factory=list)


def load_sku_pool() -> list[dict]:
    with SKU_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("sku_code")]
    pool = []
    for row in rows:
        trace = (row.get("追踪") or "").strip()
        pool.append(
            {
                "sku_code": row["sku_code"].strip(),
                "sku_name": (row.get("sku_name") or "").strip(),
                "reference_image": (row.get("reference_images") or "").strip(),
                "product_role": "COMPETITOR" if trace == "竞品" else "OWN",
            }
        )
    return pool


def scale_to_pixels(bbox: list[float], width: int, height: int, scale: int) -> Box:
    sx, sy = width / scale, height / scale
    return Box(bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy)


def to_pixel_space(item: SkuItem, width: int, height: int) -> SkuItem:
    """SKU boxes reuse the price-tag 0~1000 convention until the real API says otherwise."""
    if item.item_status != "OK":
        return item
    raw = [item.bbox.xmin, item.bbox.ymin, item.bbox.xmax, item.bbox.ymax]
    return SkuItem(
        idx=item.idx,
        bbox=scale_to_pixels(raw, width, height, COORD_SCALE),
        sku_code=item.sku_code,
        sku_name=item.sku_name,
        score=item.score,
        product_role=item.product_role,
        item_status=item.item_status,
        error_message=item.error_message,
        topk=item.topk,
    )


def build_sample_sku_response(image_name: str, tags: list[dict], rng: random.Random) -> dict:
    """Emit the real SKU agent envelope. Boxes are derived from tag geometry + jitter."""
    items: list[dict] = []
    idx = 1
    for tag in tags:
        code = tag.get("sku_code") or "UNKNOWN"
        raw = tag["bbox"]
        # Product box sits right above its price tag: same x span, taller box.
        jitter_x = rng.uniform(-0.15, 0.15) * (raw[2] - raw[0])
        tag_height = max(raw[3] - raw[1], 1.0)
        gap = rng.uniform(0.2, 1.6) * tag_height
        bbox = [
            raw[0] + jitter_x,
            max(raw[1] - gap - tag_height * rng.uniform(3.0, 5.0), 0),
            raw[2] + jitter_x,
            max(raw[1] - gap, 0),
        ]
        items.append(
            {
                "Idx": idx,
                "Bbox": [round(v, 1) for v in bbox],
                "Top1": {"SkuId": code, "SkuName": tag["sku_name"], "Score": tag["score"]},
                "Topk": [
                    {"Rank": 1, "SkuId": code, "SkuName": tag["sku_name"], "Score": tag["score"]},
                    {
                        "Rank": 2,
                        "SkuId": tag["runner_up"]["sku_code"],
                        "SkuName": tag["runner_up"]["sku_name"],
                        "Score": round(tag["score"] * rng.uniform(0.45, 0.85), 2),
                    },
                ],
            }
        )
        idx += 1

    # Distractor product boxes with no nearby tag: must not be matched confidently.
    for _ in range(2):
        left = rng.uniform(0, 1000 - 120)
        top = rng.uniform(0, 300)
        items.append(
            {
                "Idx": idx,
                "Bbox": [round(left, 1), round(top, 1), round(left + 120, 1), round(top + 380, 1)],
                "Top1": {
                    "SkuId": f"DISTRACT-{idx}",
                    "SkuName": "干扰商品框（无对应价签）",
                    "Score": round(rng.uniform(0.28, 0.46), 2),
                },
                "Topk": [],
            }
        )
        idx += 1

    # One invalid entry, mirroring the documented embedding failure.
    items.append({"Idx": idx, "Bbox": [1], "Error": "“embedding失败”", "Top1": None, "Topk": []})

    return {
        "Code": "200",
        "Data": {
            "BoxCount": len(items),
            "Data": items,
            "UsageMap": {"image_count": 1, "sku_count": len(tags)},
        },
        "Message": "OK",
        "RequestId": f"SAMPLE-{abs(hash(image_name)) % 10**16:016X}",
        "Success": True,
    }


def parse_sku_response(payload: dict, pool: list[dict]) -> tuple[list[SkuItem], list[dict]]:
    """Envelope-level failures are system errors; item-level failures stay as INVALID rows."""
    events: list[dict] = []
    if not payload.get("Success") or str(payload.get("Code")) != "200":
        raise RuntimeError(f"SKU call failed: Code={payload.get('Code')} Message={payload.get('Message')}")

    data = payload.get("Data") or {}
    raw_items = data.get("Data") or []
    declared = data.get("BoxCount")
    if declared is not None and declared != len(raw_items):
        events.append({"rule": "BOX_COUNT_MISMATCH", "declared": declared, "actual": len(raw_items)})

    role_by_code = {entry["sku_code"]: entry["product_role"] for entry in pool}
    items: list[SkuItem] = []
    for raw in raw_items:
        idx = int(raw.get("Idx") or 0)
        error = raw.get("Error")
        bbox = raw.get("Bbox") or []
        if error or len(bbox) < 4:
            items.append(
                SkuItem(
                    idx=idx,
                    bbox=Box(0, 0, 0, 0),
                    sku_code=(raw.get("Top1") or {}).get("SkuId"),
                    sku_name=(raw.get("Top1") or {}).get("SkuName"),
                    score=None,
                    product_role="UNKNOWN",
                    item_status="INVALID",
                    error_message=error or "bbox length < 4",
                )
            )
            continue
        top1 = raw.get("Top1") or {}
        code = top1.get("SkuId")
        items.append(
            SkuItem(
                idx=idx,
                bbox=Box(*[float(v) for v in bbox[:4]]),
                sku_code=code,
                sku_name=top1.get("SkuName"),
                score=top1.get("Score"),
                product_role=role_by_code.get(code, "UNKNOWN"),
                item_status="OK",
                topk=raw.get("Topk") or [],
            )
        )
    return items, events


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 1.0
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def spatial_cost(sku: Box, tag: Box, med_sku_w: float, med_sku_h: float) -> tuple[float, dict]:
    horizontal_gap = max(sku.xmin - tag.xmax, tag.xmin - sku.xmax, 0.0)
    overlap = max(0.0, min(sku.xmax, tag.xmax) - max(sku.xmin, tag.xmin))
    overlap_ratio = overlap / min(sku.width, tag.width)
    norm_w = max(med_sku_w, sku.width, tag.width, 1.0)
    norm_h = max(med_sku_h, sku.height, tag.height, 1.0)

    vertical_edge_gap = tag.ymin - sku.ymax
    direction_penalty = 1.0 if vertical_edge_gap < 0 else 0.0
    row_penalty = 0.0 if abs(sku.cy - tag.cy) < med_sku_h * 2.0 else 1.0
    size_ratio = tag.width / sku.width
    size_penalty = 0.0 if 0.4 <= size_ratio <= 2.5 else min(abs(size_ratio - 1.0), 2.0)
    semantic_penalty = 0.0  # No SKU standard price available yet.

    terms = {
        "horizontal_gap_norm": horizontal_gap / norm_w,
        "center_x_distance_norm": abs(sku.cx - tag.cx) / norm_w,
        "vertical_edge_gap_norm": abs(vertical_edge_gap) / norm_h,
        "one_minus_overlap_ratio": 1.0 - overlap_ratio,
        "center_distance_norm": (
            ((sku.cx - tag.cx) / norm_w) ** 2 + ((sku.cy - tag.cy) / norm_h) ** 2
        )
        ** 0.5,
        "direction_penalty": direction_penalty,
        "row_penalty": row_penalty,
        "size_penalty": min(size_penalty, 1.0),
        "semantic_penalty": semantic_penalty,
    }
    cost = (
        W["horizontal_gap"] * terms["horizontal_gap_norm"]
        + W["center_x_distance"] * terms["center_x_distance_norm"]
        + W["vertical_edge_gap"] * terms["vertical_edge_gap_norm"]
        + W["horizontal_overlap"] * terms["one_minus_overlap_ratio"]
        + W["center_distance"] * terms["center_distance_norm"]
        + W["direction"] * direction_penalty
        + W["row"] * row_penalty
        + W["size"] * min(size_penalty, 1.0)
        + W["semantic"] * semantic_penalty
    )
    evidence = {
        "cost": round(cost, 4),
        "horizontal_gap": round(horizontal_gap, 2),
        "horizontal_overlap_ratio": round(overlap_ratio, 4),
        "vertical_edge_gap": round(vertical_edge_gap, 2),
        "direction": "TAG_BELOW" if vertical_edge_gap >= 0 else "TAG_ABOVE",
        "terms": {key: round(value, 4) for key, value in terms.items()},
    }
    return cost, evidence


def confidence_level(cost: float, margin: float) -> str:
    if cost <= HIGH_COST and margin >= HIGH_MARGIN:
        return "HIGH"
    if cost <= MEDIUM_COST and margin >= MEDIUM_MARGIN:
        return "MEDIUM"
    return "LOW"


def match_tags(skus: list[SkuItem], tags: list[Box]) -> list[dict]:
    usable = [item for item in skus if item.item_status == "OK"]
    med_w = median([item.bbox.width for item in usable]) if usable else 1.0
    med_h = median([item.bbox.height for item in usable]) if usable else 1.0

    rows: list[dict] = []
    claimed: dict[int, list[int]] = {}
    for tag_id, tag in enumerate(tags, 1):
        scored: list[tuple[float, SkuItem, dict]] = []
        for item in usable:
            gap = tag.ymin - item.bbox.ymax
            if gap < -item.bbox.height or gap > med_h * MAX_VERTICAL_GAP_ROWS:
                continue  # Candidate pre-filter, section 5.3.
            if max(item.bbox.xmin - tag.xmax, tag.xmin - item.bbox.xmax, 0.0) > med_w * 1.5:
                continue
            cost, evidence = spatial_cost(item.bbox, tag, med_w, med_h)
            scored.append((cost, item, evidence))
        scored.sort(key=lambda row: row[0])

        if not scored:
            rows.append({"tag_id": tag_id, "match_status": "UNMATCHED", "reason": "NO_CANDIDATE"})
            continue
        best_cost, best, evidence = scored[0]
        second_cost = scored[1][0] if len(scored) > 1 else float("inf")
        margin = round(second_cost - best_cost, 4) if second_cost != float("inf") else None
        level = confidence_level(best_cost, margin if margin is not None else 0.0)
        if level == "LOW":
            status = "AMBIGUOUS"
        else:
            status = "MATCHED"
        claimed.setdefault(best.idx, []).append(tag_id)
        rows.append(
            {
                "tag_id": tag_id,
                "match_status": status,
                "spatial_confidence": level,
                "score": round(best.score or 0.0, 4),
                "sku_idx": best.idx,
                "sku_code": best.sku_code,
                "sku_name": best.sku_name,
                "product_role": best.product_role,
                "price_score": best.score,
                "cost": round(best_cost, 4),
                "second_cost": None if second_cost == float("inf") else round(second_cost, 4),
                "margin": margin,
                "second_sku_idx": scored[1][1].idx if len(scored) > 1 else None,
                "match_method_version": MATCH_METHOD_VERSION,
                "evidence": evidence,
            }
        )
    for row in rows:
        idx = row.get("sku_idx")
        if idx is not None and len(claimed.get(idx, [])) > 1:
            row["match_status"] = "CONFLICT"
            row["evidence"]["conflicting_tag_ids"] = claimed[idx]
    return rows


def draw_overlay(image_path: Path, skus: list[SkuItem], tags: list[Box], rows: list[dict], out: Path) -> None:
    with Image.open(image_path) as image:
        canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    by_idx = {item.idx: item for item in skus}
    for item in skus:
        if item.item_status != "OK":
            continue
        draw.rectangle([item.bbox.xmin, item.bbox.ymin, item.bbox.xmax, item.bbox.ymax], outline=(0, 200, 0), width=3)
    for tag in tags:
        draw.rectangle([tag.xmin, tag.ymin, tag.xmax, tag.ymax], outline=(255, 0, 0), width=3)
    for row in rows:
        item = by_idx.get(row.get("sku_idx"))
        if item is None or row["match_status"] not in {"MATCHED", "CONFLICT"}:
            continue
        tag = tags[row["tag_id"] - 1]
        color = (0, 160, 255) if row["spatial_confidence"] == "HIGH" else (255, 200, 0)
        draw.line([item.bbox.cx, item.bbox.ymax, tag.cx, tag.ymin], fill=color, width=4)
    canvas.save(out, quality=85)


def main() -> int:
    rng = random.Random(SEED)
    pool = load_sku_pool()
    (OUT / "sku_sample_responses").mkdir(parents=True, exist_ok=True)
    (OUT / "matches").mkdir(parents=True, exist_ok=True)
    (OUT / "vis").mkdir(parents=True, exist_ok=True)

    # SAMPLE SKU assignment only: one pool entry per delivered tag, cycling deterministically.
    case_files = sorted(SERVICE_RESULTS.glob("*.service.json"))
    if not case_files:
        raise FileNotFoundError(f"No service results under {SERVICE_RESULTS}")

    csv_rows: list[dict] = []
    stats = {"images": 0, "tags": 0, "sku_items": 0, "sku_invalid": 0, "matched": 0, "ambiguous": 0,
             "conflict": 0, "unmatched": 0, "high": 0, "medium": 0, "low": 0, "skipped_blocked": 0}

    for file_index, case_file in enumerate(case_files):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        if "price" not in case:
            stats["skipped_blocked"] += 1
            print(f"SKIP (no price result, outcome={case['photo']['outcome']}): {case_file.name}")
            continue
        image_name = case["price"]["image_name"]
        delivered = case["price"]["price_tags"]
        stem = image_name.rsplit(".", 1)[0]
        candidates = [path for path in IMAGES.iterdir() if stem in path.name]
        if not candidates:
            print(f"SKIP (image not found): {image_name}")
            continue
        image_path = candidates[0]
        width, height = Image.open(image_path).size

        tagged = []
        for tag in delivered:
            pick = pool[(file_index + tag["id"]) % len(pool)]
            runner_up = pool[(file_index + tag["id"] + 3) % len(pool)]
            tagged.append(
                {
                    "id": tag["id"],
                    "bbox": tag["bbox"],
                    "price": tag["price"],
                    "raw_price_text": tag["raw_price_text"],
                    "sku_code": pick["sku_code"],
                    "sku_name": pick["sku_name"],
                    "score": round(rng.uniform(0.72, 0.95), 2),
                    "runner_up": runner_up,
                }
            )

        payload = build_sample_sku_response(image_name, tagged, rng)
        (OUT / "sku_sample_responses" / f"{stem}.sku.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        skus, events = parse_sku_response(payload, pool)
        tag_boxes = [scale_to_pixels(item["bbox"], width, height, COORD_SCALE) for item in tagged]
        sku_boxes = [to_pixel_space(item, width, height) for item in skus]
        rows = match_tags(sku_boxes, tag_boxes)

        match_doc = {
            "image_name": image_name,
            "image_url": case["price"]["image_url"],
            "image_width": width,
            "image_height": height,
            "coordinate_scale": COORD_SCALE,
            "data_source": "SAMPLE_SKU_RESPONSE (real SKU API not provided yet)",
            "sku_box_count": len(skus),
            "sku_invalid_count": sum(1 for item in skus if item.item_status == "INVALID"),
            "filter_events": events,
            "price_tag_count": len(delivered),
            "matches": rows,
        }
        (OUT / "matches" / f"{stem}.match.json").write_text(
            json.dumps(match_doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        draw_overlay(image_path, sku_boxes, tag_boxes, rows, OUT / "vis" / f"{stem}_match_vis.jpg")

        price_by_id = {item["id"]: item for item in tagged}
        for row in rows:
            tag_id = row["tag_id"]
            tag = price_by_id[tag_id]
            stats["tags"] += 1
            status = row["match_status"]
            stats[{"MATCHED": "matched", "AMBIGUOUS": "ambiguous", "CONFLICT": "conflict",
                   "UNMATCHED": "unmatched"}[status]] += 1
            level = row.get("spatial_confidence")
            if level in stats:
                stats[level] += 1
            csv_rows.append(
                {
                    "image_name": image_name,
                    "tag_id": tag_id,
                    "price": tag["price"],
                    "raw_price_text": tag["raw_price_text"],
                    "tag_bbox": tag["bbox"],
                    "sku_idx": row.get("sku_idx"),
                    "sku_code": row.get("sku_code"),
                    "sku_name": row.get("sku_name"),
                    "product_role": row.get("product_role"),
                    "score": row.get("score"),
                    "cost": row.get("cost"),
                    "second_cost": row.get("second_cost"),
                    "margin": row.get("margin"),
                    "match_status": status,
                    "spatial_confidence": level,
                    "match_method_version": MATCH_METHOD_VERSION,
                }
            )
        stats["images"] += 1
        stats["sku_items"] += len(skus)
        stats["sku_invalid"] += sum(1 for item in skus if item.item_status == "INVALID")

    if csv_rows:
        with (OUT / "sku_price_pairs.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "match_method_version": MATCH_METHOD_VERSION,
                "weights": W,
                "thresholds": {
                    "HIGH": {"cost": HIGH_COST, "margin": HIGH_MARGIN},
                    "MEDIUM": {"cost": MEDIUM_COST, "margin": MEDIUM_MARGIN},
                },
                "note": "SKU boxes are SAMPLE only. This run validates plumbing and rules, not accuracy.",
                "stats": stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(stats, ensure_ascii=False))
    print(f"outputs -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
