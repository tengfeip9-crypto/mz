from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_STATE_ROOT = PROJECT_ROOT / ".local"
DEFAULT_ENV_FILES = (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


for env_file in DEFAULT_ENV_FILES:
    _load_env_file(env_file)


def _resolve_path(value: str | os.PathLike[str] | None, default: Path) -> Path:
    if value is None or not str(value).strip():
        path = default
    else:
        path = Path(os.path.expandvars(os.path.expanduser(str(value).strip())))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    return path


def _runtime_dir(env_name: str, hidden_name: str, legacy_name: str) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return _resolve_path(configured, LOCAL_STATE_ROOT / hidden_name)

    legacy_path = PROJECT_ROOT / legacy_name
    if legacy_path.exists():
        return legacy_path
    return LOCAL_STATE_ROOT / hidden_name


DEBUGGER_ADDRESS = os.environ.get("MZ_DEBUGGER_ADDRESS", "127.0.0.1:9222").strip() or "127.0.0.1:9222"
QQ_NUMBER = os.environ.get("MZ_QQ_NUMBER", "").strip()

CHROMEDRIVER_PATH = _resolve_path(
    os.environ.get("MZ_CHROMEDRIVER_PATH"),
    PROJECT_ROOT / "driver" / ("chromedriver.exe" if os.name == "nt" else "chromedriver"),
)
CHROME_PATH = _resolve_path(
    os.environ.get("MZ_CHROME_PATH"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if os.name == "nt"
    else Path("/usr/bin/google-chrome"),
)
CHROME_USER_DATA_DIR = _resolve_path(
    os.environ.get("MZ_CHROME_USER_DATA_DIR"),
    LOCAL_STATE_ROOT / "chrome-debug",
)

FRIEND_DATA_DIR = _runtime_dir("MZ_FRIEND_DATA_DIR", "friend_data", "friend_data")
RUN_LOG_DIR = _runtime_dir("MZ_RUN_LOG_DIR", "run_logs", "run_logs")
REMOTE_LOGIN_DATA_DIR = _runtime_dir("MZ_REMOTE_LOGIN_DATA_DIR", "remote_login_data", "remote_login_data")


def require_qq_number() -> str:
    if QQ_NUMBER:
        return QQ_NUMBER
    raise RuntimeError("未配置 QQ 号，请在 .env 或环境变量中设置 MZ_QQ_NUMBER。")
