from __future__ import annotations

from decimal import Decimal, InvalidOperation

DEMO_TASK_REMARK = "DEMO ONLY: synthetic business attribution and SKU price range"
DEMO_MIN_PRICE = Decimal("10.00")
DEMO_MAX_PRICE = Decimal("15.00")
DEMO_RESULT_REMARK = "DEMO ONLY: synthetic SKU price range; not a business price decision"


def demo_fusion_rows(pairs: list[dict], sku_items: list[dict], tags: list[dict]) -> list[dict]:
    sku_by_idx = {item.get("idx"): item for item in sku_items}
    tag_by_id = {tag.get("id"): tag for tag in tags}
    rows = []
    for pair in pairs:
        if pair.get("match_status") != "MATCHED":
            continue
        try:
            price = Decimal(str(pair["price"])).quantize(Decimal("0.01"))
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise ValueError("Matched demo price is invalid") from exc
        if not price.is_finite() or price < 0:
            raise ValueError("Matched demo price is invalid")
        sku_item = sku_by_idx[pair["sku_idx"]]
        tag = tag_by_id[pair["tag_id"]]
        rows.append(dict(
            sku_code=pair["sku_code"], sku_name=pair["sku_name"] or pair["sku_code"],
            price=price, sku_min_price=DEMO_MIN_PRICE, sku_max_price=DEMO_MAX_PRICE,
            price_check_result=0 if DEMO_MIN_PRICE <= price <= DEMO_MAX_PRICE else 1,
            original_sku_bbox=sku_item["bbox"], original_price_bbox=tag["bbox"],
            remark=DEMO_RESULT_REMARK,
        ))
    return rows
