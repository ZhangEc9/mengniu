# 服务化六表与 Agent 编排设计

更新时间：2026-09-22  
状态：会议结论整理与实现前设计约束  
关联工程：`price_tag_service/`

## 1. 结论

后续仍由一个统一识别编排服务承载全部能力，不按 Agent 拆成多个独立服务。页面服务暂不开发，只要求任务创建和数据模型保留后续页面所需字段。

各识别能力按 Agent 独立建模、独立运行、独立留痕，可以组合启用：

1. 质检 Agent：可选，支持“执行质检”或“跳过质检”。
2. 价签识别 Agent：识别价签 bbox、金额和普通/促销语义。
3. SKU 识别 Agent：识别或接入 SKU bbox、编码、名称、品牌等。
4. SKU-价签匹配 Agent：以确定性空间匹配为主，必要时引入模型辅助。
5. 本品结果沉淀：匹配成功且判定为本品的记录。
6. 竞品结果沉淀：匹配成功且判定为竞品的记录。

## 2. 当前 P1 结论

固定 10 张图片已完成本地脚本与服务化对比：

```text
任务级判定一致：10/10
服务侧进入识别：8
本地侧进入识别：8
服务侧业务拦截：2
本地侧业务拦截：2
服务侧交付价签：56
本地侧交付价签：60
IoU>=0.5 bbox 命中：54/56 = 96.43%
bbox 命中且价格一致：53/56 = 94.64%
失败照片：0
```

结论：服务化没有造成质检准入回退；价签识别存在少量模型抖动，属于同一基线下可解释差异。4 张图片存在数量或相邻框差异，后续用固定样本继续观察，不因单次差异调整提示词。

## 3. Agent 编排

### 3.1 流程

```text
照片任务
→ OSS 上传或复用巡店 URL
→ 质检 Agent（可选）
→ 价签识别 Agent
→ 后处理
→ SKU 识别 Agent
→ SKU-价签匹配
→ 本品/竞品结果沉淀
→ API 查询
```

不同任务可以按配置减少执行范围。例如 P1 或成本敏感任务可以先只执行：

```text
质检 → 价签识别 → 后处理
```

### 3.2 质检开关

任务级新增配置：

```json
{
  "agent_config": {
    "quality_check": {
      "enabled": true
    },
    "price_tag": {
      "enabled": true
    },
    "sku": {
      "enabled": false
    },
    "sku_price_match": {
      "enabled": false
    }
  }
}
```

质检开关规则：

1. `enabled=true`：调用现有质检 Agent，只有清晰价签、目标场景、四项质量合格的图片进入价签识别。
2. `enabled=false`：不调用质检模型，直接进入后续已启用 Agent。
3. 跳过质检不是业务拦截，照片不能因为“未质检”变成 `BLOCKED`。
4. 跳过时仍写一条 `qc_result`，状态为 `SKIPPED`，保证全链路可解释。
5. 结果和报表必须能区分“质检通过”“质检拦截”“质检跳过”。

### 3.3 Agent 独立性

每个 Agent 必须满足：

1. 独立输入、输出和版本号。
2. 独立业务状态，不依赖其他 Agent 的私有表字段。
3. 每次调用保留 attempt、原始响应、耗时、错误和提示词版本。
4. 业务拦截与系统失败继续分离。
5. 适配器可替换，worker 只负责编排，不写死模型细节。

执行阶段从当前阶段扩展为：

```text
UPLOAD
→ QUALITY_CHECK
→ PRICE_RECOGNITION
→ POSTPROCESS
→ SKU_RECOGNITION
→ SKU_PRICE_MATCH
→ PRODUCT_SETTLEMENT
→ DONE
```

## 4. 数据模型

任务、照片、run、调用日志、OSS 缓存、导入任务是编排基础设施，不计入会议说的 6 张业务结果表。业务结果建议按以下结构落地。

### 4.1 质检表 `qc_result`

现有表继续使用，补充跳过语义：

| 字段 | 说明 |
|---|---|
| photo_id / run_id | 图片与 Agent 执行关联 |
| qc_status | `PASSED` / `BLOCKED` / `SKIPPED` |
| is_valid | 四项图片质量是否有效 |
| quality_checks | 模糊、曝光、暗光、损坏 |
| scene_type / scene_group | 原始场景与归一化目标场景 |
| has_price_tag | 是否存在清晰可读价签 |
| can_proceed_to_price | 准入结论 |
| rejection_reasons | 拦截原因数组 |
| model_name / prompt_version | 可追溯版本 |

跳过时 `qc_status=SKIPPED`，模型字段可为空，`can_proceed_to_price` 为空，不作为失败。

### 4.2 价签结果表 `price_result`

一张图一次价签 Agent 执行一行：

| 字段 | 说明 |
|---|---|
| photo_id / run_id | 图片与执行关联 |
| total_raw_tags | 模型原始候选数 |
| total_tags | 后处理后普通价签数 |
| total_promotion_tags | 识别后剔除的促销签数 |
| image_width / image_height | 图片尺寸 |
| postprocess_version | 后处理版本 |
| raw_payload | 模型原始输出和过滤事件 |
| process_status | 执行状态 |

