from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import REMOTE_LOGIN_DATA_DIR, RUN_LOG_DIR  # noqa: E402

try:
    from remote_login.qzone_browser_bridge import LoginState, QzoneBrowserBridge, START_URL, SurfaceSnapshot
except ModuleNotFoundError:
    from qzone_browser_bridge import LoginState, QzoneBrowserBridge, START_URL, SurfaceSnapshot

STATIC_DIR = MODULE_DIR / "web_remote"
SESSION_ROOT = RUN_LOG_DIR / "web_sessions"
AUTO_LIKE_LOG_ROOT = RUN_LOG_DIR / "auto_like"
AUTO_LIKE_SCRIPT = PROJECT_ROOT / "mz.py"
AUTH_ROOT = REMOTE_LOGIN_DATA_DIR
AUTH_USERS_FILE = AUTH_ROOT / "users.json"
AUTH_COOKIE_NAME = "qzone_remote_auth"
USER_SPACE_ROOT = AUTH_ROOT / "users"
SESSION_TTL_SECONDS = 30 * 60
SESSION_CLEANUP_INTERVAL_SECONDS = 2.0
CLOSE_SESSION_GRACE_SECONDS = 8.0
CONSOLE_LINE_LIMIT = 160
AUTO_LIKE_TAIL_LINE_LIMIT = 40

SPECIAL_KEYS = {
    "Backspace": ("Backspace", "Backspace", 8),
    "Tab": ("Tab", "Tab", 9),
    "Enter": ("Enter", "Enter", 13),
    "Escape": ("Escape", "Escape", 27),
    "Delete": ("Delete", "Delete", 46),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37),
    "ArrowUp": ("ArrowUp", "ArrowUp", 38),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40),
    "Home": ("Home", "Home", 36),
    "End": ("End", "End", 35),
}

CTRL_SHORTCUTS = {
    "a": ("a", "KeyA", 65),
    "c": ("c", "KeyC", 67),
    "v": ("v", "KeyV", 86),
    "x": ("x", "KeyX", 88),
}


