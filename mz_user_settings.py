from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app_runtime import APP_BASE_DIR

SETTINGS_PATH = APP_BASE_DIR / "mz_user_settings.json"


@dataclass
class LauncherSettings:
    auto_post_interval_big_rounds: int = 40
    friend_compare_interval_big_rounds: int = 20
    friend_save_interval_big_rounds: int = 50
    wait_between_big_rounds_seconds: float = 60.0
    auto_post_content: str = "自动说说"
    auto_post_images: list[str] = field(default_factory=list)
    auto_post_wait_seconds: float = 60.0
    auto_post_delete_after_post: bool = True
    auto_forward_enabled: bool = False
    auto_forward_target_uins: list[str] = field(default_factory=list)
    auto_forward_keyword: str = "转发"
    auto_forward_append_text: str = "测试内容"
    auto_forward_include_forwarded_feeds: bool = False


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _normalize_uins(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def load_settings(path: Path | None = None) -> LauncherSettings:
    settings_path = path or SETTINGS_PATH
    defaults = LauncherSettings()
    if not settings_path.is_file():
        return defaults

    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    if not isinstance(raw, dict):
        return defaults

    return LauncherSettings(
        auto_post_interval_big_rounds=max(0, _coerce_int(raw.get("auto_post_interval_big_rounds"), defaults.auto_post_interval_big_rounds)),
        friend_compare_interval_big_rounds=max(0, _coerce_int(raw.get("friend_compare_interval_big_rounds"), defaults.friend_compare_interval_big_rounds)),
        friend_save_interval_big_rounds=max(0, _coerce_int(raw.get("friend_save_interval_big_rounds"), defaults.friend_save_interval_big_rounds)),
        wait_between_big_rounds_seconds=max(0.0, _coerce_float(raw.get("wait_between_big_rounds_seconds"), defaults.wait_between_big_rounds_seconds)),
        auto_post_content=str(raw.get("auto_post_content") or defaults.auto_post_content),
        auto_post_images=_normalize_images(raw.get("auto_post_images")),
        auto_post_wait_seconds=max(0.0, _coerce_float(raw.get("auto_post_wait_seconds"), defaults.auto_post_wait_seconds)),
        auto_post_delete_after_post=bool(raw.get("auto_post_delete_after_post", defaults.auto_post_delete_after_post)),
        auto_forward_enabled=bool(raw.get("auto_forward_enabled", defaults.auto_forward_enabled)),
        auto_forward_target_uins=_normalize_uins(raw.get("auto_forward_target_uins")),
        auto_forward_keyword=str(raw.get("auto_forward_keyword") or defaults.auto_forward_keyword),
        auto_forward_append_text=str(raw.get("auto_forward_append_text") or defaults.auto_forward_append_text),
        auto_forward_include_forwarded_feeds=bool(
            raw.get("auto_forward_include_forwarded_feeds", defaults.auto_forward_include_forwarded_feeds)
        ),
    )


def save_settings(settings: LauncherSettings, path: Path | None = None) -> Path:
    settings_path = path or SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings_path
