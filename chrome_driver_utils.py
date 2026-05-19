from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

LogFn = Callable[[str], None]


def _normalize_driver_path(chromedriver_path: str | None) -> str:
    return (chromedriver_path or "").strip()


def _extract_major(version_text: str | None) -> int | None:
    if not version_text:
        return None
    match = re.search(r"(\d+)\.", version_text)
    if match is None:
        return None
    return int(match.group(1))


def _summarize_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return " ".join(message.split())


def build_debugger_options(debugger_address: str) -> Options:
    options = Options()
    options.add_experimental_option("debuggerAddress", debugger_address)
    return options


def get_debug_browser_version(debugger_address: str, timeout_seconds: float = 2.0) -> str | None:
    try:
        with urlopen(f"http://{debugger_address}/json/version", timeout=timeout_seconds) as response:
            payload = json.load(response)
    except Exception:
        return None

    browser = str(payload.get("Browser") or "").strip()
    if "/" in browser:
        browser = browser.split("/", 1)[1].strip()
    return browser or None


def get_chromedriver_version(chromedriver_path: str | None, timeout_seconds: float = 3.0) -> str | None:
    path_text = _normalize_driver_path(chromedriver_path)
    if not path_text:
        return None

    path = Path(path_text)
    if not path.is_file():
        return None

    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return None

    text = (result.stdout or result.stderr or "").strip()
    match = re.search(r"ChromeDriver\s+([0-9.]+)", text)
    if match is None:
        return None
    return match.group(1)


def explain_driver_compatibility(debugger_address: str, chromedriver_path: str | None) -> tuple[bool | None, str]:
    path_text = _normalize_driver_path(chromedriver_path)
    if not path_text:
        return None, "未指定 ChromeDriver 路径，将使用 Selenium Manager 自动解析驱动。"

    path = Path(path_text)
    if not path.is_file():
        return False, f"ChromeDriver 路径不存在：{path}"

    browser_version = get_debug_browser_version(debugger_address)
    driver_version = get_chromedriver_version(path_text)
    browser_major = _extract_major(browser_version)
    driver_major = _extract_major(driver_version)

    if browser_major is None or driver_major is None:
        return None, "无法读取 Chrome 或 ChromeDriver 版本，稍后会先尝试当前驱动。"

    if browser_major == driver_major:
        return True, f"检测到 Chrome {browser_version} 与 ChromeDriver {driver_version} 主版本一致。"

    return (
        False,
        f"检测到 Chrome {browser_version} 与 ChromeDriver {driver_version} 主版本不一致，"
        "将跳过当前驱动并回退到 Selenium Manager。",
    )


def create_attached_chrome_driver(
    debugger_address: str,
    chromedriver_path: str | None = None,
    *,
    log: LogFn | None = None,
) -> webdriver.Chrome:
    options = build_debugger_options(debugger_address)
    reasons: list[str] = []

    compatible, detail = explain_driver_compatibility(debugger_address, chromedriver_path)
    if log is not None and detail:
        log(detail)

    path_text = _normalize_driver_path(chromedriver_path)
    if path_text and compatible is not False:
        try:
            return webdriver.Chrome(service=Service(path_text), options=options)
        except WebDriverException as exc:
            reasons.append(f"指定 ChromeDriver 启动失败：{_summarize_exception(exc)}")
            if log is not None:
                log("指定 ChromeDriver 启动失败，准备回退到 Selenium Manager。")

    try:
        return webdriver.Chrome(options=options)
    except WebDriverException as exc:
        reasons.append(f"Selenium Manager 启动失败：{_summarize_exception(exc)}")

    if compatible is False:
        reasons.insert(0, detail)
    elif path_text and compatible is None:
        reasons.insert(0, detail)

    raise RuntimeError("无法连接调试浏览器；" + "；".join(reasons))
