# -*- coding: utf-8 -*-
"""诊断：API 导轨框方案下，未匹配人工排的原因。"""
import json
import os

OUT = r"D:\Shixi\mengniu\密集价签专用测试\API端点导轨实验"
lines = []
for stem in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, stem, "rails.json")
    if not os.path.isfile(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    lines.append(f"== {stem[:8]} 返回{d['rails_returned']} 收{d['rails_accepted']} "
                 f"退化重试={'是' if os.path.isfile(os.path.join(OUT, stem, 'content_retry.txt')) else '否'}")
    for r in d["rejected"]:
        lines.append(f"  拒绝 rail_id={r.get('rail_id')} {r['reason']} cover={r.get('cover')}")
    for c in d["corridors"]:
        lines.append(f"  接受 rail_id={c['rail_id']} x[{c['x_range'][0]},{c['x_range'][1]}] "
                     f"ang={c['angle_deg']:+.2f} gap={c['inner_gap']:.0f} cover={c['entity_row_cover']}")
    for re_ in d["rail_eval"]:
        if re_["matched"]:
            lines.append(f"  人工排 ang={re_['user_angle']:+.2f} tags={re_['tags_in_rail']} "
                         f"-> 匹配 cont={re_['containment']}")
        else:
            lines.append(f"  人工排 ang={re_['user_angle']:+.2f} tags={re_['tags_in_rail']} "
                         f"x[{re_['user_x'][0]:.0f},{re_['user_x'][1]:.0f}] -> 未匹配 "
                         f"cont={re_['containment']:.2f}")
text = "\n".join(lines)
open(r"D:\Shixi\mengniu\密集价签专用测试\API端点导轨实验\diag_railbox.txt", "w", encoding="utf-8").write(text)
print("ok")
