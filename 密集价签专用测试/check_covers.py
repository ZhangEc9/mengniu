# -*- coding: utf-8 -*-
"""查看 entity_row_cover 分布以定门槛。"""
import json

for stem in ("45727462_6_first_normal_1782463362055_B04C0D8E",
             "45524464_6_first_normal_1781829504624_12327780",
             "45035165_6_first_normal_1781144340103_10029317"):
    d = json.load(open(rf"D:\Shixi\mengniu\密集价签专用测试\API端点导轨实验\{stem}\rails.json", encoding="utf-8"))
    covers = sorted(x["entity_row_cover"] for x in d["details"].values())
    matched = [(r["user_angle"], r["matched"], r["containment"]) for r in d["rail_eval"]]
    print(stem[:8], "covers:", covers, "| rail_eval:", matched)
