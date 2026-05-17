"""
图片压缩脚本 — 将 PNG/JPG 等格式转换为 WebP，并完整保留元信息。

功能:
  - 支持递归扫描目录，批量转换
  - 自动识别图片来源 (ComfyUI / WebUI / 其他)
  - 完整保留 prompt、workflow、parameters 等元数据
  - 支持有损/无损压缩
  - 支持自定义输出目录，不覆盖原文件
  - 支持排除已转换文件 (跳过)
  - 支持忽略路径 (可多个，可选直接跳过或复制到输出目录)

用法:
  python ImageCompress.py [输入目录/文件] [选项]

示例:
  python ImageCompress.py ./images
  python ImageCompress.py ./images --output ./webp_output --quality 85
  python ImageCompress.py ./images --lossless --method 6
  python ImageCompress.py single.png --output .
"""

import os
import sys
import json
import argparse
import glob
import shutil
import threading
import time
import concurrent.futures
from pathlib import Path

from PIL import Image, PngImagePlugin
import piexif
import piexif.helper


# ============================================================
#  支持的格式映射
# ============================================================

SUPPORTED_INPUT_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp'}

# 格式显示名称与扩展名的映射 (用于 CLI/GUI 选择)
FORMAT_EXTENSIONS = {
    'png': ['.png'],
    'jpg': ['.jpg', '.jpeg'],
    'bmp': ['.bmp'],
    'tiff': ['.tiff', '.tif'],
    'webp': ['.webp'],
}

# 所有可选格式名称列表
ALL_FORMAT_NAMES = sorted(FORMAT_EXTENSIONS.keys())

FORMAT_MAP = {
    '.webp': 'WEBP',
    '.jpg': 'JPEG',
    '.jpeg': 'JPEG',
    '.png': 'PNG',
    '.bmp': 'BMP',
    '.tiff': 'TIFF',
    '.tif': 'TIFF',
}


# ============================================================
#  元数据提取
# ============================================================

def extract_metadata(image: Image.Image) -> dict:
    """
    从 PIL.Image 中提取元数据。
    返回格式:
    {
        "prompt": dict|str|None,    # ComfyUI prompt
        "workflow": dict|str|None,  # ComfyUI workflow
        "parameters": str|None,     # WebUI parameters
        "exif_usercomment": str|None, # EXIF UserComment
        "source": str,              # 'comfyui' / 'webui' / 'unknown'
        "raw_exif": bytes|None,     # 原始 EXIF 字节 (如有)
    }
    """
    info = image.info
    metadata = {
        "prompt": None,
        "workflow": None,
        "parameters": None,
        "exif_usercomment": None,
        "source": "unknown",
        "raw_exif": None,
    }

    # ---------- 1. PNG tEXt/iTXt 文本块 ----------
    if "prompt" in info:
        try:
            metadata["prompt"] = json.loads(info["prompt"])
        except (json.JSONDecodeError, TypeError):
            metadata["prompt"] = info["prompt"]
        metadata["source"] = "comfyui"

    if "workflow" in info:
        try:
            metadata["workflow"] = json.loads(info["workflow"])
        except (json.JSONDecodeError, TypeError):
            metadata["workflow"] = info["workflow"]
        metadata["source"] = "comfyui"

    if "parameters" in info:
        metadata["parameters"] = info["parameters"]
        if metadata["source"] == "unknown":
            metadata["source"] = "webui"

    # ---------- 2. EXIF ----------
    exif_bytes = info.get("exif")
    if exif_bytes:
        metadata["raw_exif"] = exif_bytes
        try:
            exif_dict = piexif.load(exif_bytes)
        except Exception:
            exif_dict = {}

        # 提取 UserComment
        try:
            user_comment = (exif_dict or {}).get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
            if user_comment:
                if isinstance(user_comment, bytes):
                    try:
                        parsed = piexif.helper.UserComment.load(user_comment)
                        metadata["exif_usercomment"] = parsed
                    except Exception:
                        metadata["exif_usercomment"] = user_comment.decode("utf-8", errors="replace")
                else:
                    metadata["exif_usercomment"] = str(user_comment)
        except Exception:
            pass

        # 检查 Make/Model 字段 (ComfyUI 会将 workflow/prompt 写在这里)
        try:
            make = (exif_dict or {}).get("0th", {}).get(piexif.ImageIFD.Make, b"")
            model = (exif_dict or {}).get("0th", {}).get(piexif.ImageIFD.Model, b"")
            if isinstance(make, bytes):
                make_str = make.decode("utf-8", errors="replace")
            else:
                make_str = str(make)
            if isinstance(model, bytes):
                model_str = model.decode("utf-8", errors="replace")
            else:
                model_str = str(model)

            # ComfyUI 格式: "workflow:{...}" 或 "prompt:{...}"
            if make_str.startswith("workflow:"):
                try:
                    metadata["workflow"] = json.loads(make_str[len("workflow:"):])
                except (json.JSONDecodeError, TypeError):
                    metadata["workflow"] = make_str[len("workflow:"):]
                metadata["source"] = "comfyui"
            if model_str.startswith("prompt:"):
                try:
                    metadata["prompt"] = json.loads(model_str[len("prompt:"):])
                except (json.JSONDecodeError, TypeError):
                    metadata["prompt"] = model_str[len("prompt:"):]
                metadata["source"] = "comfyui"

            # 如果 Make/Model 中有内容但不是 ComfyUI 格式，保留为 exif_make/exif_model
            if make_str and not make_str.startswith("workflow:"):
                metadata["exif_make"] = make_str
            if model_str and not model_str.startswith("prompt:"):
                metadata["exif_model"] = model_str
        except Exception:
            pass

        # 如果有 UserComment 但还没确定来源，视为 webui
        if metadata["exif_usercomment"] and metadata["source"] == "unknown":
            metadata["source"] = "webui"

    return metadata


