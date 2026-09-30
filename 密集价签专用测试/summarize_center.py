# -*- coding: utf-8 -*-
"""汇总中心点A/B实验结果。"""
import json

d = json.load(open(r"D:\Shixi\mengniu\密集价签专用测试\中心点读价实验\summary.json", encoding="utf-8"))
tot = {"centers": 0}
for p in ("a", "b"):
    tot[p] = {"kept": 0, "expand_failed": 0, "m30": 0, "m50": 0, "m70": 0,
              "price_cmp": 0, "price_ok": 0}
tot["tags"] = 0
lines = []
for img in d:
    lines.append(f"== {img['image']}")
    for r in img["rails"]:
        lines.append(
            f"  rail{r['rail']}: centers={r['centers']} comps={r['components']} tags={r['user_tags']} | "
            f"A: kept={r['a']['kept']} fail={r['a']['expand_failed']} m30={r['a']['m30']} "
            f"m50={r['a']['m50']} 价对{r['a']['price_ok']}/{r['a']['price_cmp']} | "
            f"B: kept={r['b']['kept']} fail={r['b']['expand_failed']} m30={r['b']['m30']} "
            f"m50={r['b']['m50']} 价对{r['b']['price_ok']}/{r['b']['price_cmp']}")
        tot["centers"] += r["centers"]
        tot["tags"] += r["user_tags"]
        for p in ("a", "b"):
            for k in tot[p]:
                tot[p][k] += r[p][k]
lines.append(f"== 合计 centers={tot['centers']} 轨内tags={tot['tags']}")
for p, name in (("a", "A本地先行"), ("b", "B直接扩张")):
    t = tot[p]
    lines.append(f"{name}: kept={t['kept']} fail={t['expand_failed']} "
                 f"IoU0.3={t['m30']} IoU0.5={t['m50']} IoU0.7={t['m70']} "
                 f"价格对={t['price_ok']}/{t['price_cmp']}")
text = "\n".join(lines)
open(r"D:\Shixi\mengniu\密集价签专用测试\中心点读价实验\report.txt", "w", encoding="utf-8").write(text)
print("ok")
