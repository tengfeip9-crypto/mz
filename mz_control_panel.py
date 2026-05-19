from __future__ import annotations

import contextlib
import importlib
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from urllib.parse import urlparse
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_runtime import APP_BASE_DIR
from mz_env_settings import load_env_settings, save_env_settings
from mz_user_settings import LauncherSettings, load_settings, save_settings
from mz_core.runtime_cleanup import clear_build_artifacts, clear_runtime_history


class QueueWriter:
    def __init__(self, output_queue: "queue.Queue[tuple[str, object]]") -> None:
        self.output_queue = output_queue

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(("log", text))
        return len(text)

    def flush(self) -> None:
        return


class MZControlPanel:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MZ 总控面板")
        self.root.geometry("1080x940")
        self.root.minsize(980, 820)

        self.output_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None

        env_values = load_env_settings()
        settings = load_settings()

        self.qq_number_var = tk.StringVar(value=env_values["MZ_QQ_NUMBER"])
        self.debugger_address_var = tk.StringVar(value=env_values["MZ_DEBUGGER_ADDRESS"])
        self.chromedriver_path_var = tk.StringVar(value=env_values["MZ_CHROMEDRIVER_PATH"])
        self.chrome_path_var = tk.StringVar(value=env_values["MZ_CHROME_PATH"])
        self.chrome_user_data_dir_var = tk.StringVar(value=env_values["MZ_CHROME_USER_DATA_DIR"])

        self.auto_post_round_var = tk.StringVar(value=str(settings.auto_post_interval_big_rounds))
        self.friend_compare_round_var = tk.StringVar(value=str(settings.friend_compare_interval_big_rounds))
        self.friend_save_round_var = tk.StringVar(value=str(settings.friend_save_interval_big_rounds))
        self.big_round_sleep_var = tk.StringVar(value=str(settings.wait_between_big_rounds_seconds))
        self.auto_post_wait_var = tk.StringVar(value=str(settings.auto_post_wait_seconds))
        self.delete_after_post_var = tk.BooleanVar(value=settings.auto_post_delete_after_post)
        self.auto_forward_enabled_var = tk.BooleanVar(value=settings.auto_forward_enabled)
        self.auto_forward_keyword_var = tk.StringVar(value=settings.auto_forward_keyword)
        self.auto_forward_append_text_var = tk.StringVar(value=settings.auto_forward_append_text)
        self.auto_forward_include_forwarded_var = tk.BooleanVar(
            value=settings.auto_forward_include_forwarded_feeds
        )
        self.auto_forward_only_remark_suffix_emoji_var = tk.BooleanVar(
            value=settings.auto_forward_only_remark_suffix_emoji
        )
        self.auto_forward_model_enabled_var = tk.BooleanVar(value=settings.auto_forward_model_enabled)
        self.auto_forward_model_endpoint_var = tk.StringVar(value=settings.auto_forward_model_endpoint)
        self.auto_forward_model_name_var = tk.StringVar(value=settings.auto_forward_model_name)
        self.auto_forward_model_timeout_var = tk.StringVar(
            value=str(settings.auto_forward_model_timeout_seconds)
        )
        self.auto_forward_reason_model_endpoint_var = tk.StringVar(
            value=settings.auto_forward_reason_model_endpoint
        )
        self.auto_forward_reason_model_name_var = tk.StringVar(
            value=settings.auto_forward_reason_model_name
        )
        self.auto_forward_reason_model_timeout_var = tk.StringVar(
            value=str(settings.auto_forward_reason_model_timeout_seconds)
        )
        self.status_var = tk.StringVar(value="就绪")
        self.image_summary_var = tk.StringVar(value="未配置图片")
        self.detail_title_var = tk.StringVar(value="配置中心")

        self.auto_post_content: tk.Text | None = None
        self.image_listbox: tk.Listbox | None = None
        self.detail_pages: dict[str, ttk.Frame] = {}
        self.detail_page_canvases: dict[str, tk.Canvas] = {}
        self.detail_back_button: ttk.Button | None = None

        self._build_ui()
        assert self.auto_post_content is not None
        assert self.image_listbox is not None
        self.auto_post_content.insert("1.0", settings.auto_post_content)
        for image_path in settings.auto_post_images:
            self.image_listbox.insert(tk.END, image_path)
        self._refresh_image_summary()

        self.root.after(150, self._poll_output_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x")

        ttk.Label(header, text="MZ 总控面板", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="统一设置触发轮数、每大轮休眠与运行环境；自动说说和自动转发改为点击进入二级页面配置。",
        ).pack(anchor="w", pady=(6, 0))

        env_frame = ttk.LabelFrame(container, text="运行环境", padding=12)
        env_frame.pack(fill="x", pady=(14, 0))
        env_frame.columnconfigure(1, weight=1)

        ttk.Label(env_frame, text="QQ号").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(env_frame, textvariable=self.qq_number_var).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(env_frame, text="调试地址").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(env_frame, textvariable=self.debugger_address_var).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(env_frame, text="ChromeDriver").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(env_frame, textvariable=self.chromedriver_path_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(env_frame, text="浏览", command=self._browse_driver).grid(row=2, column=2, padx=(8, 0), pady=4)

        ttk.Label(env_frame, text="Chrome 程序").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(env_frame, textvariable=self.chrome_path_var).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(env_frame, text="浏览", command=self._browse_chrome).grid(row=3, column=2, padx=(8, 0), pady=4)

        ttk.Label(env_frame, text="调试用户目录").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(env_frame, textvariable=self.chrome_user_data_dir_var).grid(row=4, column=1, sticky="ew", pady=4)

        main_frame = ttk.Frame(container)
        main_frame.pack(fill="both", expand=True, pady=(14, 0))
        main_frame.columnconfigure(0, weight=2)
        main_frame.columnconfigure(1, weight=3)
        main_frame.rowconfigure(0, weight=1)

        left = ttk.Frame(main_frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)

        schedule_frame = ttk.LabelFrame(left, text="大轮触发配置", padding=12)
        schedule_frame.pack(fill="x")
        schedule_frame.columnconfigure(1, weight=1)

        self._grid_entry(schedule_frame, 0, "自动说说轮数", self.auto_post_round_var)
        self._grid_entry(schedule_frame, 1, "好友对比轮数", self.friend_compare_round_var)
        self._grid_entry(schedule_frame, 2, "好友保存轮数", self.friend_save_round_var)
        self._grid_entry(schedule_frame, 3, "每大轮休眠秒数", self.big_round_sleep_var)
        self._grid_entry(schedule_frame, 4, "说说删除等待秒数", self.auto_post_wait_var)

        delete_row = ttk.Frame(schedule_frame)
        delete_row.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(delete_row, text="自动说说发表后自动删除", variable=self.delete_after_post_var).pack(side="left")

        folder_frame = ttk.LabelFrame(left, text="历史文件夹", padding=12)
        folder_frame.pack(fill="x", pady=(14, 0))
        ttk.Button(folder_frame, text="打开好友列表导出目录", command=self._open_exports_folder).pack(fill="x", pady=4)
        ttk.Button(folder_frame, text="打开好友对比日志目录", command=self._open_logs_folder).pack(fill="x", pady=4)
        ttk.Button(folder_frame, text="打开好友快照目录", command=self._open_snapshots_folder).pack(fill="x", pady=4)
        ttk.Button(folder_frame, text="清空运行历史", command=self._clear_runtime_history).pack(fill="x", pady=(10, 4))
        ttk.Button(folder_frame, text="清理构建缓存", command=self._clear_build_artifacts).pack(fill="x", pady=4)

        action_frame = ttk.LabelFrame(left, text="操作", padding=12)
        action_frame.pack(fill="x", pady=(14, 0))
        ttk.Button(action_frame, text="保存配置", command=self._save_only).pack(fill="x", pady=4)
        self.browser_button = ttk.Button(action_frame, text="打开调试浏览器", command=self._open_debug_browser)
        self.browser_button.pack(fill="x", pady=4)
        self.start_button = ttk.Button(action_frame, text="开始运行", command=self._start_run)
        self.start_button.pack(fill="x", pady=4)
        self.stop_button = ttk.Button(action_frame, text="停止运行", command=self._stop_run, state="disabled")
        self.stop_button.pack(fill="x", pady=4)
        ttk.Label(action_frame, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(main_frame)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=2)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        detail_shell = ttk.LabelFrame(right, text="配置中心", padding=12)
        detail_shell.grid(row=0, column=0, sticky="nsew")
        detail_shell.rowconfigure(1, weight=1)
        detail_shell.columnconfigure(0, weight=1)

        detail_header = ttk.Frame(detail_shell)
        detail_header.grid(row=0, column=0, sticky="ew")
        detail_header.columnconfigure(1, weight=1)
        self.detail_back_button = ttk.Button(detail_header, text="返回总览", command=lambda: self._show_detail_page("home"))
        self.detail_back_button.grid(row=0, column=0, sticky="w")
        ttk.Label(detail_header, textvariable=self.detail_title_var, font=("Microsoft YaHei UI", 12, "bold")).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(10, 0),
        )

        detail_body = ttk.Frame(detail_shell)
        detail_body.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        detail_body.rowconfigure(0, weight=1)
        detail_body.columnconfigure(0, weight=1)

        home_page = self._create_scrollable_detail_page(detail_body, "home")
        home_page.columnconfigure(0, weight=1)
        home_page.columnconfigure(1, weight=1)

        ttk.Label(
            home_page,
            text="点击下面的入口进入二级配置页面。",
            font=("Microsoft YaHei UI", 11),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            home_page,
            text="自动说说的正文和配图合并到了同一个配置页；自动转发规则独立为另一个配置页。",
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))

        auto_post_card = ttk.LabelFrame(home_page, text="自动说说配置", padding=16)
        auto_post_card.grid(row=2, column=0, sticky="nsew", padx=(0, 8))
        auto_post_card.columnconfigure(0, weight=1)
        ttk.Label(
            auto_post_card,
            text="统一维护说说正文和配图列表。",
            wraplength=220,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            auto_post_card,
            text="进入自动说说配置",
            command=lambda: self._show_detail_page("auto_post"),
        ).grid(row=1, column=0, sticky="ew", pady=(14, 0))

        auto_forward_card = ttk.LabelFrame(home_page, text="自动转发配置", padding=16)
        auto_forward_card.grid(row=2, column=1, sticky="nsew", padx=(8, 0))
        auto_forward_card.columnconfigure(0, weight=1)
        ttk.Label(
            auto_forward_card,
            text="配置屏蔽关键词、附加文案和是否允许转发列表转发动态。",
            wraplength=220,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            auto_forward_card,
            text="进入自动转发配置",
            command=lambda: self._show_detail_page("auto_forward"),
        ).grid(row=1, column=0, sticky="ew", pady=(14, 0))

        auto_post_page = self._create_scrollable_detail_page(detail_body, "auto_post")
        auto_post_page.rowconfigure(1, weight=3)
        auto_post_page.rowconfigure(2, weight=2)
        auto_post_page.columnconfigure(0, weight=1)

        ttk.Label(
            auto_post_page,
            text="在这个二级页面里统一配置自动说说正文和配图资源。",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        content_frame = ttk.LabelFrame(auto_post_page, text="自动说说内容", padding=12)
        content_frame.grid(row=1, column=0, sticky="nsew")
        content_frame.rowconfigure(0, weight=1)
        content_frame.columnconfigure(0, weight=1)
        self.auto_post_content = tk.Text(
            content_frame,
            height=10,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
        )
        self.auto_post_content.grid(row=0, column=0, sticky="nsew")
        content_scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=self.auto_post_content.yview)
        content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.auto_post_content.configure(yscrollcommand=content_scrollbar.set)

        image_frame = ttk.LabelFrame(auto_post_page, text="自动说说配图", padding=12)
        image_frame.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        image_frame.rowconfigure(0, weight=1)
        image_frame.columnconfigure(0, weight=1)
        self.image_listbox = tk.Listbox(
            image_frame,
            height=7,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=("Microsoft YaHei UI", 9),
        )
        self.image_listbox.grid(row=0, column=0, sticky="nsew")
        image_scrollbar = ttk.Scrollbar(image_frame, orient="vertical", command=self.image_listbox.yview)
        image_scrollbar.grid(row=0, column=1, sticky="ns")
        self.image_listbox.configure(yscrollcommand=image_scrollbar.set)
        ttk.Label(image_frame, textvariable=self.image_summary_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        image_buttons = ttk.Frame(image_frame)
        image_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(image_buttons, text="添加图片", command=self._add_images).pack(side="left")
        ttk.Button(image_buttons, text="移除选中", command=self._remove_selected_images).pack(side="left", padx=(8, 0))
        ttk.Button(image_buttons, text="清空列表", command=self._clear_images).pack(side="left", padx=(8, 0))

        auto_forward_page = self._create_scrollable_detail_page(detail_body, "auto_forward")
        auto_forward_page.columnconfigure(1, weight=1)

        ttk.Label(
            auto_forward_page,
            text="在这个二级页面里配置动态自动转发规则。",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            auto_forward_page,
            text="启用动态自动转发",
            variable=self.auto_forward_enabled_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            auto_forward_page,
            text="仅自动转发备注名末尾带 emoji 表情的好友动态",
            variable=self.auto_forward_only_remark_suffix_emoji_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            auto_forward_page,
            text="开启后会读取最新好友快照里的备注名；只有备注名最后一个字符组带表情的好友才会进入自动转发候选。",
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(auto_forward_page, text="屏蔽关键词").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=(10, 4))
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_keyword_var).grid(row=4, column=1, sticky="ew", pady=(10, 4))
        ttk.Label(auto_forward_page, text="支持换行、逗号、顿号分隔；命中这些关键词的动态会直接跳过。").grid(
            row=5,
            column=1,
            sticky="w",
            pady=(0, 8),
        )
        ttk.Label(auto_forward_page, text="附加文案").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_append_text_var).grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Checkbutton(
            auto_forward_page,
            text="允许转发列表里本身就是转发的动态",
            variable=self.auto_forward_include_forwarded_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            auto_forward_page,
            text="启用本地模型判断是否转发",
            variable=self.auto_forward_model_enabled_var,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Label(auto_forward_page, text="模型接口地址").grid(row=9, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_model_endpoint_var).grid(
            row=9,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(auto_forward_page, text="模型名称").grid(row=10, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_model_name_var).grid(
            row=10,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(auto_forward_page, text="超时秒数").grid(row=11, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_model_timeout_var).grid(
            row=11,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(auto_forward_page, text="理由模型接口地址").grid(row=12, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_reason_model_endpoint_var).grid(
            row=12,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(auto_forward_page, text="理由模型名称").grid(row=13, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_reason_model_name_var).grid(
            row=13,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(auto_forward_page, text="理由模型超时秒数").grid(row=14, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(auto_forward_page, textvariable=self.auto_forward_reason_model_timeout_var).grid(
            row=14,
            column=1,
            sticky="ew",
            pady=4,
        )
        ttk.Label(
            auto_forward_page,
            text="启用后会先用判定模型决定是否转发，再用理由模型给每条候选生成复盘理由，并写入运行日志目录里的新 txt 文件。",
        ).grid(
            row=15,
            column=1,
            sticky="w",
            pady=(0, 8),
        )

        log_frame = ttk.LabelFrame(right, text="运行日志", padding=12)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self._show_detail_page("home")

    def _show_detail_page(self, page_name: str) -> None:
        page = self.detail_pages[page_name]
        page.tkraise()
        titles = {
            "home": "配置中心",
            "auto_post": "自动说说配置",
            "auto_forward": "自动转发配置",
        }
        self.detail_title_var.set(titles[page_name])
        canvas = self.detail_page_canvases.get(page_name)
        if canvas is not None:
            canvas.yview_moveto(0)
        if self.detail_back_button is not None:
            self.detail_back_button.config(state="disabled" if page_name == "home" else "normal")

    def _create_scrollable_detail_page(self, parent: ttk.Frame, page_name: str) -> ttk.Frame:
        page_shell = ttk.Frame(parent)
        page_shell.grid(row=0, column=0, sticky="nsew")
        page_shell.rowconfigure(0, weight=1)
        page_shell.columnconfigure(0, weight=1)

        canvas = tk.Canvas(
            page_shell,
            highlightthickness=0,
            borderwidth=0,
            background=self.root.cget("background"),
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(page_shell, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        content = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def _sync_scrollregion(_event=None) -> None:
            bbox = canvas.bbox("all")
            if bbox is not None:
                canvas.configure(scrollregion=bbox)

        def _fit_width(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        content.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _fit_width)

        self.detail_pages[page_name] = page_shell
        self.detail_page_canvases[page_name] = canvas
        return content

    def _grid_entry(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=4)

    def _browse_driver(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 ChromeDriver",
            filetypes=[("Executable", "*.exe"), ("All Files", "*.*")],
        )
        if path:
            self.chromedriver_path_var.set(path)

    def _browse_chrome(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Chrome",
            filetypes=[("Chrome", "chrome.exe"), ("Executable", "*.exe"), ("All Files", "*.*")],
        )
        if path:
            self.chrome_path_var.set(path)

    def _add_images(self) -> None:
        assert self.image_listbox is not None
        paths = filedialog.askopenfilenames(
            title="选择自动说说配图",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"), ("All Files", "*.*")],
        )
        existing = list(self.image_listbox.get(0, tk.END))
        for path in paths:
            if path not in existing:
                self.image_listbox.insert(tk.END, path)
                existing.append(path)
        self._refresh_image_summary()

    def _remove_selected_images(self) -> None:
        assert self.image_listbox is not None
        selected = list(self.image_listbox.curselection())
        selected.reverse()
        for index in selected:
            self.image_listbox.delete(index)
        self._refresh_image_summary()

    def _clear_images(self) -> None:
        assert self.image_listbox is not None
        self.image_listbox.delete(0, tk.END)
        self._refresh_image_summary()

    def _refresh_image_summary(self) -> None:
        assert self.image_listbox is not None
        total = self.image_listbox.size()
        if total == 0:
            self.image_summary_var.set("未配置图片")
            return
        self.image_summary_var.set(f"已配置 {total} 张图片，列表中显示的是完整路径。")

    def _friend_data_root(self) -> Path:
        return self._runtime_root("MZ_FRIEND_DATA_DIR", "friend_data")

    def _run_log_root(self) -> Path:
        return self._runtime_root("MZ_RUN_LOG_DIR", "run_logs")

    def _runtime_root(self, env_key: str, legacy_name: str) -> Path:
        env_values = load_env_settings()
        configured = env_values.get(env_key, "").strip()
        if configured:
            path = Path(os.path.expandvars(os.path.expanduser(configured)))
            if not path.is_absolute():
                path = APP_BASE_DIR / path
            return path
        legacy = APP_BASE_DIR / legacy_name
        if legacy.exists():
            return legacy
        return APP_BASE_DIR / ".local" / legacy_name

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return
        subprocess.Popen(["xdg-open", str(path)])

    def _open_exports_folder(self) -> None:
        self._open_folder(self._friend_data_root() / "exports")

    def _open_logs_folder(self) -> None:
        self._open_folder(self._friend_data_root() / "logs")

    def _open_snapshots_folder(self) -> None:
        self._open_folder(self._friend_data_root() / "snapshots")

    def _clear_runtime_history(self) -> None:
        confirmed = messagebox.askyesno(
            "清空运行历史",
            "这会删除好友快照、导出文件、对比日志，以及自动转发历史记录。是否继续？",
            parent=self.root,
        )
        if not confirmed:
            return

        stats = clear_runtime_history(self._friend_data_root(), self._run_log_root())
        summary = stats.summary_text()
        self.status_var.set("运行历史已清空")
        self._append_log(f"运行历史清理完成：{summary}\n")
        messagebox.showinfo("清理完成", summary, parent=self.root)

    def _clear_build_artifacts(self) -> None:
        confirmed = messagebox.askyesno(
            "清理构建缓存",
            "这会删除 build 目录和各处 __pycache__ 缓存目录。是否继续？",
            parent=self.root,
        )
        if not confirmed:
            return

        stats = clear_build_artifacts(APP_BASE_DIR)
        summary = stats.summary_text()
        self.status_var.set("构建缓存已清理")
        self._append_log(f"构建缓存清理完成：{summary}\n")
        messagebox.showinfo("清理完成", summary, parent=self.root)

    def _collect_settings(self) -> tuple[dict[str, str], LauncherSettings]:
        env_values = load_env_settings()
        env_values.update(
            {
                "MZ_QQ_NUMBER": self.qq_number_var.get().strip(),
                "MZ_DEBUGGER_ADDRESS": self.debugger_address_var.get().strip() or "127.0.0.1:9222",
                "MZ_CHROMEDRIVER_PATH": self.chromedriver_path_var.get().strip(),
                "MZ_CHROME_PATH": self.chrome_path_var.get().strip(),
                "MZ_CHROME_USER_DATA_DIR": self.chrome_user_data_dir_var.get().strip(),
            }
        )

        def require_non_negative_int(name: str, value: str) -> int:
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError(f"{name} 需要填写整数。") from exc
            if parsed < 0:
                raise ValueError(f"{name} 不能小于 0。")
            return parsed

        def require_non_negative_float(name: str, value: str) -> float:
            try:
                parsed = float(value)
            except ValueError as exc:
                raise ValueError(f"{name} 需要填写数字。") from exc
            if parsed < 0:
                raise ValueError(f"{name} 不能小于 0。")
            return parsed

        def require_positive_float(name: str, value: str) -> float:
            parsed = require_non_negative_float(name, value)
            if parsed <= 0:
                raise ValueError(f"{name} 必须大于 0。")
            return parsed

        assert self.auto_post_content is not None
        assert self.image_listbox is not None
        content = self.auto_post_content.get("1.0", "end").strip()
        auto_forward_model_enabled = bool(self.auto_forward_model_enabled_var.get())
        auto_forward_model_endpoint = self.auto_forward_model_endpoint_var.get().strip()
        auto_forward_model_name = self.auto_forward_model_name_var.get().strip()
        auto_forward_reason_model_endpoint = self.auto_forward_reason_model_endpoint_var.get().strip()
        auto_forward_reason_model_name = self.auto_forward_reason_model_name_var.get().strip()
        if auto_forward_model_enabled and not auto_forward_model_endpoint:
            raise ValueError("启用本地模型判断时，请填写模型接口地址。")
        if auto_forward_model_enabled and not auto_forward_model_name:
            raise ValueError("启用本地模型判断时，请填写模型名称。")
        if auto_forward_model_enabled and not auto_forward_reason_model_endpoint:
            raise ValueError("启用本地模型判断时，请填写理由模型接口地址。")
        if auto_forward_model_enabled and not auto_forward_reason_model_name:
            raise ValueError("启用本地模型判断时，请填写理由模型名称。")
        settings = LauncherSettings(
            auto_post_interval_big_rounds=require_non_negative_int("自动说说轮数", self.auto_post_round_var.get().strip()),
            friend_compare_interval_big_rounds=require_non_negative_int("好友对比轮数", self.friend_compare_round_var.get().strip()),
            friend_save_interval_big_rounds=require_non_negative_int("好友保存轮数", self.friend_save_round_var.get().strip()),
            wait_between_big_rounds_seconds=require_non_negative_float("每大轮休眠秒数", self.big_round_sleep_var.get().strip()),
            auto_post_content=content,
            auto_post_images=[str(item) for item in self.image_listbox.get(0, tk.END)],
            auto_post_wait_seconds=require_non_negative_float("说说删除等待秒数", self.auto_post_wait_var.get().strip()),
            auto_post_delete_after_post=bool(self.delete_after_post_var.get()),
            auto_forward_enabled=bool(self.auto_forward_enabled_var.get()),
            auto_forward_keyword=self.auto_forward_keyword_var.get().strip(),
            auto_forward_append_text=self.auto_forward_append_text_var.get().strip(),
            auto_forward_include_forwarded_feeds=bool(self.auto_forward_include_forwarded_var.get()),
            auto_forward_only_remark_suffix_emoji=bool(self.auto_forward_only_remark_suffix_emoji_var.get()),
            auto_forward_model_enabled=auto_forward_model_enabled,
            auto_forward_model_endpoint=auto_forward_model_endpoint,
            auto_forward_model_name=auto_forward_model_name,
            auto_forward_model_timeout_seconds=require_positive_float(
                "本地模型超时秒数",
                self.auto_forward_model_timeout_var.get().strip(),
            ),
            auto_forward_reason_model_endpoint=auto_forward_reason_model_endpoint,
            auto_forward_reason_model_name=auto_forward_reason_model_name,
            auto_forward_reason_model_timeout_seconds=require_positive_float(
                "理由模型超时秒数",
                self.auto_forward_reason_model_timeout_var.get().strip(),
            ),
        )
        return env_values, settings

    def _debugger_port_is_open(self, debugger_address: str) -> bool:
        try:
            host, port_text = debugger_address.rsplit(":", 1)
            port = int(port_text)
        except ValueError:
            return False

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex((host, port)) == 0

    def _wait_for_debugger_port(self, debugger_address: str, timeout_seconds: float = 20.0) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._debugger_port_is_open(debugger_address):
                return True
            time.sleep(0.5)
        return False

    def _endpoint_port_is_open(self, endpoint: str) -> bool:
        parsed = urlparse(str(endpoint or "").strip())
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex((host, port)) == 0

    def _find_lm_studio_cli(self) -> Path | None:
        candidates = [
            Path.home() / ".lmstudio" / "bin" / "lms.exe",
            Path(r"E:\LM Studio\resources\app\.webpack\lms.exe"),
        ]
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _ensure_local_model_server(self, settings: LauncherSettings) -> None:
        if not settings.auto_forward_model_enabled:
            return
        endpoints = []
        for endpoint in (
            settings.auto_forward_model_endpoint.strip(),
            settings.auto_forward_reason_model_endpoint.strip(),
        ):
            if endpoint and endpoint not in endpoints:
                endpoints.append(endpoint)

        for endpoint in endpoints:
            if self._endpoint_port_is_open(endpoint):
                self._append_log(f"本地模型接口已就绪：{endpoint}\n")
                continue

            parsed = urlparse(endpoint)
            host = parsed.hostname or ""
            port = parsed.port or 0
            if host not in {"127.0.0.1", "localhost"} or port != 1234:
                raise RuntimeError(f"本地模型接口未就绪：{endpoint}")

            lms_cli = self._find_lm_studio_cli()
            if lms_cli is None:
                raise RuntimeError("未找到 LM Studio CLI，请先在 LM Studio 里启动本地服务器。")

            self._append_log("本地模型接口未就绪，正在尝试启动 LM Studio Server...\n")
            result = subprocess.run(
                [str(lms_cli), "server", "start"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
            if output:
                self._append_log(output + "\n")
            if result.returncode != 0:
                raise RuntimeError("LM Studio Server 启动失败，请在 LM Studio 界面手动开启 Local Server。")

            deadline = time.time() + 10
            while time.time() < deadline:
                if self._endpoint_port_is_open(endpoint):
                    self._append_log(f"本地模型接口已启动：{endpoint}\n")
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError(f"LM Studio Server 已尝试启动，但接口仍未响应：{endpoint}")

    def _launch_debug_chrome(self, env_values: dict[str, str]) -> None:
        debugger_address = env_values.get("MZ_DEBUGGER_ADDRESS", "").strip() or "127.0.0.1:9222"
        if self._debugger_port_is_open(debugger_address):
            self._append_log(f"调试浏览器已在 {debugger_address} 运行，直接连接。\n")
            return

        chrome_path = env_values.get("MZ_CHROME_PATH", "").strip()
        if not chrome_path or not Path(chrome_path).is_file():
            raise RuntimeError("Chrome 程序路径无效，请在运行环境里填写 chrome.exe 路径。")

        user_data_dir = env_values.get("MZ_CHROME_USER_DATA_DIR", "").strip() or r"C:\ChromeDebug"
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        try:
            _host, port_text = debugger_address.rsplit(":", 1)
            int(port_text)
        except ValueError as exc:
            raise RuntimeError("调试地址格式应类似 127.0.0.1:9222。") from exc

        self._append_log(
            "正在启动调试浏览器："
            f"{chrome_path} --remote-debugging-port={port_text} --user-data-dir=\"{user_data_dir}\"\n"
        )
        subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={port_text}",
                f"--user-data-dir={user_data_dir}",
                "--window-size=1280,900",
                "--no-first-run",
                "--no-default-browser-check",
                "https://qzone.qq.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not self._wait_for_debugger_port(debugger_address):
            raise RuntimeError(f"Chrome 调试端口 {debugger_address} 未启动成功。")
        self._append_log(f"调试浏览器已启动：{debugger_address}\n")

    def _save_only(self) -> None:
        try:
            env_values, settings = self._collect_settings()
            save_env_settings(env_values)
            save_settings(settings)
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return

        self.status_var.set("配置已保存")
        self._append_log("配置已保存到 exe 同目录。\n")

    def _open_debug_browser(self) -> None:
        try:
            env_values, settings = self._collect_settings()
            save_env_settings(env_values)
            save_settings(settings)
            self._launch_debug_chrome(env_values)
        except ValueError as exc:
            messagebox.showerror("无法打开调试浏览器", str(exc), parent=self.root)
            return
        except RuntimeError as exc:
            messagebox.showerror("无法打开调试浏览器", str(exc), parent=self.root)
            return

        self.status_var.set("调试浏览器已就绪")

    def _set_running_state(self, running: bool) -> None:
        self.browser_button.config(state="disabled" if running else "normal")
        self.start_button.config(state="disabled" if running else "normal")
        self.stop_button.config(state="normal" if running else "disabled")
        self.status_var.set("运行中" if running else "就绪")

    def _start_run(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return

        try:
            env_values, settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showerror("无法启动", str(exc), parent=self.root)
            return

        if not env_values["MZ_QQ_NUMBER"]:
            messagebox.showerror("无法启动", "请先填写 QQ 号。", parent=self.root)
            return
        if settings.auto_post_interval_big_rounds > 0 and not settings.auto_post_content:
            messagebox.showerror("无法启动", "自动说说轮数大于 0 时，请填写说说内容。", parent=self.root)
            return

        save_env_settings(env_values)
        save_settings(settings)
        try:
            self._ensure_local_model_server(settings)
        except RuntimeError as exc:
            messagebox.showerror("无法启动", str(exc), parent=self.root)
            return

        debugger_address = env_values.get("MZ_DEBUGGER_ADDRESS", "").strip() or "127.0.0.1:9222"
        if not self._debugger_port_is_open(debugger_address):
            messagebox.showerror(
                "无法启动",
                f"调试浏览器未就绪，请先点击“打开调试浏览器”。\n调试地址：{debugger_address}",
                parent=self.root,
            )
            return

        self.stop_event = threading.Event()
        self._set_running_state(True)
        self._append_log("\n========== 开始运行 ==========\n")
        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()

    def _stop_run(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.status_var.set("正在停止")
            self._append_log("已发送停止信号，等待当前轮次收尾。\n")

    def _reload_runtime_modules(self):
        module_names = [
            "project_config",
            "mz_user_settings",
            "mz_core.friend_storage",
            "mz_core.ds",
            "mz_core.jc",
            "mz_core.db",
            "mz_core.feed_forward",
            "mz_core.mz",
        ]
        reloaded = {}
        for name in module_names:
            if name in sys.modules:
                reloaded[name] = importlib.reload(sys.modules[name])
            else:
                reloaded[name] = importlib.import_module(name)
        return reloaded["mz_core.mz"]

    def _run_worker(self) -> None:
        writer = QueueWriter(self.output_queue)
        exit_code = 1

        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                mz_module = self._reload_runtime_modules()
                exit_code = int(mz_module.main([], stop_event=self.stop_event))
        except Exception:
            self.output_queue.put(("log", traceback.format_exc()))
            exit_code = 1
        finally:
            self.output_queue.put(("finished", exit_code))

    def _poll_output_queue(self) -> None:
        while True:
            try:
                kind, payload = self.output_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(str(payload))
            elif kind == "finished":
                code = int(payload)
                self._set_running_state(False)
                self.status_var.set("已停止" if code == 0 else "运行失败")
                self._append_log(f"\n========== 结束，退出码 {code} ==========\n")
                self.worker_thread = None
                self.stop_event = None

        self.root.after(150, self._poll_output_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            should_close = messagebox.askyesno(
                "正在运行",
                "主流程还在运行。要发送停止信号并关闭窗口吗？",
                parent=self.root,
            )
            if not should_close:
                return
            self._stop_run()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = MZControlPanel()
    app.run()


if __name__ == "__main__":
    main()