# ============================================================
#  元数据写入 (嵌入到目标文件的 EXIF)
# ============================================================

def build_exif_from_metadata(metadata: dict) -> dict:
    """
    根据提取的元数据构建 EXIF 字典 (供 piexir.insert 使用)。
    """
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    # ComfyUI: workflow -> Make, prompt -> Model (使用 JSON 紧凑格式)
    if metadata.get("workflow"):
        workflow_str = f"workflow:{json.dumps(metadata['workflow'], separators=(',', ':'))}"
        exif_dict["0th"][piexif.ImageIFD.Make] = workflow_str.encode("utf-8")

    if metadata.get("prompt"):
        prompt_str = f"prompt:{json.dumps(metadata['prompt'], separators=(',', ':'))}"
        exif_dict["0th"][piexif.ImageIFD.Model] = prompt_str.encode("utf-8")

    # WebUI: parameters -> UserComment
    if metadata.get("parameters"):
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = \
            piexif.helper.UserComment.dump(metadata["parameters"], encoding="unicode")

    # 如果是从 EXIF UserComment 读取的且没有 parameters，回写 UserComment
    if metadata.get("exif_usercomment") and not metadata.get("parameters"):
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = \
            piexif.helper.UserComment.dump(metadata["exif_usercomment"], encoding="unicode")

    # 保留原始的 Make/Model (非 ComfyUI 格式)
    if metadata.get("exif_make") and not metadata.get("workflow"):
        if isinstance(metadata["exif_make"], str):
            exif_dict["0th"][piexif.ImageIFD.Make] = metadata["exif_make"].encode("utf-8")
    if metadata.get("exif_model") and not metadata.get("prompt"):
        if isinstance(metadata["exif_model"], str):
            exif_dict["0th"][piexif.ImageIFD.Model] = metadata["exif_model"].encode("utf-8")

    # 清理空字典
    for ifd in list(exif_dict.keys()):
        if not exif_dict[ifd]:
            del exif_dict[ifd]

    return exif_dict


# ============================================================
#  文件大小格式化
# ============================================================

def format_size(size_bytes: int) -> str:
    """
    将字节数自动转为合适的单位 (B, KB, MB, GB, TB)。
    最多保留 2 位小数，单位间按 1024 换算。
    """
    if size_bytes < 1024:
        return f"{size_bytes:.1f} B"
    units = ["KB", "MB", "GB", "TB"]
    value = size_bytes / 1024.0
    for unit in units:
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


# ============================================================
#  文件时间戳保留
# ============================================================

