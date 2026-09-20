# -*- coding: utf-8 -*-
import os
import sys
import glob
import json
from pathlib import Path
from PIL import Image
from price_postprocess import resolve_tag_bbox

def convert_single_pipeline_to_labelme(pipeline_json_path: str, img_dir: str, output_dir: str) -> str:
    pipeline_json_path = Path(pipeline_json_path)
    img_dir = Path(img_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(pipeline_json_path, 'r', encoding='utf-8') as f:
        pipe_data = json.load(f)

    img_name = pipe_data.get('image_name')
    if not img_name:
        stem = pipeline_json_path.name.replace('.pipeline.json', '')
        candidates = list(img_dir.glob(f'{stem}.*'))
        if candidates:
            img_name = candidates[0].name
        else:
            return None

    img_path = img_dir / img_name
    if not img_path.exists():
        candidates = list(img_dir.glob(f'{Path(img_name).stem}.*'))
        if candidates:
            img_path = candidates[0]
            img_name = img_path.name
        else:
            print(f'Warning: Image not found: {img_name}')
            return None

    try:
        with Image.open(img_path) as im:
            img_w, img_h = im.size
    except Exception as e:
        print(f'Error opening image {img_path}: {e}')
        return None

    step2 = pipe_data.get('step2_price_tags') or {}
    tags = step2.get('tags') or []
    shapes = []
    for tag in tags:
        bbox = tag.get('bbox', [])
        coords = resolve_tag_bbox(bbox, img_w, img_h)
        if not coords:
            continue
        xmin, ymin, xmax, ymax = coords
        price = str(tag.get('price', '')).strip()
        tag_type = tag.get('tag_type', 'regular_price')
        label = price if price else 'tag'

        shape = {
            'label': label,
            'points': [
                [float(xmin), float(ymin)],
                [float(xmax), float(ymax)]
            ],
            'group_id': None,
            'shape_type': 'rectangle',
            'flags': {
                'tag_type': tag_type,
                'raw_price_text': str(tag.get('raw_price_text', ''))
            }
        }
        shapes.append(shape)

    labelme_data = {
        'version': '0.4.0',
        'flags': {},
        'shapes': shapes,
        'imagePath': img_name,
        'imageData': None,
        'imageHeight': img_h,
        'imageWidth': img_w
    }

    out_json_path = output_dir / f'{Path(img_name).stem}.json'
    with open(out_json_path, 'w', encoding='utf-8') as f:
        json.dump(labelme_data, f, ensure_ascii=False, indent=2)

    return str(out_json_path)

def convert_all(pipeline_results_dir: str, img_dir: str, output_dir: str):
    pipeline_jsons = glob.glob(os.path.join(pipeline_results_dir, '*.pipeline.json'))
    print(f'Found {len(pipeline_jsons)} pipeline jsons.')
    success_count = 0
    for p in pipeline_jsons:
        res = convert_single_pipeline_to_labelme(p, img_dir, output_dir)
        if res:
            success_count += 1
    print(f'Successfully converted {success_count}/{len(pipeline_jsons)} into {output_dir}')

if __name__ == '__main__':
    default_pipe_dir = r'D:/Shixi/mengniu/全流程运行结果/final_results'
    default_img_dir = r'D:/Shixi/mengniu/蒙牛 poc0805_images'
    default_out_dir = r'D:/Shixi/mengniu/标注数据集_AnyLabeling'
    
    if len(sys.argv) > 1 and sys.argv[1].endswith('.json'):
        target = sys.argv[1]
        out = convert_single_pipeline_to_labelme(target, default_img_dir, default_out_dir)
        print(f'Finished: {out}')
    else:
        convert_all(default_pipe_dir, default_img_dir, default_out_dir)