### 4.3 价签明细表 `price_tag_detail`

一个价签一行，属于价签结果的明细表：

| 字段 | 说明 |
|---|---|
| price_result_id / photo_id | 关联价签结果 |
| tag_id | 图内价签序号 |
| bbox_xmin / bbox_ymin / bbox_xmax / bbox_ymax | 拆列存储 |
| coordinate_scale | 坐标量纲，默认 1000 |
| price / raw_price_text | 清洗后价格与原文 |
| tag_type | 普通价签、组合促销、第二件促销 |
| promotion_type / bundle_quantity / bundle_price / second_item_price | 促销审计字段 |
| confidence / price_confidence | 只入库分析，不直接剔除 |

交付口径不变：最终 API 只交付普通零售价签；促销只入库审计。

### 4.4 SKU 识别表 `sku_result`

一个 SKU 候选一行：

| 字段 | 说明 |
|---|---|
| photo_id / run_id | 图片与执行关联 |
| sku_bbox_xmin / ymin / xmax / ymax | SKU 位置 |
| coordinate_scale | 坐标量纲 |
| sku_code | 标准 SKU 编码 |
| sku_name | SKU 名称 |
| brand | 品牌 |
| category | 品类 |
| specification | 规格 |
| product_role | `OWN` / `COMPETITOR` / `UNKNOWN` |
| recognition_mode | 服务接入、模型识别、人工导入 |
| raw_payload | 原始输出 |

SKU 服务未接入时，表和接口先保留，不阻塞价签链路。

### 4.5 SKU-价签匹配表 `sku_price_relation`

一个匹配关系一行：

| 字段 | 说明 |
|---|---|
| sku_result_id | SKU 候选 ID |
| price_tag_detail_id | 价签明细 ID |
| match_status | `MATCHED` / `UNMATCHED` / `CONFLICT` |
| match_method | 空间匹配、规则匹配、模型辅助 |
| match_score | 置信度或得分 |
| iou / center_distance / overlap_ratio | 空间证据 |
| final_price | 匹配后的最终零售价 |
| final_price_source | SKU 价、价签价、人工修正 |
| conflict_reason | 冲突原因 |

一期优先使用可解释规则：同一货架层、bbox 中心距离、水平重叠、IoU、价格区间和 SKU 标准价格。

### 4.6 本品结果表 `own_product_result`

沉淀最终判定为本品的业务结果：

| 字段 | 说明 |
|---|---|
| task_id / photo_id / batch_no | 任务和批次 |
| sku_result_id / price_tag_detail_id / sku_price_relation_id | 溯源链路 |
| store_id / store_name / region | 业务维度 |
| sku_code / sku_name / specification | SKU 信息 |
| final_price | 最终价格 |
| price_type | 普通价、打包价、促销价 |
| match_score | 匹配置信度 |
| review_status | 人工复核状态，一期可固定免复核 |

### 4.7 竞品结果表 `competitor_product_result`

结构与本品结果表保持一致，另补竞品分析字段：

| 字段 | 说明 |
|---|---|
| competitor_brand | 竞品品牌 |
| competitor_category | 竞品品类 |
| price_vs_own_reference | 与本品参考价关系，后续可选 |
| analysis_tags | 陈列、价格带、促销等标签，后续可选 |

本品表和竞品表分开建表，避免业务字段持续膨胀后互相污染；同时可通过相同的 `task_id / photo_id / batch_no` 做联合查询。

## 5. API 与页面预留

页面暂不开发，但 API 配置不要按页面字段设计，按 Agent 配置设计：

```json
{
  "photos": [
    {
      "image_url": "https://example.com/a.jpg"
    }
  ],
  "agent_config": {
    "quality_check": {
      "enabled": true
    },
    "price_tag": {
      "enabled": true
    },
    "sku": {
      "enabled": false
    },
    "sku_price_match": {
      "enabled": false
    }
  }
}
```

后续页面只读取任务配置和结果状态，不反向驱动表结构。

## 6. 实施顺序

1. 冻结 Agent 配置和六表字段口径，输出 Alembic 迁移。
2. 实现质检跳过开关和 `SKIPPED` 状态，补状态机测试。
3. 新增 SKU 适配器占位、SKU 表和查询接口。
4. 实现确定性 SKU-价签匹配，先不做模型辅助。
5. 实现本品/竞品沉淀表和查询接口。
6. 补齐批量查询、任务级配置快照和可观测性。
7. 最后再评估页面服务。

## 7. 待确认事项

1. SKU 正式服务接口、字段 schema、bbox 是否可用。
2. 本品/竞品判定来源：SKU 主数据、任务配置，还是后续 Agent 判定。
3. 最终结果粒度：按图片、按任务，还是按批次汇总。
4. 跳过质检的图片是否允许进入正式报表，还是单独标记统计。
5. 本品/竞品结果重跑时是覆盖、软删旧批次，还是保留全部历史 run。
6. SKU-价签多对一或一对多冲突时的优先级规则。
