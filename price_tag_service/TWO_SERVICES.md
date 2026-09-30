# 双服务首轮实现

更新时间：2026-09-24。本目录保留原有 `app.main` 服务，不自动迁移其旧表与任务。新边界分别使用 `app.agent_api.main:app` 和 `app.business_api.main:app_factory`；两个进程可在不同主机运行。

## 服务边界

- Agent 进程提供单图 `POST /v1/process` 和批量 `POST /v1/process-batch`。批量输入是 `request_id`、`task_id`、`quality_check`、`images[]`，每张图片包含 `image_list_id`、`image_id`、`image_url`、可选 `shop_id`；响应按原顺序包含 `results[]`，每项原样带回身份及质检、SKU、价签的 `status/raw/parsed/latency_ms/error`。批量按图片顺序处理，单图内 SKU 和价签并行，单图异常不影响后续图片；最多 100 张，但大批量可能超过 HTTP 网关超时，业务端目前每次请求一张。`quality_check` 仅决定是否调用质检；启用时质检通过才并行调用 SKU/价签，不通过或调用失败则拦截；关闭时直接并行调用 SKU/价签。Agent 进程不创建数据库连接，不配置 PG 凭据。
- 业务进程创建任务和任务照片、调用 Agent、校验返回身份、执行空间匹配、通过其专属数据库账号事务写入已确认字段并查询结果。ID 与时间从 `app.business_api.service` 的统一函数产生；任务内同图 ID 原样透传。**正式融合数据尚不能写入**：真实 DDL 中 `sku_min_price`、`sku_max_price`、`price_check_result` 都是必填，缺少价格参考来源和校验枚举时只落识别结果，并在任务照片备注标为 `Fusion pending`、任务标为异常；仅在显式演示模式允许写入带 `DEMO ONLY` 标记的虚构范围，不可当作正式价格决策。
- 业务接口：`POST /v1/tasks`（请求包含 `business_code`、`business_unit`、`quality_check`、`photos`，`data_source=1` 为中台导出、默认 `2` 为人工上传，不用传 `sync_date`），`POST /v1/tasks/{task_id}/execute`，`GET /v1/tasks/{task_id}`。单图验收步骤见 `ONE_PHOTO_SMOKE.md`。执行接口原子认领任务、返回 HTTP 202 后在当前 API 进程的后台线程逐图处理；目前只实现首次执行，再次调用返回 409。**这不是持久化队列**：进程异常退出可能令任务停留在执行中，跨进程恢复与重试仍待设计。前端、上传文件存储、增量任务及 SKU 正式 API 尚未接入。

## 本地启动

在 `price_tag_service` 目录安装 `pip install -e ".[dev,postgres]"`。分别在隔离的进程环境配置：

```text
Agent: MENGNIU_AGENT_API_KEY、PRICE_SERVICE_* 模型配置及 config.json；不配置数据库 URL
业务: MENGNIU_BUSINESS_API_KEY、MENGNIU_AGENT_API_KEY、MENGNIU_AGENT_URL、MENGNIU_BUSINESS_DATABASE_URL
```

```powershell
python -m uvicorn app.agent_api.main:app --port 8101
python -m uvicorn app.business_api.main:app_factory --factory --port 8102
```

请求两侧都使用 `X-API-Key`；业务服务调用 Agent 时使用 `MENGNIU_AGENT_API_KEY`。Agent 目前用样例 SKU 客户端；实际调用必须有对应图片文件名的样例 JSON。四个运行提示词和一个明确标为模拟的 SKU 样例已移入 `app/resources/`，随包安装；私有 `config.json` 不入 Git。图片 URL 必须可被 Agent 所在主机及模型服务访问。`business_code`、`business_unit` 为表中必填业务属性。首次创建任务时，业务服务按北京时间的当前自然日生成一个 `sync_date`（数据库 TIMESTAMP 存该日 00:00:00），任务行和首次照片行复用该值；首次执行 `batch_no=1`。

