"""
ImageCompress GUI — 图片压缩工具的图形界面
基于 tkinter/ttk，无需额外安装依赖。
"""

import os
import sys
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 导入核心功能
from ImageCompress import (
    scan_images,
    convert_image,
    get_output_path,
    get_output_path_preserve_structure,
    preserve_timestamps,
    SUPPORTED_INPUT_EXTENSIONS,
)


class ScrollableText(ttk.Frame):
    """带滚动条的文本框，用于显示日志"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent)
        self.text = tk.Text(self, wrap=tk.WORD, state=tk.DISABLED,
                            font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                            insertbackground="white", relief=tk.FLAT, borderwidth=0)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=scrollbar.set)

    def append(self, text: str, tag: str = None):
        """追加文本到末尾"""
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, text + "\n", tag)
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def clear(self):
        """清空所有文本"""
        self.text.configure(state=tk.NORMAL)
        self.text.delete(1.0, tk.END)
        self.text.configure(state=tk.DISABLED)


class ImageCompressGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ImageCompress — 图片压缩工具")
        self.root.geometry("800x700")
        self.root.minsize(700, 600)

        # 设置图标 (如果有)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # 状态变量
        self.source_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.quality = tk.IntVar(value=90)
        self.lossless = tk.BooleanVar(value=False)
        self.method = tk.IntVar(value=6)
        self.recursive = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=False)
        self.flat_mode = tk.BooleanVar(value=False)
        self.delete_mode = tk.StringVar(value="off")  # off / recycle / permanent

        self.is_running = False
        self.cancel_requested = False

        self._build_ui()
        self._center_window()

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _build_ui(self):
        # 主框架
        main_frame = ttk.Frame(self.root, padding="12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ======== 路径区域 ========
        path_frame = ttk.LabelFrame(main_frame, text="路径设置", padding="8")
        path_frame.pack(fill=tk.X, pady=(0, 8))

        # 源路径
        row1 = ttk.Frame(path_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="源路径:", width=10).pack(side=tk.LEFT)
        src_entry = ttk.Entry(row1, textvariable=self.source_path)
        src_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row1, text="浏览目录", command=self._browse_source_dir, width=10).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row1, text="浏览文件", command=self._browse_source_file, width=10).pack(side=tk.RIGHT, padx=1)

        # 输出路径
        row2 = ttk.Frame(path_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="输出目录:", width=10).pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.output_path).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row2, text="浏览", command=self._browse_output_dir, width=10).pack(side=tk.RIGHT)
        ttk.Label(row2, text="(留空则输出到源目录)", foreground="gray").pack(side=tk.RIGHT, padx=4)

        # ======== 压缩参数 ========
        param_frame = ttk.LabelFrame(main_frame, text="压缩参数", padding="8")
        param_frame.pack(fill=tk.X, pady=(0, 8))

        # 质量滑块
        quality_frame = ttk.Frame(param_frame)
        quality_frame.pack(fill=tk.X, pady=2)
        ttk.Label(quality_frame, text="压缩质量:").pack(side=tk.LEFT)
        quality_slider = ttk.Scale(quality_frame, from_=1, to=100,
                                   variable=self.quality, orient=tk.HORIZONTAL,
                                   command=self._on_quality_change)
        quality_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.quality_label = ttk.Label(quality_frame, text="90", width=3)
        self.quality_label.pack(side=tk.LEFT)
        ttk.Label(quality_frame, text="(仅有损模式生效)", foreground="gray").pack(side=tk.LEFT, padx=4)

        # 无损 + 方法
        row3 = ttk.Frame(param_frame)
        row3.pack(fill=tk.X, pady=4)
        self.lossless_cb = ttk.Checkbutton(row3, text="无损压缩 (忽略质量参数)",
                                           variable=self.lossless,
                                           command=self._on_lossless_change)
        self.lossless_cb.pack(side=tk.LEFT)

        ttk.Label(row3, text="  压缩方法:").pack(side=tk.LEFT, padx=(16, 4))
        method_combo = ttk.Combobox(row3, textvariable=self.method, width=8,
                                    values=list(range(7)), state="readonly")
        method_combo.pack(side=tk.LEFT)
        ttk.Label(row3, text="(0=最快, 6=压缩率最高)", foreground="gray").pack(side=tk.LEFT, padx=4)

        # ======== 选项 ========
        opt_frame = ttk.LabelFrame(main_frame, text="选项", padding="8")
        opt_frame.pack(fill=tk.X, pady=(0, 8))

        opt_row1 = ttk.Frame(opt_frame)
        opt_row1.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(opt_row1, text="递归扫描子目录",
                        variable=self.recursive).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Checkbutton(opt_row1, text="跳过已存在的目标文件 (不覆盖)",
                        variable=self.skip_existing).pack(side=tk.LEFT)

        opt_row2 = ttk.Frame(opt_frame)
        opt_row2.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(opt_row2, text="不保留目录结构 (所有文件输出到同一目录)",
                        variable=self.flat_mode).pack(side=tk.LEFT)

        # 删除选项
        del_frame = ttk.Frame(opt_frame)
        del_frame.pack(fill=tk.X, pady=4)
        ttk.Label(del_frame, text="转换后删除原文件:").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(del_frame, text="不删除", variable=self.delete_mode,
                        value="off").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Radiobutton(del_frame, text="移至回收站", variable=self.delete_mode,
                        value="recycle").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Radiobutton(del_frame, text="永久删除", variable=self.delete_mode,
                        value="permanent").pack(side=tk.LEFT)

        # ======== 操作按钮 ========
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        self.preview_btn = ttk.Button(btn_frame, text="🔍 预览",
                                      command=self._preview, width=12)
        self.preview_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.start_btn = ttk.Button(btn_frame, text="▶ 开始转换",
                                    command=self._start_convert, width=14)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.cancel_btn = ttk.Button(btn_frame, text="■ 取消",
                                     command=self._cancel, width=10, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT)

        ttk.Button(btn_frame, text="清空日志", command=self._clear_log,
                   width=10).pack(side=tk.RIGHT)

        # ======== 日志区域 ========
        log_frame = ttk.LabelFrame(main_frame, text="输出日志", padding="4")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log = ScrollableText(log_frame)
        self.log.pack(fill=tk.BOTH, expand=True)

        # 配置日志颜色标签
        self.log.text.tag_configure("success", foreground="#4ec9b0")
        self.log.text.tag_configure("error", foreground="#f44747")
        self.log.text.tag_configure("skip", foreground="#dcdcaa")
        self.log.text.tag_configure("copy", foreground="#9cdcfe")
        self.log.text.tag_configure("info", foreground="#888888")
        self.log.text.tag_configure("header", foreground="#569cd6", font=("Consolas", 10, "bold"))
        self.log.text.tag_configure("comfyui", foreground="#c586c0")
        self.log.text.tag_configure("webui", foreground="#ce9178")
        self.log.text.tag_configure("delete", foreground="#ff6b6b")

        # ======== 状态栏 ========
        self.status_var = tk.StringVar(value="就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W, padding=(8, 2))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 欢迎信息
        self.log.append("=" * 50, "header")
        self.log.append("  ImageCompress — 图片压缩工具", "header")
        self.log.append(f"  支持格式: {', '.join(sorted(SUPPORTED_INPUT_EXTENSIONS))}", "info")
        self.log.append("=" * 50, "header")
        self.log.append("")

    # -------- 回调 --------

    def _on_quality_change(self, _=None):
        self.quality_label.configure(text=str(self.quality.get()))

    def _on_lossless_change(self):
        if self.lossless.get():
            self.quality_label.configure(text="-")
        else:
            self.quality_label.configure(text=str(self.quality.get()))

    # -------- 文件浏览 --------

    def _browse_source_dir(self):
        path = filedialog.askdirectory(title="选择源目录")
        if path:
            self.source_path.set(path)

    def _browse_source_file(self):
        path = filedialog.askopenfilename(
            title="选择源文件",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif *.webp"),
                       ("所有文件", "*.*")],
        )
        if path:
            self.source_path.set(path)

    def _browse_output_dir(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_path.set(path)

    # -------- 预览 --------

    def _preview(self):
        source = self.source_path.get().strip()
        if not source:
            messagebox.showwarning("提示", "请先选择源路径")
            return

        if not os.path.exists(source):
            messagebox.showerror("错误", f"路径不存在:\n{source}")
            return

        self.log.append("")
        self.log.append("─" * 50, "info")

        files = self._get_files(source)
        if not files:
            self.log.append("📭 未找到支持的图片文件", "error")
            return

        output_dir = self._get_output_dir(source)
        self.log.append(f"📁 源路径: {os.path.abspath(source)}", "info")
        self.log.append(f"📂 输出目录: {os.path.abspath(output_dir)}", "info")
        self.log.append(f"🔍 找到 {len(files)} 个图片文件", "info")
        self.log.append("")

        for f in files:
            self.log.append(f"  · {os.path.relpath(f, source) if os.path.isdir(source) else os.path.basename(f)}", "info")

        self.log.append(f"\n✅ 共 {len(files)} 个文件待处理", "info")
        self.status_var.set(f"预览完成: {len(files)} 个文件")

    # -------- 转换核心 --------

    def _get_files(self, source):
        if os.path.isfile(source):
            ext = os.path.splitext(source)[1].lower()
            if ext in SUPPORTED_INPUT_EXTENSIONS:
                return [source]
            return []
        return scan_images(source, recursive=self.recursive.get())

    def _get_output_dir(self, source):
        out = self.output_path.get().strip()
        if out:
            return out
        if os.path.isfile(source):
            return os.path.dirname(source) or "."
        return source

    def _start_convert(self):
        source = self.source_path.get().strip()
        if not source:
            messagebox.showwarning("提示", "请先选择源路径")
            return

        if not os.path.exists(source):
            messagebox.showerror("错误", f"路径不存在:\n{source}")
            return

        # 禁用按钮
        self.is_running = True
        self.cancel_requested = False
        self.start_btn.configure(state=tk.DISABLED)
        self.preview_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)

        self.log.append("")
        self.log.append("=" * 50, "header")
        self.log.append("  开始转换", "header")
        self.log.append("=" * 50, "header")

        # 在后台线程执行转换
        thread = threading.Thread(target=self._run_convert, args=(source,), daemon=True)
        thread.start()

    def _cancel(self):
        self.cancel_requested = True
        self.log.append("\n⏹ 正在取消...", "skip")
        self.status_var.set("正在取消...")

    def _run_convert(self, source):
        try:
            files = self._get_files(source)
            if not files:
                self._append_log("📭 未找到支持的图片文件", "error")
                self._finish()
                return

            output_dir = self._get_output_dir(source)
            input_is_file = os.path.isfile(source)
            recursive = self.recursive.get()
            quality = self.quality.get()
            lossless = self.lossless.get()
            method_val = self.method.get()
            skip_existing = self.skip_existing.get()
            flat = self.flat_mode.get()
            delete_mode = self.delete_mode.get()

            # 转换参数
            bg_color = (255, 255, 255)

            self._append_log(f"📁 源路径: {os.path.abspath(source)}", "info")
            self._append_log(f"📂 输出目录: {os.path.abspath(output_dir)}", "info")
            self._append_log(f"🔍 共 {len(files)} 个文件", "info")
            if lossless:
                self._append_log("🎯 模式: 无损压缩", "info")
            else:
                self._append_log(f"🎯 模式: 有损压缩, quality={quality}", "info")
            self._append_log(f"🛠  method={method_val}", "info")            
            if delete_mode != "off":
                action = "移至回收站" if delete_mode == "recycle" else "永久删除"
                self._append_log(f"  🗑️  删除原文件: {action}", "info")            
            self._append_log("")

            success_count = 0
            skip_count = 0
            error_count = 0
            delete_count = 0
            total_input_size = 0
            total_output_size = 0

            for idx, file_path in enumerate(files, 1):
                if self.cancel_requested:
                    self._append_log("\n⏹ 已取消", "skip")
                    break

                # 确定输出路径
                if not flat and not input_is_file:
                    out_path = get_output_path_preserve_structure(
                        file_path, source, output_dir
                    )
                else:
                    out_path = get_output_path(file_path, output_dir)

                # 输出路径与输入路径不同时，需要复制跳过/未处理的文件
                # 使用 normcase 确保 Windows 上大小写不敏感比较
                output_differs = os.path.normcase(os.path.abspath(out_path)) != os.path.normcase(os.path.abspath(file_path))

                # 检查取消
                if self.cancel_requested:
                    break

                # 跳过已存在的文件
                if skip_existing and os.path.exists(out_path):
                    rel = os.path.relpath(file_path, source) if os.path.isdir(source) else os.path.basename(file_path)
                    self._append_log(f"  ⏭️  [{idx}/{len(files)}] 跳过 (已存在): {rel}", "skip")
                    skip_count += 1
                    continue

                # 跳过目标格式自身 (如 .webp -> .webp)
                if file_path.lower().endswith(".webp") and not lossless:
                    rel = os.path.relpath(file_path, source) if os.path.isdir(source) else os.path.basename(file_path)
                    if output_differs:
                        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                        shutil.copy2(file_path, out_path)
                        preserve_timestamps(file_path, out_path)
                        self._append_log(f"  📋  [{idx}/{len(files)}] 复制: {rel}", "copy")
                        # 复制后删除原文件
                        if delete_mode != "off":
                            from ImageCompress import delete_original
                            if delete_original(file_path, delete_mode):
                                delete_count += 1
                                action = "移至回收站" if delete_mode == "recycle" else "永久删除"
                                self._append_log(f"    🗑️  原文件已{action}", "delete")
                            else:
                                self._append_log(f"    ⚠️  原文件删除失败", "skip")
                    else:
                        self._append_log(f"  ⏭️  [{idx}/{len(files)}] 跳过 (已是 WebP): {rel}", "skip")
                    skip_count += 1
                    continue

                # 更新状态
                rel = os.path.relpath(file_path, source) if os.path.isdir(source) else os.path.basename(file_path)
                self._append_log(f"  🔄  [{idx}/{len(files)}] {rel}", "info")

                # 执行转换
                result = convert_image(
                    input_path=file_path,
                    output_path=out_path,
                    quality=quality,
                    lossless=lossless,
                    method=method_val,
                    bg_color=bg_color,
                )

                self._update_status(f"处理中: [{idx}/{len(files)}]")

                if result["success"]:
                    ratio = ""
                    if result["input_size"] and result["output_size"]:
                        pct = (1 - result["output_size"] / result["input_size"]) * 100
                        sign = "-" if pct >= 0 else "+"
                        ratio = f" ({sign}{abs(pct):.1f}%)"

                    source_tag = {
                        "comfyui": " [ComfyUI]",
                        "webui": " [WebUI]",
                        "unknown": "",
                    }.get(result.get("source", ""), "")

                    tag = "comfyui" if result.get("source") == "comfyui" else ("webui" if result.get("source") == "webui" else "success")
                    self._append_log(f"  ✅ → {os.path.basename(out_path)}{source_tag}{ratio}", tag)

                    success_count += 1
                    total_input_size += result.get("input_size", 0)
                    total_output_size += result.get("output_size", 0)

                    # ---- 删除原文件 ----
                    if delete_mode != "off":
                        # 延迟导入
                        from ImageCompress import delete_original
                        if delete_original(file_path, delete_mode):
                            delete_count += 1
                            action = "移至回收站" if delete_mode == "recycle" else "永久删除"
                            self._append_log(f"    🗑️  原文件已{action}", "delete")
                        else:
                            self._append_log(f"    ⚠️  原文件删除失败", "skip")

                    if result.get("warning"):
                        self._append_log(f"    ⚠️  {result['warning']}", "skip")
                else:
                    self._append_log(f"  ❌ {result['error']}", "error")
                    error_count += 1

            # 输出统计
            self._append_log("")
            self._append_log("─" * 40, "header")
            self._append_log(f"  ✅ 成功: {success_count}", "success")
            self._append_log(f"  ⏭️  跳过: {skip_count}", "skip")
            if delete_count:
                self._append_log(f"  🗑️  已删除: {delete_count}", "delete")
            self._append_log(f"  ❌ 失败: {error_count}", "error" if error_count else "info")

            if success_count and total_input_size > 0:
                saved = total_input_size - total_output_size
                saved_pct = (1 - total_output_size / total_input_size) * 100
                self._append_log(f"  💾 原始大小: {total_input_size / 1024:.1f} KB", "info")
                self._append_log(f"  💾 输出大小: {total_output_size / 1024:.1f} KB", "info")
                self._append_log(f"  📉 节省空间: {saved / 1024:.1f} KB ({saved_pct:.1f}%)", "success")

            self._append_log("─" * 40, "header")

            status_msg = f"处理完成: 成功 {success_count}, 跳过 {skip_count}, 失败 {error_count}"
            self._update_status(status_msg)

        except Exception as e:
            self._append_log(f"\n❌ 发生异常: {e}", "error")
            import traceback
            self._append_log(traceback.format_exc(), "error")
        finally:
            self._finish()

    def _append_log(self, text, tag=None):
        """线程安全地追加日志"""
        self.root.after(0, self.log.append, text, tag)

    def _update_status(self, text):
        """线程安全地更新状态栏"""
        self.root.after(0, self.status_var.set, text)

    def _finish(self):
        """恢复界面状态"""
        self.root.after(0, self._reset_ui)

    def _reset_ui(self):
        self.is_running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.preview_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        if not self.cancel_requested:
            self.log.append("")  # 空行

    def _clear_log(self):
        self.log.clear()

    # -------- 启动 --------

    def run(self):
        self.root.mainloop()


def main():
    """GUI 入口"""
    app = ImageCompressGUI()
    app.run()


if __name__ == "__main__":
    main()
