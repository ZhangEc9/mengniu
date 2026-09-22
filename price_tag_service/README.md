# 蒙牛价签识别 Python 服务

一期最小识别编排服务。它复用现有 AISM 质检、价签识别接口和提示词，把本地脚本流程迁移为可查询、可重试、可留痕的任务服务。

## 流程

```text
照片任务
→ OSS 上传或直接使用巡店 URL
→ 质检与目标场景过滤
→ 整图 VLM 价签识别
→ 价签后处理 / 防幻觉过滤
→ 质检结果、价签明细、原始响应、调用日志入数据库
→ API 查询状态与结果
```

图片级状态和业务结果分离：

- 执行状态：`QUEUED / RUNNING / COMPLETED / FAILED / CANCELLED`
- 业务结果：`PENDING / PROCESSED / BLOCKED / FAILED / CANCELLED`
- 执行阶段：`UPLOAD / QUALITY_CHECK / PRICE_RECOGNITION / POSTPROCESS / DONE`

质检不合格、非目标场景、无价签都视为“处理成功但业务拦截”，不会进入失败重试。

## 交付口径

提示词仍要求模型识别普通价签、`第二件X元` 和组合促销；这样做的目的是减少普通单价误读。后处理继续识别并规范化这两类促销，但它们不进入最终交付结果。

`/v1/photos/{photo_id}/tags` 的交付结构固定为：

```json
{
  "image_name": "example.jpg",
  "image_url": "https://example.com/example.jpg",
  "scene_type": "货架照",
  "price_tag_count": 1,
  "price_tags": [
    {
      "id": 1,
      "bbox": [120, 320, 230, 365],
      "coordinate_scale": 1000,
      "price": "9.90",
      "raw_price_text": "9.90元",
      "unit": "元"
    }
  ]
}
```

促销候选、模型 confidence、剔除原因和原始响应仍保留在数据库中，用于审计和回归分析，但不作为交付字段输出。AISM 签名与 HTTP 请求体使用同一条序列化 JSON 字符串，避免因 `requests` 与 `httpx` 序列化差异导致验签失败。

AISM 价签接口可能返回 JSON 数组，也可能用 Markdown 代码块包裹数组。服务端会先提取合法 JSON，再把数组规范化为内部 `{ "price_tags": [...] }` 结构；质检接口仍要求 JSON 对象。

## 项目结构

```text
price_tag_service/
├── app/
│   ├── api/                 # FastAPI 路由、鉴权、序列化
│   ├── clients/             # AISM 工作流与 OSS 上传适配器
│   ├── core/                # 配置、JSON 解析、通用异常
│   ├── db/                  # SQLAlchemy engine/session
│   ├── importers/           # 巡店 Excel 导入
│   ├── models/              # 任务、照片、run、结果、日志表
│   ├── processing/          # 质检归一化、价签后处理
│   ├── schemas/             # API 请求/响应模型
│   ├── services/            # 任务创建、计数、重置
│   ├── worker/              # PG/SQLite 队列、照片处理器
│   └── main.py
├── scripts/                 # API、worker、初始化数据库入口
└── tests/                   # 单元测试与假适配器全流程测试
```

## 配置

服务默认从仓库根目录启动，并读取：

- 现有 `config.json` 中的 `qc_config`、`price_tag_config`、`oss_config`
- 现有 `prompts/quality_*.txt` 与 `prompts/price_*.txt`

可以复制 `price_tag_service/.env.example` 为 `price_tag_service/.env` 后覆盖配置。环境变量前缀为 `PRICE_SERVICE_`。

常用配置：

```text
PRICE_SERVICE_DATABASE_URL=postgresql+psycopg://user:password@host:5432/price_tag
PRICE_SERVICE_API_KEY=xxx
PRICE_SERVICE_REQUIRE_API_KEY=true
PRICE_SERVICE_WORKER_CONCURRENCY=4
PRICE_SERVICE_MIN_PRICE=2
PRICE_SERVICE_MAX_PRICE=99
PRICE_SERVICE_PHOTO_SOURCES=["priceTagPhotos","productCloseupPhotos"]
```

