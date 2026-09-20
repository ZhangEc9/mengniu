
import os
import sys
import argparse
import pandas as pd
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def download_one(url, save_dir):
    try:
        fname = url.split('/')[-1].split('?')[0]
        out_file = save_dir / fname
        if out_file.exists() and out_file.stat().st_size > 1000:
            return True, fname, 'cached'
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            with open(out_file, 'wb') as f:
                f.write(resp.content)
            return True, fname, 'downloaded'
        return False, fname, f'http {resp.status_code}'
    except Exception as e:
        return False, url, str(e)

def extract_and_download(excel_path, output_dir, col_name='productCloseupPhotos', max_images=50, max_workers=8):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f'Loading excel: {excel_path}')
    df = pd.read_excel(excel_path, usecols=[col_name])
    
    urls = []
    for row in df[col_name].dropna():
        for u in str(row).split(','):
            u = u.strip()
            if u.startswith('http'):
                urls.append(u)
                if len(urls) >= max_images:
                    break
        if len(urls) >= max_images:
            break

    print(f'Total {len(urls)} image URLs ready to download into: {output_dir}')
    
    success = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_one, u, output_dir): u for u in urls}
        for idx, f in enumerate(as_completed(futures), 1):
            ok, fname, status = f.result()
            if ok:
                success += 1
            if idx % 10 == 0 or idx == len(urls):
                print(f'[{idx}/{len(urls)}] {fname} -> {status}')

    print(f'Download finished: {success}/{len(urls)} downloaded into {output_dir}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=30, help='download count')
    parser.add_argument('--out-dir', default=r'D:/Shixi/mengniu/downloaded_from_excel', help='save dir')
    parser.add_argument('--col', default='productCloseupPhotos', help='column name')
    args = parser.parse_args()

    excel_path = r'D:/Shixi/mengniu/2026-09-18-14-38-17_EXPORT_XLSX_27962701_827/2026-09-18-14-38-17_EXPORT_XLSX_27962701_917_0.xlsx'
    extract_and_download(excel_path, args.out_dir, col_name=args.col, max_images=args.limit)
