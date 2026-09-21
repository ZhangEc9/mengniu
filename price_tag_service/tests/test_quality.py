from app.processing.quality import normalize_quality_payload


def test_normalize_quality_aliases_and_strict_gate():
    payload = {
        "qc_result": {
            "is_valid": True,
            "should_continue": True,
            "quality_checks": {
                "图片模糊": "合格",
                "严重过曝": "合格",
                "光线不足": "合格",
                "文件损坏": "合",
            },
            "invalid_reason": "",
            "invalid_reasons": [],
        },
        "content_info": {"has_price_tag": True, "scene_type": "冷柜"},
    }
    result = normalize_quality_payload(payload)
    assert result.is_quality_pass is True
    assert result.scene_group == "冰箱照"
    assert result.is_target_scene is True
    assert result.can_proceed_to_price is True
    assert result.stop_reason is None


def test_quality_blocked_is_not_failed():
    payload = {
        "qc_result": {
            "is_valid": False,
            "quality_checks": {
                "图片模糊": "不合格",
                "过度曝光": "合格",
                "光线不足": "合格",
                "文件损坏": "合格",
            },
            "invalid_reason": "金额模糊",
        },
        "content_info": {"has_price_tag": False, "scene_type": "门店照"},
    }
    result = normalize_quality_payload(payload)
    assert result.can_proceed_to_price is False
    assert result.stop_reason == "QUALITY_REJECTED"
    assert len(result.rejection_reasons) == 3
