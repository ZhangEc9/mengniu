# -*- coding: utf-8 -*-
"""
价签后处理与防幻觉过滤器模块 (Post-Processing & Hallucination Filter)
独立承接 run_full_pipeline.py 中的清洗、去重、防脑补与误报过滤逻辑。
"""

import re

# 1. 组合促销正则（如：两件18元、2件20.9元）
BUNDLE_PROMOTION_RE = re.compile(
    r"(\d+|两)\s*件\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块|￥|RMB)?", 
    re.IGNORECASE
)

# 2. 第二件促销正则（如：第二件2元、第2件半价）
SECOND_ITEM_PROMOTION_RE = re.compile(r"第\s*(?:二|2)\s*件")
SECOND_ITEM_PRICE_RE = re.compile(
    r"第\s*(?:二|2)\s*件\D{0,8}(\d+(?:\.\d{1,2})?)\s*(?:元|块|￥|RMB)?",
    re.IGNORECASE,
)

# 3. 拦截纯增购促销语（如：加1元多一件、加X元多）
ADD_PROMOTION_RE = re.compile(
    r"加\s*(?:\d+|[一两])\s*(?:元|块|￥)?\s*(?:多|换|购|送)", 
    re.IGNORECASE
)


def _promotion_text(tag: dict) -> str:
    fields = (
        "raw_promotion_text",
        "promotion_text",
        "raw_price_text",
        "label_text",
        "text",
    )
    return " ".join(str(tag.get(field, "")).strip() for field in fields).strip()


def _compute_iou(b1, b2):
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0


def resolve_tag_bbox(bbox: list, img_w: int, img_h: int):
    """自适应宽高比与坐标倒置纠偏"""
    if len(bbox) < 4:
        return None
    v0, v1, v2, v3 = bbox[:4]
    xmin_A, xmax_A = min(v0, v2), max(v0, v2)
    ymin_A, ymax_A = min(v1, v3), max(v1, v3)
    w_px_A = (xmax_A - xmin_A) / 1000.0 * img_w
    h_px_A = (ymax_A - ymin_A) / 1000.0 * img_h
    
    ymin_B, ymax_B = min(v0, v2), max(v0, v2)
    xmin_B, xmax_B = min(v1, v3), max(v1, v3)
    w_px_B = (xmax_B - xmin_B) / 1000.0 * img_w
    h_px_B = (ymax_B - ymin_B) / 1000.0 * img_h

    if h_px_A > img_h * 0.45 or (h_px_A > w_px_A * 2.5 and w_px_B > h_px_B * 0.5):
        xmin = int(xmin_B / 1000.0 * img_w)
        ymin = int(ymin_B / 1000.0 * img_h)
        xmax = int(xmax_B / 1000.0 * img_w)
        ymax = int(ymax_B / 1000.0 * img_h)
    else:
        xmin = int(xmin_A / 1000.0 * img_w)
        ymin = int(ymin_A / 1000.0 * img_h)
        xmax = int(xmax_A / 1000.0 * img_w)
        ymax = int(ymax_A / 1000.0 * img_h)
        
    ymin = max(0, min(img_h - 1, ymin))
    ymax = max(0, min(img_h - 1, ymax))
    xmin = max(0, min(img_w - 1, xmin))
    xmax = max(0, min(img_w - 1, xmax))
    return xmin, ymin, xmax, ymax


def is_pure_discount_tag(tag: dict) -> bool:
    """过滤打折宣传（8折/全场5折）"""
    raw_text = _promotion_text(tag)
    if re.search(r"^(?:[打]?\s*\d+(?:\.\d+)?\s*折|全场\s*\d+(?:\.\d+)?\s*折)$", raw_text):
        return True
    tag_type = str(tag.get("tag_type", "")).lower()
    if tag_type in ("discount", "discount_promotion"):
        return True
    return False


def is_invalid_misread_price(tag: dict) -> bool:
    """
    核心过滤：误将型号规格数字(PRO4.0/250ml/3.6g)或增购标语(加1元多一件)当成单价的伪标签
    """
    raw_text = _promotion_text(tag)
    price = str(tag.get("price", "")).strip()

    # 1. 过滤“加1元多一件”、“加X元换购”等增购活动标语
    if ADD_PROMOTION_RE.search(raw_text):
        print(f"        [!] 拦截增购宣传带非商品单价: '{raw_text}' (误读价格: {price})")
        return True

    # 2. 过滤误将商品型号 PRO4.0 / PRO3.6 提取为价格 4.00 / 3.60
    if re.search(r"pro\s*\d(?:\.\d+)?", raw_text, re.IGNORECASE):
        m = re.search(r"pro\s*(\d(?:\.\d+)?)", raw_text, re.IGNORECASE)
        if m:
            spec_num = m.group(1)
            try:
                if float(price) == float(spec_num):
                    print(f"        [!] 拦截商品型号(PRO4.0)误读单价: '{raw_text}' (误读价格: {price})")
                    return True
            except Exception:
                pass

    # 3. 过滤文字中只包含品名与规格(ml/g/L)，无任何价格单位/符号，却提取出容量数字的伪标签
    if re.search(r"\d+\s*(?:ml|毫升|g|克|l|升)", raw_text, re.IGNORECASE):
        if not re.search(r"[元块￥]|rmb", raw_text, re.IGNORECASE):
            # 进一步排除像 '每日鲜语PRO4.0鲜牛奶250ml' 这种纯品名
            if any(kw in raw_text for kw in ["牛奶", "鲜奶", "乳", "饮品", "每日鲜语", "光明"]):
                print(f"        [!] 拦截纯品名规格包装文字误读: '{raw_text}'")
                return True

    return False


