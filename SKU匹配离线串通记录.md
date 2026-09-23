# SKU 识别与价签匹配离线串通记录

更新时间：2026-09-23
脚本：`run_sku_match_offline.py`
产物：`sku_match_offline/`
关联口径：`服务化六表与Agent编排设计.md` 第 4.4.1 节、`价签识别经验与优化方向.md` 第 5 节

## 目的

真实 SKU 接口未提供，本步**不入库、不改 `price_tag_service/`**，只用样例 SKU 结果把
`价签交付结果 → SKU 结果解析 → SKU-价签空间匹配 → JSON/CSV 导出` 串通一遍，
验证管道和匹配规则能正常工作。

## 输入

- 价签：`P1_fixed10/service_results/*.service.json`（服务交付口径，10 张图中 8 张有价签，共 56 签；2 张 `BLOCKED` 跳过）。
- SKU 池：`标准sku图-鲜奶.csv`（10 个 SKU，2 条 `追踪=竞品` 判 `COMPETITOR`）。
- 图片尺寸：`P1_fixed10/images/`（720×1612 / 4096×3072），用于 0~1000 → 像素换算。

## SKU 样例响应

结构完全照真实接口，置信度字段对外统一为 `score`：

```text
Code / Message / RequestId / Success
Data.BoxCount / Data.Data[].{Idx, Bbox, Error, Top1.{SkuId,SkuName,Score}, Topk[].{Rank,SkuId,SkuName,Score}} / Data.UsageMap
```

每图含：与价签一一对应的商品框（从价签框上方加抖动派生）、2 个无对应价签的干扰框、
1 个 `Error="“embedding失败”"` 且 `Bbox` 长度不足的无效项。

## 匹配规则

- 候选预筛：垂直间距、水平间距超阈值直接排除。
- 代价函数：`0.25 水平gap + 0.25 中心x距离 + 0.30 垂直边距 + 0.08 水平重叠不足 + 0.04 中心距离 + 0.15 方向惩罚 + 0.10 行不一致 + 0.05 尺寸异常 + 0.10 语义`（语义暂为 0，无 SKU 标准价）。
- 用商品框宽高做归一化，不以 IoU 为主规则。
- 分级：`HIGH` cost≤0.65 且 margin≥0.12；`MEDIUM` cost≤1.10 且 margin≥0.04；否则 `LOW` → 标 `AMBIGUOUS`。
- 一个 SKU 框被多个价签抢占 → 标 `CONFLICT`。
- 规则版本：`SPATIAL_RULE_V0`。

## 结果

```text
images=8  tags=56  sku_items=80  sku_invalid=8  skipped_blocked=2
MATCHED=49  AMBIGUOUS=5  CONFLICT=2  UNMATCHED=0
```

MATCHED 中 48 个配到预期 SKU 框，1 个配到相邻候选。

## 输出文件

- `sku_match_offline/sku_sample_responses/<图>.sku.json`
- `sku_match_offline/matches/<图>.match.json`（含 `cost / second_cost / margin / evidence / filter_events`）
- `sku_match_offline/sku_price_pairs.csv`（一张图一个价签一行）
- `sku_match_offline/summary.json`（权重、阈值、统计）
- `sku_match_offline/vis/<图>_match_vis.jpg`（绿框 SKU、红框价签、连线为匹配结果）

## 局限

1. SKU bbox 是派生样例，上述比例**只代表管道与规则在正常工作，不代表匹配准确率**。
2. 真实接口需确认：`api_url`/鉴权、bbox 坐标量纲、`Idx` 语义、一个框是陈列面还是同款分组、是否返回竞品。
3. 权重与阈值需在人工配对标注（50~100 张）上重标，当前值沿用历史经验，未标定。
4. 尚未进入服务编排：`SKU_RECOGNITION` / `SKU_PRICE_MATCH` 阶段、`sku_result` 与 `sku_price_relation` 表、与价签 Agent 的并行执行都还没做。

## 复现

```cmd
cd /d D:\Shixi\mengniu
D:\Anaconda\python.exe run_sku_match_offline.py
```