def preserve_timestamps(src_path: str, dst_path: str):
    """
    将 src_path 的创建时间和修改时间复制到 dst_path。
    跨平台: 修改时间用 os.utime，Windows 创建时间用 ctypes SetFileTime。
    """
    try:
        stat_info = os.stat(src_path)
        # 修改时间 (所有平台)
        os.utime(dst_path, (stat_info.st_atime, stat_info.st_mtime))

        # 创建时间 (仅 Windows)
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                # 转换时间戳到 FILETIME (1601-01-01 以来的 100ns 间隔)
                epoch_as_filetime = 116444736000000000  # 1970-01-01 in FILETIME
                hundred_ns = 10000000  # 每秒 10000000 个 100ns 间隔

                def time_to_filetime(t):
                    return int(t * hundred_ns + epoch_as_filetime)

                handle = ctypes.windll.kernel32.CreateFileW(
                    dst_path,
                    0x40000000,  # GENERIC_WRITE
                    0x3,         # FILE_SHARE_READ | FILE_SHARE_WRITE
                    None,
                    3,           # OPEN_EXISTING
                    0,           # FILE_ATTRIBUTE_NORMAL
                    None,
                )
                if handle != -1 and handle is not None:
                    ctime = ctypes.c_longlong(time_to_filetime(stat_info.st_ctime))
                    atime = ctypes.c_longlong(time_to_filetime(stat_info.st_atime))
                    mtime = ctypes.c_longlong(time_to_filetime(stat_info.st_mtime))
                    ctypes.windll.kernel32.SetFileTime(
                        handle,
                        ctypes.byref(ctime),
                        ctypes.byref(atime),
                        ctypes.byref(mtime),
                    )
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass  # 创建时间设置失败不阻塞流程
    except Exception:
        pass  # 时间戳设置失败不阻塞流程


# ============================================================
#  删除原文件
# ============================================================

def delete_original(file_path: str, mode: str):
    """
    根据 mode 删除原文件。
    mode: 'off' 不删除, 'recycle' 移至回收站, 'permanent' 永久删除。
    返回 True 表示成功删除/移动, False 表示跳过或失败。
    """
    if mode == "off":
        return False

    if not os.path.isfile(file_path):
        return False

    try:
        if mode == "recycle":
            if sys.platform == "win32":
                # 使用 PowerShell 调用 Microsoft.VisualBasic 移动到回收站
                import subprocess
                ps_code = (
                    'Add-Type -AssemblyName Microsoft.VisualBasic; '
                    f'[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("{file_path}", '
                    '"OnlyErrorDialogs", "SendToRecycleBin")'
                )
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_code],
                    capture_output=True, timeout=30,
                    startupinfo=startupinfo,
                )
                return result.returncode == 0 and not os.path.isfile(file_path)
            else:
                # 非 Windows 平台回退到永久删除
                os.remove(file_path)
                return not os.path.isfile(file_path)
        else:  # permanent
            os.remove(file_path)
            return not os.path.isfile(file_path)
    except Exception:
        return False


# ============================================================
#  图片转换核心
# ============================================================

def convert_image(
    input_path: str,
    output_path: str,
    quality: int = 90,
    lossless: bool = False,
    method: int = 6,
    bg_color: tuple = (255, 255, 255),
) -> dict:
    """
    转换单张图片为 WebP，并保留元数据。
    返回包含处理结果信息的字典。
    """
    result = {
        "input": input_path,
        "output": output_path,
        "success": False,
        "error": None,
        "source": "unknown",
        "input_size": 0,
        "output_size": 0,
    }

    try:
        input_size = os.path.getsize(input_path)
        result["input_size"] = input_size
    except OSError:
        pass

    try:
        image = Image.open(input_path)
    except Exception as e:
        result["error"] = f"无法打开图片: {e}"
        return result

    # ---- 提取元数据 ----
    metadata = extract_metadata(image)
    result["source"] = metadata["source"]

    # ---- 处理图片模式 ----
    try:
        image = prepare_image_for_webp(image, bg_color)
    except Exception as e:
        result["error"] = f"图片模式转换失败: {e}"
        return result

    # ---- 构建 EXIF 并保存为 WebP ----
    try:
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 构建 EXIF 字典并 dump 为字节 (即使空字典也要 dump，传 None 会导致 Pillow 报错)
        exif_dict = build_exif_from_metadata(metadata)
        exif_bytes = piexif.dump(exif_dict)

        image.save(
            output_path,
            format="WEBP",
            quality=quality,
            lossless=lossless,
            method=method,
            exif=exif_bytes,
        )
    except Exception as e:
        result["error"] = f"保存 WebP 失败: {e}"
        return result

    try:
        result["output_size"] = os.path.getsize(output_path)
    except OSError:
        pass

    # ---- 保留原始文件时间戳 ----
    preserve_timestamps(input_path, output_path)

    result["success"] = True
    return result