def is_bundle_promotion(tag: dict) -> bool:
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").strip().lower()
    if tag_type in ("bundle_promotion", "multi_item_promotion"):
        return True
    raw_text = _promotion_text(tag)
    return bool(BUNDLE_PROMOTION_RE.search(raw_text))


def normalize_bundle_promotion(tag: dict) -> dict:
    """规范化多件促销 (如两件18元, 2件20.9元)"""
    normalized = dict(tag)
    raw_text = _promotion_text(normalized)
    qty = normalized.get("bundle_quantity")
    bprice = normalized.get("bundle_price")
    if not qty or not bprice:
        m = BUNDLE_PROMOTION_RE.search(raw_text)
        if m:
            qty_str, price_str = m.group(1), m.group(2)
            qty = 2 if qty_str == "两" else int(qty_str)
            bprice = price_str
    normalized["tag_type"] = "bundle_promotion"
    normalized["promotion_type"] = "bundle_promotion"
    normalized["bundle_quantity"] = qty or 2
    raw_bp = str(bprice or normalized.get("price") or "").strip()
    try:
        normalized["bundle_price"] = f"{float(raw_bp):.2f}"
    except Exception:
        normalized["bundle_price"] = raw_bp
    if not normalized.get("price"):
        normalized["price"] = normalized["bundle_price"]
    normalized["raw_promotion_text"] = raw_text
    return normalized


def is_second_item_promotion(tag: dict) -> bool:
    tag_type = str(tag.get("tag_type") or tag.get("promotion_type") or "").strip().lower()
    return tag_type in ("second_item_price", "second_item_promotion") or bool(SECOND_ITEM_PROMOTION_RE.search(_promotion_text(tag)))


def normalize_second_item_promotion(tag: dict) -> dict:
    """规范化第二件促销"""
    normalized = dict(tag)
    raw_text = _promotion_text(normalized)
    price = str(normalized.get("second_item_price") or "").strip()
    if not price:
        matched = SECOND_ITEM_PRICE_RE.search(raw_text)
        price = matched.group(1) if matched else str(normalized.get("price") or "").strip()
    normalized["promotion_type"] = "second_item_price"
    normalized["second_item_price"] = price
    normalized["raw_promotion_text"] = raw_text
    normalized.pop("price", None)
    normalized.pop("raw_price_text", None)
    return normalized


def is_hallucinated_sequence(ltags: list) -> bool:
    """轻量级防脑补检查：若某层 >=5 个等距网格或公差固定的等差价格，视为自回归脑补"""
    if len(ltags) < 5:
        return False
    widths = [abs(t.get("bbox", [0, 0, 0, 0])[2] - t.get("bbox", [0, 0, 0, 0])[0]) for t in ltags if len(t.get("bbox", [])) >= 4]
    xmins = [t.get("bbox", [0, 0, 0, 0])[0] for t in ltags if len(t.get("bbox", [])) >= 4]
    if len(widths) < 5:
        return False
    if max(widths) - min(widths) <= 3:
        steps = [xmins[i+1] - xmins[i] for i in range(len(xmins)-1)]
        if max(steps) - min(steps) <= 3:
            return True
    try:
        p_floats = [float(re.sub(r"[^\d.]", "", str(t.get("price", "")))) for t in ltags if t.get("price")]
        if len(p_floats) >= 5:
            p_diffs = [round(p_floats[i+1] - p_floats[i], 2) for i in range(len(p_floats)-1)]
            if len(set(p_diffs)) == 1 and p_diffs[0] in (0.5, 1.0, 2.0):
                return True
    except Exception:
        pass
    return False


