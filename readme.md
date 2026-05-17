# ImageCompress — 图片压缩工具

将 PNG/JPG/BMP/TIFF 等格式转换为 **WebP**，并完整保留图片元信息（ComfyUI workflow/prompt、WebUI parameters 等）。

## 特性

- ✅ 支持递归扫描目录，批量转换
- ✅ 自动识别图片来源（ComfyUI / WebUI）
- ✅ 完整保留元数据：
  - ComfyUI: `workflow` → EXIF Make, `prompt` → EXIF Model
  - WebUI: `parameters` → EXIF UserComment
- ✅ 支持有损/无损压缩
- ✅ 支持自定义输出目录，**不覆盖原文件**
- ✅ 跳过已转换的文件（--skip-existing）
- ✅ 默认保留目录结构（使用 `--flat` 禁用）
- ✅ 未处理的 WebP 文件自动复制到输出目录
- ✅ 支持多种输入格式：PNG, JPG, JPEG, BMP, TIFF, WebP

## 安装依赖

```bash
pip install Pillow piexif
```

## GUI 图形界面

无需记忆命令行参数，提供直观的图形界面操作：

```bash
python ImageCompressGUI.py
```

功能包括:
- 路径选择（目录或单个文件）
- 压缩质量滑块
- 无损/有损切换
- 压缩方法选择
- 递归扫描、跳过已存在文件等选项
- 忽略路径设置（可添加多个，支持直接跳过或复制到输出目录）
- 预览模式
- 实时日志输出
- 取消操作支持

## 用法 (CLI)

```bash
# 转换当前目录下所有图片到 WebP（原地输出）
python ImageCompress.py

# 转换指定目录
python ImageCompress.py ./images

# 指定输出目录
python ImageCompress.py ./images --output ./webp_output

# 有损压缩（质量 85）
python ImageCompress.py ./images --quality 85

# 无损压缩
python ImageCompress.py ./images --lossless

# 不保留目录结构（所有文件输出到同一目录）
python ImageCompress.py ./images --output ./webp_output --flat

# 跳过已存在的文件
python ImageCompress.py ./images --skip-existing

# 预览模式（不实际转换）
python ImageCompress.py ./images --dry-run

# 转换单个文件
python ImageCompress.py single.png --output .

# 转换时自动复制已存在的 WebP 文件到输出目录
python ImageCompress.py ./images --output ./webp_output

# 转换后删除原文件（移至回收站）
python ImageCompress.py ./images --output ./webp --delete recycle

# 转换后永久删除原文件
python ImageCompress.py ./images --output ./webp --delete permanent

# 忽略指定路径（直接跳过）
python ImageCompress.py ./images --ignore ./images/screenshots --ignore ./images/backup

# 忽略指定路径（跳过但复制到输出目录）
python ImageCompress.py ./images --output ./webp --ignore ./images/screenshots --ignore-mode copy
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `input` | 输入文件或目录路径（默认：当前目录） |
| `-o, --output` | 输出目录（默认：与输入相同目录） |
| `-q, --quality` | WebP 质量 0-100（默认：90，仅对有损模式生效） |
| `-l, --lossless` | 使用无损压缩 |
| `-m, --method` | 压缩方法 0-6（默认：6，压缩率最高） |
| `--no-recursive` | 不递归扫描子目录 |
| `--skip-existing` | 跳过已存在的目标文件 |
| `--flat` | 不保留目录结构，所有文件输出到同一目录 |
| `--ignore` | 忽略路径（可多次使用），匹配路径包含此字符串的文件将跳过处理 |
| `--ignore-mode` | 忽略文件的处理方式: `skip`=直接跳过, `copy`=跳过但复制到输出目录（默认: skip） |
| `--delete` | 删除原文件: `off`=不删除, `recycle`=移至回收站, `permanent`=永久删除（默认: off） |
| `--bg-color` | RGBA 转 RGB 时的背景色，格式 R,G,B（默认：255,255,255） |
| `--dry-run` | 仅扫描预览，不执行转换 |

## 元数据存储格式

### ComfyUI
| 介质 | 存储位置 |
|------|----------|
| PNG | `tEXt` 文本块：`prompt` (JSON), `workflow` (JSON) |
| WebP/JPEG | EXIF: `Make` → `workflow:{...}`, `Model` → `prompt:{...}` |

### WebUI (AUTOMATIC1111)
| 介质 | 存储位置 |
|------|----------|
| PNG | `tEXt` 文本块：`parameters` (文本) |
| WebP/JPEG | EXIF: `UserComment` → parameters 文本 |


