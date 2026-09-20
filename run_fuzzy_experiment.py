# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import requests
import hashlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from run_full_pipeline import (
    PRICE_API_URL, PRICE_APPID, PRICE_SECRET,
    make_mn_sign, parse_robust_json, load_oss_map, save_oss_map, upload_to_mengniu_oss,
    resolve_tag_bbox
)

BASE_DIR = Path(r'D:/Shixi/mengniu')
FUZZY_IMG_DIR = BASE_DIR / '标注数据集_AnyLabeling' / '有价签但价签模糊'
OUTPUT_DIR = BASE_DIR / '模糊价签识别实验_无防御'
JSON_OUT_DIR = OUTPUT_DIR / 'final_results'
VIS_OUT_DIR = OUTPUT_DIR / 'vis_images'

JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
VIS_OUT_DIR.mkdir(parents=True, exist_ok=True)

PROMPTS_DIR = BASE_DIR / 'prompts'
SYS_PROMPT_FILE = PROMPTS_DIR / 'price_system_prompt_unrestricted.txt'
USR_PROMPT_FILE = PROMPTS_DIR / 'price_user_prompt_unrestricted.txt'

sys_prompt = SYS_PROMPT_FILE.read_text(encoding='utf-8').strip()
usr_prompt = USR_PROMPT_FILE.read_text(encoding='utf-8').strip()

def call_price_model(image_url: str, img_w: int, img_h: int, timeout: int = 360):
    body = {
        'image': image_url,
        'system_text': sys_prompt,
        'text': usr_prompt,
        'userId': '',
        'envSystemName': '',
        'userName': '',
        'envSystemVersion': '',
        'unionId': '',
        'apiVersion': '0.0.2'
    }
    timestamp = str(int(time.time() * 1000))
    headers = {
        'X-MN-APP-ID': PRICE_APPID,
        'X-MN-SIGN': make_mn_sign(body, PRICE_SECRET, timestamp),
        'X-MN-TIMESTAMP': timestamp,
        'Content-Type': 'application/json',
    }
    t0 = time.time()
    try:
        resp = requests.post(PRICE_API_URL, headers=headers, json=body, timeout=timeout)
        resp.raise_for_html = False
        res_data = resp.json()
    except Exception as e:
        return {'error': str(e)}, time.time() - t0

    elapsed = time.time() - t0
    payload = res_data.get('payload', {})
    page_content = payload.get('result', {}).get('page_content', '')
    parsed = parse_robust_json(page_content)
    return parsed, elapsed

def draw_bboxes(img_path: Path, tags: list, out_path: Path):
    try:
        font = ImageFont.truetype('msyh.ttc', 16)
    except Exception:
        font = ImageFont.load_default()

    with Image.open(img_path) as im:
        drawn = im.convert('RGB')
        w, h = drawn.size

    draw = ImageDraw.Draw(drawn)
    for t in tags:
        bbox = t.get('bbox', [])
        coords = resolve_tag_bbox(bbox, w, h)
        if not coords:
            continue
        xmin, ymin, xmax, ymax = coords
        price = str(t.get('price', 'tag')).strip()
        draw.rectangle([xmin, ymin, xmax, ymax], outline='red', width=3)
        
        text_y = max(0, ymin - 20)
        tb = draw.textbbox((xmin, text_y), price, font=font)
        draw.rectangle([tb[0]-2, tb[1]-2, tb[2]+2, tb[3]+2], fill='red')
        draw.text((xmin, text_y), price, fill='white', font=font)

    drawn.save(out_path, quality=95)

def main():
    print('=' * 70)
    print('开始执行【模糊价签无防御识别实验】')
    print('输入目录:', FUZZY_IMG_DIR)
    print('输出目录:', OUTPUT_DIR)
    print('=' * 70)

    oss_map = load_oss_map()
    valid_exts = {'.jpg', '.jpeg', '.png', '.webp'}
    images = sorted([p for p in FUZZY_IMG_DIR.iterdir() if p.suffix.lower() in valid_exts])
    print(f'待测试图片总数: {len(images)} 张\n')

    for idx, img_p in enumerate(images, 1):
        print(f'[{idx}/{len(images)}] 正在处理: {img_p.name} ...')
        
        img_url = oss_map.get(img_p.name)
        if not img_url:
            print('  -> 上传图片至 OSS...')
            img_url = upload_to_mengniu_oss(img_p)
            if img_url:
                oss_map[img_p.name] = img_url
                save_oss_map(oss_map)
            else:
                print('  [!] OSS 上传失败，跳过')
                continue

        with Image.open(img_p) as im:
            w, h = im.size

        parsed_tags, elapsed = call_price_model(img_url, w, h)
        tag_count = len(parsed_tags) if isinstance(parsed_tags, list) else 0
        print(f'  -> 模型耗时: {elapsed:.2f}s, 检出价签数: {tag_count}')

        res_record = {
            'image_name': img_p.name,
            'image_url': img_url,
            'width': w,
            'height': h,
            'elapsed_seconds': elapsed,
            'raw_detected_tags': parsed_tags
        }
        json_file = JSON_OUT_DIR / f'{img_p.stem}.json'
        with open(json_file, 'w', encoding='utf-8') as jf:
            json.dump(res_record, jf, ensure_ascii=False, indent=2)

        if isinstance(parsed_tags, list) and len(parsed_tags) > 0:
            vis_file = VIS_OUT_DIR / f'{img_p.stem}.jpg'
            draw_bboxes(img_p, parsed_tags, vis_file)

    print('\n' + '=' * 70)
    print('模糊价签无防御实验全部完成！')
    print('JSON 结果路径:', JSON_OUT_DIR)
    print('带框可视化图路径:', VIS_OUT_DIR)
    print('=' * 70)

if __name__ == '__main__':
    main()
