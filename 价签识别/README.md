# 蒙牛货架价签识别与定位批处理系统 (POC)

本模块用于对 `D:\Shixi\mengniu\蒙牛 poc0805_images` 目录下的货架陈列图进行全量价签检测、坐标定位 (`bbox`)、价格数字提取以及自动化结果可视化。

---

## 一、Python 环境与常见报错排查

### 1. 为什么会出现 `can't open file ... [Errno 2] No such file or directory`？
- **原因 1：命令行转义字符问题**  
  在部分终端或复制粘贴时，路径中的下划线被转义成反斜杠加下划线（例如 `run\_price\_batch.py` 代替了 `run_price_batch.py`），导致 Windows 寻找名为 `run\_price\_batch.py` 的不存在文件。
- **原因 2：终端默认 `python` 路径重定向**  
  Windows 自带的应用商店别名 `C:\Users\20667\AppData\Local\Microsoft\WindowsApps\python.exe` 常常未安装或失效。
- **确切的 Python 解释器绝对路径**：
  ```cmd
  D:\Anaconda\python.exe
  ```
  版本：Python 3.13.9

---

## 二、工作进展与核心功能实现

1. **构建批量化批处理脚本 (`run_price_batch.py`)**
   - 自动遍历 `D:\Shixi\mengniu\蒙牛 poc0805_images` 下的 63 张原图。
   - 实现本地断点续跑：遇到已处理的图片自动复用结果，支持 `--force` 重新推理。
   - 支持单张图片目标验证 `--target-image <filename>`。

2. **解决模型接口 60 秒超时与公网转存问题**
   - **痛点**：先前通过免费外网穿透或临时文件床上传图片时，蒙牛后台接口下载耗时超长，频繁抛出 `<400> Download multimodal file timed out`（超过 60s 失败，检出 0 个价签）。
   - **解决方案**：逆向复用了蒙牛内部官方 OSS 直传接口 (`https://aism.mengniu.cn/brcapi/oss/api/oss/getFileUrl`)，使用用户的 Bearer Token 自动将本地图片上传至 `ossaism.mengniu.cn`。
   - **缓存加速**：自动维护 `oss_image_map.json`，已上传的图片直接复用 OSS URL，单张耗时从 60s+ 超时降低至 **18~26 秒**。

3. **模型提示词迭代与鲁棒性优化**
   - **排除黄色封箱胶带与反光杂物**：增加严格的负样本约束，禁止把货架上粘贴的黄色打包胶带、促销胶条、生产日期喷码误判为价签。
   - **倒贴/插反价签召回**：引入导轨反贴识别策略，指示模型将倒贴价签坐标正常框选，并在逻辑上旋转 180 度还原正向价格（如颠倒的 `08.5` 扶正为 `5.80`）。
   - **规范化 JSON 结构**：输出每张图的所有价签 `id`、货架层数 `shelf_layer`、归一化坐标 `bbox: [ymin, xmin, ymax, xmax]`、`price`、`unit` 及原始识别文本 `raw_price_text`。

4. **自动化可视化画框工具 (OpenCV)**
   - 识别完成后，自动读取原始图片并在图像上绘制绿色标注框，并在左上角标注检测出的价格。
   - 输出图像统一存放于 `output_price_tags/vis_images/<image_name>.vis.jpg`，方便人工快速比对审核。

---

## 三、目录结构说明

```text
D:\Shixi\mengniu\价签识别\
├── run_price_batch.py          # 主执行脚本（包含直传OSS、调用大模型、生成JSON和绘制可视化图）
├── oss_image_map.json          # 图片名称与蒙牛官方 OSS 链接的本地缓存映射
├── generated-code.py           # 蒙牛官方导出的流程调用参考代码
├── 提示词.docx                 # 原始提示词文档
├── 输出样例.docx               # 原始格式说明
└── output_price_tags\          # 批量处理产物输出目录
    ├── <image_name>.price.json # 每张图片独立的价签识别结果 JSON
    ├── summary.json            # 批量运行的整体统计概览
    └── vis_images\             # 可视化标注画框图输出目录
        └── <image_name>.vis.jpg
```

---

## 四、使用方法与执行命令

在 Windows 终端中，直接使用绝对路径运行以下命令：

### 1. 验证单张图片（例如测试第 3 张图）
```cmd
D:\Anaconda\python.exe "D:\Shixi\mengniu\价签识别\run_price_batch.py" --target-image "45058541_6_first_normal_1781238841860_4565122C.jpg" --force
```

### 2. 验证单张图片（例如测试第 4 张倒插价签图）
```cmd
D:\Anaconda\python.exe "D:\Shixi\mengniu\价签识别\run_price_batch.py" --target-image "45436384_6_first_normal_1781579747265_3C895D5E.jpg" --force
```

### 3. 全量批量执行 63 张图片
```cmd
D:\Anaconda\python.exe "D:\Shixi\mengniu\价签识别\run_price_batch.py"
```
*(注：如果中断后重新执行，脚本会自动跳过已经成功生成的图片，实现增量断点续跑)*

---

## 五、已知问题与后续建议

1. **Token 鉴权时效**：
   - 脚本内当前配置的 `DEFAULT_BEARER_TOKEN` 为登录凭证，有效期通常有限。若日后出现上传 OSS 报 401/403 错误，需从网页端控制台重新获取最新 Token 替换。
2. **极小字与模糊价格的兜底**：
   - 对部分距离过远或严重反光的价签，模型可能仅能检出 `bbox` 位置而价格为 `null`，属正常视觉边缘现象。
3. **提示词线上部署同步**：
   - 当前在批处理脚本中通过 API 动态覆盖系统提示词进行快速试验；待提示词最终调优定型后，建议同步发布至蒙牛 Flow 平台生产模型节点。
> 本目录说明面向历史本地价签识别脚本。服务化流程以仓库根目录的 `price_tag_service/README.md` 和 `价签识别服务当前状态与下一步.md` 为准。
