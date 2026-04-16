from __future__ import annotations

import argparse
import tkinter as tk
from io import BytesIO

from PIL import Image, ImageTk
try:
    from remote_login.qzone_browser_bridge import LoginState, QzoneBrowserBridge, SurfaceSnapshot
except ModuleNotFoundError:
    from qzone_browser_bridge import LoginState, QzoneBrowserBridge, SurfaceSnapshot


class QzoneSoftwareUI:
    SPECIAL_KEYS = {
        "BackSpace": ("Backspace", "Backspace", 8),
        "Tab": ("Tab", "Tab", 9),
        "Return": ("Enter", "Enter", 13),
        "Escape": ("Escape", "Escape", 27),
        "Delete": ("Delete", "Delete", 46),
        "Left": ("ArrowLeft", "ArrowLeft", 37),
        "Up": ("ArrowUp", "ArrowUp", 38),
        "Right": ("ArrowRight", "ArrowRight", 39),
        "Down": ("ArrowDown", "ArrowDown", 40),
        "Home": ("Home", "Home", 36),
        "End": ("End", "End", 35),
    }

    def __init__(self, bridge: QzoneBrowserBridge) -> None:
        self.bridge = bridge
        self.root = tk.Tk()
        self.root.title("QQ空间登录软件")
        self.root.geometry("860x760")
        self.root.minsize(760, 660)
        self.root.configure(bg="#f4efe7")

        self.status_var = tk.StringVar(value="正在连接浏览器...")
        self.meta_var = tk.StringVar(value="")

        self.current_surface: SurfaceSnapshot | None = None
        self.display_width = 0
        self.display_height = 0
        self.image_left = 0.0
        self.image_top = 0.0
        self.dragging = False
        self.keyboard_active = False
        self.mapping_closed = False
        self.photo_image: ImageTk.PhotoImage | None = None
        self.refresh_job: str | None = None
        self.input_flush_job: str | None = None
        self.suspend_input_trace = False
        self.input_var = tk.StringVar()

        self._build_ui()

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, bg="#f4efe7", padx=28, pady=24)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg="#f4efe7")
        header.pack(fill="x")

        title = tk.Label(
            header,
            text="QQ 空间登录",
            font=("Microsoft YaHei UI", 20, "bold"),
            fg="#1c2a39",
            bg="#f4efe7",
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            header,
            text="软件界面原型，仅保留一个可映射登录板块的位置。",
            font=("Microsoft YaHei UI", 10),
            fg="#6b7280",
            bg="#f4efe7",
        )
        subtitle.pack(anchor="w", pady=(6, 0))

        status_row = tk.Frame(shell, bg="#f4efe7", pady=18)
        status_row.pack(fill="x")

        status_badge = tk.Label(
            status_row,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 10),
            fg="#0f5132",
            bg="#d9f5e8",
            padx=12,
            pady=7,
        )
        status_badge.pack(side="left")

        refresh_button = tk.Button(
            status_row,
            text="刷新映射",
            command=self.refresh_surface,
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#ffffff",
            bg="#2f6fed",
            activebackground="#2559be",
            activeforeground="#ffffff",
            relief="flat",
            padx=16,
            pady=8,
            cursor="hand2",
        )
        refresh_button.pack(side="right")

        self.mapping_panel = tk.Frame(shell, bg="#fffaf2", highlightbackground="#d7c9b2", highlightthickness=1)
        self.mapping_panel.pack(fill="both", expand=True)

        panel_header = tk.Frame(self.mapping_panel, bg="#fffaf2", padx=18, pady=16)
        panel_header.pack(fill="x")

        panel_title = tk.Label(
            panel_header,
            text="登录映射区域",
            font=("Microsoft YaHei UI", 14, "bold"),
            fg="#2b3545",
            bg="#fffaf2",
        )
        panel_title.pack(anchor="w")

        panel_meta = tk.Label(
            panel_header,
            textvariable=self.meta_var,
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
            fg="#7b7f87",
            bg="#fffaf2",
        )
        panel_meta.pack(anchor="w", pady=(6, 0))

        canvas_wrap = tk.Frame(self.mapping_panel, bg="#fffaf2", padx=18, pady=18)
        canvas_wrap.pack(fill="both", expand=True, pady=(0, 18))

        self.canvas = tk.Canvas(
            canvas_wrap,
            bg="#ffffff",
            highlightbackground="#c8d3e1",
            highlightthickness=1,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

        self.input_proxy = tk.Entry(
            self.canvas,
            textvariable=self.input_var,
            width=1,
            bd=0,
            highlightthickness=0,
            relief="flat",
            fg="#ffffff",
            bg="#ffffff",
            insertbackground="#ffffff",
        )
        self.input_proxy.place_forget()
        self.input_proxy.bind("<KeyPress>", self.on_input_key_press)
        self.input_var.trace_add("write", self.on_input_var_change)

        self.placeholder_id = self.canvas.create_text(
            0,
            0,
            text="正在准备登录板块映射区域...",
            fill="#8b93a1",
            font=("Microsoft YaHei UI", 12),
        )

        self.login_done_card = tk.Frame(shell, bg="#eef7f1", highlightbackground="#b8dbc4", highlightthickness=1)
        self.login_done_title = tk.Label(
            self.login_done_card,
            text="已检测到登录成功",
            font=("Microsoft YaHei UI", 18, "bold"),
            fg="#1f5134",
            bg="#eef7f1",
            pady=24,
        )
        self.login_done_title.pack(anchor="center")
        self.login_done_desc = tk.Label(
            self.login_done_card,
            text="登录映射已自动关闭，软件仍保持运行。",
            font=("Microsoft YaHei UI", 10),
            fg="#4d6b57",
            bg="#eef7f1",
            pady=0,
        )
        self.login_done_desc.pack(anchor="center", pady=(0, 28))

        self.footer_label = tk.Label(
            shell,
            text="先点击登录映射区域，再输入文字；键盘和鼠标会同步到浏览器。",
            font=("Microsoft YaHei UI", 9),
            fg="#7b7f87",
            bg="#f4efe7",
            pady=14,
        )
        self.footer_label.pack(anchor="w")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def run(self) -> None:
        self.refresh_surface()
        self.root.mainloop()

    def on_close(self) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.input_flush_job is not None:
            self.root.after_cancel(self.input_flush_job)
            self.input_flush_job = None
        self.root.destroy()

    def refresh_surface(self) -> None:
        try:
            login_state = self.bridge.get_login_state()
        except Exception as exc:
            self.status_var.set(f"连接失败: {exc}")
            self._schedule_refresh(1500)
            return

        if login_state.status == "logged_in":
            self._show_logged_in_state(login_state)
            return

        if self.mapping_closed:
            self._show_mapping_panel()

        try:
            surface = self.bridge.capture_surface()
        except Exception as exc:
            self.status_var.set(f"连接失败: {exc}")
            self._schedule_refresh(1500)
            return

        self.current_surface = surface
        self.status_var.set("已连接浏览器")
        mode_text = "登录板块" if surface.mode == "login_frame" else "浏览器视图"
        self.meta_var.set(f"{mode_text} | {surface.title} | {surface.url}")

        image = Image.open(BytesIO(surface.image_bytes))
        canvas_width = max(self.canvas.winfo_width() - 24, 360)
        canvas_height = max(self.canvas.winfo_height() - 24, 360)
        image = self._fit_image(image, max_width=canvas_width, max_height=canvas_height)
        self.display_width, self.display_height = image.size
        self.photo_image = ImageTk.PhotoImage(image)

        self.canvas.delete("all")
        canvas_mid_x = self.canvas.winfo_width() / 2
        canvas_mid_y = self.canvas.winfo_height() / 2
        self.image_left = canvas_mid_x - self.display_width / 2
        self.image_top = canvas_mid_y - self.display_height / 2
        self.canvas.create_image(canvas_mid_x, canvas_mid_y, image=self.photo_image, anchor="center")
        self._schedule_refresh(500)

    def _show_logged_in_state(self, login_state: LoginState) -> None:
        self.current_surface = None
        self.photo_image = None
        self.display_width = 0
        self.display_height = 0
        self.image_left = 0.0
        self.image_top = 0.0
        self._deactivate_input_proxy()

        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None

        self.mapping_closed = True
        self.status_var.set("已登录，映射已关闭")
        self.meta_var.set(f"已登录 | {login_state.title} | {login_state.url}")
        self.login_done_desc.config(text="登录映射已自动关闭，软件仍保持运行。")

        if self.mapping_panel.winfo_manager():
            self.mapping_panel.pack_forget()
        if not self.login_done_card.winfo_manager():
            self.login_done_card.pack(fill="both", expand=True, before=self.footer_label)

    def _show_mapping_panel(self) -> None:
        self.mapping_closed = False
        if self.login_done_card.winfo_manager():
            self.login_done_card.pack_forget()
        if not self.mapping_panel.winfo_manager():
            self.mapping_panel.pack(fill="both", expand=True, before=self.footer_label)

    def _schedule_refresh(self, delay_ms: int) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(delay_ms, self.refresh_surface)

    def _fit_image(self, image: Image.Image, max_width: int, max_height: int) -> Image.Image:
        width, height = image.size
        ratio = min(max_width / width, max_height / height, 1.0)
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        if new_size == image.size:
            return image
        return image.resize(new_size, Image.LANCZOS)

    def on_canvas_resize(self, _event) -> None:
        if self.photo_image is None:
            self.canvas.delete("all")
            self.canvas.create_text(
                self.canvas.winfo_width() / 2,
                self.canvas.winfo_height() / 2,
                text="正在准备登录板块映射区域...",
                fill="#8b93a1",
                font=("Microsoft YaHei UI", 12),
            )

    def _translate_point(self, ui_x: float, ui_y: float) -> tuple[float, float] | None:
        surface = self.current_surface
        if surface is None or self.display_width <= 0 or self.display_height <= 0:
            return None

        relative_x = ui_x - self.image_left
        relative_y = ui_y - self.image_top

        if relative_x < 0 or relative_y < 0:
            return None
        if relative_x > self.display_width or relative_y > self.display_height:
            return None

        scale_x = surface.css_width / self.display_width
        scale_y = surface.css_height / self.display_height
        page_x = surface.css_x + relative_x * scale_x
        page_y = surface.css_y + relative_y * scale_y
        return page_x, page_y

    def on_button_press(self, event) -> None:
        point = self._translate_point(event.x, event.y)
        if point is None:
            self._deactivate_input_proxy()
            return

        self.dragging = True
        self.keyboard_active = True
        self._activate_input_proxy(event.x, event.y)
        x, y = point
        self.bridge.send_mouse_event("mouseMoved", x, y, buttons=0)
        self.bridge.send_mouse_event("mousePressed", x, y, buttons=1)

    def on_mouse_drag(self, event) -> None:
        if not self.dragging:
            return

        point = self._translate_point(event.x, event.y)
        if point is None:
            return

        x, y = point
        self.bridge.send_mouse_event("mouseMoved", x, y, buttons=1)

    def on_button_release(self, event) -> None:
        point = self._translate_point(event.x, event.y)
        if point is None:
            self.dragging = False
            return

        self.dragging = False
        x, y = point
        self.bridge.send_mouse_event("mouseReleased", x, y, buttons=0)

    def on_mouse_wheel(self, event) -> None:
        point = self._translate_point(event.x, event.y)
        if point is None:
            return

        x, y = point
        self.bridge.send_mouse_event("mouseWheel", x, y, delta_y=-int(event.delta))

    def _activate_input_proxy(self, ui_x: float, ui_y: float) -> None:
        self.keyboard_active = True
        x = max(1, min(int(ui_x), max(self.canvas.winfo_width() - 2, 1)))
        y = max(1, min(int(ui_y), max(self.canvas.winfo_height() - 2, 1)))
        self.input_proxy.place(x=x, y=y, width=2, height=2)
        self.input_proxy.lift()
        self.input_proxy.focus_set()
        self.input_proxy.icursor(tk.END)

    def _deactivate_input_proxy(self) -> None:
        self.keyboard_active = False
        if self.input_flush_job is not None:
            self.root.after_cancel(self.input_flush_job)
            self.input_flush_job = None
        self._set_input_var("")
        self.input_proxy.place_forget()

    def _set_input_var(self, value: str) -> None:
        self.suspend_input_trace = True
        try:
            self.input_var.set(value)
        finally:
            self.suspend_input_trace = False

    def on_input_var_change(self, *_args) -> None:
        if self.suspend_input_trace or not self.keyboard_active:
            return
        self._schedule_input_flush(180)

    def _schedule_input_flush(self, delay_ms: int) -> None:
        if self.input_flush_job is not None:
            self.root.after_cancel(self.input_flush_job)
        self.input_flush_job = self.root.after(delay_ms, self._flush_input_buffer)

    def _flush_input_buffer(self) -> None:
        self.input_flush_job = None
        if not self.keyboard_active:
            return

        text = self.input_var.get()
        if not text:
            return

        self.bridge.insert_text(text)
        self._set_input_var("")

    def on_input_key_press(self, event) -> str | None:
        if not self.keyboard_active:
            return None

        ctrl_pressed = bool(event.state & 0x4)
        key_lower = event.keysym.lower()

        if ctrl_pressed:
            self._flush_input_buffer()

            if key_lower == "a":
                self.bridge.send_ctrl_shortcut("a", "KeyA", 65)
                return "break"

            if key_lower == "c":
                self.bridge.send_ctrl_shortcut("c", "KeyC", 67)
                return "break"

            if key_lower == "x":
                self.bridge.send_ctrl_shortcut("x", "KeyX", 88)
                return "break"

            if key_lower == "v":
                try:
                    self.bridge.insert_text(self.root.clipboard_get())
                except tk.TclError:
                    pass
                return "break"

        special = self.SPECIAL_KEYS.get(event.keysym)
        if special is not None:
            self._flush_input_buffer()
            self.bridge.send_key(*special)
            return "break"

        if event.keysym in {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"}:
            return "break"

        return None


def run_smoke_test() -> None:
    bridge = QzoneBrowserBridge()
    login_state = bridge.get_login_state()
    snapshot = bridge.capture_surface()
    image = Image.open(BytesIO(snapshot.image_bytes))
    print(f"login_state={login_state.status}")
    print(f"mode={snapshot.mode}")
    print(f"title={snapshot.title}")
    print(f"url={snapshot.url}")
    print(f"css_rect=({snapshot.css_x}, {snapshot.css_y}, {snapshot.css_width}, {snapshot.css_height})")
    print(f"image_size={image.size}")


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ空间软件界面原型")
    parser.add_argument("--smoke-test", action="store_true", help="只做一次截图和桥接检查，不打开UI")
    args = parser.parse_args()

    if args.smoke_test:
        run_smoke_test()
        return

    app = QzoneSoftwareUI(QzoneBrowserBridge())
    app.run()


if __name__ == "__main__":
    main()
