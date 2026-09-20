import os
import glob
import json
import shutil
from pathlib import Path

def organize_dataset(base_dir: str):
    base_path = Path(base_dir)
    with_tags_dir = base_path / '有价签'
    without_tags_dir = base_path / '无价签'

    with_tags_dir.mkdir(parents=True, exist_ok=True)
    without_tags_dir.mkdir(parents=True, exist_ok=True)

    img_exts = {'.jpg', '.jpeg', '.png', '.bmp'}
    
    # 扫描 [有价签] 和 [无价签] 两个子目录
    moved_to_with = 0
    moved_to_without = 0

    # 1. 检查无价签目录中是否有被补画了框 (shapes > 0)，如果有则移入【有价签】
    for f in list(without_tags_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in img_exts:
            json_file = without_tags_dir / f'{f.stem}.json'
            has_tags = False
            if json_file.exists():
                try:
                    with open(json_file, 'r', encoding='utf-8') as jf:
                        d = json.load(jf)
                    if len(d.get('shapes', [])) > 0:
                        has_tags = True
                except:
                    pass
            if has_tags:
                shutil.move(str(f), str(with_tags_dir / f.name))
                if json_file.exists():
                    shutil.move(str(json_file), str(with_tags_dir / json_file.name))
                moved_to_with += 1

    # 2. 检查有价签目录中是否有人工删空了框 (shapes == 0)，如果有则移入【无价签】
    for f in list(with_tags_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in img_exts:
            json_file = with_tags_dir / f'{f.stem}.json'
            has_tags = False
            if json_file.exists():
                try:
                    with open(json_file, 'r', encoding='utf-8') as jf:
                        d = json.load(jf)
                    if len(d.get('shapes', [])) > 0:
                        has_tags = True
                except:
                    pass
            if not has_tags:
                shutil.move(str(f), str(without_tags_dir / f.name))
                if json_file.exists():
                    shutil.move(str(json_file), str(without_tags_dir / json_file.name))
                moved_to_without += 1

    print('=' * 40)
    print(f'重新整理完成！')
    print(f'【无价签】中补画了框移入【有价签】: {moved_to_with} 张')
    print(f'【有价签】中删光了框移入【无价签】: {moved_to_without} 张')
    print('=' * 40)

if __name__ == '__main__':
    target_path = r'D:/Shixi/mengniu/标注数据集_AnyLabeling'
    organize_dataset(target_path)