def prepare_image_for_webp(image: Image.Image, bg_color: tuple = (255, 255, 255)) -> Image.Image:
    """
    将图片转换为 WebP 兼容的模式。
    WebP 支持 RGB (有损) 和 RGBA (无损/有损+透明)。
    
    规则:
    - RGBA: 保留 (WebP 支持透明度)
    - RGB:  直接保留
    - P / PA (调色板): 转换为 RGB/RGBA
    - L / LA (灰度): 转换为 RGB/RGBA
    - I, F (32位): 转换到 RGB
    - 其他: 尝试转换为 RGB
    """
    mode = image.mode

    if mode == "RGB":
        return image
    elif mode == "RGBA":
        return image
    elif mode == "P":
        # 调色板模式: 检查是否有透明信息
        if "transparency" in image.info:
            return image.convert("RGBA")
        return image.convert("RGB")
    elif mode == "PA":
        return image.convert("RGBA")
    elif mode == "L":
        return image.convert("RGB")
    elif mode == "LA":
        return image.convert("RGBA")
    elif mode in ("I", "F"):
        return image.convert("RGB")
    elif mode == "I;16":
        return image.point(lambda p: p * 0.0038910505836576).convert("RGB")
    else:
        # 未知模式: 尝试转为 RGB
        try:
            return image.convert("RGB")
        except Exception:
            return image.convert("RGBA")


# ============================================================
#  文件扫描
# ============================================================

def scan_images(input_path: str, recursive: bool = True, format_filter: set = None) -> list:
    """
    扫描路径下所有支持的图片文件。
    如果 input_path 是文件，直接返回该文件。
    如果 input_path 是目录，递归扫描所有图片文件。
    format_filter: 要包含的格式集合，如 {'png', 'jpg'}。默认 None 表示全部。
    """
    # 确定要扫描的扩展名
    if format_filter:
        exts_to_scan = set()
        for fmt in format_filter:
            fmt = fmt.lower().lstrip('.')
            if fmt in FORMAT_EXTENSIONS:
                exts_to_scan.update(FORMAT_EXTENSIONS[fmt])
        if not exts_to_scan:
            exts_to_scan = SUPPORTED_INPUT_EXTENSIONS
    else:
        exts_to_scan = SUPPORTED_INPUT_EXTENSIONS

    path = Path(input_path)

    if path.is_file():
        ext = path.suffix.lower()
        if ext in exts_to_scan:
            return [str(path)]
        else:
            print(f"  ⚠️  不支持的格式: {path.name}")
            return []

    if not path.is_dir():
        print(f"  ❌ 路径不存在: {input_path}")
        return []

    files = []
    for ext in exts_to_scan:
        if recursive:
            pattern = str(path / "**" / f"*{ext}")
            pattern_upper = str(path / "**" / f"*{ext.upper()}")
        else:
            pattern = str(path / f"*{ext}")
            pattern_upper = str(path / f"*{ext.upper()}")
        files.extend(glob.glob(pattern, recursive=recursive))
        files.extend(glob.glob(pattern_upper, recursive=recursive))

    # 去重并排序 (保留原文件名大小写)
    # Windows 文件系统大小写不敏感，因此不同大小写的同名文件不会同时出现
    files = sorted(set(f for f in files))
    return files


# ============================================================
#  忽略路径匹配
# ============================================================

