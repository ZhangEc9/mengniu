from app.processing.postprocess import postprocess_price_payload


def test_postprocess_excludes_bundle_and_second_item_promotions():
    parsed = {
        "price_tags": [
            {
                "id": 1,
                "bbox": [100, 100, 180, 130],
                "price": None,
                "raw_price_text": "两件19.9元",
                "tag_type": "bundle_promotion",
            },
            {"id": 2, "bbox": [200, 100, 280, 130], "price": "9.90", "tag_type": "regular_price"},
        ],
        "promotion_tags": [
            {
                "id": 1,
                "bbox": [300, 100, 380, 130],
                "price": "1",
                "raw_price_text": "第二件1元",
                "tag_type": "second_item_promotion",
            }
        ],
    }
    result = postprocess_price_payload(
        parsed,
        image_width=1000,
        image_height=1000,
        min_price=2,
        max_price=99,
    )
    assert len(result.price_tags) == 1
    assert result.price_tags[0]["id"] == 1
    assert result.price_tags[0]["price"] == "9.90"
    assert result.price_tags[0]["unit"] == "元"
    assert len(result.promotion_tags) == 2
    assert result.promotion_tags[-1]["second_item_price"] == "1"
    rules = {event["rule"] for event in result.filter_events}
    assert "BUNDLE_PROMOTION_EXCLUDED" in rules
    assert "SECOND_ITEM_PROMOTION_EXCLUDED" in rules
    assert not any(event["rule"] == "PRICE_OUT_OF_RANGE" for event in result.filter_events)


def test_postprocess_filters_misread_price_range_and_duplicate():
    parsed = {
        "price_tags": [
            {
                "id": 1,
                "bbox": [100, 100, 180, 130],
                "price": "1.0",
                "raw_price_text": "每日鲜语250ml",
            },
            {"id": 2, "bbox": [200, 100, 280, 130], "price": "1.0", "raw_price_text": "1.0元"},
            {"id": 3, "bbox": [300, 100, 380, 130], "price": "8.90", "raw_price_text": "8.90元"},
            {"id": 4, "bbox": [300, 100, 380, 130], "price": "8.90", "raw_price_text": "8.90元"},
        ]
    }
    result = postprocess_price_payload(
        parsed, image_width=1000, image_height=1000, min_price=2, max_price=99
    )
    rules = {event["rule"] for event in result.filter_events}
    assert "MISREAD_MARKETING_OR_SPEC" in rules
    assert "PRICE_OUT_OF_RANGE" in rules
    assert "IOU_DUPLICATE" in rules
    assert len(result.price_tags) == 1
    assert result.price_tags[0]["price"] == "8.90"


def test_postprocess_blocks_hallucinated_grid():
    tags = []
    for index in range(5):
        tags.append(
            {
                "id": index + 1,
                "shelf_layer": 1,
                "bbox": [50 + index * 100, 100, 100 + index * 100, 130],
                "price": f"{5 + index}.00",
                "raw_price_text": f"{5 + index}.00元",
            }
        )
    result = postprocess_price_payload(
        {"price_tags": tags}, image_width=1000, image_height=1000, min_price=2, max_price=99
    )
    assert result.price_tags == []
    assert any(event["rule"] == "HALLUCINATED_GRID" for event in result.filter_events)
