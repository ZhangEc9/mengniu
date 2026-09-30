# 单图演示链路验收（会向 PG 提交模拟业务数据）

本样例来自 `2026-09-18-14-38-17_EXPORT_XLSX_27962701_827` 的第 12106 行（表头计入），记录 `id=213482`、`storeCode=167722`、`xlOrderId=49098981`、`priceTagPhotos`。选择其中一条 URL：

```text
http://sjimgpub.slicejobs.com/algapp/order/type_4/100/7741/49098981/49098981_7_first_normal_1788763058147_19960398.jpeg
```

2026-09-24 在当前机器 HTTP GET 返回 200、`image/jpeg`；这不保证 A 服务或模型部署环境也能访问。此前本地价签结果对这张图识别出一个 bbox `[135,804,298,846]`、价格 `12.90`，不保证再次调用模型得到同样结果。模拟 SKU 响应随服务打包在 `app/resources/sku_samples/49098981_7_first_normal_1788763058147_19960398.sku.json`，两个 bbox 各对应 Top1/Topk；用于打通流程，不是对图片的真实识别。历史价签框和模拟 SKU 可触发 `MATCHED` 分支。

导出字段没有真实事业部信息，`storeCode=167722` 不是 `business_code`。本轮经确认仅为联调，固定使用**虚构** `business_code=DEMO_MN_9F3A7C`、`business_unit=模拟事业部_9F3A7C`。演示融合价格范围统一为 `[10.00,15.00]`，`price_check_result=0` 表示落在这个虚构范围内，否则为 `1`；这些数字不是蒙牛的价格标准。B 服务需显式设置 `MENGNIU_ALLOW_DEMO_FUSION=1`，并且任务同时传 `demo_mode=true` 和 `DEMO_` 前缀编码才会写演示融合行，任务和融合行均带 `DEMO ONLY` 备注。**执行后会在真实 PG 留下六表测试记录，不是回滚验证；不要将其提供给业务方作为正式数据。**

在三处 PowerShell 终端中操作，当前本机均从仓库根目录启动；密钥和数据库密码仅在本机输入，不写入文件或聊天。当前根目录的私人 `config.json` 提供质检与价签两套不同的模型 `appid`/`secret`；提示词与 SKU 模拟样本已打包在 `price_tag_service/app/resources`。公司独立部署时请将私有配置放在服务工作目录或通过 `PRICE_SERVICE_LEGACY_CONFIG_FILE` 指向外部私有文件，绝不提交。下方 `MENGNIU_AGENT_API_KEY` 是 B 调 A 的内部访问口令，`MENGNIU_BUSINESS_API_KEY` 是终端 C 调 B 的内部访问口令，**都不是模型密钥，不要复用模型 secret**。以下命令不运行建表脚本。

## 终端 A：Agent 服务

```powershell
cd D:\Shixi\mengniu
$env:PYTHONPATH = 'D:\Shixi\mengniu\price_tag_service'
$env:MENGNIU_AGENT_API_KEY = Read-Host '自定 A 服务内部口令（B 终端输入相同的值；不是模型 key）'
D:\Anaconda\python.exe -m uvicorn app.agent_api.main:app --host 127.0.0.1 --port 8101
```

## 终端 B：业务与数据库服务

仅在本机 B 终端设置数据库连接；以下 URL 组装会对密码中的特殊字符编码，不打印密码。数据库名默认 `image_recognition`，端口默认 5432。A 终端不设置数据库环境变量。

```powershell
cd D:\Shixi\mengniu
$env:PYTHONPATH = 'D:\Shixi\mengniu\price_tag_service'
$pgHost = Read-Host 'PG host'
$pgPort = Read-Host 'PG port（默认 5432）'; if (-not $pgPort) { $pgPort = '5432' }
$pgUser = Read-Host 'PG username'
$pgDatabase = Read-Host 'PG database（默认 image_recognition）'; if (-not $pgDatabase) { $pgDatabase = 'image_recognition' }
$pgSecure = Read-Host 'PG password' -AsSecureString
$pgPassword = [System.Net.NetworkCredential]::new('', $pgSecure).Password
$env:MENGNIU_BUSINESS_DATABASE_URL = 'postgresql+psycopg://' + [uri]::EscapeDataString($pgUser) + ':' + [uri]::EscapeDataString($pgPassword) + '@' + $pgHost + ':' + $pgPort + '/' + $pgDatabase
$pgPassword = $null
$env:MENGNIU_AGENT_URL = 'http://127.0.0.1:8101'
$env:MENGNIU_AGENT_API_KEY = Read-Host '与终端 A 相同的内部口令（不是模型 key）'
$env:MENGNIU_BUSINESS_API_KEY = Read-Host '自定 B 服务内部口令（终端 C 输入相同的值）'
$env:MENGNIU_ALLOW_DEMO_FUSION = '1'
D:\Anaconda\python.exe -m uvicorn app.business_api.main:app_factory --factory --host 127.0.0.1 --port 8102
```

## 终端 C：一张图片创建、执行和查询

这里使用导出来源 `data_source=1`，`image_id` 暂用导出的文件名 stem，任务明细和三个 Agent 的结果沿用同一 ID；没有增量或重跑操作。`quality_check=true` 时若本次质检拦截，就不会调用 SKU 和价签，可先查返回的质检原因，不要把拦截当作入库失败。

```powershell
$apiKey = Read-Host '与终端 B 相同的 B 服务内部口令'
$headers = @{ 'X-API-Key' = $apiKey }
$payload = @{
  data_source = 1
  demo_mode = $true
  business_code = 'DEMO_MN_9F3A7C'
  business_unit = '模拟事业部_9F3A7C'
  quality_check = $true
  photos = @(@{
    image_id = '49098981_7_first_normal_1788763058147_19960398'
    image_url = 'http://sjimgpub.slicejobs.com/algapp/order/type_4/100/7741/49098981/49098981_7_first_normal_1788763058147_19960398.jpeg'
  })
} | ConvertTo-Json -Depth 5
$created = Invoke-RestMethod -Uri 'http://127.0.0.1:8102/v1/tasks' -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($payload))
$taskId = $created.task_id
$taskId
Invoke-RestMethod -Uri "http://127.0.0.1:8102/v1/tasks/$taskId/execute" -Method Post -Headers $headers
Start-Sleep -Seconds 15
$response = Invoke-WebRequest -Uri "http://127.0.0.1:8102/v1/tasks/$taskId" -Headers $headers -UseBasicParsing
$json = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
$result = $json | ConvertFrom-Json
$result | ConvertTo-Json -Depth 10
```

若 `task_status=1`，模型还在运行，稍等后只重复上面从 `$response = ...` 开始的四行查询，**不要再调 execute**；第二次执行当前会返回 409。Windows PowerShell 5.1 对不带 charset 的 JSON 响应可能按错误字符集解析，故查询使用响应原始字节明确按 UTF-8 解码；提交时也将 JSON 明确转为 UTF-8 字节。`ConvertTo-Json -Depth 1` 会把嵌套结果缩成字符串，应使用 `-Depth 10`。若价签模型本次产出的框/价格与模拟 SKU 没有形成 `MATCHED`，融合表可能仍为空；不能为了验收声称有匹配。若质检拦截、模型失败或图片不可访问，先查看 `photos[].remark` 与 A/B 终端错误，不要对同一任务重发 execute。成功的融合行在 `matches[]` 里显示模拟范围和 `DEMO ONLY` 标记；这只验证链路与入表，不代表价格业务逻辑或正式 SKU 模型已上线。验收后关闭 B 服务，该开关只在当前终端会话有效。
