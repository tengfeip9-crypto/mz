from __future__ import annotations

import argparse
import importlib
import os
import random
import sys
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

from project_config import CHROMEDRIVER_PATH, DEBUGGER_ADDRESS  # noqa: E402
from mz_user_settings import load_settings  # noqa: E402

MODULE_DIR = os.fspath(MODULE_DIR_PATH)


@dataclass
class BrowserConfig:
    driver_path: str = os.fspath(CHROMEDRIVER_PATH)
    debugger_address: str = DEBUGGER_ADDRESS
    startup_wait_seconds: float = 1.0


@dataclass
class TaskConfig:
    auto_post_interval_big_rounds: int = 40 #说说
    friend_compare_interval_big_rounds: int = 20 #对比
    friend_save_interval_big_rounds: int = 50 #保存
    auto_post_script: str = "ds.py"
    friend_compare_script: str = "db.py"
    friend_save_script: str = "jc.py"


@dataclass
class SelectorConfig:
    dynamic_selector: str = "li.f-single.f-s-s"
    praise_button_selector: str = "i.fui-icon.icon-op-praise"
    refresh_button_selector: str = "i.ui-icon.icon-refresh-v9"
    top_button_xpath: str = "//span[text()='顶部']"
    qq_login_link_xpath: str = "//a[contains(normalize-space(.), 'QQ登录')]"
    personal_center_link_xpath: str = "//a[contains(@href,'/infocenter') and contains(normalize-space(.), '个人中心')]"
    friend_feed_tab_xpath: str = "//span[@class='sn-title' and text()='好友动态']"
    active_feed_tab_selector: str = "div.feed-control-tab a.item-on, div.feed-control-tab a.item-on-slt"
    all_feed_trigger_selector: str = "#feed_tab_hover"
    all_feed_text_selector: str = "#feed_hover_text"
    all_feed_menu_selector: str = "#feed_tab_all"
    login_iframe_selector: str = "iframe#login_frame, iframe[src*='ptlogin2.qq.com']"
    login_avatar_selectors: tuple[str, ...] = (
        "a.face",
        "span[id^='img_out_']",
        "img[id^='img_']",
        "span[id^='nick_']",
    )
    login_qr_only_texts: tuple[str, ...] = (
        "二维码失效",
        "扫码登录",
        "请使用QQ手机版扫码登录",
        "请打开QQ手机版，确认登录",
        "QQ手机版授权",
    )


@dataclass
class FeedState:
    tab_text: str
    dynamic_count: int
    praise_count: int


@dataclass
class LoopConfig:
    max_small_rounds: int = 50
    max_idle_small_rounds: int = 3
    scroll_pause_seconds: float = 3.0
    wait_between_big_rounds_seconds: float = 60.0#每大轮间隔
    max_big_rounds: Optional[int] = None


@dataclass
class LikeConfig:
    verify_after_click: bool = True
    verify_wait_seconds: float = 0.35
    click_pause_min_seconds: float = 0.35
    click_pause_max_seconds: float = 0.80
    max_new_likes_per_small_round: Optional[int] = None


