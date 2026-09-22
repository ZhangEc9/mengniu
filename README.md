# 蒙牛零售巡店智能视觉分析全流程管线 (Mengniu Retail Inspection Pipeline)

> 历史本地管线说明。当前服务化工程入口是 `price_tag_service/README.md`；下一步以 `价签识别服务当前状态与下一步.md` 为准。

本项目为蒙牛数智化零售巡店场景下的计算机视觉与多模态大模型分析管线，覆盖**图片质量前置预检**、**陈列场景识别**、**商品价签高精检测与读数**以及**预留 SKU 商品品类识别**三大阶段。

---

## 一、系统全流程架构设计

巡店图片经过标准的多级门禁流水线处理，有效拦截无效图像，节约大模型推理成本：

```
                    ┌────────────────────────┐
                    │ 待巡检现场照片 (原图)   │
                    └───────────┬────────────┘
                                │
                                ▼
               【Stage 1: 图像前置质量预检与场景识别】
                 - 模糊 / 严重过曝 / 光线暗光 / 文件损坏检测
                 - 场景分类识别 (货架照 / 冰箱照 / 堆头照 / 其他)
                 - 价签存在性初步校验 (has_price_tag)
                                │
                     ┌──────────┴──────────┐
                     │ 质检通过 & 存在价签? │
                     └──────────┬──────────┘
                         No     │     Yes
                   ┌────────────┘     └────────────────────────┐
                   ▼                                           ▼
            【提前拦截退出】                              【Stage 2: 纯全图高精价签检测】
            - 记录不合格原因与场景特征                    - 纯全图单次高精推理 (单张 60s)
            - 终止后续流程，节省调用成本                  - 智能连续复读机链熔断机制
                                                         - 原生几何长宽比自适应纠偏
                                                         - 像素级 Bounding Box 严丝贴合
                                                               │
                                                               ▼
                                                  【Stage 3: [预留] SKU 品类识别】
                                                   - `run_sku_recognition()` 扩展接口
                                                   - 待模型就绪后无缝接入
                                                               │
                                                               ▼
                                                  【Stage 4: 沉淀结构化全流程报告】
                                                   - 输出 `.pipeline.json` 全指标数据
                                                   - 生成端正的可视化标注图 (.vis.jpg)
```

---

## 二、图片公网上传机制（蒙牛内部 OSS 直连）

### 1. 为什么必须上传公网 OSS？
- 巡店高清相机拍摄的照片分辨率通常高达 `3000x2000` 到 `4000x3000`，单张文件达 5MB~15MB；
- 若直接使用 Base64 编码内嵌请求，会导致 HTTP 请求体过大（超 20MB），极易触发网关超时（60s Timeout）或连接中断；
- 蒙牛 Flow API 最优的交互方式为通过公网可访问的图片 URL 传参。

### 2. 上传与本地缓存复用机制
项目在 `run_full_pipeline.py` 与 `价签识别/run_simple.py` 中内置了自动化的 OSS 缓存管理器：
1. **优先查询缓存**：脚本启动时优先读取 `oss_image_map.json`。若该图片已存在历史上传 URL，直接复用，**0 耗时即刻进入模型识别**；
2. **缺失自动直连上传**：
   - 接口地址：`https://aism.mengniu.cn/brcapi/oss/api/oss/getFileUrl`
   - 请求方式：`POST (multipart/form-data)`，带 `Authorization: Bearer <TOKEN>` 请求头与 `type=0`；
   - 上传成功后解析获取公网返回的 `fileUrl`；
3. **自动持久化**：将新上传的 URL 写回 `oss_image_map.json`，确保后续多次调试不重复消耗网络上传带宽。

---

## 三、模型选型与核心算法演进经验

### 1. 模型能力梯队分析
在密集的零售价签识别场景中，不同模型的表现差异巨大：
- **`Qwen3-VL-plus-2025-12-19`（最强推荐，首选）**：
  通义千问 3 代视觉旗舰的固定微调快照，对微小目标检测和细粒度 OCR 具备强位置监督，空间坐标稳定不飘；
