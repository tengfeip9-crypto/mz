from __future__ import annotations

import sys
from pathlib import Path


def get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return get_app_base_dir()


APP_BASE_DIR = get_app_base_dir()
BUNDLE_ROOT = get_bundle_root()
