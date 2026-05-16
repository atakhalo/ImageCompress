#!/usr/bin/env python3
"""
PNGInfo 读取工具
读取文件夹下的图片（PNG, JPG, WebP），提取并显示 PNG 图片的 pnginfo（元数据）。

支持:
  - PNG: 读取 tEXt/iTXt 文本块（如 prompt、workflow、parameters 等）
  - JPG: 读取 EXIF 元数据
  - WebP: 读取 EXIF 元数据

可识别图片来源:
  - ComfyUI: 包含 prompt / workflow 文本块
  - WebUI (AUTOMATIC1111): 包含 parameters 文本块

用法:
  python read_pnginfo.py [文件夹路径]
  如果不指定路径，则扫描当前文件夹
"""

import os
import sys
import json
import struct
import glob
from pathlib import Path


# ============================================================
#  文件扫描
# ============================================================

def get_image_files(folder_path):
    """获取文件夹下所有支持的图片文件 (递归)"""
    extensions = ('*.png', '*.jpg', '*.jpeg', '*.webp')
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(folder_path, '**', ext), recursive=True))
    return sorted(files)


# ============================================================
#  PNG 解析
# ============================================================

def read_png_chunks(filepath):
    """读取 PNG 文件的所有数据块，返回 (chunks, error)"""
    chunks = []
    with open(filepath, 'rb') as f:
        signature = f.read(8)
        if signature != b'\x89PNG\r\n\x1a\n':
            return None, "无效的 PNG 文件签名"

        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length = struct.unpack('>I', header[:4])[0]
            chunk_type = header[4:8]
            chunk_data = f.read(length)
            f.read(4)  # CRC
            chunks.append((chunk_type, chunk_data))
            if chunk_type == b'IEND':
                break
    return chunks, None


def parse_png_text_chunk(chunk_type, chunk_data):
    """
    解析 PNG 文本块，返回 (keyword, text)。
    支持 tEXt 和 iTXt 两种格式。
    """
    if chunk_type == b'tEXt':
        null_idx = chunk_data.find(b'\x00')
        if null_idx < 0:
            return None, None
        keyword = chunk_data[:null_idx].decode('latin-1', errors='replace')
        text = chunk_data[null_idx + 1:].decode('latin-1', errors='replace')
        return keyword, text

    elif chunk_type == b'iTXt':
        null_idx = chunk_data.find(b'\x00')
        if null_idx < 0:
            return None, None
        keyword = chunk_data[:null_idx].decode('latin-1', errors='replace')
        rest = chunk_data[null_idx + 1:]
        if len(rest) < 2:
            return None, None
        comp_flag = rest[0]
        rest = rest[2:]                     # skip compression flag & method
        lang_end = rest.find(b'\x00')      # language tag
        if lang_end < 0:
            return None, None
        rest = rest[lang_end + 1:]
        tk_end = rest.find(b'\x00')        # translated keyword
        if tk_end < 0:
            return None, None
        text_data = rest[tk_end + 1:]
        if comp_flag == 1:
            import zlib
            try:
                text_data = zlib.decompress(text_data)
            except Exception:
                pass
        text = text_data.decode('utf-8', errors='replace')
        return keyword, text

    return None, None


# ============================================================
#  EXIF 解析
# ============================================================

def parse_exif_data(exif_bytes):
    """解析 EXIF 二进制数据，返回标签字典"""
    result = {}
    try:
        import piexif
        exif_dict = piexif.load(exif_bytes)
        for ifd_name in exif_dict:
            if not exif_dict[ifd_name]:
                continue
            for tag, value in exif_dict[ifd_name].items():
                tag_info = piexif.TAGS.get(ifd_name, {}).get(tag, {})
                tag_name = tag_info.get('name', str(tag))
                if isinstance(value, bytes):
                    try:
                        decoded = value.decode('utf-8', errors='replace')
                        decoded = decoded.replace('\x00', '')  # 清理空字符
                        result[tag_name] = decoded
                    except Exception:
                        result[tag_name] = str(value[:80])
                else:
                    result[tag_name] = str(value)
    except ImportError:
        result['_warning'] = 'piexif 未安装，无法解析 EXIF'
    except Exception as e:
        result['_warning'] = f'EXIF 解析异常: {e}'
    return result


# ============================================================
#  各格式读取器
# ============================================================

def read_png_info(filepath):
    """读取 PNG 文件的元信息"""
    info = {
        'file': os.path.basename(filepath),
        'path': filepath,
        'size': os.path.getsize(filepath),
        'type': 'PNG',
        'text_chunks': {},
        'exif': {},
        'source': 'unknown',
    }

    chunks, error = read_png_chunks(filepath)
    if error:
        info['error'] = error
        return info

    for chunk_type, chunk_data in chunks:
        if chunk_type in (b'tEXt', b'iTXt'):
            keyword, text = parse_png_text_chunk(chunk_type, chunk_data)
            if keyword:
                info['text_chunks'][keyword] = text
        elif chunk_type == b'eXIf':
            info['exif'] = parse_exif_data(chunk_data)

    # 识别图片来源
    if 'prompt' in info['text_chunks'] or 'workflow' in info['text_chunks']:
        info['source'] = 'ComfyUI'
    if 'parameters' in info['text_chunks']:
        info['source'] = 'WebUI (AUTOMATIC1111)'

    return info