截图所示的 `image_recognition.public` 已有六张 `mn_*` 表，**不要运行** `app.business_api.create_schema` 或生产 `create_all`。以实际已建库及更新后的《蒙牛_关键资产部分数据库建表脚本_V1.2最终版_PG.sql》为结构基线。由 B 服务所在环境运行只读命令 `python -m app.business_api.check_schema`，按提示输入 PG 主机、账号、端口、库名和隐藏输入的密码；若已设置本地 `MENGNIU_BUSINESS_DATABASE_URL`，它会优先使用该变量。工具核对六表字段、类型、长度、空值、主键、唯一约束和当前账号的 SELECT/INSERT 权限，**不插入数据**。所有数据库凭据仅配置在业务进程，绝不可传给 Agent 或提交到 Git。

只读检查通过后，B 服务环境可运行 `python -m app.business_api.verify_write`，试插六张表并在同一事务内校验、回滚。脚本不调用 Agent、不访问图片，也不提交测试数据；仅验证写权限、字段映射与约束，不等于真实任务流程验收。若实际库有外部副作用的 INSERT 触发器，需先请 DBA 确认再运行。另有显式演示模式（`MENGNIU_ALLOW_DEMO_FUSION=1` + `demo_mode=true` + `DEMO_` 前缀）用于向 PG 实际写入带标记的虚构业务归属与价格区间，操作与风险见 `ONE_PHOTO_SMOKE.md`，不可用于正式业务决策。

`task_status` 已确认为 0=待执行、1=执行中、2=已完成、3=异常，统一定义在 `app.business_api.status`。实际 SQL 的 `quality_check_result` 为 0=不合格、1=合格；SKU/价签没有 `check_status` 字段，仅成功时各插一行，失败时在任务照片备注标记，不将跳过伪装成成功。质检表要求 `score NUMERIC(5,2) NOT NULL`，成功/拦截时写归一化分值；`quality_issue VARCHAR(16)` 允许 NULL：无问题写 NULL，多个问题编号用逗号连接（如 `"1,2"`），完整原始结果也保留在 JSON。质检 `image_id` 已不唯一。后台作业恢复、正式 SKU API 和失败重试尚未实现，不能据此声明生产可用。

`sync_date` 首次创建为创建日期；自动增量任务开启后，同一个 `task_id` 可以跨日期继续执行。目标口径：任务行 `sync_date` 表示最近执行到的日期；新一轮照片使用同一个 `task_id`、新的 `image_list_id` 和当轮 `sync_date`，`batch_no` 随轮次递增。当前仅实现首次创建/首次执行（`auto_run=0`），**尚未实现**增量任务开关、按日期取新照片、同任务追加照片或跨日调度；不要把现有的“一任务只能启动一次”误认作增量流程已完成。建表 SQL 的 `sync_date` 注释仍只写自动同步语义，业务上以上述新确认口径为准。融合表价格区间和校验结果仍待蒙牛提供。

图片由业务侧上传阿里云 OSS，取得长期稳定、在蒙牛 VPN 网络环境可访问的 URL 后向 Agent 传递；本期不按 3600 秒临时签名 URL 设计，也不要求逐图重新签名。当前代码只接受已上传的 URL，尚未实现 OSS 上传。业务任务接口和 Agent 契约均按 `image_url VARCHAR(512)` 校验 URL 长度；以后如要扩长，须同步修改表结构及校验规则。当前本地实验通过 VPN 可访问 URL，不代表未来 A 服务部署主机或其调用的模型服务也能访问：现有 AISM 调用把 URL 放在请求体的 `image` 字段中，必须在部署环境分别验证 Agent 能读取图片、模型服务能读取同一 URL。VPN 网络访问与数据库账号权限是两个独立问题，A 服务仍不持有 PostgreSQL 凭据。