本地开发可先使用 SQLite 默认值：

```text
sqlite:///./price_tag_service.dev.db
```

注意：生产建议 PostgreSQL 15+。当前一期使用 SQLAlchemy `create_all()` 初始化表，后续生产化时可补 Alembic 迁移。

## 启动

在仓库根目录执行：

```powershell
cd D:\Shixi\mengniu\price_tag_service
D:\Anaconda\python.exe -m pip install -e ".[dev,postgres]"
cd D:\Shixi\mengniu

D:\Anaconda\python.exe -m price_tag_service.scripts.init_db

# 终端 1：API
D:\Anaconda\python.exe -m uvicorn price_tag_service.app.main:app --host 0.0.0.0 --port 8000

# 终端 2：worker
D:\Anaconda\python.exe -m price_tag_service.scripts.run_worker
```

文档地址：

```text
http://127.0.0.1:8000/docs
```

## API 摘要

```text
POST /v1/tasks                          创建 URL 照片任务
POST /v1/tasks/upload                   上传本地照片并创建任务
POST /v1/tasks/import-excel             异步导入巡店导出表
GET  /v1/import-jobs/{import_job_id}    查询 Excel 导入进度
GET  /v1/tasks                          任务列表
GET  /v1/tasks/{task_id}                任务状态
GET  /v1/tasks/{task_id}/photos         任务照片列表
GET  /v1/photos/{photo_id}              单张照片完整结果
GET  /v1/photos/{photo_id}/qc           质检结果
GET  /v1/photos/{photo_id}/tags         价签结果
GET  /v1/photos/{photo_id}/sku          SKU 预留结果
POST /v1/photos/{photo_id}/retry        重跑照片或指定阶段
GET  /v1/queue/stats                    队列统计
```

鉴权头：

```text
X-API-Key: xxx
```

## 创建任务示例

```powershell
curl.exe -X POST http://127.0.0.1:8000/v1/tasks `
  -H "Content-Type: application/json" `
  -H "X-API-Key: xxx" `
  -d "{\"photos\":[{\"image_url\":\"https://example.com/a.jpg\"}],\"min_price\":2,\"max_price\":99}"
```

## 一期已实现

- PostgreSQL/SQLite 任务与照片表
- `FOR UPDATE SKIP LOCKED` 队列；SQLite 本地测试时降级为普通抢占
- worker 租约与超时任务回收
- 失败重试、退避、阶段保留
- 任务、run 历史、质检、价签汇总、价签明细
- AISM 每次调用 attempt 留痕，不保存签名密钥
- 业务拦截与系统失败分离
- 价签后处理独立成正式阶段，并保存 `filter_events`
- 第二件促销和组合促销只识别、不计入交付结果
- 本地上传 OSS 缓存
- Excel 异步导入与导入任务查询
- API Key 鉴权

## 已知边界

1. SKU 只保留查询接口，不执行真实识别。
2. 图片尺寸通过 Pillow 读取；如果下载失败，会退化为 1000×1000 归一化坐标过滤，物理尺寸规则不生效。
3. AISM 客户端内每次外部调用尝试 1 次，worker 层统一做最多 3 次退避重试，避免双重重试放大。
4. Excel URL 按逗号拆分；如果导出表 URL 本身包含未转义逗号，需要先确认中台导出规则。
5. 生产数据库迁移建议下一阶段补 Alembic。

## 测试

在 `price_tag_service/` 目录执行：

```powershell
D:\Anaconda\python.exe -m pytest tests -q --basetemp=.pytest_tmp
```

测试覆盖：

- 质检别名、严格门槛、业务拦截
- 组合价和第二件促销识别后剔除
- AISM 签名与实际请求体一致
- 营销语/规格误读过滤
- IoU 去重与等距幻觉熔断
- API 建任务和查询
- 假 AISM 客户端全流程入库
