from __future__ import annotations

import base64
import socket
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

from project_config import (  # noqa: E402
    CHROME_PATH as DEFAULT_CHROME_PATH,
    CHROME_USER_DATA_DIR,
    CHROMEDRIVER_PATH as DEFAULT_CHROMEDRIVER_PATH,
    DEBUGGER_ADDRESS,
)

CHROME_PATH = str(DEFAULT_CHROME_PATH)
CHROMEDRIVER_PATH = str(DEFAULT_CHROMEDRIVER_PATH)
USER_DATA_DIR = str(CHROME_USER_DATA_DIR)
START_URL = "https://qzone.qq.com/"


@dataclass
class SurfaceSnapshot:
    mode: str
    title: str
    url: str
    image_bytes: bytes
    mime_type: str
    css_x: float
    css_y: float
    css_width: float
    css_height: float


@dataclass
class LoginState:
    status: str
    title: str
    url: str
    cookie_names: set[str]


def _default_login_panel_state() -> dict:
    return {
        "mode": "unknown",
        "qqInputVisible": False,
        "passwordInputVisible": False,
        "loginButtonVisible": False,
        "verifyCodeVisible": False,
        "verifyIframeVisible": False,
        "verificationRequired": False,
        "verificationKind": "",
        "phoneVerificationVisible": False,
        "switchToPasswordAvailable": False,
        "errorText": "",
        "bodyText": "",
    }