class AuthManager:
    CN_PATTERN = re.compile(r"^[A-Za-z\u4e00-\u9fff]{1,24}$")
    PASSWORD_PATTERN = re.compile(r"^\d{4}$")

    def __init__(self, users_file: Path) -> None:
        self.users_file = users_file
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, float | str]] = {}
        self.users: dict[str, dict[str, str | float]] = {}
        self._load_users()

    def _load_users(self) -> None:
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.users_file.exists():
            self.users = {}
            self._save_users()
            return

        try:
            data = json.loads(self.users_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        self.users = data if isinstance(data, dict) else {}

    def _save_users(self) -> None:
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
        self.users_file.write_text(
            json.dumps(self.users, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def validate_cn(self, cn: str) -> str:
        normalized = (cn or "").strip()
        if not normalized:
            raise ValueError("CN 不能为空。")
        if not self.CN_PATTERN.fullmatch(normalized):
            raise ValueError("CN 只能使用中文或英文字母，不要加入特殊字符。")
        return normalized

    def validate_password(self, password: str) -> str:
        normalized = (password or "").strip()
        if not self.PASSWORD_PATTERN.fullmatch(normalized):
            raise ValueError("密码必须是 4 位数字。")
        return normalized

    def _hash_password(self, password: str, salt_hex: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            120_000,
        )
        return digest.hex()

    def _create_auth_session(self, cn: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"cn": cn, "createdAt": time.time(), "lastSeenAt": time.time()}
        return token

    def register_and_login(self, cn: str, password: str) -> tuple[str, str]:
        normalized_cn = self.validate_cn(cn)
        normalized_password = self.validate_password(password)

        with self.lock:
            if normalized_cn in self.users:
                raise ValueError("该 CN 已注册，请直接登录。")

            salt_hex = secrets.token_hex(16)
            self.users[normalized_cn] = {
                "salt": salt_hex,
                "passwordHash": self._hash_password(normalized_password, salt_hex),
                "createdAt": time.time(),
            }
            self._save_users()
            token = self._create_auth_session(normalized_cn)
        return normalized_cn, token

    def login(self, cn: str, password: str) -> tuple[str, str]:
        normalized_cn = self.validate_cn(cn)
        normalized_password = self.validate_password(password)

        with self.lock:
            user = self.users.get(normalized_cn)
            if user is None:
                raise ValueError("CN 或密码错误。")

            expected_hash = str(user.get("passwordHash") or "")
            salt_hex = str(user.get("salt") or "")
            if not expected_hash or not salt_hex:
                raise ValueError("该账号数据损坏，请重新注册。")

            actual_hash = self._hash_password(normalized_password, salt_hex)
            if not hmac.compare_digest(expected_hash, actual_hash):
                raise ValueError("CN 或密码错误。")

            token = self._create_auth_session(normalized_cn)
        return normalized_cn, token

    def get_user_cn(self, token: str) -> str | None:
        if not token:
            return None
        with self.lock:
            session = self.sessions.get(token)
            if session is None:
                return None
            session["lastSeenAt"] = time.time()
            return str(session.get("cn") or "")

    def logout(self, token: str) -> None:
        if not token:
            return
        with self.lock:
            self.sessions.pop(token, None)


def mask_qq_number(qq_number: str) -> str:
    normalized = (qq_number or "").strip()
    if len(normalized) <= 4:
        return normalized
    return f"{normalized[:3]}{'*' * max(1, len(normalized) - 5)}{normalized[-2:]}"


class UserStorageManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.lock = threading.RLock()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, cn: str) -> Path:
        return self.root_dir / cn

    def _records_file(self, cn: str) -> Path:
        return self._user_dir(cn) / "records.json"

    def _default_records(self, cn: str) -> dict:
        now = time.time()
        return {
            "cn": cn,
            "createdAt": now,
            "visitCount": 0,
            "lastWebsiteLoginAt": 0.0,
            "lastWebsiteLogoutAt": 0.0,
            "lastQqNumber": "",
            "lastQqLoginAt": 0.0,
            "lastLoginSessionId": "",
            "recentSessions": [],
        }

    def _load_records_locked(self, cn: str) -> dict:
        user_dir = self._user_dir(cn)
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "browser_sessions").mkdir(parents=True, exist_ok=True)
        records_file = self._records_file(cn)
        if not records_file.exists():
            records = self._default_records(cn)
            self._save_records_locked(cn, records)
            return records

        try:
            data = json.loads(records_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        records = self._default_records(cn)
        if isinstance(data, dict):
            records.update(data)
        self._save_records_locked(cn, records)
        return records

    def _save_records_locked(self, cn: str, records: dict) -> None:
        records_file = self._records_file(cn)
        records_file.parent.mkdir(parents=True, exist_ok=True)
        records_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def _public_records_view_locked(self, cn: str, records: dict) -> dict:
        sessions = records.get("recentSessions") if isinstance(records.get("recentSessions"), list) else []
        recent_sessions = sessions[-8:]
        return {
            "storageRoot": str(self._user_dir(cn)),
            "browserSessionRoot": str(self._user_dir(cn) / "browser_sessions"),
            "createdAt": float(records.get("createdAt") or 0.0),
            "visitCount": int(records.get("visitCount") or 0),
            "lastWebsiteLoginAt": float(records.get("lastWebsiteLoginAt") or 0.0),
            "lastWebsiteLogoutAt": float(records.get("lastWebsiteLogoutAt") or 0.0),
            "lastQqNumber": mask_qq_number(str(records.get("lastQqNumber") or "")),
            "lastQqLoginAt": float(records.get("lastQqLoginAt") or 0.0),
            "lastLoginSessionId": str(records.get("lastLoginSessionId") or ""),
            "recentSessions": recent_sessions,
        }

    def ensure_user_space(self, cn: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            return self._public_records_view_locked(cn, records)

    def record_visit(self, cn: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            records["visitCount"] = int(records.get("visitCount") or 0) + 1
            records["lastWebsiteLoginAt"] = time.time()
            self._save_records_locked(cn, records)
            return self._public_records_view_locked(cn, records)

    def record_logout(self, cn: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            records["lastWebsiteLogoutAt"] = time.time()
            self._save_records_locked(cn, records)
            return self._public_records_view_locked(cn, records)

    def allocate_browser_session(self, cn: str, session_id: str) -> tuple[str, Path]:
        with self.lock:
            records = self._load_records_locked(cn)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            session_name = f"session_{timestamp}_{session_id[:8]}"
            session_dir = self._user_dir(cn) / "browser_sessions" / session_name
            session_dir.mkdir(parents=True, exist_ok=True)

            recent_sessions = records.get("recentSessions")
            if not isinstance(recent_sessions, list):
                recent_sessions = []
            recent_sessions.append(
                {
                    "sessionId": session_id,
                    "sessionName": session_name,
                    "status": "starting",
                    "createdAt": time.time(),
                }
            )
            records["recentSessions"] = recent_sessions[-8:]
            self._save_records_locked(cn, records)
            return session_name, session_dir

    def record_session_status(self, cn: str, session_id: str, status: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            recent_sessions = records.get("recentSessions")
            if not isinstance(recent_sessions, list):
                recent_sessions = []
            for item in reversed(recent_sessions):
                if str(item.get("sessionId") or "") == session_id:
                    item["status"] = status
                    item["updatedAt"] = time.time()
                    break
            records["recentSessions"] = recent_sessions[-8:]
            self._save_records_locked(cn, records)
            return self._public_records_view_locked(cn, records)

    def record_qq_login_attempt(self, cn: str, qq_number: str, session_id: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            records["lastQqNumber"] = (qq_number or "").strip()
            records["lastQqLoginAt"] = time.time()
            records["lastLoginSessionId"] = session_id
            self._save_records_locked(cn, records)
            return self._public_records_view_locked(cn, records)

    def get_public_records(self, cn: str) -> dict:
        with self.lock:
            records = self._load_records_locked(cn)
            return self._public_records_view_locked(cn, records)


def build_idle_payload(*, session_id: str = "", status: str = "idle", last_error: str = "") -> dict:
    lines = ["等待创建会话。"]
    if status == "expired":
        lines = [last_error or "当前会话已过期。"]
    return {
        "sessionId": session_id,
        "sessionEnabled": False,
        "sessionName": "",
        "status": status,
        "title": "",
        "url": "",
        "frameToken": 0,
        "imageAvailable": False,
        "lastError": last_error,
        "autoLike": {
            "status": "idle",
            "running": False,
            "startedAt": 0.0,
            "lastExitCode": None,
            "lastError": "",
            "logPath": "",
            "config": {},
        },
        "console": {
            "threadStatus": {
                "refreshThreadAlive": False,
                "autoLikeRunning": False,
                "sessionEnabled": False,
                "sessionAgeSeconds": 0,
                "closePending": False,
            },
            "lines": lines,
        },
    }


class RemoteSessionController:
    def __init__(self, session_id: str, owner_cn: str, user_storage: UserStorageManager) -> None:
        self.session_id = session_id
        self.owner_cn = owner_cn
        self.user_storage = user_storage
        self.bridge: QzoneBrowserBridge | None = None
        self.lock = threading.RLock()
        self.latest_surface: SurfaceSnapshot | None = None
        self.latest_state = LoginState(status="idle", title="", url="", cookie_names=set())
        self.login_panel_state = {
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
        self.frame_token = 0
        self.last_error = ""
        self.session_enabled = False
        self.session_name = ""
        self.created_at = time.time()
        self.last_access_at = self.created_at
        self.console_lines: deque[str] = deque(maxlen=CONSOLE_LINE_LIMIT)
        self.last_reported_status = "idle"
        self.close_requested = False
        self.close_deadline_at = 0.0
        self.close_reason = ""
        self.auto_like_process: subprocess.Popen | None = None
        self.auto_like_log_handle = None
        self.auto_like_status = "idle"
        self.auto_like_started_at = 0.0
        self.auto_like_last_exit_code: int | None = None
        self.auto_like_last_error = ""
        self.auto_like_log_path = ""
        self.auto_like_config: dict = {}
        self.auto_like_stop_requested = False
        self.stop_event = threading.Event()
        self.refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread_started = False
        self._log(f"会话控制器已创建，归属账号 {owner_cn}，等待唤起浏览器。")

    def start(self) -> None:
        with self.lock:
            if self._thread_started:
                return
            self._thread_started = True
            self.refresh_thread.start()
            self._log("刷新线程已启动。")

    def stop(self) -> None:
        self.stop_event.set()
        self._log("会话即将停止，准备释放浏览器和后台任务。")
        self._terminate_auto_like()
        with self.lock:
            bridge = self.bridge
            self.bridge = None
            self.latest_surface = None
            self.session_enabled = False
            self.latest_state = LoginState(status="idle", title="", url="", cookie_names=set())
            self.login_panel_state = {
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
        if bridge is not None:
            bridge.close()

    def touch(self) -> None:
        self.last_access_at = time.time()
        if self.close_requested:
            self.close_requested = False
            self.close_deadline_at = 0.0
            self.close_reason = ""
            self._log("检测到会话恢复活动，已取消挂起的退出清理。")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.console_lines.append(f"[{timestamp}] {message}")

    def _empty_login_panel_state_locked(self) -> dict:
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

    def _apply_login_state_locked(self, state: LoginState) -> None:
        if state.status != self.last_reported_status:
            self._log(f"会话状态变更为 {state.status}。")
            self.last_reported_status = state.status
            self.user_storage.record_session_status(self.owner_cn, self.session_id, state.status)
        self.latest_state = state

    def _collect_bridge_snapshot(
        self,
        bridge: QzoneBrowserBridge,
        *,
        prepare_password: bool,
    ) -> tuple[LoginState, dict, SurfaceSnapshot | None]:
        state = bridge.get_login_state()
        if state.status != "login_required":
            return state, self._empty_login_panel_state_locked(), None

        panel_state = bridge.prepare_password_login() if prepare_password else bridge.get_login_panel_state()
        surface = bridge.capture_surface()
        return state, panel_state, surface

    def _apply_bridge_snapshot_locked(
        self,
        *,
        state: LoginState,
        panel_state: dict,
        surface: SurfaceSnapshot | None,
    ) -> None:
        self._apply_login_state_locked(state)
        self.last_error = ""
        self.login_panel_state = dict(panel_state)
        self.latest_surface = surface
        if surface is not None:
            self.frame_token += 1

    def _refresh_surface_after_input(self, bridge: QzoneBrowserBridge) -> None:
        time.sleep(0.08)
        state, panel_state, surface = self._collect_bridge_snapshot(bridge, prepare_password=False)
        with self.lock:
            if bridge is not self.bridge:
                return
            self._apply_bridge_snapshot_locked(
                state=state,
                panel_state=panel_state,
                surface=surface,
            )

    def _build_state_payload_locked(self) -> dict:
        surface = self.latest_surface
        state = self.latest_state
        payload = {
            "sessionId": self.session_id,
            "sessionEnabled": self.session_enabled,
            "sessionName": self.session_name,
            "status": state.status,
            "title": state.title,
            "url": state.url,
            "frameToken": self.frame_token,
            "imageAvailable": surface is not None,
            "lastError": self.last_error,
            "autoLike": self._build_auto_like_payload_locked(),
            "console": self._build_console_payload_locked(),
            "qqLogin": dict(self.login_panel_state),
        }
        if surface is not None:
            payload["surface"] = {
                "mode": surface.mode,
                "cssX": surface.css_x,
                "cssY": surface.css_y,
                "cssWidth": surface.css_width,
                "cssHeight": surface.css_height,
            }
        return payload

    def _tail_log_file(self, path_text: str, max_lines: int) -> list[str]:
        if not path_text:
            return []

        path = Path(path_text)
        if not path.exists():
            return []

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []

        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return [f"[auto-like] {line}" for line in lines if line.strip()]

    def is_expired(self, now: float, ttl_seconds: float) -> bool:
        with self.lock:
            self._refresh_auto_like_status_locked()
            if self.auto_like_process is not None and self.auto_like_process.poll() is None:
                return False
        return (now - self.last_access_at) > ttl_seconds

    def should_close_now(self, now: float) -> bool:
        return self.close_requested and self.close_deadline_at > 0 and now >= self.close_deadline_at

    def request_close(self, grace_seconds: float, reason: str = "") -> dict:
        self.touch()
        with self.lock:
            self.close_requested = True
            self.close_deadline_at = time.time() + max(0.0, grace_seconds)
            self.close_reason = reason
            close_at = time.strftime("%H:%M:%S", time.localtime(self.close_deadline_at))
            if grace_seconds <= 0:
                self._log("收到立即退出请求，会话将立刻清理。")
            else:
                suffix = f"，原因: {reason}" if reason else ""
                self._log(f"收到退出请求，会话将在 {close_at} 自动清理{suffix}。")
            return {
                "ok": True,
                "sessionId": self.session_id,
                "closeRequested": True,
                "closeDeadlineAt": self.close_deadline_at,
            }

    def _refresh_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                self._refresh_auto_like_status_locked()

            if not self.session_enabled:
                self.latest_surface = None
                self.latest_state = LoginState(status="idle", title="", url="", cookie_names=set())
                self.stop_event.wait(0.5)
                continue

            state = self.latest_state
            try:
                with self.lock:
                    bridge = self.bridge
                    if bridge is None:
                        self.latest_state = LoginState(status="starting", title="", url="", cookie_names=set())
                        self.stop_event.wait(0.5)
                        continue
                state, panel_state, surface = self._collect_bridge_snapshot(
                    bridge,
                    prepare_password=True,
                )
                with self.lock:
                    if bridge is self.bridge:
                        self._apply_bridge_snapshot_locked(
                            state=state,
                            panel_state=panel_state,
                            surface=surface,
                        )
            except Exception as exc:
                state = LoginState(status="error", title="", url="", cookie_names=set())
                with self.lock:
                    self.latest_state = state
                    self.last_error = str(exc)
                    self._log(f"刷新线程捕获异常: {exc}")
                    self.user_storage.record_session_status(self.owner_cn, self.session_id, "error")

            sleep_seconds = 0.45 if state.status == "login_required" else 1.2
            self.stop_event.wait(sleep_seconds)

    def start_session(self) -> dict:
        self.start()
        self.touch()
        with self.lock:
            if self.session_enabled and self.bridge is not None:
                self._log("复用当前已启动的浏览器会话。")
                return {
                    "ok": True,
                    "sessionId": self.session_id,
                    "status": self.latest_state.status,
                    "sessionName": self.session_name,
                }

            self.session_enabled = True
            self.bridge = self._build_bridge()
            self.latest_state = LoginState(status="starting", title="", url="", cookie_names=set())
            self.latest_surface = None
            self.frame_token = 0
            self.last_error = ""
            self.last_reported_status = "starting"
            self._log(f"开始创建独立浏览器会话 {self.session_name}。")
        return {"ok": True, "sessionId": self.session_id, "status": "starting", "sessionName": self.session_name}

    def get_state_payload(self) -> dict:
        self.touch()
        with self.lock:
            self._refresh_auto_like_status_locked()
            return self._build_state_payload_locked()

    def _build_bridge(self) -> QzoneBrowserBridge:
        self.session_name, session_dir = self.user_storage.allocate_browser_session(self.owner_cn, self.session_id)
        debugger_address = f"127.0.0.1:{find_free_port()}"
        self._log(f"已分配浏览器目录 {session_dir}。")
        self._log(f"已分配调试地址 {debugger_address}。")
        return QzoneBrowserBridge(
            debugger_address=debugger_address,
            user_data_dir=str(session_dir),
            start_url=START_URL,
        )

    def get_frame_payload(self) -> tuple[bytes | None, str]:
        self.touch()
        with self.lock:
            if self.latest_surface is None:
                return None, "image/jpeg"
            return self.latest_surface.image_bytes, self.latest_surface.mime_type

    def _build_auto_like_payload_locked(self) -> dict:
        return {
            "status": self.auto_like_status,
            "running": self.auto_like_process is not None and self.auto_like_process.poll() is None,
            "startedAt": self.auto_like_started_at,
            "lastExitCode": self.auto_like_last_exit_code,
            "lastError": self.auto_like_last_error,
            "logPath": self.auto_like_log_path,
            "config": dict(self.auto_like_config),
        }

    def _build_console_payload_locked(self) -> dict:
        tail_lines = self._tail_log_file(self.auto_like_log_path, AUTO_LIKE_TAIL_LINE_LIMIT)
        merged_lines = list(self.console_lines)
        for line in tail_lines:
            if line not in merged_lines:
                merged_lines.append(line)
        if len(merged_lines) > CONSOLE_LINE_LIMIT:
            merged_lines = merged_lines[-CONSOLE_LINE_LIMIT:]

        return {
            "threadStatus": {
                "refreshThreadAlive": self.refresh_thread.is_alive(),
                "autoLikeRunning": self.auto_like_process is not None and self.auto_like_process.poll() is None,
                "sessionEnabled": self.session_enabled,
                "sessionAgeSeconds": int(max(0, time.time() - self.created_at)),
                "closePending": self.close_requested,
            },
            "lines": merged_lines,
        }

    def _close_auto_like_log_locked(self) -> None:
        if self.auto_like_log_handle is not None:
            try:
                self.auto_like_log_handle.close()
            except Exception:
                pass
            finally:
                self.auto_like_log_handle = None

    def _refresh_auto_like_status_locked(self) -> None:
        process = self.auto_like_process
        if process is None:
            return

        exit_code = process.poll()
        if exit_code is None:
            return

        self.auto_like_last_exit_code = exit_code
        if self.auto_like_stop_requested:
            self.auto_like_status = "stopped"
            self._log("自动点赞任务已按请求停止。")
        elif exit_code == 0:
            self.auto_like_status = "completed"
            self._log("自动点赞任务已正常结束。")
        else:
            self.auto_like_status = "error"
            if not self.auto_like_last_error:
                self.auto_like_last_error = f"自动点赞进程异常退出，退出码 {exit_code}"
            self._log(self.auto_like_last_error)

        self.auto_like_process = None
        self.auto_like_stop_requested = False
        self._close_auto_like_log_locked()

    def _terminate_auto_like(self) -> None:
        with self.lock:
            process = self.auto_like_process
            if process is None:
                self.auto_like_status = "idle"
                self.auto_like_stop_requested = False
                self._close_auto_like_log_locked()
                return

            self.auto_like_stop_requested = True
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception:
                    pass
            self.auto_like_last_exit_code = None
            self.auto_like_process = None
            self.auto_like_status = "stopped"
            self._close_auto_like_log_locked()
            self._log("自动点赞任务已手动终止。")

    def _normalize_auto_like_config(self, payload: dict) -> dict:
        config: dict[str, object] = {}

        max_big_rounds = payload.get("maxBigRounds")
        if max_big_rounds not in (None, "", 0):
            value = int(max_big_rounds)
            if value <= 0:
                raise ValueError("maxBigRounds 必须大于 0")
            config["maxBigRounds"] = value

        max_new_likes = payload.get("maxNewLikesPerSmallRound")
        if max_new_likes not in (None, "", 0):
            value = int(max_new_likes)
            if value <= 0:
                raise ValueError("maxNewLikesPerSmallRound 必须大于 0")
            config["maxNewLikesPerSmallRound"] = value

        wait_between = payload.get("waitBetweenBigRounds")
        if wait_between not in (None, ""):
            value = float(wait_between)
            if value < 0:
                raise ValueError("waitBetweenBigRounds 不能小于 0")
            config["waitBetweenBigRounds"] = value

        config["skipExternalTasks"] = bool(payload.get("skipExternalTasks", True))
        return config

    def _build_auto_like_command_locked(self, config: dict) -> list[str]:
        if self.bridge is None:
            raise RuntimeError("浏览器会话尚未启动")

        command = [
            sys.executable,
            str(AUTO_LIKE_SCRIPT),
            "--debugger-address",
            self.bridge.debugger_address,
            "--driver-path",
            self.bridge.chromedriver_path,
            "--startup-wait-seconds",
            "0.5",
        ]

        if "maxBigRounds" in config:
            command.extend(["--max-big-rounds", str(config["maxBigRounds"])])
        if "maxNewLikesPerSmallRound" in config:
            command.extend(["--max-new-likes-per-small-round", str(config["maxNewLikesPerSmallRound"])])
        if "waitBetweenBigRounds" in config:
            command.extend(["--wait-between-big-rounds", str(config["waitBetweenBigRounds"])])
        if config.get("skipExternalTasks"):
            command.append("--skip-external-tasks")

        return command

    def start_auto_like(self, payload: dict) -> dict:
        self.touch()
        with self.lock:
            if not self.session_enabled or self.bridge is None:
                raise RuntimeError("浏览器会话尚未启动")
            if self.latest_state.status != "logged_in":
                raise RuntimeError("请先完成登录，再启动自动点赞")

            self._refresh_auto_like_status_locked()
            if self.auto_like_process is not None and self.auto_like_process.poll() is None:
                return {"ok": True, "sessionId": self.session_id, "autoLike": self._build_auto_like_payload_locked()}

            config = self._normalize_auto_like_config(payload)
            command = self._build_auto_like_command_locked(config)

            AUTO_LIKE_LOG_ROOT.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            log_path = AUTO_LIKE_LOG_ROOT / f"{self.session_name or self.session_id}_{timestamp}.log"
            log_handle = open(log_path, "w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )

            self.auto_like_process = process
            self.auto_like_log_handle = log_handle
            self.auto_like_status = "running"
            self.auto_like_started_at = time.time()
            self.auto_like_last_exit_code = None
            self.auto_like_last_error = ""
            self.auto_like_log_path = str(log_path)
            self.auto_like_config = config
            self.auto_like_stop_requested = False
            self._log(f"自动点赞任务已启动，进程 PID={process.pid}。")
            self._log(f"自动点赞日志输出到 {log_path}。")

            return {"ok": True, "sessionId": self.session_id, "autoLike": self._build_auto_like_payload_locked()}

    def stop_auto_like(self) -> dict:
        self.touch()
        self._terminate_auto_like()
        with self.lock:
            return {"ok": True, "sessionId": self.session_id, "autoLike": self._build_auto_like_payload_locked()}

    def _translate_display_point(
        self,
        display_x: float,
        display_y: float,
        rendered_width: float,
        rendered_height: float,
    ) -> tuple[float, float]:
        if rendered_width <= 0 or rendered_height <= 0:
            raise ValueError("无效的映射尺寸")

        surface = self.latest_surface
        if surface is None:
            raise RuntimeError("当前没有可操作的登录映射区域")

        page_x = surface.css_x + (display_x / rendered_width) * surface.css_width
        page_y = surface.css_y + (display_y / rendered_height) * surface.css_height
        return page_x, page_y

    def handle_pointer_event(self, payload: dict) -> dict:
        self.touch()
        with self.lock:
            if self.bridge is None:
                raise RuntimeError("浏览器会话尚未启动")
            bridge = self.bridge
            action = str(payload.get("action") or "").strip()
            display_x = float(payload.get("displayX"))
            display_y = float(payload.get("displayY"))
            rendered_width = float(payload.get("renderedWidth"))
            rendered_height = float(payload.get("renderedHeight"))
            page_x, page_y = self._translate_display_point(display_x, display_y, rendered_width, rendered_height)

        if action == "press":
            bridge.send_mouse_event("mouseMoved", page_x, page_y, buttons=0)
            bridge.send_mouse_event("mousePressed", page_x, page_y, buttons=1)
            self._refresh_surface_after_input(bridge)
        elif action == "move":
            bridge.send_mouse_event("mouseMoved", page_x, page_y, buttons=1)
        elif action == "release":
            bridge.send_mouse_event("mouseReleased", page_x, page_y, buttons=0)
            self._refresh_surface_after_input(bridge)
        elif action == "wheel":
            delta_y = int(payload.get("deltaY") or 0)
            bridge.send_mouse_event("mouseWheel", page_x, page_y, delta_y=delta_y)
            self._refresh_surface_after_input(bridge)
        else:
            raise ValueError(f"未识别的操作: {action}")

        return {
            "ok": True,
            "sessionId": self.session_id,
            "state": self.get_state_payload(),
        }

    def handle_text_input(self, payload: dict) -> dict:
        self.touch()
        text = str(payload.get("text") or "")
        with self.lock:
            if self.bridge is None:
                raise RuntimeError("浏览器会话尚未启动")
            bridge = self.bridge
        bridge.insert_text(text)
        self._refresh_surface_after_input(bridge)
        return {
            "ok": True,
            "sessionId": self.session_id,
            "length": len(text),
            "state": self.get_state_payload(),
        }

    def handle_key_input(self, payload: dict) -> dict:
        self.touch()
        key = str(payload.get("key") or "")
        ctrl = bool(payload.get("ctrl"))

        with self.lock:
            if self.bridge is None:
                raise RuntimeError("浏览器会话尚未启动")
            bridge = self.bridge

        if ctrl:
            shortcut = CTRL_SHORTCUTS.get(key.lower())
            if shortcut is None:
                raise ValueError(f"未识别的 Ctrl 快捷键: {key}")
            bridge.send_ctrl_shortcut(*shortcut)
            self._refresh_surface_after_input(bridge)
            return {
                "ok": True,
                "sessionId": self.session_id,
                "type": "ctrl_shortcut",
                "state": self.get_state_payload(),
            }

        special = SPECIAL_KEYS.get(key)
        if special is None:
            raise ValueError(f"未识别的按键: {key}")
        bridge.send_key(*special)
        self._refresh_surface_after_input(bridge)
        return {
            "ok": True,
            "sessionId": self.session_id,
            "type": "special_key",
            "state": self.get_state_payload(),
        }

    def submit_qq_credentials(self, payload: dict) -> dict:
        self.touch()
        qq_number = str(payload.get("qqNumber") or "").strip()
        qq_password = str(payload.get("qqPassword") or "").strip()
        if not re.fullmatch(r"\d{5,20}", qq_number):
            raise ValueError("QQ 号必须是 5 到 20 位数字。")
        if not qq_password:
            raise ValueError("QQ 密码不能为空。")

        with self.lock:
            if self.bridge is None:
                raise RuntimeError("浏览器会话尚未启动")
            bridge = self.bridge
        panel_state = bridge.submit_password_login(qq_number, qq_password)
        with self.lock:
            if bridge is not self.bridge:
                raise RuntimeError("浏览器会话已刷新，请重新提交一次 QQ 登录。")
            self.login_panel_state = panel_state
            self.user_storage.record_qq_login_attempt(self.owner_cn, qq_number, self.session_id)
            if panel_state.get("verificationRequired"):
                verification_kind = panel_state.get("verificationKind") or "unknown"
                self._log(f"QQ 密码已提交，当前需要继续完成验证: {verification_kind}。")
            else:
                self._log("QQ 密码已提交，等待浏览器完成登录反馈。")
        self._refresh_surface_after_input(bridge)
        with self.lock:
            return {
                "ok": True,
                "sessionId": self.session_id,
                "qqLogin": dict(self.login_panel_state),
                "records": self.user_storage.get_public_records(self.owner_cn),
                "state": self._build_state_payload_locked(),
            }


class SessionManager:
    def __init__(self, user_storage: UserStorageManager, ttl_seconds: float = SESSION_TTL_SECONDS) -> None:
        self.user_storage = user_storage
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.sessions: dict[str, RemoteSessionController] = {}
        self.stop_event = threading.Event()
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)

    def start(self) -> None:
        self.cleanup_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for controller in sessions:
            controller.stop()

    def create_and_start_session(self, owner_cn: str) -> dict:
        session_id = uuid.uuid4().hex[:12]
        controller = RemoteSessionController(session_id, owner_cn, self.user_storage)
        controller.start()
        with self.lock:
            self.sessions[session_id] = controller
        controller._log(f"会话已注册到管理器，session_id={session_id}。")
        return controller.start_session()

    def get_state_payload(self, session_id: str, owner_cn: str) -> dict:
        if not session_id:
            return build_idle_payload()

        controller = self._get_session(session_id)
        if controller is None:
            return build_idle_payload(
                session_id=session_id,
                status="expired",
                last_error="当前会话不存在或已过期，请重新点击按钮唤起新的浏览器。",
            )
        if controller.owner_cn != owner_cn:
            return build_idle_payload(
                session_id=session_id,
                status="expired",
                last_error="当前会话不存在、已过期，或不属于当前登录账号。",
            )
        return controller.get_state_payload()

    def get_frame_payload(self, session_id: str, owner_cn: str) -> tuple[bytes | None, str]:
        controller = self._require_session(session_id, owner_cn)
        return controller.get_frame_payload()

    def handle_pointer_event(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.handle_pointer_event(payload)

    def handle_text_input(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.handle_text_input(payload)

    def handle_key_input(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.handle_key_input(payload)

    def start_auto_like(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.start_auto_like(payload)

    def stop_auto_like(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.stop_auto_like()

    def submit_qq_credentials(self, payload: dict, owner_cn: str) -> dict:
        controller = self._require_session(str(payload.get("sessionId") or "").strip(), owner_cn)
        return controller.submit_qq_credentials(payload)

    def close_session(self, payload: dict, owner_cn: str) -> dict:
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            raise KeyError("缺少 sessionId")

        immediate = bool(payload.get("immediate", False))
        reason = str(payload.get("reason") or "").strip()
        controller = self._require_session(session_id, owner_cn)

        if immediate:
            with self.lock:
                self.sessions.pop(session_id, None)
            controller._log("会话已收到立即关闭请求。")
            controller.stop()
            return {"ok": True, "sessionId": session_id, "closed": True}

        return controller.request_close(CLOSE_SESSION_GRACE_SECONDS, reason=reason)

    def _get_session(self, session_id: str) -> RemoteSessionController | None:
        with self.lock:
            return self.sessions.get(session_id)

    def _require_session(self, session_id: str, owner_cn: str) -> RemoteSessionController:
        if not session_id:
            raise KeyError("缺少 sessionId")
        controller = self._get_session(session_id)
        if controller is None:
            raise KeyError("会话不存在或已过期")
        if controller.owner_cn != owner_cn:
            raise KeyError("会话不存在、已过期，或不属于当前登录账号。")
        return controller

    def close_user_sessions(self, owner_cn: str) -> None:
        with self.lock:
            owned = [
                (session_id, controller)
                for session_id, controller in self.sessions.items()
                if controller.owner_cn == owner_cn
            ]
            for session_id, _controller in owned:
                self.sessions.pop(session_id, None)

        for _session_id, controller in owned:
            controller._log("网站账号已退出，关联会话将被立即清理。")
            controller.stop()

    def _cleanup_loop(self) -> None:
        while not self.stop_event.wait(SESSION_CLEANUP_INTERVAL_SECONDS):
            now = time.time()
            expired: list[RemoteSessionController] = []
            with self.lock:
                for session_id, controller in list(self.sessions.items()):
                    if controller.should_close_now(now):
                        controller._log("挂起关闭时间已到，开始清理浏览器和后台任务。")
                        expired.append(controller)
                        del self.sessions[session_id]
                        continue
                    if controller.is_expired(now, self.ttl_seconds):
                        controller._log("会话长时间无访问，已被清理器回收。")
                        expired.append(controller)
                        del self.sessions[session_id]
            for controller in expired:
                controller.stop()


class QzoneWebRequestHandler(BaseHTTPRequestHandler):
    session_manager: SessionManager
    auth_manager: AuthManager
    user_storage_manager: UserStorageManager

    def _build_auth_cookie_value(self, token: str, *, clear: bool = False) -> str:
        parts = [f"{AUTH_COOKIE_NAME}={token if not clear else ''}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if clear:
            parts.append("Max-Age=0")
        return "; ".join(parts)

    def _get_auth_token(self) -> str:
        raw_cookie = self.headers.get("Cookie") or ""
        if not raw_cookie:
            return ""
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(AUTH_COOKIE_NAME)
        return morsel.value if morsel is not None else ""

    def _get_current_user_cn(self, *, required: bool) -> str:
        token = self._get_auth_token()
        user_cn = self.auth_manager.get_user_cn(token) or ""
        if required and not user_cn:
            raise PermissionError("请先登录网站账号。")
        return user_cn

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/app.js":
                self._serve_static("app.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/styles.css":
                self._serve_static("styles.css", "text/css; charset=utf-8")
                return
            if parsed.path == "/api/auth/me":
                user_cn = self._get_current_user_cn(required=False)
                records = self.user_storage_manager.get_public_records(user_cn) if user_cn else {}
                self._send_json({"loggedIn": bool(user_cn), "cn": user_cn, "records": records})
                return
            if parsed.path == "/api/user/records":
                user_cn = self._get_current_user_cn(required=True)
                self._send_json({"ok": True, "records": self.user_storage_manager.get_public_records(user_cn)})
                return
            if parsed.path == "/api/state":
                user_cn = self._get_current_user_cn(required=True)
                session_id = self._get_session_id_from_query(parsed)
                self._send_json(self.session_manager.get_state_payload(session_id, user_cn))
                return
            if parsed.path == "/api/frame.png":
                user_cn = self._get_current_user_cn(required=True)
                session_id = self._get_session_id_from_query(parsed)
                try:
                    image_bytes, mime_type = self.session_manager.get_frame_payload(session_id, user_cn)
                except KeyError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                if image_bytes is None:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(image_bytes)))
                self.end_headers()
                self.wfile.write(image_bytes)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        try:
            payload = self._read_json()

            if parsed.path == "/api/auth/register":
                cn, token = self.auth_manager.register_and_login(
                    str(payload.get("cn") or ""),
                    str(payload.get("password") or ""),
                )
                records = self.user_storage_manager.record_visit(cn)
                self._send_json(
                    {"ok": True, "loggedIn": True, "cn": cn, "records": records},
                    headers=[("Set-Cookie", self._build_auth_cookie_value(token))],
                )
                return
            if parsed.path == "/api/auth/login":
                cn, token = self.auth_manager.login(
                    str(payload.get("cn") or ""),
                    str(payload.get("password") or ""),
                )
                records = self.user_storage_manager.record_visit(cn)
                self._send_json(
                    {"ok": True, "loggedIn": True, "cn": cn, "records": records},
                    headers=[("Set-Cookie", self._build_auth_cookie_value(token))],
                )
                return
            if parsed.path == "/api/auth/logout":
                token = self._get_auth_token()
                user_cn = self.auth_manager.get_user_cn(token) or ""
                if user_cn:
                    self.session_manager.close_user_sessions(user_cn)
                    self.user_storage_manager.record_logout(user_cn)
                self.auth_manager.logout(token)
                self._send_json(
                    {"ok": True, "loggedIn": False},
                    headers=[("Set-Cookie", self._build_auth_cookie_value("", clear=True))],
                )
                return

            user_cn = self._get_current_user_cn(required=True)
            if parsed.path == "/api/session/start":
                self._send_json(self.session_manager.create_and_start_session(user_cn))
                return
            if parsed.path == "/api/input/pointer":
                self._send_json(self.session_manager.handle_pointer_event(payload, user_cn))
                return
            if parsed.path == "/api/input/text":
                self._send_json(self.session_manager.handle_text_input(payload, user_cn))
                return
            if parsed.path == "/api/input/key":
                self._send_json(self.session_manager.handle_key_input(payload, user_cn))
                return
            if parsed.path == "/api/qq-login/submit":
                self._send_json(self.session_manager.submit_qq_credentials(payload, user_cn))
                return
            if parsed.path == "/api/auto-like/start":
                self._send_json(self.session_manager.start_auto_like(payload, user_cn))
                return
            if parsed.path == "/api/auto-like/stop":
                self._send_json(self.session_manager.stop_auto_like(payload, user_cn))
                return
            if parsed.path == "/api/session/close":
                self._send_json(self.session_manager.close_session(payload, user_cn))
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)
        except KeyError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _get_session_id_from_query(self, parsed) -> str:
        params = parse_qs(parsed.query or "")
        return str(params.get("session_id", [""])[0] or "").strip()

    def _serve_static(self, filename: str, content_type: str) -> None:
        file_path = STATIC_DIR / filename
        content = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"
        return json.loads(raw or "{}")

    def _send_json(
        self,
        payload: dict,
        status: HTTPStatus = HTTPStatus.OK,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if headers:
            for key, value in headers:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def discover_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_smoke_test() -> None:
    user_storage = UserStorageManager(USER_SPACE_ROOT)
    manager = SessionManager(user_storage)
    manager.start()
    result = manager.create_and_start_session("SmokeUser")
    time.sleep(2.0)
    payload = manager.get_state_payload(result["sessionId"], "SmokeUser")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    manager.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="QQ空间网页登录原型")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--smoke-test", action="store_true", help="只做后端桥接检查，不启动服务")
    args = parser.parse_args()

    if args.smoke_test:
        run_smoke_test()
        return

    auth_manager = AuthManager(AUTH_USERS_FILE)
    user_storage_manager = UserStorageManager(USER_SPACE_ROOT)
    session_manager = SessionManager(user_storage_manager)
    session_manager.start()

    QzoneWebRequestHandler.auth_manager = auth_manager
    QzoneWebRequestHandler.session_manager = session_manager
    QzoneWebRequestHandler.user_storage_manager = user_storage_manager
    server = ThreadingHTTPServer((args.host, args.port), QzoneWebRequestHandler)

    local_ip = discover_local_ip()
    print(f"Web 登录原型已启动: http://127.0.0.1:{args.port}")
    print(f"局域网访问地址: http://{local_ip}:{args.port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session_manager.stop()
        server.server_close()


if __name__ == "__main__":
    main()