def is_matched_ignore(file_path: str, ignore_patterns: list) -> bool:
    """
    检查 file_path 是否匹配任意忽略模式。
    匹配规则: 路径中任意部分包含模式字符串 (大小写不敏感)。
    路径分隔符在比较前统一为系统默认分隔符。
    """
    if not ignore_patterns:
        return False
    normalized_path = os.path.normcase(os.path.abspath(file_path))
    for pattern in ignore_patterns:
        if not pattern:
            continue
        # 统一路径分隔符: 将 / 替换为系统分隔符，再统一大小写
        norm_pattern = os.path.normcase(pattern.replace("/", os.sep))
        # 如果模式是绝对路径，精确匹配或前缀匹配
        if os.path.isabs(norm_pattern):
            if normalized_path == norm_pattern or normalized_path.startswith(norm_pattern + os.sep):
                return True
            # 即使绝对路径未匹配，也尝试子串匹配
            # (适用于忽略路径与文件在不同驱动器等场景)
            if norm_pattern in normalized_path:
                return True
        else:
            # 在路径中搜索模式字符串
            if norm_pattern in normalized_path:
                return True
    return False


# ============================================================
#  路径工具
# ============================================================

def get_output_path(input_path: str, output_dir: str, target_ext: str = ".webp") -> str:
    """
    根据输入路径和输出目录，生成输出文件路径。
    保留相对目录结构。
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    # 使用输入文件的文件名，替换扩展名为目标格式
    new_name = input_path.stem + target_ext
    return str(output_dir / new_name)


def get_output_path_preserve_structure(
    input_path: str,
    input_base_dir: str,
    output_dir: str,
    target_ext: str = ".webp",
) -> str:
    """
    保留相对于输入基础目录的目录结构。
    例如: input_base_dir/images/sub/file.png -> output_dir/sub/file.webp
    """
    input_path = Path(input_path)
    input_base = Path(input_base_dir)
    output_base = Path(output_dir)

    try:
        rel_path = input_path.relative_to(input_base)
    except ValueError:
        # 如果不在基础目录下，直接使用文件名
        return get_output_path(input_path, output_dir, target_ext)

    new_name = input_path.stem + target_ext
    return str(output_base / rel_path.parent / new_name)


# ============================================================
#  CLI 入口
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="图片压缩工具 — 转换为 WebP 并保留元数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s ./images
  %(prog)s ./images --output ./webp_output --quality 85
  %(prog)s ./images --lossless --method 6
  %(prog)s single.png --output .
  %(prog)s ./images --skip-existing --no-recursive
  %(prog)s ./images --output ./webp --delete recycle
  %(prog)s ./images --output ./webp --delete permanent
  %(prog)s ./images --ignore ./screenshots --ignore ./backup
  %(prog)s ./images --output ./webp --ignore ./screenshots --ignore-mode copy
        """,
    )

    parser.add_argument(
        "input",
        nargs="?",
        default=os.getcwd(),
        help="输入文件或目录路径 (默认: 当前目录)",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出目录 (默认: 与输入相同目录，即原地转换)",
    )
    parser.add_argument(
        "-q", "--quality",
        type=int,
        default=90,
        help="WebP 压缩质量 0-100 (默认: 90，仅有损模式生效)",
    )
    parser.add_argument(
        "-l", "--lossless",
        action="store_true",
        help="使用无损压缩",
    )
    parser.add_argument(
        "-m", "--method",
        type=int,
        default=6,
        choices=range(0, 7),
        help="压缩方法 0-6, 6 压缩率最高但最慢 (默认: 6)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="不递归扫描子目录",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="忽略的路径 (可多次使用)，匹配路径包含此字符串的文件将被忽略",
    )
    parser.add_argument(
        "--ignore-mode",
        type=str,
        choices=["skip", "copy"],
        default="skip",
        help="忽略文件的处理方式: skip=直接跳过, copy=跳过但复制到输出目录 (默认: skip)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="跳过已存在的目标文件(不覆盖)",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="不保留目录结构，所有文件直接输出到同一目录",
    )
    parser.add_argument(
        "--delete",
        type=str,
        choices=["off", "recycle", "permanent"],
        default="off",
        help="转换后删除原文件: off=不删除, recycle=移至回收站, permanent=永久删除 (默认: off)",
    )
    parser.add_argument(
        "--bg-color",
        type=str,
        default="255,255,255",
        help="RGBA 转 RGB 时的背景色, 格式 R,G,B (默认: 255,255,255)",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=None,
        help="并行处理线程数 (默认: CPU 核心数，单文件时自动设为 1)",
    )
    parser.add_argument(
        "-f", "--format",
        action="append",
        default=[],
        dest="formats",
        choices=ALL_FORMAT_NAMES,
        help=f"要处理的图片格式 (可多次使用，如 -f png -f jpg)。默认: 全部 ({', '.join(ALL_FORMAT_NAMES)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描和预览，不执行转换",
    )

    return parser.parse_args(argv)