- **`Qwen3.7-plus` / `Qwen3-vl-plus`（质量审核推荐）**：
  做前置分类与质量预检响应极快（2~4s），逻辑严密，无幻觉；
- **`蒙牛G5.6-terra`（不适合价签检测）**：
  以长文本为主干的大模型，视觉分辨率下采样严重，缺乏像素回归能力，极易产生“坐标幻觉”（将框画到瓶盖或瓶身上）。

### 2. 核心算法防御机制
- **自回归连续复读机熔断（`smart_consecutive_repetition_filter`）**：
  当导轨贴有长条促销纸（如“第二件2元”）时，大模型极易陷入自回归复读（连刷 10 个同价）。算法支持真实同价商品并排（允许连续 2~3 个），但在同一层紧挨着连续出现 $\ge 4$ 个相同价格时，强制熔断截断多余假框。
- **物理长宽比自适应纠偏（`resolve_tag_bbox`）**：
  解决大模型在不同图片上动态在 `[ymin, xmin, ymax, xmax]` 与 `[xmin, ymin, xmax, ymax]` 之间切换的问题。利用实体价签“宽横扁方（宽 > 高）”的物理常识，自适应翻转校正，彻底消灭细长竖立柱。

---

## 四、安全与密钥隔离配置说明

为保护企业敏感凭证安全，**代码已对所有 API Key 和 Token 进行脱敏解耦**，绝不将真实密钥推送到公共代码库。

### 敏感凭据独立配置文件说明：
- **模板文件**：`config.example.json`（已提交至代码库，包含配置结构样例）
- **真实凭据文件**：`config.json`（**已被 `.gitignore` 彻底排除，绝不上传 GitHub**）

### 如何配置真实密钥：
克隆代码后，在项目根目录下创建 `config.json`，填入你的实际业务凭证：
```json
{
  "qc_config": {
    "api_url": "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/fbe5e46fcd?version=dev",
    "appid": "你的质量审核APPID",
    "secret": "你的质量审核SECRET"
  },
  "price_tag_config": {
    "api_url": "https://aismapi.mengniu.cn/brcapi/brain/api/v1/flowApi/45c1d7fe3c?version=dev",
    "appid": "你的价签识别APPID",
    "secret": "你的价签识别SECRET"
  },
  "oss_config": {
    "upload_url": "https://aism.mengniu.cn/brcapi/oss/api/oss/getFileUrl",
    "bearer_token": "Bearer 你的蒙牛内部用户Token"
  }
}
```

---

## 五、使用指引与常用命令

### 1. 运行环境
- Python 3.9+
- 依赖库：`requests`, `opencv-python`, `numpy`, `python-docx`

### 2. 执行全流程一体化管线（质量审核 -> 价签检测 -> 预留SKU）
```bash
# 从第 0 张开始，批量执行前 12 张图片的全流程巡检
python run_full_pipeline.py --offset 0 --limit 12 --force

# 指定某一张特定图片精确执行
python run_full_pipeline.py --target-image 45035165_6_first_normal_1781144340103_10029317.jpeg --force
```

### 3. 单独执行价签识别模块调试
```bash
cd 价签识别
python run_simple.py --target-image 45035165 --force --env dev
```

---

## 六、目录结构说明

```
mengniu/
├── run_full_pipeline.py        # 全流程总控一体化管线脚本 (质检->价签->SKU)
├── config.example.json         # 敏感凭据配置范例模板
├── config.json                 # 本地真实凭据文件 (Git 忽略，安全隔离)
├── .gitignore                  # Git 忽略规则 (屏蔽大图片、密钥与临时图)
├── README.md                   # 项目工程说明文档
├── 质量审核/
│   ├── 提示词.docx             # 最新的质量预检+场景分类标准提示词
│   └── prompt_text.txt         # 纯文本提示词源文件
└── 价签识别/
    ├── run_simple.py           # 高精全图价签识别核心脚本
    ├── oss_image_map.json      # OSS 上传持久化 URL 映射表
    ├── PROMPT_OPTIMIZATION_LOG.md # 提示词优化与幻觉对抗历史复盘日志
    └── backup_overcomplicated/ # 历史复杂切片实验与演进备份存档
```
