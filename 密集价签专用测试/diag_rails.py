# -*- coding: utf-8 -*-
"""诊断：实体排走廊 vs 人工导轨（新方案字段）。"""
import json

d = json.load(open(r"D:\Shixi\mengniu\密集价签专用测试\导轨优先几何实验\summary.json", encoding="utf-8"))
out = []
for r in d:
    out.append(f"{r['image'][:8]} entities={r['entities_total']} rows={r['rows_total']} auto={len(r['auto_rails'])}")
    matched_ids = {re_["matched_auto"] for re_ in r["rail_eval"] if re_["matched_auto"] is not None}
    for re_ in r["rail_eval"]:
        base = (f"  user x[{re_['user_x'][0]:.0f},{re_['user_x'][1]:.0f}] ang={re_['user_angle']:+.2f} "
                f"tags={re_['tags_in_rail']}")
        if re_["matched_auto"] is not None:
            base += (f" -> R{re_['matched_auto']} cont={re_['containment']:.2f} iou={re_['iou']:.2f} "
                     f"dAng={re_.get('angle_err_deg', 0):+.2f} topOff={re_.get('top_offset_px', 0):+.0f} "
                     f"botOff={re_.get('bottom_offset_px', 0):+.0f} cov={re_.get('x_coverage', 0):.2f}")
        else:
            base += f" -> 未匹配 cont={re_['containment']:.2f}"
        out.append(base)
    for a in r["auto_rails"]:
        mark = " *MATCH*" if a["index"] in matched_ids else ""
        det = a["detail"]
        span = a["x_range"][1] - a["x_range"][0]
        out.append(f"  R{a['index']} x[{a['x_range'][0]:.0f},{a['x_range'][1]:.0f}] span={span:.0f} "
                   f"ang={a['angle_deg']:+.2f} gap={a['gap']:.0f} ent={a['n_entities']} "
                   f"sup={a['edge_support']} resid={a['resid_max_px']:.0f} dens={a['band_edge_density']:.3f} "
                   f"{a['sources'][0][:3]}/{a['sources'][1][:3]} B={det.get('stage_b','?')}:{det.get('candidates','-')}{mark}")
    for s in r["stage_b_eval"]:
        out.append(f"    B[rail{s['rail']}] tags={s['user_tags']} preds={s['preds']} "
                   f"m03={s['matched_0.3']} m05={s['matched_0.5']} m07={s['matched_0.7']} merged2={s['preds_covering_2plus_tags']}")
text = "\n".join(out)
with open(r"D:\Shixi\mengniu\密集价签专用测试\导轨优先几何实验\diag.txt", "w", encoding="utf-8") as f:
    f.write(text)
print("written", len(out), "lines")