@dataclass
class AppConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    tasks: TaskConfig = field(default_factory=TaskConfig)
    selectors: SelectorConfig = field(default_factory=SelectorConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    like: LikeConfig = field(default_factory=LikeConfig)


@dataclass
class LikeStats:
    found_buttons: int = 0
    clicked: int = 0
    effective: int = 0
    canceled: int = 0
    skipped_already_liked: int = 0
    errors: int = 0


CONFIG = AppConfig()


def 应用持久化配置(base_config: AppConfig) -> AppConfig:
    config = deepcopy(base_config)
    settings = load_settings()
    config.tasks.auto_post_interval_big_rounds = settings.auto_post_interval_big_rounds
    config.tasks.friend_compare_interval_big_rounds = settings.friend_compare_interval_big_rounds
    config.tasks.friend_save_interval_big_rounds = settings.friend_save_interval_big_rounds
    config.loop.wait_between_big_rounds_seconds = settings.wait_between_big_rounds_seconds
    return config


def _执行内部任务(script_name: str) -> int:
    if script_name == "ds.py":
        module = importlib.import_module("mz_core.ds")
        return int(module.执行保存的自动说说任务())
    if script_name == "jc.py":
        module = importlib.import_module("mz_core.jc")
        result = module.主程序()
        return 0 if result in (None, 0) else int(result)
    if script_name == "db.py":
        module = importlib.import_module("mz_core.db")
        result = module.主程序()
        return 0 if result in (None, 0) else int(result)
    raise FileNotFoundError(script_name)


def 等待可中断(seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
    if seconds <= 0:
        return False
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


def 获取页面文本片段(driver: webdriver.Chrome, limit: int = 600) -> str:
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        return (body.text or "").strip()[:limit]
    except Exception:
        return ""


def 记录异常现象(
    category: str,
    reason: str,
    symptom: str,
    driver: Optional[webdriver.Chrome] = None,
    selectors: Optional[SelectorConfig] = None,
    extra: Optional[dict] = None,
    dedupe_key: Optional[str] = None,
    cooldown_seconds: float = 180.0,
) -> None:
    return


def 监控当前异常场景(driver: webdriver.Chrome, config: AppConfig, stage: str) -> None:
    current_url = driver.current_url or ""
    page_text = 获取页面文本片段(driver, limit=1000)
    normalized_text = f"{current_url}\n{page_text}"

    if (
        current_url.startswith("chrome-error://")
        or "ERR_INTERNET_DISCONNECTED" in normalized_text
        or "无法访问此网站" in normalized_text
        or "已断开互联网连接" in normalized_text
        or "没有可用网络" in normalized_text
        or "无法连接到互联网" in normalized_text
    ):
        记录异常现象(
            category="网络异常页面",
            reason="浏览器进入断网或网站不可达错误页，脚本无法继续操作 QQ 空间。",
            symptom=f"{stage}：页面出现断网提示或 Chrome 错误页文案，通常表现为整个页面变成浏览器错误提示页。",
            driver=driver,
            selectors=config.selectors,
            dedupe_key="network_error_page",
        )
        return

    if 当前处于登录授权页(driver, config.selectors):
        has_avatar_auth = 登录页存在头像授权入口(driver, config.selectors)
        login_text = 获取登录框文本(driver, config.selectors)

        if has_avatar_auth:
            记录异常现象(
                category="登录授权中断",
                reason="页面跳转到 QQ 登录授权页，需要重新点击 QQ 登录或头像授权。",
                symptom=f"{stage}：当前不在空间动态页，而是出现登录授权框，可见头像授权入口。",
                driver=driver,
                selectors=config.selectors,
                dedupe_key="login_page_with_avatar",
            )
        elif any(text in login_text for text in config.selectors.login_qr_only_texts):
            记录异常现象(
                category="登录授权中断",
                reason="页面跳转到登录页，但没有可点击头像授权入口，只能扫码或手机确认。",
                symptom=f"{stage}：登录框仅显示二维码、QQ 手机版授权或手机确认提示，脚本无法自行完成登录。",
                driver=driver,
                selectors=config.selectors,
                dedupe_key="login_page_qr_only",
            )
        return

    if 疑似卡在动态页加载(driver, config.selectors):
        记录异常现象(
            category="动态页卡死",
            reason="QQ 空间页面外壳已加载，但动态流一直为空，页面疑似卡在刷新状态。",
            symptom=f"{stage}：页签或刷新控件存在，但动态数和可点赞数都为 0，通常表现为中间区域一直空白或转圈。",
            driver=driver,
            selectors=config.selectors,
            dedupe_key="feed_shell_loaded_but_empty",
        )


def 连接浏览器(browser_config: BrowserConfig) -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", browser_config.debugger_address)
    service = Service(browser_config.driver_path)
    return webdriver.Chrome(service=service, options=chrome_options)


def 点击元素(driver: webdriver.Chrome, element, desc: str) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.2)
        element.click()
        print(f"{desc}成功")
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            print(f"{desc}成功")
            return True
        except Exception as exc:
            print(f"{desc}失败: {exc}")
            return False


def 运行外部脚本(script_name: str, success_msg: str, fail_prefix: str) -> bool:
    script_path = os.path.join(MODULE_DIR, script_name)
    print(f"调用任务: {script_name}")

    try:
        returncode = _执行内部任务(script_name)
    except FileNotFoundError:
        print(f"{fail_prefix}: 脚本不存在")
        记录异常现象(
            category="外部脚本异常",
            reason="外部脚本文件不存在。",
            symptom=f"调用 {script_name} 时未找到脚本文件，相关定时任务无法执行。",
            extra={"script_name": script_name, "script_path": script_path},
            dedupe_key=f"missing_script:{script_name}",
        )
        return False
    except Exception as exc:
        print(f"{fail_prefix}: {exc}")
        记录异常现象(
            category="外部脚本异常",
            reason="调用外部脚本时抛出异常。",
            symptom=f"{script_name} 启动失败，定时任务未完成。",
            extra={"script_name": script_name, "script_path": script_path, "error": str(exc)},
            dedupe_key=f"run_script_exception:{script_name}",
        )
        return False

    if returncode == 0:
        print(success_msg)
        return True

    print(f"{fail_prefix}: 退出码 {returncode}")
    记录异常现象(
        category="外部脚本异常",
        reason="外部脚本返回非 0 退出码。",
        symptom=f"{script_name} 执行失败，程序收到退出码 {returncode}。",
        extra={"script_name": script_name, "script_path": script_path, "returncode": returncode},
        dedupe_key=f"run_script_failed:{script_name}:{returncode}",
    )
    return False


def 尝试点击刷新(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    try:
        refresh_button = driver.find_element(By.CSS_SELECTOR, selectors.refresh_button_selector)
    except Exception:
        return False

    if not 点击元素(driver, refresh_button, "刷新按钮"):
        return False

    time.sleep(3)
    return True


def 尝试切回好友动态(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    try:
        tab = driver.find_element(By.XPATH, selectors.friend_feed_tab_xpath)
    except Exception:
        return False

    if not 点击元素(driver, tab, "好友动态标签"):
        return False

    time.sleep(3)
    return True


def 获取当前动态页签(driver: webdriver.Chrome, selectors: SelectorConfig) -> str:
    for selector in (selectors.all_feed_text_selector, selectors.all_feed_trigger_selector):
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
            text = (element.text or element.get_attribute("textContent") or "").strip()
            if text:
                return text
        except Exception:
            pass

    try:
        tab = driver.find_element(By.CSS_SELECTOR, selectors.active_feed_tab_selector)
        return (tab.text or tab.get_attribute("textContent") or "").strip()
    except Exception:
        return ""


def 获取动态流状态(driver: webdriver.Chrome, selectors: SelectorConfig) -> FeedState:
    try:
        dynamic_count = len(driver.find_elements(By.CSS_SELECTOR, selectors.dynamic_selector))
    except Exception:
        dynamic_count = 0

    try:
        praise_count = len(driver.find_elements(By.CSS_SELECTOR, selectors.praise_button_selector))
    except Exception:
        praise_count = 0

    return FeedState(
        tab_text=获取当前动态页签(driver, selectors),
        dynamic_count=dynamic_count,
        praise_count=praise_count,
    )


def 动态流已加载(feed_state: FeedState) -> bool:
    return feed_state.dynamic_count > 0 or feed_state.praise_count > 0


def 位于可点赞动态页(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    state = 获取动态流状态(driver, selectors)
    if state.praise_count > 0:
        return True
    if state.tab_text in {"全部动态", "好友动态", "特别关心"} and state.dynamic_count > 0:
        return True
    return False


def 等待动态流加载(
    driver: webdriver.Chrome,
    selectors: SelectorConfig,
    timeout_seconds: float = 8.0,
    require_praise: bool = False,
) -> FeedState:
    deadline = time.time() + timeout_seconds
    last_state = 获取动态流状态(driver, selectors)

    while time.time() < deadline:
        if require_praise:
            if last_state.praise_count > 0:
                return last_state
        else:
            if 动态流已加载(last_state):
                return last_state

        time.sleep(1)
        last_state = 获取动态流状态(driver, selectors)

    return last_state


def 尝试切回个人中心(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    try:
        link = driver.find_element(By.XPATH, selectors.personal_center_link_xpath)
    except Exception:
        return False

    if not 点击元素(driver, link, "个人中心入口"):
        return False

    time.sleep(4)
    return True


def 尝试切到全部动态(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    for selector, desc in (
        (selectors.all_feed_trigger_selector, "全部动态切换器"),
        (selectors.all_feed_menu_selector, "全部动态菜单项"),
    ):
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            continue

        if 点击元素(driver, element, desc):
            time.sleep(3)
            return True

    return False


def 查找首个可见元素(
    driver: webdriver.Chrome,
    by: By,
    selectors: Iterable[str],
):
    for selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
        except Exception:
            continue

        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue

    return None


def 当前处于登录授权页(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    current_url = driver.current_url or ""
    if any(host in current_url for host in ("i.qq.com", "ptlogin2.qq.com")):
        return True

    try:
        driver.switch_to.default_content()
        return bool(driver.find_elements(By.CSS_SELECTOR, selectors.login_iframe_selector))
    except Exception:
        return False


def 尝试点击QQ登录入口(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    try:
        driver.switch_to.default_content()
        link = driver.find_element(By.XPATH, selectors.qq_login_link_xpath)
    except Exception:
        return False

    if not 点击元素(driver, link, "QQ登录入口"):
        return False

    time.sleep(3)
    return True


def 切换到登录框(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    try:
        driver.switch_to.default_content()
    except Exception:
        return False

    try:
        frames = driver.find_elements(By.CSS_SELECTOR, selectors.login_iframe_selector)
    except Exception:
        frames = []

    for frame in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            return True
        except Exception:
            continue

    driver.switch_to.default_content()
    return False


def 获取登录框文本(driver: webdriver.Chrome, selectors: SelectorConfig) -> str:
    if not 切换到登录框(driver, selectors):
        return ""

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        return (body.text or "").strip()
    except Exception:
        return ""
    finally:
        driver.switch_to.default_content()


def 登录页存在头像授权入口(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    if not 切换到登录框(driver, selectors):
        return False

    try:
        avatar = 查找首个可见元素(
            driver,
            By.CSS_SELECTOR,
            selectors.login_avatar_selectors,
        )
        return avatar is not None
    finally:
        driver.switch_to.default_content()


def 尝试处理登录授权(driver: webdriver.Chrome, config: AppConfig) -> bool:
    if not 当前处于登录授权页(driver, config.selectors):
        return False

    print("检测到登录授权页，尝试自动点击头像授权登录...")
    监控当前异常场景(driver, config, stage="恢复流程进入登录页")

    if not 切换到登录框(driver, config.selectors):
        if "i.qq.com" in (driver.current_url or "") and 尝试点击QQ登录入口(driver, config.selectors):
            if not 切换到登录框(driver, config.selectors):
                print("已点击 QQ 登录入口，但登录框仍未出现")
                return False
        else:
            print("检测到登录页，但未找到登录框 iframe")
            return False

    try:
        avatar = 查找首个可见元素(
            driver,
            By.CSS_SELECTOR,
            config.selectors.login_avatar_selectors,
        )
        if avatar is None:
            login_text = 获取登录框文本(driver, config.selectors)
            if any(text in login_text for text in config.selectors.login_qr_only_texts):
                print("当前登录页没有可点击头像授权入口，当前只能扫码或手机确认登录")
                记录异常现象(
                    category="登录授权中断",
                    reason="登录框没有可用头像授权入口。",
                    symptom="当前登录框仅显示二维码、QQ 手机版授权或手机确认文案，脚本无法自动继续。",
                    driver=driver,
                    selectors=config.selectors,
                    dedupe_key="login_page_qr_only",
                )
            else:
                print("当前登录页未找到可用的头像授权入口")
                记录异常现象(
                    category="登录授权中断",
                    reason="登录框存在，但未找到可点击的头像授权元素。",
                    symptom="页面停留在登录授权页，脚本未能定位到头像授权按钮。",
                    driver=driver,
                    selectors=config.selectors,
                    dedupe_key="login_page_no_avatar_element",
                )
            return False
 
        if not 点击元素(driver, avatar, "头像授权登录"):
            return False
    finally:
        driver.switch_to.default_content()

    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(1)
        if not 当前处于登录授权页(driver, config.selectors):
            print(f"头像授权后已离开登录页，当前网址：{driver.current_url}")
            return True

    login_text = 获取登录框文本(driver, config.selectors)
    if "QQ手机版授权" in login_text or "请打开QQ手机版，确认登录" in login_text:
        print("头像已点击，但当前需要在 QQ 手机版确认登录")
        记录异常现象(
            category="登录授权中断",
            reason="头像授权后仍需手机确认登录。",
            symptom="登录框进入“QQ 手机版授权/请打开 QQ 手机版确认登录”状态，脚本无法替代手机确认。",
            driver=driver,
            selectors=config.selectors,
            dedupe_key="login_page_mobile_confirm",
        )
    elif any(text in login_text for text in config.selectors.login_qr_only_texts):
        print("头像点击后仍停留在登录页，当前可能只能扫码或手机确认登录")
        记录异常现象(
            category="登录授权中断",
            reason="头像点击后仍停留在二维码或手机确认登录页。",
            symptom="登录页没有自动跳转回空间页，当前需要人工扫码或手机确认。",
            driver=driver,
            selectors=config.selectors,
            dedupe_key="login_page_qr_only_after_click",
        )
    else:
        print("头像点击后仍未离开登录页")
        记录异常现象(
            category="登录授权中断",
            reason="头像授权点击后未成功离开登录页。",
            symptom="点击头像后页面仍停留在登录页，没有进入 QQ 空间页面。",
            driver=driver,
            selectors=config.selectors,
            dedupe_key="login_page_still_open_after_click",
        )
    return False


def 获取规范动态页网址(driver: webdriver.Chrome) -> str:
    current_url = driver.current_url or ""
    if not current_url:
        return ""

    try:
        parsed = urlsplit(current_url)
    except Exception:
        return current_url

    if not parsed.scheme or not parsed.netloc:
        return current_url

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "user.qzone.qq.com" in parsed.netloc and "/infocenter" in parsed.path:
        query["loginfrom"] = query.get("loginfrom") or "31"
    query["_t_"] = f"{time.time():.6f}"

    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
    )


def 疑似卡在动态页加载(driver: webdriver.Chrome, selectors: SelectorConfig) -> bool:
    current_url = driver.current_url or ""
    if "user.qzone.qq.com" not in current_url or "/infocenter" not in current_url:
        return False

    state = 获取动态流状态(driver, selectors)
    if 动态流已加载(state):
        return False

    try:
        ready_state = driver.execute_script("return document.readyState") or ""
    except Exception:
        ready_state = ""

    if ready_state not in {"interactive", "complete"}:
        return False

    has_feed_shell = any(
        (
            state.tab_text in {"全部动态", "好友动态", "特别关心"},
            bool(driver.find_elements(By.CSS_SELECTOR, selectors.all_feed_trigger_selector)),
            bool(driver.find_elements(By.CSS_SELECTOR, selectors.refresh_button_selector)),
            bool(driver.find_elements(By.XPATH, selectors.friend_feed_tab_xpath)),
        )
    )
    return has_feed_shell


def 强制重载当前动态页(driver: webdriver.Chrome, config: AppConfig) -> bool:
    target_url = 获取规范动态页网址(driver)
    print(f"执行终极恢复：整页重载当前网址 -> {target_url or '当前页'}")
    记录异常现象(
        category="动态页卡死",
        reason="常规恢复失败后触发整页重载。",
        symptom="页面疑似卡在刷新态或动态流为空，脚本开始强制重载整个空间网址。",
        driver=driver,
        selectors=config.selectors,
        dedupe_key="force_reload_current_feed_page",
    )

    try:
        if target_url:
            driver.get(target_url)
        else:
            driver.refresh()

        time.sleep(8)
        if 当前处于登录授权页(driver, config.selectors):
            print("整页重载后进入登录授权页，尝试自动登录...")
            if not 尝试处理登录授权(driver, config):
                return False

        state = 等待动态流加载(
            driver,
            config.selectors,
            timeout_seconds=8,
            require_praise=False,
        )
        print(
            f"整页重载后状态：页签={state.tab_text or '未知'}，"
            f"动态={state.dynamic_count}，可点赞={state.praise_count}"
        )
        if not 动态流已加载(state):
            记录异常现象(
                category="动态页卡死",
                reason="整页重载后动态流仍未恢复。",
                symptom="完成整页重载后，动态数和可点赞数仍为空，页面没有恢复正常动态流。",
                driver=driver,
                selectors=config.selectors,
                dedupe_key="force_reload_without_feed_recovery",
            )
        return 动态流已加载(state)
    except Exception as exc:
        print(f"整页重载失败: {exc}")
        记录异常现象(
            category="动态页卡死",
            reason="整页重载当前动态页时抛出异常。",
            symptom="终极恢复阶段执行 driver.get/refresh 失败，无法重新加载空间页。",
            driver=driver,
            selectors=config.selectors,
            extra={"error": str(exc)},
            dedupe_key="force_reload_exception",
        )
        return False


def 尝试恢复点赞流(
    driver: webdriver.Chrome,
    config: AppConfig,
    require_praise: bool = False,
    allow_force_reload: bool = True,
) -> bool:
    if 当前处于登录授权页(driver, config.selectors):
        if not 尝试处理登录授权(driver, config):
            return False

    监控当前异常场景(driver, config, stage="开始恢复点赞流")
    state = 等待动态流加载(driver, config.selectors, timeout_seconds=2, require_praise=require_praise)
    if require_praise:
        if state.praise_count > 0:
            return True
    elif 动态流已加载(state):
        return True

    if 尝试切回个人中心(driver, config.selectors):
        state = 等待动态流加载(driver, config.selectors, require_praise=require_praise)
        if require_praise:
            if state.praise_count > 0:
                print(
                    f"已恢复到点赞流，当前页签：{state.tab_text or '未知'}，"
                    f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
                )
                return True
        elif 动态流已加载(state):
            print(
                f"已恢复到动态页，当前页签：{state.tab_text or '未知'}，"
                f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
            )
            return True
        print("已进入个人中心，但点赞流还未完全恢复")

    if 尝试切到全部动态(driver, config.selectors):
        state = 等待动态流加载(driver, config.selectors, require_praise=require_praise)
        if require_praise:
            if state.praise_count > 0:
                print(
                    f"已切到全部动态并恢复点赞流，当前页签：{state.tab_text or '未知'}，"
                    f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
                )
                return True
        elif 动态流已加载(state):
            print(
                f"已切到全部动态，当前页签：{state.tab_text or '未知'}，"
                f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
            )
            return True
        print("已尝试切到全部动态，但点赞流仍未恢复")

    if 尝试切回好友动态(driver, config.selectors):
        state = 等待动态流加载(driver, config.selectors, require_praise=require_praise)
        if require_praise:
            if state.praise_count > 0:
                print(
                    f"已通过好友动态入口恢复点赞流，当前页签：{state.tab_text or '未知'}，"
                    f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
                )
                return True
        elif 动态流已加载(state):
            print(
                f"已通过好友动态入口恢复，当前页签：{state.tab_text or '未知'}，"
                f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
            )
            return True
        print("已点击好友动态入口，但点赞流仍未恢复")

    state = 获取动态流状态(driver, config.selectors)
    if require_praise:
        if state.praise_count > 0:
            print(
                f"当前页面已包含可点赞动态流，页签：{state.tab_text or '未知'}，"
                f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
            )
            return True
    elif 动态流已加载(state):
        print(
            f"当前页面已包含动态流，页签：{state.tab_text or '未知'}，"
            f"动态 {state.dynamic_count}，可点赞 {state.praise_count}"
        )
        return True

    if allow_force_reload and 疑似卡在动态页加载(driver, config.selectors):
        print("检测到页面外壳已加载但动态流为空，疑似卡在刷新页，尝试整页重载当前网址...")
        if 强制重载当前动态页(driver, config):
            return 尝试恢复点赞流(
                driver,
                config,
                require_praise=require_praise,
                allow_force_reload=False,
            )
        print("整页重载后仍未恢复到动态流")
        监控当前异常场景(driver, config, stage="整页重载后仍未恢复")

    return False


def 刷新动态页(driver: webdriver.Chrome, 连续刷新失败轮数: int, config: AppConfig) -> int:
    监控当前异常场景(driver, config, stage="刷新动态页前")

    if 位于可点赞动态页(driver, config.selectors):
        if 尝试点击刷新(driver, config.selectors):
            return 0

        print(
            "当前已在可点赞动态页"
            f"（{获取当前动态页签(driver, config.selectors) or '未知页签'}），"
            "跳过强制刷新"
        )
        return 0

    连续刷新失败轮数 += 1
    print(f"未找到刷新按钮（连续 {连续刷新失败轮数} 轮）")

    if 连续刷新失败轮数 < 2:
        return 连续刷新失败轮数

    print("连续两轮未找到刷新按钮，强制重载页面...")

    try:
        driver.refresh()
        time.sleep(5)
        if not 尝试恢复点赞流(driver, config, require_praise=False):
            print("强制刷新后未恢复到可点赞动态页")
            记录异常现象(
                category="点赞流中断",
                reason="页面刷新后仍未恢复到动态页或点赞流。",
                symptom="刷新后页面没有恢复出正常动态流，后续无法继续正常点赞。",
                driver=driver,
                selectors=config.selectors,
                dedupe_key="refresh_without_feed_recovery",
            )
        else:
            time.sleep(3)
        return 0
    except Exception as exc:
        print(f"强制重载页面失败: {exc}")
        记录异常现象(
            category="点赞流中断",
            reason="刷新动态页时抛出异常。",
            symptom="执行浏览器刷新时失败，当前页无法通过常规刷新恢复。",
            driver=driver,
            selectors=config.selectors,
            extra={"error": str(exc)},
            dedupe_key="refresh_feed_exception",
        )
        return 连续刷新失败轮数


def 回到顶部(driver: webdriver.Chrome, selectors: SelectorConfig) -> None:
    try:
        top_button = driver.find_element(By.XPATH, selectors.top_button_xpath)
    except Exception:
        print("没有找到顶部按钮")
        return

    点击元素(driver, top_button, "返回顶部")


def 自动说说(driver: webdriver.Chrome, config: AppConfig) -> None:
    if 运行外部脚本(
        config.tasks.auto_post_script,
        "自动说说发表完成",
        "自动说说发表失败",
    ):
        刷新动态页(driver, 0, config)

def 执行好友保存(driver: webdriver.Chrome, config: AppConfig) -> None:
    if 运行外部脚本(
        config.tasks.friend_save_script,
        "好友列表保存完成",
        "好友保存失败",
    ):
        刷新动态页(driver, 0, config)


def 执行好友对比(driver: webdriver.Chrome, config: AppConfig) -> None:
    if 运行外部脚本(
        config.tasks.friend_compare_script,
        "好友对比完成",
        "好友对比失败",
    ):
        刷新动态页(driver, 0, config)


def 已点赞(btn) -> bool:
    try:
        parent = btn.find_element(By.XPATH, "..")
        parent_class = parent.get_attribute("class") or ""
        return "item-on" in parent_class.split()
    except Exception:
        return False


def 执行点赞(driver: webdriver.Chrome, config: AppConfig) -> LikeStats:
    buttons = driver.find_elements(By.CSS_SELECTOR, config.selectors.praise_button_selector)
    stats = LikeStats(found_buttons=len(buttons))

    for btn in buttons:
        try:
            if 已点赞(btn):
                stats.skipped_already_liked += 1
                continue

            driver.execute_script("arguments[0].click();", btn)
            stats.clicked += 1

            if config.like.verify_after_click:
                time.sleep(config.like.verify_wait_seconds)
                if 已点赞(btn):
                    stats.effective += 1
                else:
                    stats.canceled += 1
            else:
                stats.effective += 1

            if config.like.max_new_likes_per_small_round is not None:
                if stats.clicked >= config.like.max_new_likes_per_small_round:
                    break

            time.sleep(
                random.uniform(
                    config.like.click_pause_min_seconds,
                    config.like.click_pause_max_seconds,
                )
            )
        except Exception:
            stats.errors += 1
            continue

    return stats


def 处理定时任务(driver: webdriver.Chrome, big_round_count: int, config: AppConfig) -> None:
    if (
        config.tasks.auto_post_interval_big_rounds > 0
        and big_round_count % config.tasks.auto_post_interval_big_rounds == 0
    ):
        print("\n触发自动说说脚本")
        自动说说(driver, config)

    if (
        config.tasks.friend_save_interval_big_rounds > 0
        and big_round_count % config.tasks.friend_save_interval_big_rounds == 0
    ):
        print("\n触发好友列表保存")
        执行好友保存(driver, config)

    if (
        config.tasks.friend_compare_interval_big_rounds > 0
        and big_round_count % config.tasks.friend_compare_interval_big_rounds == 0
    ):
        print("\n触发好友变化检测")
        执行好友对比(driver, config)


def 打印配置摘要(config: AppConfig) -> None:
    print("\n当前配置")
    print(f"  调试浏览器: {config.browser.debugger_address}")
    print(f"  ChromeDriver: {config.browser.driver_path}")
    print(
        f"  轮次: 每大轮最多 {config.loop.max_small_rounds} 小轮, "
        f"连续空闲 {config.loop.max_idle_small_rounds} 小轮结束"
    )
    print(
        f"  大轮限制: "
        f"{config.loop.max_big_rounds if config.loop.max_big_rounds is not None else '无限制'}"
    )
    print(
        f"  点赞校验: {'开启' if config.like.verify_after_click else '关闭'}, "
        f"每大轮间隔 {config.loop.wait_between_big_rounds_seconds} 秒"
    )
    print(
        f"  定时任务: 说说/{config.tasks.auto_post_interval_big_rounds}, "
        f"好友保存/{config.tasks.friend_save_interval_big_rounds}, "
        f"好友对比/{config.tasks.friend_compare_interval_big_rounds}"
    )


def 自动点赞循环(
    driver: webdriver.Chrome,
    config: AppConfig,
    stop_event: Optional[threading.Event] = None,
) -> None:
    big_round_count = 0
    refresh_fail_count = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            print("\n收到停止信号，程序准备退出。")
            break

        if config.loop.max_big_rounds is not None and big_round_count >= config.loop.max_big_rounds:
            print(f"\n已达到设定的大轮上限 {config.loop.max_big_rounds}，程序退出")
            break

        big_round_count += 1
        print(f"\n=== 开始第 {big_round_count} 大轮自动点赞 ===")
        idle_small_rounds = 0

        for small_round_index in range(config.loop.max_small_rounds):
            if stop_event is not None and stop_event.is_set():
                print("\n收到停止信号，本大轮提前结束。")
                break

            print(f"\n--- 第 {small_round_index + 1} 小轮扫描点赞 ---")
            监控当前异常场景(
                driver,
                config,
                stage=f"第 {big_round_count} 大轮第 {small_round_index + 1} 小轮开始",
            )

            state_before_round = 获取动态流状态(driver, config.selectors)
            if state_before_round.praise_count == 0:
                print(
                    "当前页面没有可点赞按钮，尝试恢复点赞流："
                    f"页签={state_before_round.tab_text or '未知'}，"
                    f"动态={state_before_round.dynamic_count}"
                )
                if not 尝试恢复点赞流(driver, config, require_praise=True):
                    记录异常现象(
                        category="点赞流中断",
                        reason="当前页面没有可点赞按钮，且恢复流程未能找回点赞流。",
                        symptom="程序检测到动态页无点赞按钮，执行恢复后仍未恢复出可点赞状态。",
                        driver=driver,
                        selectors=config.selectors,
                        dedupe_key="recover_praise_flow_failed",
                    )

            like_stats = 执行点赞(driver, config)
            effective_likes = like_stats.effective if config.like.verify_after_click else like_stats.clicked

            print(
                "本小轮点赞："
                f"按钮 {like_stats.found_buttons} / "
                f"点击 {like_stats.clicked} / "
                f"生效 {like_stats.effective} / "
                f"疑似被取消 {like_stats.canceled} / "
                f"已跳过 {like_stats.skipped_already_liked} / "
                f"错误 {like_stats.errors}"
            )

            if like_stats.canceled > 0:
                print("检测到疑似风控取消，本轮已按“生效点赞数”判断是否继续。")

            if effective_likes == 0:
                idle_small_rounds += 1
            else:
                idle_small_rounds = 0

            if idle_small_rounds >= config.loop.max_idle_small_rounds:
                print(f"\n连续 {idle_small_rounds} 小轮无新点赞 -> 本大轮结束")
                break

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            if 等待可中断(config.loop.scroll_pause_seconds, stop_event):
                print("\n收到停止信号，停止滚动等待。")
                break

        回到顶部(driver, config.selectors)
        refresh_fail_count = 刷新动态页(driver, refresh_fail_count, config)
        处理定时任务(driver, big_round_count, config)

        if stop_event is not None and stop_event.is_set():
            print("\n收到停止信号，程序退出。")
            break

        if config.loop.max_big_rounds is not None and big_round_count >= config.loop.max_big_rounds:
            print(f"\n已完成设定的 {config.loop.max_big_rounds} 大轮，程序退出")
            break

        print(f"等待 {config.loop.wait_between_big_rounds_seconds} 秒后开始下一大轮...\n")
        if 等待可中断(config.loop.wait_between_big_rounds_seconds, stop_event):
            print("\n收到停止信号，结束大轮等待。")
            break


def 构建参数解析器() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ 空间好友动态自动点赞正式版")
    parser.add_argument("--debugger-address", help="覆盖要连接的 Chrome 调试地址，例如 127.0.0.1:9222")
    parser.add_argument("--driver-path", help="覆盖 ChromeDriver 路径")
    parser.add_argument("--startup-wait-seconds", type=float, help="覆盖连接浏览器后的启动等待秒数")
    parser.add_argument("--max-big-rounds", type=int, help="限制大轮数量，便于测试")
    parser.add_argument("--max-small-rounds", type=int, help="覆盖每大轮最多小轮数")
    parser.add_argument("--max-idle-small-rounds", type=int, help="覆盖连续空闲小轮阈值")
    parser.add_argument("--wait-between-big-rounds", type=float, help="覆盖大轮之间等待秒数")
    parser.add_argument(
        "--max-new-likes-per-small-round",
        type=int,
        help="限制每小轮最多新点击的点赞数，便于规避高频风险",
    )
    parser.add_argument(
        "--disable-like-verify",
        action="store_true",
        help="关闭点赞生效校验，恢复旧版只点不验的行为",
    )
    parser.add_argument(
        "--skip-auto-post",
        action="store_true",
        help="本次运行不触发 ds.py",
    )
    parser.add_argument(
        "--skip-friend-save",
        action="store_true",
        help="本次运行不触发 jc.py",
    )
    parser.add_argument(
        "--skip-friend-compare",
        action="store_true",
        help="本次运行不触发 db.py",
    )
    parser.add_argument(
        "--skip-external-tasks",
        action="store_true",
        help="本次运行跳过全部外部脚本任务",
    )
    return parser


def 应用命令行配置(base_config: AppConfig, args: argparse.Namespace) -> AppConfig:
    config = deepcopy(base_config)

    if args.debugger_address is not None:
        config.browser.debugger_address = args.debugger_address
    if args.driver_path is not None:
        config.browser.driver_path = args.driver_path
    if args.startup_wait_seconds is not None:
        config.browser.startup_wait_seconds = args.startup_wait_seconds
    if args.max_big_rounds is not None:
        config.loop.max_big_rounds = args.max_big_rounds
    if args.max_small_rounds is not None:
        config.loop.max_small_rounds = args.max_small_rounds
    if args.max_idle_small_rounds is not None:
        config.loop.max_idle_small_rounds = args.max_idle_small_rounds
    if args.wait_between_big_rounds is not None:
        config.loop.wait_between_big_rounds_seconds = args.wait_between_big_rounds
    if args.max_new_likes_per_small_round is not None:
        config.like.max_new_likes_per_small_round = args.max_new_likes_per_small_round
    if args.disable_like_verify:
        config.like.verify_after_click = False

    if args.skip_external_tasks:
        config.tasks.auto_post_interval_big_rounds = 0
        config.tasks.friend_save_interval_big_rounds = 0
        config.tasks.friend_compare_interval_big_rounds = 0
    else:
        if args.skip_auto_post:
            config.tasks.auto_post_interval_big_rounds = 0
        if args.skip_friend_save:
            config.tasks.friend_save_interval_big_rounds = 0
        if args.skip_friend_compare:
            config.tasks.friend_compare_interval_big_rounds = 0

    return config


def 运行自动点赞(
    config: AppConfig,
    stop_event: Optional[threading.Event] = None,
) -> int:
    打印配置摘要(config)

    try:
        driver = 连接浏览器(config.browser)
        if 等待可中断(config.browser.startup_wait_seconds, stop_event):
            print("\n启动等待阶段收到停止信号，程序退出。")
            return 0
        print("\n准备开始自动点赞...\n")
        自动点赞循环(driver, config, stop_event=stop_event)
        return 0
    except Exception as exc:
        print(f"\n程序异常退出: {exc}")
        return 1


def main(argv: Optional[list[str]] = None, stop_event: Optional[threading.Event] = None) -> int:
    parser = 构建参数解析器()
    args = parser.parse_args(argv)
    config = 应用命令行配置(应用持久化配置(CONFIG), args)
    return 运行自动点赞(config, stop_event=stop_event)


if __name__ == "__main__":
    raise SystemExit(main())