def read_jpg_info(filepath):
    """读取 JPEG 文件的元信息"""
    info = {
        'file': os.path.basename(filepath),
        'path': filepath,
        'size': os.path.getsize(filepath),
        'type': 'JPEG',
        'exif': {},
    }
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        info['exif'] = parse_exif_data(data)
    except Exception as e:
        info['error'] = f'读取失败: {e}'
    return info


def read_webp_info(filepath):
    """读取 WebP 文件的元信息"""
    info = {
        'file': os.path.basename(filepath),
        'path': filepath,
        'size': os.path.getsize(filepath),
        'type': 'WebP',
        'exif': {},
    }
    try:
        with open(filepath, 'rb') as f:
            riff = f.read(4)
            if riff != b'RIFF':
                info['error'] = '无效的 WebP 文件'
                return info
            f.read(4)                     # file size
            if f.read(4) != b'WEBP':
                info['error'] = '无效的 WebP 文件'
                return info

            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack('<I', f.read(4))[0]
                chunk_data = f.read(chunk_size)
                if chunk_size % 2:
                    f.read(1)            # padding

                if chunk_id == b'EXIF':
                    info['exif'] = parse_exif_data(chunk_data)

                # WebP 规范允许 EXIF 出现在图像数据前后，
                # 因此不提前 break，继续扫描所有 chunk
    except Exception as e:
        info['error'] = f'读取失败: {e}'
    return info


# ============================================================
#  格式化输出
# ============================================================

def try_format_json(text, max_len=2000):
    """尝试将文本按 JSON 格式化，失败则原样返回"""
    try:
        parsed = json.loads(text)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        if len(formatted) > max_len:
            return formatted[:max_len] + f"\n    ... (已截断, 共 {len(formatted)} 字符)"
        return formatted
    except json.JSONDecodeError:
        if len(text) > max_len:
            return text[:max_len] + f"\n    ... (已截断, 共 {len(text)} 字符)"
        return text


def print_file_info(info):
    """打印单个文件的元数据"""
    # 第一行: 文件名 + 类型 + 大小 + 来源
    tags = []
    if info.get('source') and info['source'] != 'unknown':
        tags.append(info['source'])
    tag_str = f" [{', '.join(tags)}]" if tags else ""
    print(f"\n{'=' * 70}")
    print(f"  📄 {info['file']}{tag_str}")
    print(f"  📂 {info['path']}")
    print(f"  💾 {info['size']:,} bytes  |  类型: {info['type']}")
    print(f"{'=' * 70}")

    if 'error' in info:
        print(f"  ❌ 错误: {info['error']}")
        return

    # 文本块
    if info.get('text_chunks'):
        for keyword, text in info['text_chunks'].items():
            print(f"\n  📝 [{keyword}]")
            for line in try_format_json(text).split('\n'):
                print(f"    {line}")

    # EXIF
    if info.get('exif'):
        exif = info['exif']
        # 过滤无内容或纯警告的键
        content_items = {k: v for k, v in exif.items()
                         if v and v.strip() and not k.startswith('_')}
        if content_items:
            print(f"\n  📷 EXIF 元数据:")
            for tag_name, value in content_items.items():
                val_str = value[:500]
                print(f"    {tag_name}: {val_str}")

        if '_warning' in exif and exif['_warning']:
            print(f"\n  ⚠️  {exif['_warning']}")


def scan_folder(folder_path):
    """扫描文件夹并输出所有图片的元数据"""
    folder = Path(folder_path).resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"❌ 错误: 文件夹 '{folder_path}' 不存在")
        return

    files = get_image_files(str(folder))
    if not files:
        print(f"📭 在 '{folder}' 中未找到图片文件 (png/jpg/webp)")
        return

    print(f"\n{'#' * 70}")
    print(f"  📁 扫描文件夹: {folder}")
    print(f"  🔍 找到 {len(files)} 个图片文件")
    print(f"{'#' * 70}")

    png_count = 0
    for filepath in files:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.png':
            info = read_png_info(filepath)
            png_count += 1
        elif ext in ('.jpg', '.jpeg'):
            info = read_jpg_info(filepath)
        elif ext == '.webp':
            info = read_webp_info(filepath)
        else:
            continue
        print_file_info(info)

    print(f"\n{'=' * 70}")
    print(f"  ✅ 扫描完成: 共 {len(files)} 个文件 ({png_count} 个 PNG)")
    print(f"{'=' * 70}\n")


# ============================================================
#  入口
# ============================================================

def main():
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
    else:
        folder_path = os.getcwd()
    scan_folder(folder_path)


if __name__ == '__main__':
    main()