class QzoneBrowserBridge:
    def __init__(
        self,
        *,
        debugger_address: str = DEBUGGER_ADDRESS,
        user_data_dir: str = USER_DATA_DIR,
        start_url: str = START_URL,
        chrome_path: str = CHROME_PATH,
        chromedriver_path: str = CHROMEDRIVER_PATH,
    ) -> None:
        self.debugger_address = debugger_address
        self.user_data_dir = user_data_dir
        self.start_url = start_url
        self.chrome_path = chrome_path
        self.chromedriver_path = chromedriver_path
        self.driver: webdriver.Chrome | None = None
        self.chrome_process: subprocess.Popen | None = None
        self._target_page_ready = False

    def ensure_browser(self) -> webdriver.Chrome:
        if self.driver is not None:
            return self.driver

        if not self._port_is_open():
            self._start_chrome()
            if not self._wait_for_port():
                raise RuntimeError(f"Chrome 调试端口 {self.debugger_address} 未启动成功。")

        options = Options()
        options.add_experimental_option("debuggerAddress", self.debugger_address)
        self.driver = webdriver.Chrome(service=Service(self.chromedriver_path), options=options)
        self._ensure_target_page()
        return self.driver

    def _port_is_open(self) -> bool:
        host, port_text = self.debugger_address.split(":")
        port = int(port_text)
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.settimeout(1)
            return sock.connect_ex((host, port)) == 0

    def _wait_for_port(self, timeout_seconds: float = 20.0) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._port_is_open():
                return True
            time.sleep(0.5)
        return False

    def _start_chrome(self) -> None:
        _host, port_text = self.debugger_address.split(":")
        self.chrome_process = subprocess.Popen(
            [
                self.chrome_path,
                f"--remote-debugging-port={port_text}",
                f"--user-data-dir={self.user_data_dir}",
                "--window-size=1280,900",
                "--disable-background-networking",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
                self.start_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ensure_target_page(self) -> None:
        if self._target_page_ready:
            return

        driver = self.ensure_browser()
        current_url = (driver.current_url or "").lower()
        if "qzone.qq.com" not in current_url and "i.qq.com" not in current_url:
            driver.get(self.start_url)
            time.sleep(1.5)
        self._target_page_ready = True

    def capture_surface(self) -> SurfaceSnapshot:
        driver = self.ensure_browser()
        title = driver.title
        url = driver.current_url
        driver.switch_to.default_content()
        login_frame = self._find_login_frame()
        if login_frame is not None:
            panel_state = self.get_login_panel_state()
            if panel_state.get("verificationRequired"):
                iframe_surface = self._capture_login_frame_surface(login_frame, title, url)
                if iframe_surface is not None:
                    return iframe_surface
            if panel_state.get("mode") == "password":
                form_surface = self._capture_login_panel_surface(title, url)
                if form_surface is not None:
                    return form_surface
            iframe_surface = self._capture_login_frame_surface(login_frame, title, url)
            if iframe_surface is not None:
                return iframe_surface

        viewport = driver.execute_script(
            """
            return {
                width: window.innerWidth || document.documentElement.clientWidth || 1280,
                height: window.innerHeight || document.documentElement.clientHeight || 900
            };
            """
        )
        screenshot = driver.execute_cdp_cmd(
            "Page.captureScreenshot",
            {
                "format": "jpeg",
                "quality": 65,
                "captureBeyondViewport": False,
                "fromSurface": True,
            },
        )
        image_bytes = base64.b64decode(screenshot["data"])
        return SurfaceSnapshot(
            mode="viewport",
            title=title,
            url=url,
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            css_x=0.0,
            css_y=0.0,
            css_width=float(viewport["width"]),
            css_height=float(viewport["height"]),
        )

    def _capture_login_frame_surface(
        self,
        login_frame,
        title: str,
        url: str,
    ) -> SurfaceSnapshot | None:
        driver = self.ensure_browser()
        driver.switch_to.default_content()
        frame = login_frame if login_frame is not None else self._find_login_frame()
        if frame is None:
            return None
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", frame)
        rect = frame.rect
        return SurfaceSnapshot(
            mode="login_frame",
            title=title,
            url=url,
            image_bytes=frame.screenshot_as_png,
            mime_type="image/png",
            css_x=float(rect["x"]),
            css_y=float(rect["y"]),
            css_width=float(rect["width"]),
            css_height=float(rect["height"]),
        )

    def _capture_login_panel_surface(self, title: str, url: str) -> SurfaceSnapshot | None:
        driver = self.ensure_browser()
        driver.switch_to.default_content()
        login_frame = self._find_login_frame()
        if login_frame is None:
            return None

        frame_rect = login_frame.rect

        def _read_panel(driver_in_frame: webdriver.Chrome):
            for element_id in ("loginform", "web_login", "login"):
                try:
                    candidate = driver_in_frame.find_element(By.ID, element_id)
                except NoSuchElementException:
                    continue
                rect = candidate.rect
                if float(rect.get("width") or 0) > 0 and float(rect.get("height") or 0) > 0:
                    return candidate, rect, candidate.screenshot_as_png

            candidate = driver_in_frame.execute_script(
                """
                function visible(el) {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && Number(style.opacity || '1') !== 0
                        && rect.width > 0
                        && rect.height > 0;
                }

                let seed = document.getElementById('u')
                    || document.getElementById('p')
                    || document.getElementById('login_button');
                if (!visible(seed)) {
                    return null;
                }

                let current = seed;
                let best = seed;
                while (current && current !== document.body) {
                    const rect = current.getBoundingClientRect();
                    if (rect.width >= 280 && rect.height >= 180) {
                        best = current;
                    }
                    current = current.parentElement;
                }
                return best;
                """
            )
            if candidate is not None:
                rect = candidate.rect
                if float(rect.get("width") or 0) > 0 and float(rect.get("height") or 0) > 0:
                    return candidate, rect, candidate.screenshot_as_png
            return None

        panel_data = self._with_login_frame(_read_panel)
        if panel_data is None:
            return None

        _element, rect, image_bytes = panel_data
        return SurfaceSnapshot(
            mode="password_panel",
            title=title,
            url=url,
            image_bytes=image_bytes,
            mime_type="image/png",
            css_x=float(frame_rect["x"]) + float(rect["x"]),
            css_y=float(frame_rect["y"]) + float(rect["y"]),
            css_width=float(rect["width"]),
            css_height=float(rect["height"]),
        )

    def get_login_state(self) -> LoginState:
        driver = self.ensure_browser()
        login_frame = self._find_login_frame()
        title = driver.title or ""
        url = driver.current_url or ""
        cookie_names = {cookie["name"] for cookie in driver.get_cookies()}

        if login_frame is not None:
            return LoginState(
                status="login_required",
                title=title,
                url=url,
                cookie_names=cookie_names,
            )

        if {"p_skey", "p_uin", "uin", "skey"} & cookie_names:
            return LoginState(
                status="logged_in",
                title=title,
                url=url,
                cookie_names=cookie_names,
            )

        body_text = driver.execute_script(
            """
            return document.body ? (document.body.innerText || "").slice(0, 2000) : "";
            """
        )
        if any(marker in body_text for marker in ("好友动态", "个人中心", "说说", "留言板")):
            return LoginState(
                status="logged_in",
                title=title,
                url=url,
                cookie_names=cookie_names,
            )

        return LoginState(
            status="unknown",
            title=title,
            url=url,
            cookie_names=cookie_names,
        )

    def _find_login_frame(self):
        driver = self.ensure_browser()
        driver.switch_to.default_content()
        try:
            return driver.find_element(By.ID, "login_frame")
        except NoSuchElementException:
            return None

    def _with_login_frame(self, callback):
        driver = self.ensure_browser()
        driver.switch_to.default_content()
        login_frame = self._find_login_frame()
        if login_frame is None:
            raise RuntimeError("当前未找到 QQ 登录框。")
        driver.switch_to.frame(login_frame)
        try:
            return callback(driver)
        finally:
            driver.switch_to.default_content()

    def _collect_login_panel_state_in_frame(self, driver: webdriver.Chrome) -> dict:
        state = driver.execute_script(
            """
            function visible(el) {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || '1') !== 0
                    && rect.width > 0
                    && rect.height > 0;
            }

            const qlogin = document.getElementById('qlogin');
            const qqInput = document.getElementById('u');
            const passwordInput = document.getElementById('p');
            const loginButton = document.getElementById('login_button');
            const verifyCode = document.getElementById('verifycode');
            const verifyArea = document.getElementById('verifyArea');
            const verifyFrame = document.getElementById('newVcodeIframe');
            const verifyFrameArea = document.getElementById('newVcodeArea');
            const switchToPassword = document.getElementById('switcher_plogin');
            const phoneVerify = document.getElementById('qlogin_vcode')
                || document.getElementById('sms_code')
                || document.getElementById('smsVerify')
                || document.getElementById('smslogin');
            const bodyText = document.body ? (document.body.innerText || '').trim().slice(0, 2000) : '';
            const errorText = ((document.getElementById('err_m') || {}).innerText || '').trim();

            let mode = 'unknown';
            if (visible(qqInput) || visible(passwordInput)) {
                mode = 'password';
            } else if (visible(qlogin)) {
                mode = 'quick';
            }

            let verificationKind = '';
            const phoneVerificationVisible = visible(phoneVerify)
                || /手机验证|短信验证|短信码|验证码已发送|手机号码/.test(bodyText);

            if (phoneVerificationVisible) {
                verificationKind = 'phone_code';
            } else if (visible(verifyCode) || visible(verifyArea)) {
                verificationKind = 'image_code';
            } else if (visible(verifyFrame) || visible(verifyFrameArea) || /安全验证|拖动|验证/.test(bodyText)) {
                verificationKind = 'robot';
            }

            return {
                mode,
                qqInputVisible: visible(qqInput),
                passwordInputVisible: visible(passwordInput),
                loginButtonVisible: visible(loginButton),
                verifyCodeVisible: visible(verifyCode) || visible(verifyArea),
                verifyIframeVisible: visible(verifyFrame) || visible(verifyFrameArea),
                verificationRequired: Boolean(verificationKind),
                verificationKind,
                phoneVerificationVisible,
                switchToPasswordAvailable: visible(switchToPassword),
                errorText,
                bodyText,
            };
            """
        )
        if not isinstance(state, dict):
            return _default_login_panel_state()
        return {**_default_login_panel_state(), **state}

    def _get_visible_rect_in_login_frame(self, element_id: str) -> dict | None:
        def _read_rect(driver: webdriver.Chrome):
            return driver.execute_script(
                """
                const el = document.getElementById(arguments[0]);
                if (!el) return null;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
                    return null;
                }
                if (rect.width <= 0 || rect.height <= 0) {
                    return null;
                }
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                """,
                element_id,
            )

        try:
            return self._with_login_frame(_read_rect)
        except RuntimeError:
            return None

    def get_login_panel_state(self) -> dict:
        try:
            return self._with_login_frame(self._collect_login_panel_state_in_frame)
        except RuntimeError:
            return _default_login_panel_state()

    def prepare_password_login(self) -> dict:
        def _prepare(driver: webdriver.Chrome):
            state = self._collect_login_panel_state_in_frame(driver)
            if state["mode"] == "password":
                return state

            switch_to_password = driver.find_element(By.ID, "switcher_plogin")
            driver.execute_script("arguments[0].click();", switch_to_password)
            deadline = time.time() + 4.0
            while time.time() < deadline:
                time.sleep(0.25)
                state = self._collect_login_panel_state_in_frame(driver)
                if state["mode"] == "password":
                    break
            return state

        return self._with_login_frame(_prepare)

    def submit_password_login(self, qq_number: str, qq_password: str) -> dict:
        normalized_qq = (qq_number or "").strip()
        normalized_password = (qq_password or "").strip()
        if not normalized_qq:
            raise ValueError("QQ 号不能为空。")
        if not normalized_password:
            raise ValueError("QQ 密码不能为空。")

        def _submit(driver: webdriver.Chrome):
            state = self._collect_login_panel_state_in_frame(driver)
            if state["mode"] != "password" and state["switchToPasswordAvailable"]:
                switch_to_password = driver.find_element(By.ID, "switcher_plogin")
                driver.execute_script("arguments[0].click();", switch_to_password)
                time.sleep(0.35)

            driver.execute_script(
                """
                const [qqNumber, qqPassword] = arguments;
                function assignValue(input, value) {
                    if (!input) return;
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(input, '');
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    setter.call(input, value);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.blur();
                }

                assignValue(document.getElementById('u'), qqNumber);
                assignValue(document.getElementById('p'), qqPassword);
                const button = document.getElementById('login_button');
                if (button) {
                    button.click();
                }
                """,
                normalized_qq,
                normalized_password,
            )
            time.sleep(1.0)
            return self._collect_login_panel_state_in_frame(driver)

        return self._with_login_frame(_submit)

    def send_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        button: str = "left",
        buttons: int = 0,
        click_count: int = 1,
        delta_y: int = 0,
    ) -> None:
        driver = self.ensure_browser()
        params = {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "buttons": buttons,
            "clickCount": click_count,
        }
        if event_type == "mouseWheel":
            params["deltaX"] = 0
            params["deltaY"] = delta_y
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", params)

    def insert_text(self, text: str) -> None:
        if not text:
            return
        driver = self.ensure_browser()
        driver.execute_cdp_cmd("Input.insertText", {"text": text})

    def send_key(self, key: str, code: str, key_code: int) -> None:
        driver = self.ensure_browser()
        base = {
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": key_code,
            "nativeVirtualKeyCode": key_code,
        }
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def send_ctrl_shortcut(self, key: str, code: str, key_code: int) -> None:
        driver = self.ensure_browser()
        control_base = {
            "key": "Control",
            "code": "ControlLeft",
            "windowsVirtualKeyCode": 17,
            "nativeVirtualKeyCode": 17,
        }
        key_base = {
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": key_code,
            "nativeVirtualKeyCode": key_code,
            "modifiers": 2,
        }
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "rawKeyDown", **control_base, "modifiers": 2})
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "rawKeyDown", **key_base})
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", **key_base})
        driver.execute_cdp_cmd("Input.dispatchKeyEvent", {"type": "keyUp", **control_base})

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

        if self.chrome_process is not None and self.chrome_process.poll() is None:
            try:
                self.chrome_process.terminate()
                self.chrome_process.wait(timeout=8)
            except Exception:
                try:
                    self.chrome_process.kill()
                    self.chrome_process.wait(timeout=5)
                except Exception:
                    pass
            finally:
                self.chrome_process = None
