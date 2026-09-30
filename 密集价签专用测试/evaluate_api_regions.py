"""Evaluate saved semantic regions; annotations never enter inference."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from dense_api_regions import HERE, read_image, save_image, save_json
from rail_first_experiment import load_annotations


def intersection_area(first, second):
    first = np.asarray(first, np.float32)
    second = np.asarray(second, np.float32)
    return float(cv2.intersectConvexConvex(first, second)[0])


def polygon_iou(first, second):
    intersection = intersection_area(first, second)
    union = cv2.contourArea(np.asarray(first, np.float32)) + cv2.contourArea(np.asarray(second, np.float32)) - intersection
    return intersection / max(1e-9, union)


def box_polygon(box):
    left, top, right, bottom = box
    return np.asarray([[left, top], [right, top], [right, bottom], [left, bottom]], np.float32)


def coverage(box, polygon):
    area = (box[2] - box[0]) * (box[3] - box[1])
    return float(min(1.0, intersection_area(box_polygon(box), polygon) / max(1e-9, area)))


def evaluate_one(saved_path, annotations, polygon_key):
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    tags, rails, size = load_annotations(annotations / f"{saved_path.parent.name}.json")
    polygons = [np.asarray(region[polygon_key], np.float32) for region in saved["regions"]]
    tag_results = []
    for index, tag in enumerate(tags):
        overlaps = [coverage(tag["bbox"], polygon) for polygon in polygons]
        best_index = int(np.argmax(overlaps)) if overlaps else None
        best_coverage = max(overlaps, default=0)
        tag_results.append({"index": index, "bbox": tag["bbox"], "label": tag["label"],
                            "best_region": best_index, "coverage": best_coverage,
                            "center_covered": any(cv2.pointPolygonTest(polygon, ((tag["bbox"][0] + tag["bbox"][2]) / 2,
                                (tag["bbox"][1] + tag["bbox"][3]) / 2), False) >= 0 for polygon in polygons),
                            "fully_covered": best_coverage >= 0.95})
    rail_results = []
    for index, rail in enumerate(rails):
        contained = [result for result in tag_results if cv2.pointPolygonTest(rail["poly"].astype(np.float32),
                     ((result["bbox"][0] + result["bbox"][2]) / 2,
                      (result["bbox"][1] + result["bbox"][3]) / 2), False) >= 0]
        overlaps = [polygon_iou(rail["poly"], polygon) for polygon in polygons]
        best_index = int(np.argmax(overlaps)) if overlaps else None
        rail_results.append({"rail_index": index, "best_region": best_index,
            "best_polygon_iou": max(overlaps, default=0), "tag_count": len(contained),
            "fully_covered_tags": sum(result["fully_covered"] for result in contained),
            "missed_tags": [result["index"] for result in contained if not result["fully_covered"]]})
    pairs = sorted([(polygon_iou(rail["poly"], polygon), rail_index, region_index)
                   for rail_index, rail in enumerate(rails) for region_index, polygon in enumerate(polygons)], reverse=True)
    used_rails, used_regions, matches = set(), set(), []
    for score, rail_index, region_index in pairs:
        if score >= 0.5 and rail_index not in used_rails and region_index not in used_regions:
            matches.append({"iou": score, "rail_index": rail_index, "region_index": region_index})
            used_rails.add(rail_index)
            used_regions.add(region_index)
    areas = [cv2.contourArea(polygon) for polygon in polygons]
    summary = {"image": saved_path.parent.name, "gt_tags": len(tags), "gt_rails": len(rails),
               "pred_regions": len(polygons), "rail_matches_iou05": len(matches),
               "fully_covered_tags": sum(result["fully_covered"] for result in tag_results),
               "center_covered_tags": sum(result["center_covered"] for result in tag_results),
               "region_area_sum_ratio": sum(areas) / (size[0] * size[1]),
               "invalid_regions": len(saved.get("invalid", [])), "tag_results": tag_results,
               "rail_results": rail_results, "rail_matches": matches,
               "regions_without_tag_center": [index for index, polygon in enumerate(polygons) if not any(
                   cv2.pointPolygonTest(polygon, ((tag["bbox"][0] + tag["bbox"][2]) / 2,
                       (tag["bbox"][1] + tag["bbox"][3]) / 2), False) >= 0 for tag in tags)]}
    image = read_image(Path(saved["image"]))
    for rail in rails:
        cv2.polylines(image, [rail["poly"].astype(np.int32)], True, (255, 100, 0), 2)
    for polygon in polygons:
        cv2.polylines(image, [polygon.astype(np.int32)], True, (0, 180, 255), 2)
    for result in tag_results:
        left, top, right, bottom = [round(value) for value in result["bbox"]]
        color = (0, 180, 0) if result["fully_covered"] else (0, 0, 255)
        cv2.rectangle(image, (left, top), (right, bottom), color, 1)
    save_image(saved_path.parent / f"evaluation_{polygon_key}.jpg", image)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate saved region polygons with all manual tags and rails")
    parser.add_argument("--predictions", type=Path, default=HERE / "API区域本地找框实验" / "region_v1")
    parser.add_argument("--annotations", type=Path, default=HERE / "新数据")
    parser.add_argument("--polygon-key", default="api_polygon")
    args = parser.parse_args()
    reports = []
    for annotation in sorted(args.annotations.glob("*.json")):
        saved_path = args.predictions / annotation.stem / "regions.json"
        if saved_path.exists():
            reports.append(evaluate_one(saved_path, args.annotations, args.polygon_key))
        else:
            tags, rails, _ = load_annotations(annotation)
            reports.append({"image": annotation.stem, "gt_tags": len(tags), "gt_rails": len(rails),
                            "pred_regions": 0, "rail_matches_iou05": 0, "fully_covered_tags": 0,
                            "center_covered_tags": 0, "error": "Missing predictions; counted as missed"})
    totals = {key: sum(report[key] for report in reports) for key in (
        "gt_tags", "gt_rails", "pred_regions", "rail_matches_iou05", "fully_covered_tags", "center_covered_tags")}
    totals["full_tag_coverage"] = totals["fully_covered_tags"] / max(1, totals["gt_tags"])
    report = {"polygon_key": args.polygon_key, "totals": totals, "images": reports,
              "note": "Full coverage means >=95% of each tag's area inside a single predicted region. Not tag detection recall."}
    save_json(args.predictions / f"evaluation_{args.polygon_key}.json", report)
    for item in reports:
        print(item["image"][:8], "regions", item["pred_regions"], "rails@.5", f"{item['rail_matches_iou05']}/{item['gt_rails']}",
              "full tags", f"{item['fully_covered_tags']}/{item['gt_tags']}", "center", item["center_covered_tags"])
    print(json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    main()