def main():
    args = parse_args()
    input_path = args.input

    # ---- 解析背景色 ----
    try:
        bg_color = tuple(int(c.strip()) for c in args.bg_color.split(","))
        if len(bg_color) != 3:
            raise ValueError
    except (ValueError, TypeError):
        print("  ⚠️  背景色格式无效，使用默认值 255,255,255")
        bg_color = (255, 255, 255)

    # ---- 确定输出目录 ----
    input_is_file = Path(input_path).is_file()
    if args.output:
        output_dir = args.output
    elif input_is_file:
        output_dir = os.path.dirname(input_path) or "."
    else:
        output_dir = input_path  # 原地转换

    # ---- 扫描文件 ----
    recursive = not args.no_recursive
    format_filter = set(args.formats) if args.formats else None
    files = scan_images(input_path, recursive=recursive, format_filter=format_filter)

    if not files:
        print(f"📭 未找到支持的图片文件 (支持格式: {', '.join(sorted(SUPPORTED_INPUT_EXTENSIONS))})")
        return

    # ---- 忽略路径过滤 (skip 模式：扫描阶段直接移除) ----
    if args.ignore and args.ignore_mode == "skip":
        before = len(files)
        files = [f for f in files if not is_matched_ignore(f, args.ignore)]
        filtered = before - len(files)
        if filtered:
            print(f"  🚫 忽略路径过滤: 移除了 {filtered} 个文件")
        if not files:
            print(f"📭 所有文件均被忽略路径排除")
            return

    print(f"\n{'=' * 60}")
    print(f"  📁 输入: {os.path.abspath(input_path)}")
    print(f"  📂 输出: {os.path.abspath(output_dir)}")
    print(f"  🔍 找到 {len(files)} 个图片文件")
    if args.lossless:
        print(f"  🎯 模式: 无损压缩 (quality 参数将被忽略)")
    else:
        print(f"  🎯 模式: 有损压缩, quality={args.quality}")
    print(f"  🛠  method={args.method}")
    if format_filter:
        print(f"  📋 格式: {', '.join(sorted(format_filter))}")
    if args.delete != "off":
        action = "移至回收站" if args.delete == "recycle" else "永久删除"
        print(f"  🗑️  删除原文件: {action}")
    if args.ignore and args.ignore_mode == "copy":
        print(f"  🚫  忽略路径 (跳过并复制):")
        for p in args.ignore:
            print(f"       · {p}")
    # ---- 线程数 ----
    workers = args.workers
    if workers is None:
        # 单文件用 1 线程，否则用 CPU 核心数
        workers = 1 if len(files) <= 1 else min(os.cpu_count() or 4, len(files))
    print(f"  🧵 并行线程: {workers}" if workers > 1 else f"  🧵 单线程处理")
    print(f"{'=' * 60}")

    if args.dry_run:
        print("\n  📋 预览模式 (--dry-run)，未执行转换:")
        for f in files:
            print(f"    · {f}")
        print(f"\n  ✅ 共 {len(files)} 个文件待处理")
        return

    # ---- 计时开始 ----
    _start_time = time.time()

    # ---- 构建任务列表（串行，过滤跳过/忽略/复制）----
    tasks = []
    skip_count = 0
    total_files = len(files)

    for idx, file_path in enumerate(files, 1):
        # 确定输出路径
        if not args.flat and not input_is_file:
            out_path = get_output_path_preserve_structure(
                file_path, input_path, output_dir
            )
        else:
            out_path = get_output_path(file_path, output_dir)

        output_differs = os.path.normcase(os.path.abspath(out_path)) != os.path.normcase(os.path.abspath(file_path))

        # 跳过已存在的文件
        if args.skip_existing and os.path.exists(out_path):
            print(f"  ⏭️  [{idx}/{total_files}] 跳过 (已存在): {file_path}")
            skip_count += 1
            continue

        # 忽略路径 (copy 模式：跳过但复制)
        if args.ignore and args.ignore_mode == "copy" and is_matched_ignore(file_path, args.ignore):
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            shutil.copy2(file_path, out_path)
            preserve_timestamps(file_path, out_path)
            print(f"  📋  [{idx}/{total_files}] 忽略并复制: {file_path}")
            skip_count += 1
            continue

        # 跳过目标格式自身
        if file_path.lower().endswith(".webp") and not args.lossless:
            if output_differs:
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                shutil.copy2(file_path, out_path)
                preserve_timestamps(file_path, out_path)
                print(f"  📋  [{idx}/{total_files}] 复制 (已是 WebP): {file_path}")
            else:
                print(f"  ⏭️  [{idx}/{total_files}] 跳过 (已是 WebP): {file_path}")
            skip_count += 1
            continue

        # 加入转换任务
        tasks.append((idx, file_path, out_path))

    # ---- 并行转换 ----
    if not tasks:
        print("\n  📭 没有需要转换的文件")
    else:
        total_tasks = len(tasks)
        print(f"\n  🚀 开始处理 {total_tasks} 个文件 (线程数: {workers})")

        # 线程安全计数器
        _lock = threading.Lock()
        _success_count = 0
        _error_count = 0
        _delete_count = 0
        _total_input_size = 0
        _total_output_size = 0

        def _process_one(task_item):
            nonlocal _success_count, _error_count, _delete_count, _total_input_size, _total_output_size
            idx, fpath, opath = task_item

            result = convert_image(
                input_path=fpath,
                output_path=opath,
                quality=args.quality,
                lossless=args.lossless,
                method=args.method,
                bg_color=bg_color,
            )

            with _lock:
                if result["success"]:
                    ratio_str = ""
                    if result["input_size"] and result["output_size"]:
                        pct = (1 - result["output_size"] / result["input_size"]) * 100
                        sign = "-" if pct >= 0 else "+"
                        ratio_str = f" ({sign}{abs(pct):.1f}%)"

                    source_tag = {
                        "comfyui": " [ComfyUI]",
                        "webui": " [WebUI]",
                        "unknown": "",
                    }.get(result.get("source", ""), "")

                    print(f"  ✅  [{idx}/{total_files}] {os.path.basename(opath)}{source_tag}{ratio_str}")
                    _success_count += 1
                    _total_input_size += result.get("input_size", 0)
                    _total_output_size += result.get("output_size", 0)

                    if result.get("warning"):
                        print(f"    ⚠️  {result['warning']}")

                    # ------ 转换成功，立即删除原文件 ------
                    if args.delete != "off":
                        if delete_original(fpath, args.delete):
                            _delete_count += 1
                            print(f"    🗑️  [{idx}/{total_files}] 原文件已{'移至回收站' if args.delete == 'recycle' else '永久删除'}")
                else:
                    print(f"  ❌  [{idx}/{total_files}] {fpath} — {result['error']}")
                    _error_count += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(_process_one, tasks))

        success_count = _success_count
        error_count = _error_count
        delete_count = _delete_count
        total_input_size = _total_input_size
        total_output_size = _total_output_size

    # ---- 输出统计 ----
    elapsed = time.time() - _start_time
    # 格式化耗时
    if elapsed < 60:
        elapsed_str = f"{elapsed:.1f} 秒"
    else:
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        elapsed_str = f"{minutes} 分 {seconds:.0f} 秒"

    print(f"\n{'=' * 60}")
    print(f"  📊 处理完成:")
    print(f"     ✅ 成功: {success_count}")
    print(f"     ⏭️  跳过: {skip_count}")
    print(f"     ❌ 失败: {error_count}")
    print(f"     ⏱️  耗时: {elapsed_str}")
    if delete_count:
        print(f"     🗑️  已删除: {delete_count}")
    if success_count and total_input_size > 0:
        saved = total_input_size - total_output_size
        saved_pct = (1 - total_output_size / total_input_size) * 100
        print(f"     💾 原始大小: {format_size(total_input_size)}")
        print(f"     💾 输出大小: {format_size(total_output_size)}")
        print(f"     📉 节省空间: {format_size(saved)} ({saved_pct:.1f}%)")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
