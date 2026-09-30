# -*- coding: utf-8 -*-
"""Image-only angle-sweep row grouping experiment."""

import json
import math
from pathlib import Path

import numpy as np

import rail_first_experiment as base
from rail_followup_experiment import run_stage_b


HERE = Path(__file__).resolve().parent
NEW_DIR = HERE / "\u65b0\u6570\u636e"
OUT_DIR = HERE / "\u5bfc\u8f68\u4f18\u5148\u540e\u7eed\u5b9e\u9a8c" / "\u89d2\u5ea6\u626b\u63cf"


def fit_candidate(members, h_med, angle):
    if len(members) < 2:
        return None
    x = np.asarray([m["cx"] for m in members], float)
    y = np.asarray([m["cy"] for m in members], float)
    slope = math.tan(math.radians(angle))
    intercept = float(np.median(y - slope * x))
    residual = np.abs(y - (intercept + slope * x))
    good = residual <= base.ROW_RESIDUAL_REL * h_med
    if good.sum() < 2:
        return None
    x_good = x[good]
    y_good = y[good]
    fit_slope, fit_intercept = np.polyfit(x_good, y_good, 1)
    fit_residual = np.abs(y_good - (fit_intercept + fit_slope * x_good))
    if fit_residual.max() > base.ROW_RESIDUAL_REL * h_med:
        return None
    selected = [member for member, keep in zip(members, good) if keep]
    return {
        "members": selected,
        "a": float(fit_intercept),
        "b": float(fit_slope),
        "angle": base.normalize_angle_deg(math.degrees(math.atan(fit_slope))),
        "x0": min(member["bbox"][0] for member in selected),
        "x1": max(member["bbox"][2] for member in selected),
        "n": len(selected),
        "resid_max": float(fit_residual.max()),
        "h_med": h_med,
    }


def overlap_ratio(first, second):
    first_ids = {id(item) for item in first["members"]}
    second_ids = {id(item) for item in second["members"]}
    return len(first_ids & second_ids) / max(1, min(len(first_ids), len(second_ids)))


def sweep_rows(entities, width):
    if not entities:
        return []
    h_med = float(np.median([entity["h"] for entity in entities]))
    cluster_tol = 0.55 * h_med
    candidates = []
    for angle in np.arange(-12.01, 12.01, 0.25):
        slope = math.tan(math.radians(float(angle)))
        ordered = sorted(entities, key=lambda item: item["cy"] - slope * item["cx"])
        clusters = []
        for entity in ordered:
            value = entity["cy"] - slope * entity["cx"]
            if not clusters or value - clusters[-1]["last"] > cluster_tol:
                clusters.append({"last": value, "members": [entity]})
            else:
                clusters[-1]["members"].append(entity)
                clusters[-1]["last"] = value
        for cluster in clusters:
            row = fit_candidate(cluster["members"], h_med, float(angle))
            if row is None:
                continue
            if row["n"] < base.ROW_MIN_ENTITIES:
                continue
            if row["x1"] - row["x0"] < base.ROW_X_SPAN_REL * width:
                continue
            candidates.append(row)
    candidates.sort(key=lambda row: (row["n"], row["x1"] - row["x0"], -row["resid_max"]), reverse=True)
    selected = []
    for candidate in candidates:
        duplicate = False
        for other in selected:
            if overlap_ratio(candidate, other) >= 0.55:
                duplicate = True
                break
            lo = max(candidate["x0"], other["x0"])
            hi = min(candidate["x1"], other["x1"])
            if hi <= lo:
                continue
            x = (lo + hi) / 2
            distance = abs(candidate["a"] + candidate["b"] * x - other["a"] - other["b"] * x)
            if distance < 0.8 * h_med and abs(candidate["angle"] - other["angle"]) < 1.5:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
    selected.sort(key=lambda row: row["a"])
    return selected


def build_rails(rows, image, edges):
    height, width = image.shape[:2]
    rails = []
    for row in rows:
        row["bot_edge_sup"] = base.bottom_edge_support(row, edges)
        rail = base.build_corridor(row, width, height, edges)
        if rail:
            rails.append(rail)
    return rails


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for annotation in sorted(NEW_DIR.glob("*.json")):
        stem = annotation.stem
        image_path = next((NEW_DIR / f"{stem}{ext}" for ext in (".jpg", ".jpeg") if (NEW_DIR / f"{stem}{ext}").exists()), None)
        if image_path is None:
            continue
        image = base.read_image(image_path)
        height, width = image.shape[:2]
        edges = base.canny_edges(image)
        entities = base.detect_entities(image)
        rows = sweep_rows(entities, width)
        rails = build_rails(rows, image, edges)
        rail_boxes, details = run_stage_b(image, edges, rails)
        tags, user_rails, _ = base.load_annotations(NEW_DIR / f"{stem}.json")
        predictions = [box for index in sorted(rail_boxes) for box in rail_boxes[index]]
        ground_truth = [tag["bbox"] for tag in tags]
        matches = {}
        for threshold in (0.3, 0.5, 0.7):
            found = base.match_greedy(predictions, ground_truth, threshold)
            matches[str(threshold)] = len(found)
        rail_eval, false_rails = base.evaluate_rails(rails, user_rails, tags, width, height)
        result = {
            "image": stem,
            "entities": len(entities),
            "rows": len(rows),
            "auto_rails": len(rails),
            "false_auto_rails": len(false_rails),
            "predictions": len(predictions),
            "gt": len(ground_truth),
            "matched": matches,
            "rail_eval": rail_eval,
            "rail_debug": [
                {
                    "index": index,
                    "angle": round(rail["angle_deg"], 3),
                    "x_range": [round(value, 1) for value in rail["x_range"]],
                    "n_entities": rail["n_entities"],
                    "bot_edge_sup": rail["bot_edge_sup"],
                    "candidates": details[index].get("candidates", 0),
                }
                for index, rail in enumerate(rails)
            ],
        }
        summary.append(result)
        (OUT_DIR / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
        print(f"{stem[:8]}: rows={len(rows)} rails={len(rails)} preds={len(predictions)} m03={matches['0.3']} m05={matches['0.5']}")
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
