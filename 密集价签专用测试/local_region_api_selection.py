"""Locally measured line-pair bands; API selects IDs instead of inventing coordinates."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

from dense_api_regions import Api, HERE, read_image, save_image, save_json
from rail_line_pair_experiment import detect_long_lines, make_pairs, edge_support, corridor_iou


SYSTEM = "你是价签带图像筛选助手。图片是本地测量后生成的多个候选裁图，左侧ID不属于价签内容。只按ID筛选，不输出坐标和价格。"
SELECT_PROMPT = """这是一张候选价签带裁图拼图。每一行是独立候选区域，左边有唯一ID，绿色上下边界之间是本地找到的窄带。
选择其中确实包含真实价签排的候选ID：一排独立商品零售价签纸或电子价签（能看到价格、商品名、条码等布局）。
不要选择商品包装上的文字数字、空导轨、灯带、格栅。被绿色边界切掉一半价签的候选也不要选。
若多个候选展示同一物理价签排，优先选绿色上下边界最完整容纳所有价签、且无大量商品背景的一行，不要重复选。
尽量不漏任何真实价签排，斜排已被本地旋转拉平。不要读价格，不要生成坐标。
只输出{"selected_ids":[1,2],"uncertain_ids":[]}。没有真实价签排返回空数组。"""


def padded_polygon(pair, width, padding):
    left, right = pair["x_range"]
    left, right = max(0, left - 32), min(width, right + 32)
    top, bottom = pair["top_line"], pair["bottom_line"]
    return np.asarray([[left, top["a"] + top["b"] * left - padding],
                       [right, top["a"] + top["b"] * right - padding],
                       [right, bottom["a"] + bottom["b"] * right + padding],
                       [left, bottom["a"] + bottom["b"] * left + padding]], np.float32)


def proposals(image, seed_regions):
    height, width = image.shape[:2]
    lines, edges = detect_long_lines(image)
    pairs = make_pairs(lines, width, height)
    for pair in pairs:
        first, second = [lines[index] for index in pair["line_indices"]]
        pair["x_range"] = [min(first["left"], second["left"]), max(first["right"], second["right"])]
        left, right = pair["x_range"]
        pair["support"] = min(edge_support(edges, pair[name], left, right) for name in ("top_line", "bottom_line"))
        pair["poly"] = padded_polygon(pair, width, 0)
        pair["center_y"] = (pair["top_line"]["a"] + pair["bottom_line"]["a"]) / 2 + pair["top_line"]["b"] * width / 2
    selected = []
    for region in seed_regions:
        if region.get("kind") != "row":
            continue
        poly = np.asarray(region["api_polygon"], float)
        center_y = float(poly[:, 1].mean())
        nearby = [pair for pair in pairs if abs(pair["center_y"] - center_y) <= max(100, height * 0.18)]
        for pair in nearby:
            pair["rank"] = 0.75 * pair["support"] + 0.25 * (pair["x_range"][1] - pair["x_range"][0]) / width - abs(
                pair["center_y"] - center_y) / max(60, height * 0.12)
        per_seed = []
        for pair in sorted(nearby, key=lambda item: item["rank"], reverse=True):
            if all(corridor_iou(pair, other) < 0.35 for other in per_seed):
                per_seed.append(pair)
            if len(per_seed) >= 6:
                break
        for pair in per_seed:
            if all(corridor_iou(pair, other) < 0.60 for other in selected):
                selected.append(pair)
    selected.sort(key=lambda item: item["center_y"])
    for index, pair in enumerate(selected, 1):
        pair["id"] = index
        pair["safe_polygon"] = padded_polygon(pair, width, max(10, pair["gap"] * 0.20)).tolist()
        pair["local_polygon"] = pair["poly"].tolist()
        pair.pop("poly")
    return selected, {"long_lines": len(lines), "line_pairs": len(pairs), "proposals": len(selected)}


def render_strip(image, pair):
    width = image.shape[1]
    padding = max(20, pair["gap"] * 0.50)
    polygon = padded_polygon(pair, width, padding)
    crop_width = max(1, round(polygon[1, 0] - polygon[0, 0]))
    crop_height = max(1, round(pair["gap"] + 2 * padding))
    target = np.asarray([[0, 0], [crop_width - 1, 0], [crop_width - 1, crop_height - 1], [0, crop_height - 1]], np.float32)
    transform = cv2.getPerspectiveTransform(polygon, target)
    crop = cv2.warpPerspective(image, transform, (crop_width, crop_height), borderMode=cv2.BORDER_CONSTANT,
                               borderValue=(235, 235, 235))
    for offset in (padding, padding + pair["gap"]):
        cv2.line(crop, (0, round(offset)), (crop_width - 1, round(offset)), (0, 170, 0), 2)
    scale = 1280 / crop_width
    crop = cv2.resize(crop, (1280, max(48, round(crop_height * scale))), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((crop.shape[0] + 12, 1376, 3), 255, np.uint8)
    canvas[6:6 + crop.shape[0], 96:] = crop
    cv2.putText(canvas, f"ID{pair['id']:02d}", (7, min(38, canvas.shape[0] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.70,
                (0, 0, 0), 2, cv2.LINE_AA)
    return canvas


def process(seed_path, output, api, workers):
    saved = json.loads(seed_path.read_text(encoding="utf-8"))
    image = read_image(Path(saved["image"]))
    directory = output / seed_path.parent.name
    candidates, counts = proposals(image, saved["regions"])
    save_json(directory / "proposals.json", {"counts": counts, "proposals": candidates,
              "gt_used_in_inference": False, "seed_file": str(seed_path)})
    pages = [candidates[index:index + 10] for index in range(0, len(candidates), 10)]
    selected_ids, uncertain_ids, failures = set(), set(), []

    def select_page(page_index, page):
        contact_path = directory / "contact_sheets" / f"page_{page_index:02d}.jpg"
        save_image(contact_path, np.concatenate([render_strip(image, pair) for pair in page], axis=0))
        parsed = api.call(contact_path, SYSTEM, SELECT_PROMPT, directory / "selection_api" / f"page_{page_index:02d}")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("selected_ids"), list):
            raise ValueError("Expected selected_ids array")
        allowed = {pair["id"] for pair in page}
        selected = {int(value) for value in parsed["selected_ids"]}
        uncertain = {int(value) for value in parsed.get("uncertain_ids", [])}
        if not (selected | uncertain) <= allowed:
            raise ValueError("Unknown proposal ID returned")
        return selected, uncertain

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(select_page, index, page): index for index, page in enumerate(pages)}
        for future in as_completed(pending):
            try:
                selected, uncertain = future.result()
                selected_ids.update(selected)
                uncertain_ids.update(uncertain)
            except Exception as exc:
                failures.append({"page": pending[future], "error": str(exc)})
    selected = [pair for pair in candidates if pair["id"] in selected_ids]
    regions = [{"id": pair["id"], "kind": "row", "local_polygon": pair["local_polygon"],
                "safe_polygon": pair["safe_polygon"], "line_support": pair["support"],
                "angle_deg": pair["angle_deg"]} for pair in selected]
    save_json(directory / "regions.json", {"image": saved["image"], "image_size": saved["image_size"],
              "regions": regions, "selected_ids": sorted(selected_ids), "uncertain_ids": sorted(uncertain_ids),
              "failures": failures, "gt_used_in_inference": False})
    canvas = image.copy()
    for region in regions:
        cv2.polylines(canvas, [np.asarray(region["local_polygon"], np.int32)], True, (0, 180, 0), 2)
        cv2.polylines(canvas, [np.asarray(region["safe_polygon"], np.int32)], True, (0, 180, 255), 1)
    save_image(directory / "selected_regions.jpg", canvas)
    return {"image": seed_path.parent.name, **counts, "selected_ids": sorted(selected_ids), "failures": failures}


def main():
    parser = argparse.ArgumentParser(description="API selects locally generated bands by ID, never supplies geometry")
    parser.add_argument("--seeds", type=Path, default=HERE / "API区域本地找框实验" / "region_v1")
    parser.add_argument("--output", type=Path, default=HERE / "API区域本地找框实验" / "local_select_v1")
    parser.add_argument("--stems", nargs="*")
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    api = Api(args.max_calls, args.timeout)
    summaries = []
    for seed_path in sorted(args.seeds.glob("*/regions.json")):
        if args.stems and not any(seed_path.parent.name.startswith(prefix) for prefix in args.stems):
            continue
        summaries.append(process(seed_path, args.output, api, args.workers))
        save_json(args.output / "run_summary.json", {"api_calls_this_run": api.calls, "images": summaries})
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
