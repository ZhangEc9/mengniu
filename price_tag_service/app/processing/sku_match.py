from __future__ import annotations

from math import hypot
from statistics import median


def normalize_sku(payload: dict) -> dict:
    envelope = payload.get("body", payload)
    if not isinstance(envelope, dict) or not envelope.get("Success", True) or str(envelope.get("Code", "success")).lower() not in {"success", "200"}:
        raise ValueError("SKU response failed")
    data = envelope.get("Data") or {}
    if not isinstance(data, dict) or not isinstance(data.get("Data"), list):
        raise ValueError("SKU response missing Data.Data")
    items = []
    for position, raw in enumerate(data["Data"]):
        top1 = raw.get("Top1") or {}
        error = raw.get("Error")
        try:
            bbox = [float(value) for value in raw.get("Bbox")]
            if len(bbox) != 4 or not (0 <= bbox[0] < bbox[2] <= 1000 and 0 <= bbox[1] < bbox[3] <= 1000):
                raise ValueError("invalid bbox")
            if not top1.get("SkuId") or top1.get("Score") is None or not 0 <= float(top1["Score"]) <= 1:
                raise ValueError("invalid Top1")
        except (TypeError, ValueError):
            bbox = None
            error = error or "invalid bbox or Top1"
        items.append({"idx": raw.get("Idx", position), "bbox": bbox,
                      "sku_code": top1.get("SkuId"), "sku_name": top1.get("SkuName"),
                      "score": float(top1["Score"]) if bbox is not None and not error else None,
                      "item_status": "INVALID" if error else "OK", "error": error})
    events = []
    if data.get("BoxCount") is not None and data["BoxCount"] != len(items):
        events.append({"rule": "BOX_COUNT_MISMATCH", "declared": data["BoxCount"], "actual": len(items)})
    return {"status": "SUCCEEDED", "request_id": envelope.get("RequestId", payload.get("api_request_id")),
            "items": items, "raw_payload": payload, "events": events}


def match_sku_tags(skus: list[dict], tags: list[dict]) -> list[dict]:
    usable = [item for item in skus if item.get("item_status") == "OK" and item.get("bbox")]
    widths = [item["bbox"][2] - item["bbox"][0] for item in usable]
    heights = [item["bbox"][3] - item["bbox"][1] for item in usable]
    median_width = median(widths) if widths else 1.0
    median_height = median(heights) if heights else 1.0
    rows = []
    claims = {}
    for tag in tags:
        tag_box = tag.get("bbox")
        if not tag_box or any(value is None for value in tag_box):
            rows.append({"tag_id": tag["id"], "match_status": "UNMATCHED", "reason": "INVALID_TAG_BBOX"})
            continue
        scored = []
        for item in usable:
            box = item["bbox"]
            width, height = box[2] - box[0], box[3] - box[1]
            vertical_gap = tag_box[1] - box[3]
            horizontal_gap = max(box[0] - tag_box[2], tag_box[0] - box[2], 0)
            if vertical_gap < -height or vertical_gap > median_height * 3 or horizontal_gap > median_width * 1.5:
                continue
            tag_width = tag_box[2] - tag_box[0]
            tag_height = tag_box[3] - tag_box[1]
            if tag_width <= 0 or tag_height <= 0:
                continue
            norm_width = max(median_width, width, tag_width, 1)
            norm_height = max(median_height, height, tag_height, 1)
            center_x = abs((box[0] + box[2] - tag_box[0] - tag_box[2]) / 2) / norm_width
            center_y = abs((box[1] + box[3] - tag_box[1] - tag_box[3]) / 2) / norm_height
            overlap = max(0, min(box[2], tag_box[2]) - max(box[0], tag_box[0])) / min(width, tag_width)
            size_penalty = 0 if 0.4 <= tag_width / width <= 2.5 else min(abs(tag_width / width - 1), 1)
            row_penalty = 0 if abs((box[1] + box[3] - tag_box[1] - tag_box[3]) / 2) < median_height * 2 else 1
            cost = (0.25 * horizontal_gap / norm_width + 0.25 * center_x
                    + 0.30 * abs(vertical_gap) / norm_height + 0.08 * (1 - overlap)
                    + 0.04 * hypot(center_x, center_y) + 0.15 * (vertical_gap < 0)
                    + 0.10 * row_penalty + 0.05 * size_penalty)
            scored.append((cost, str(item["idx"]), item, {"horizontal_gap": horizontal_gap,
                            "overlap_ratio": round(overlap, 4), "vertical_gap": vertical_gap,
                            "direction": "TAG_BELOW" if vertical_gap >= 0 else "TAG_ABOVE"}))
        scored.sort(key=lambda entry: (entry[0], entry[1]))
        if not scored:
            rows.append({"tag_id": tag["id"], "match_status": "UNMATCHED", "reason": "NO_CANDIDATE"})
            continue
        best_cost, _, best, evidence = scored[0]
        second_cost = scored[1][0] if len(scored) > 1 else None
        margin = second_cost - best_cost if second_cost is not None else None
        level = ("HIGH" if best_cost <= 0.65 and margin is not None and margin >= 0.12 else
                 "MEDIUM" if best_cost <= 1.10 and margin is not None and margin >= 0.04 else "LOW")
        claims.setdefault(best["idx"], []).append(tag["id"])
        rows.append({"tag_id": tag["id"], "sku_idx": best["idx"], "sku_code": best["sku_code"],
                     "sku_name": best["sku_name"], "sku_score": best["score"],
                     "price": tag.get("price"), "price_score": tag.get("score"),
                     "match_status": "MATCHED" if level != "LOW" else "AMBIGUOUS",
                     "spatial_confidence": level, "score": round(max(0.0, min(1.0, 1 - best_cost / 1.10)), 4),
                     "cost": round(best_cost, 4), "second_cost": round(second_cost, 4) if second_cost is not None else None,
                     "margin": round(margin, 4) if margin is not None else None,
                     "second_sku_idx": scored[1][2]["idx"] if len(scored) > 1 else None,
                     "evidence": evidence, "match_method_version": "SPATIAL_RULE_V0"})
    for row in rows:
        if row.get("match_status") == "MATCHED" and len(claims.get(row.get("sku_idx"), [])) > 1:
            row["match_status"] = "CONFLICT"
            row["evidence"]["conflicting_tag_ids"] = claims[row["sku_idx"]]
    return rows
