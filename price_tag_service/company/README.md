# 蒙牛识别双服务

一个代码仓库、两个独立进程：A 只做质检、SKU 和价签识别；B 创建任务、调 A、匹配、持有 PostgreSQL 写权限并对外查询。`app/contracts` 是两侧共享的请求/响应契约，`app/processing` 与 `app/clients` 是可复用的处理和模型客户端。部署可分别在不同主机进行，A **不能配置 PG 凭据**；页面尚未包含在本仓库。

## 目录与边界

- `app/agent_api/`：A 服务入口和三 Agent 编排；SKU 暂为按图片文件名读取 `app/resources/sku_samples/` 的模拟客户端，正式模型 API 尚未接入。
- `app/business_api/`：B 服务入口、表映射、任务执行与受控入库；`check_schema.py` 只读核表，`verify_write.py` 事务内试插六表后回滚；**不要在已有数据库运行 `create_all` 或参考建表脚本**。
- `app/contracts/`：A/B 共用的 Pydantic 数据契约；`app/processing/`：质检归一化、价签后处理、SKU 匹配、图片大小；`app/resources/`：仅版本化提示词和明确标识为模拟的 SKU 样本。
- `tests/test_service_boundary.py`：本地 SQLite 和假 Agent 的边界回归。`docs/schema_reference.sql` 只作已有表结构参考，不是部署迁移。

## 配置与启动

建议为 A、B 使用不同的部署环境及各自的密钥管理系统。将 `config.example.json` 复制为 A 私有的 `config.json` 并填写质检和价签**各自不同**的 `appid`/`secret`；`config.json` 已被 Git 忽略。也可将 A 私有配置放在任意非仓库路径，并以 `PRICE_SERVICE_LEGACY_CONFIG_FILE` 指向该文件。提示词和模拟 SKU 样本随 Python 包安装，不能把业务图片、Excel 导出、真实模型原始响应或数据库文件放入 Git。

```powershell
python -m pip install -e ".[postgres,dev]"
```

A 进程只需 `MENGNIU_AGENT_API_KEY`（**服务间口令，不是模型密钥**）与私有模型配置；从项目目录运行：

```powershell
python -m uvicorn app.agent_api.main:app --host 127.0.0.1 --port 8101
```

B 进程设置 `MENGNIU_BUSINESS_DATABASE_URL`、`MENGNIU_AGENT_URL`、`MENGNIU_AGENT_API_KEY`（与 A 口令相同）、`MENGNIU_BUSINESS_API_KEY`（页面或调用方使用），不设置模型密钥；从项目目录运行：

```powershell
python -m app.business_api.check_schema
python -m app.business_api.verify_write
python -m uvicorn app.business_api.main:app_factory --factory --host 127.0.0.1 --port 8102
```

模型访问 URL 的网络权限需在 A 部署机器与模型侧分别验证；本机能访问不代表部署环境能访问。首次执行时 B 在 API 进程的后台线程逐图调用 A，**不是持久队列**：重启恢复、增量任务、OSS 上传、正式 SKU API 和前端仍待实现。未取得真实 SKU 价格区间/价格校验规则时，正式流程不会向融合表编造值；状态和照片备注可显示 `Fusion pending`。

仅内部联调可将 B 的 `MENGNIU_ALLOW_DEMO_FUSION=1` 且请求 `demo_mode=true`、`business_code` 以 `DEMO_` 开头，写入备注为 `DEMO ONLY` 的模拟区间 `10.00–15.00`；这会在 PG **永久留下测试行**，绝不可对业务方当作价格结论。关闭此开关恢复正式行为。

## Git 交付

本目录是从原实验仓库筛出的最小可部署源代码，未附带任何真实模型密钥、数据库口令、业务图片或原始 Excel。公司仓库使用此目录作为唯一代码源，以后不要同时维护两份分叉代码。提交前运行 `python -m pytest -q` 与 `git status --short`，核查新增文件；首次上传只连接公司平台仓库，不使用实验仓库的个人 GitHub `origin`。