def smart_consecutive_repetition_filter(tags: list, img_w: int = 1000, img_h: int = 1000, min_h_px: int = 30, min_w_px: int = 40) -> list:
    """行级清洗、去重与防大框"""
    if not tags:
        return []
    layer_groups = {}
    for t in tags:
        l = t.get("shelf_layer", 1)
        layer_groups.setdefault(l, []).append(t)

    cleaned_tags = []
    for layer, ltags in layer_groups.items():
        ltags = [t for t in ltags if not is_pure_discount_tag(t) and not is_invalid_misread_price(t)]
        
        if is_hallucinated_sequence(ltags):
            print(f"        [!] 拦截模型等差数列/固定网格脑补幻觉: 层级 {layer} 共有 {len(ltags)} 个规则排列项，整行剔除！")
            continue

        distinct_p = {str(t.get("price", "")).strip() for t in ltags}
        if len(ltags) >= 6 and len(distinct_p) == 1:
            print(f"        [!] 触发整行自回归虚构熔断: 层级 {layer} 纯同价 {distinct_p} 数量达 {len(ltags)} 个，整行剔除！")
            continue

        filtered_layer = []
        last_price = None
        consecutive_count = 0
        for t in ltags:
            curr_price = str(t.get("price", "")).strip()
            if curr_price == last_price:
                consecutive_count += 1
            else:
                last_price = curr_price
                consecutive_count = 1
            if consecutive_count <= 3:
                filtered_layer.append(t)
        cleaned_tags.extend(filtered_layer)

    # 尺寸硬防御:
    # 1. 过滤通栏超宽大框 (宽度 > 60% 判定为整排合并错误大框)
    # 2. 物理像素硬过滤: 若价签在真实图片中的高 < min_h_px(默认30px) 或 宽 < min_w_px(默认40px)，坚决剔除远景不可辨噪点
    valid_size_tags = []
    for t in cleaned_tags:
        b = t.get("bbox", [])
        if len(b) >= 4:
            coords = resolve_tag_bbox(b, img_w, img_h)
            if not coords:
                continue
            xmin, ymin, xmax, ymax = coords
            tag_w = xmax - xmin
            tag_h = ymax - ymin

            # 通栏大框防御 (跨度 > 60% 全图宽)
            if tag_w > img_w * 0.60:
                print(f"        [!] 拦截异常通栏超宽大框: 宽={tag_w}px(>{img_w*0.6:.0f}px), 价格={t.get('price')}")
                continue

            # 仅做底线微型噪点过滤 (高度 < 12px 或 宽度 < 20px，避免误杀宽幅近景清晰图)
            if tag_h < 12 or tag_w < 20:
                print(f"        [!] 过滤微型噪点价签: {tag_w}x{tag_h}px, 价格={t.get('price')}")
                continue
        valid_size_tags.append(t)

    final_list = []
    for t in valid_size_tags:
        b = t.get("bbox", [])
        coords = resolve_tag_bbox(b, img_w, img_h)
        if not coords:
            continue
        is_dup = False
        for _, existing_coords in final_list:
            if _compute_iou(coords, existing_coords) > 0.65:
                is_dup = True
                break
        if not is_dup:
            final_list.append((t, coords))

    res = [item[0] for item in final_list]
    for idx, t in enumerate(res, 1):
        t["id"] = idx
    return res


def split_price_and_promotion_tags(parsed) -> tuple[list, list]:
    """拆分普通价签与促销价签，并执行前置误报剔除"""
    if isinstance(parsed, list):
        raw_price_tags, raw_promotion_tags = parsed, []
    elif isinstance(parsed, dict):
        raw_price_tags = parsed.get("price_tags", [])
        raw_promotion_tags = parsed.get("promotion_tags", [])
    else:
        raw_price_tags, raw_promotion_tags = [], []

    price_tags = []
    promotion_tags = []
    for tag in raw_price_tags if isinstance(raw_price_tags, list) else []:
        if not isinstance(tag, dict):
            continue
        if is_pure_discount_tag(tag) or is_invalid_misread_price(tag):
            continue
        if is_second_item_promotion(tag):
            promotion_tags.append(normalize_second_item_promotion(tag))
        elif is_bundle_promotion(tag):
            norm_bundle = normalize_bundle_promotion(tag)
            price_tags.append(norm_bundle)
            promotion_tags.append(norm_bundle)
        else:
            price_tags.append(tag)
    for tag in raw_promotion_tags if isinstance(raw_promotion_tags, list) else []:
        if isinstance(tag, dict):
            if is_pure_discount_tag(tag) or is_invalid_misread_price(tag):
                continue
            if is_bundle_promotion(tag):
                promotion_tags.append(normalize_bundle_promotion(tag))
            else:
                promotion_tags.append(normalize_second_item_promotion(tag))
    return price_tags, promotion_tags


def valid_promotion_tags(tags: list) -> list:
    """最终促销签有效性校验"""
    result = []
    for tag in tags:
        bbox = tag.get("bbox", [])
        if tag.get("promotion_type") == "bundle_promotion":
            price = str(tag.get("bundle_price") or tag.get("price") or "").strip().lower()
        else:
            price = str(tag.get("second_item_price", "")).strip().lower()
        if len(bbox) >= 4 and price not in ("", "none", "null"):
            result.append(tag)
    for idx, tag in enumerate(result, 1):
        tag["id"] = idx
    return result