from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from selenium.webdriver.common.by import By

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from project_config import QQ_NUMBER, RUN_LOG_DIR  # noqa: E402

try:
    from mz_core.friend_storage import load_json, now_iso, now_timestamp, save_json
except ModuleNotFoundError:
    from friend_storage import load_json, now_iso, now_timestamp, save_json


动态卡片选择器 = "li.f-single.f-s-s"
转发弹层选择器 = ".qz_dialog_layer"
转发输入框选择器 = ".qz_dialog_layer .retweet_bd_box [contenteditable='true'].textinput.textarea.c_tx2"
转发发送按钮选择器 = ".qz_dialog_layer .retweet_op input.gb_bt.gb_bt2[value='发送']"
转发关闭按钮选择器 = ".qz_dialog_layer .qz_dialog_btn_close"
自动转发目录 = RUN_LOG_DIR / "auto_forward"
转发状态路径 = 自动转发目录 / "forward_state.json"
转发日志目录 = 自动转发目录 / "history"


def 规范化文本(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def 标准化QQ号列表(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        for item in re.split(r"[\s,，;；]+", text):
            qq = item.strip()
            if not qq or qq in seen:
                continue
            seen.add(qq)
            result.append(qq)
    return result


def 从空间网址提取QQ号(url: str) -> str:
    match = re.search(r"user\.qzone\.qq\.com/(\d+)", str(url or "").strip())
    if match:
        return match.group(1)
    return ""


def 安全整数(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def 判断动态类别(actor_uin: str, original_uin: str, self_uin: str) -> str:
    actor = actor_uin.strip()
    original = original_uin.strip()
    current = self_uin.strip()

    if actor == "0" and original == "0":
        return "ad"
    if not actor and not original:
        return "unknown"
    if not original or actor == original:
        if actor == current and current:
            return "self_original"
        return "other_original"
    if actor == current and current:
        return "self_forwarded_other"
    if original == current and current:
        return "other_forwarded_self"
    return "other_forwarded_other"


def 提取动态卡片(driver, limit: int) -> list[dict[str, Any]]:
    script = """
const limit = arguments[0];
const selector = arguments[1];
const nodes = Array.from(document.querySelectorAll(selector));
const capped = limit > 0 ? nodes.slice(0, limit) : nodes;

return capped.map((item, index) => {
  const feedData = item.querySelector("[name='feed_data']");
  const actorLink = item.querySelector(".f-single-head .f-name");
  const originalAuthorLink = item.querySelector(".qz_summary .txt-box .nickname.name, .qz_summary .txt-box .nickname");
  const ownContent = item.querySelector(".f-info");
  const forwardContent = item.querySelector(".qz_summary .txt-box");
  const forwardButton = item.querySelector(".qz_retweet_btn");

  return {
    index,
    card_id: item.id || "",
    card_classes: item.className || "",
    is_ad: item.classList.contains("f-single-biz"),
    actor_name: actorLink ? (actorLink.textContent || "").trim() : "",
    actor_uin: feedData ? (feedData.getAttribute("data-uin") || "") : "",
    original_author_name: originalAuthorLink ? (originalAuthorLink.textContent || "").trim() : "",
    original_uin: feedData ? (feedData.getAttribute("data-origuin") || "") : "",
    tid: feedData ? (feedData.getAttribute("data-tid") || "") : "",
    original_tid: feedData ? (feedData.getAttribute("data-origtid") || "") : "",
    own_content_text: ownContent ? (ownContent.textContent || "").trim() : "",
    forward_content_text: forwardContent ? (forwardContent.textContent || "").trim() : "",
    full_text: (item.innerText || "").replace(/\\s+/g, " ").trim(),
    retweet_count_raw: feedData ? (feedData.getAttribute("data-retweetcount") || "") : "",
    total_tweet_raw: feedData ? (feedData.getAttribute("data-totweet") || "") : "",
    has_forward_button: Boolean(forwardButton),
  };
});
"""
    result = driver.execute_script(script, limit, 动态卡片选择器)
    return result if isinstance(result, list) else []


def 丰富动态信息(raw_cards: list[dict[str, Any]], self_uin: str) -> list[dict[str, Any]]:
    normalized_self = str(self_uin or "").strip()
    enriched: list[dict[str, Any]] = []

    for raw in raw_cards:
        actor_uin = str(raw.get("actor_uin") or "").strip()
        original_uin = str(raw.get("original_uin") or "").strip()
        own_content = 规范化文本(raw.get("own_content_text"))
        forward_content = 规范化文本(raw.get("forward_content_text"))
        card_type = 判断动态类别(actor_uin, original_uin, normalized_self)
        is_forwarded_feed = bool(actor_uin and original_uin and actor_uin != original_uin)
        content_text = own_content
        if not content_text and not is_forwarded_feed:
            content_text = forward_content
        if not content_text and is_forwarded_feed:
            content_text = forward_content

        item = dict(raw)
        item.update(
            {
                "card_type": card_type,
                "is_forwarded_feed": is_forwarded_feed,
                "content_text": content_text,
                "retweet_count": max(
                    安全整数(raw.get("retweet_count_raw")),
                    安全整数(raw.get("total_tweet_raw")),
                ),
            }
        )
        enriched.append(item)

    return enriched


def 载入转发状态() -> dict[str, Any]:
    default = {"version": 1, "updated_at": None, "forwarded": {}}
    data = load_json(str(转发状态路径), default=default)
    if not isinstance(data, dict):
        return default
    forwarded = data.get("forwarded")
    if not isinstance(forwarded, dict):
        data["forwarded"] = {}
    if "version" not in data:
        data["version"] = 1
    if "updated_at" not in data:
        data["updated_at"] = None
    return data


def 保存转发状态(state: dict[str, Any]) -> None:
    自动转发目录.mkdir(parents=True, exist_ok=True)
    forwarded = state.get("forwarded") if isinstance(state.get("forwarded"), dict) else {}
    if len(forwarded) > 500:
        forwarded = dict(list(forwarded.items())[-500:])
    state["forwarded"] = forwarded
    state["updated_at"] = now_iso()
    save_json(state, str(转发状态路径))


def 构建动态唯一键(item: dict[str, Any]) -> str:
    actor_uin = str(item.get("actor_uin") or "").strip()
    tid = str(item.get("tid") or "").strip()
    original_tid = str(item.get("original_tid") or "").strip()
    return f"{actor_uin}:{tid or original_tid}"


def 已经转发过(state: dict[str, Any], item: dict[str, Any]) -> bool:
    forwarded = state.get("forwarded") if isinstance(state.get("forwarded"), dict) else {}
    return 构建动态唯一键(item) in forwarded


def 记录转发历史(item: dict[str, Any], append_text: str) -> None:
    自动转发目录.mkdir(parents=True, exist_ok=True)
    转发日志目录.mkdir(parents=True, exist_ok=True)

    record = {
        "forwarded_at": now_iso(),
        "actor_name": item.get("actor_name"),
        "actor_uin": item.get("actor_uin"),
        "original_uin": item.get("original_uin"),
        "tid": item.get("tid"),
        "original_tid": item.get("original_tid"),
        "card_type": item.get("card_type"),
        "content_text": item.get("content_text"),
        "append_text": append_text,
        "retweet_count": item.get("retweet_count"),
    }
    log_path = 转发日志目录 / f"forward_{now_timestamp()}.json"
    save_json(record, str(log_path))


def 写入转发状态(state: dict[str, Any], item: dict[str, Any], append_text: str) -> None:
    forwarded = state.get("forwarded")
    if not isinstance(forwarded, dict):
        forwarded = {}
        state["forwarded"] = forwarded
    forwarded[构建动态唯一键(item)] = {
        "forwarded_at": now_iso(),
        "actor_uin": item.get("actor_uin"),
        "tid": item.get("tid"),
        "keyword": item.get("matched_keyword"),
        "append_text": append_text,
    }
    保存转发状态(state)
    记录转发历史(item, append_text)


def 筛选候选动态(
    cards: list[dict[str, Any]],
    watch_uins: Iterable[str],
    keyword: str,
    include_forwarded_feeds: bool,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized_watch = set(标准化QQ号列表(watch_uins))
    normalized_keyword = str(keyword or "").strip()
    keyword_lower = normalized_keyword.lower()
    result: list[dict[str, Any]] = []

    for item in cards:
        actor_uin = str(item.get("actor_uin") or "").strip()
        content_text = 规范化文本(item.get("content_text"))
        if item.get("is_ad"):
            continue
        if not item.get("has_forward_button"):
            continue
        if not actor_uin:
            continue
        if normalized_watch and actor_uin not in normalized_watch:
            continue
        if not include_forwarded_feeds and item.get("is_forwarded_feed"):
            continue
        if normalized_keyword and keyword_lower not in content_text.lower():
            continue
        if 已经转发过(state, item):
            item = dict(item)
            item["already_forwarded"] = True
            result.append(item)
            continue

        item = dict(item)
        item["matched_keyword"] = normalized_keyword
        item["already_forwarded"] = False
        result.append(item)

    return result


def 关闭转发弹层(driver) -> None:
    for element in driver.find_elements(By.CSS_SELECTOR, 转发关闭按钮选择器):
        try:
            if element.is_displayed():
                driver.execute_script("arguments[0].click();", element)
                time.sleep(0.2)
                return
        except Exception:
            continue


def 等待转发弹层出现(driver, timeout_seconds: float = 5.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for element in driver.find_elements(By.CSS_SELECTOR, 转发弹层选择器):
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
        time.sleep(0.2)
    return None


def 打开转发弹层(driver, card_id: str) -> bool:
    关闭转发弹层(driver)

    card = driver.find_element(By.ID, card_id)
    button = card.find_element(By.CSS_SELECTOR, ".qz_retweet_btn")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
    time.sleep(0.2)
    driver.execute_script("arguments[0].click();", button)
    return 等待转发弹层出现(driver) is not None


def 填写附加文案(driver, append_text: str) -> bool:
    text = str(append_text or "").strip()
    editor = driver.find_element(By.CSS_SELECTOR, 转发输入框选择器)
    driver.execute_script(
        """
const el = arguments[0];
el.focus();
el.innerHTML = "";
el.textContent = "";
el.dispatchEvent(new InputEvent('input', {bubbles: true, data: '', inputType: 'deleteContentBackward'}));
        """,
        editor,
    )
    time.sleep(0.2)

    try:
        editor.click()
        editor.send_keys(text)
    except Exception:
        driver.execute_script(
            """
const el = arguments[0];
const text = arguments[1];
el.focus();
el.innerHTML = "";
el.textContent = text;
el.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'}));
el.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            editor,
            text,
        )

    time.sleep(0.4)
    current_text = 规范化文本(
        driver.execute_script(
            "const el = document.querySelector(arguments[0]); return el ? (el.innerText || el.textContent || '') : '';",
            转发输入框选择器,
        )
    )
    return current_text == text


def 发送转发(driver, timeout_seconds: float = 8.0) -> bool:
    button = driver.find_element(By.CSS_SELECTOR, 转发发送按钮选择器)
    driver.execute_script("arguments[0].click();", button)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        visible_dialog = False
        for element in driver.find_elements(By.CSS_SELECTOR, 转发弹层选择器):
            try:
                if element.is_displayed():
                    visible_dialog = True
                    break
            except Exception:
                continue
        if not visible_dialog:
            return True
        time.sleep(0.2)
    return False


def 执行自动转发候选动态(
    driver,
    watch_uins: Iterable[str],
    keyword: str,
    append_text: str,
    include_forwarded_feeds: bool = False,
    self_uin: str = "",
    scan_limit: int = 30,
    max_forwards: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_watch = 标准化QQ号列表(watch_uins)
    stats = {
        "enabled": True,
        "scanned": 0,
        "matched": 0,
        "already_forwarded": 0,
        "attempted": 0,
        "forwarded": 0,
        "errors": 0,
        "dry_run": dry_run,
        "preview": [],
    }

    current_self_uin = str(self_uin or "").strip() or str(QQ_NUMBER or "").strip() or 从空间网址提取QQ号(driver.current_url)
    raw_cards = 提取动态卡片(driver, max(scan_limit, 0))
    cards = 丰富动态信息(raw_cards, current_self_uin)
    state = 载入转发状态()
    candidates = 筛选候选动态(
        cards,
        normalized_watch,
        keyword,
        include_forwarded_feeds,
        state,
    )

    stats["scanned"] = len(cards)
    stats["matched"] = len(candidates)
    stats["already_forwarded"] = sum(1 for item in candidates if item.get("already_forwarded"))
    stats["preview"] = [
        {
            "actor_name": item.get("actor_name"),
            "actor_uin": item.get("actor_uin"),
            "content_text": 规范化文本(item.get("content_text"))[:120],
            "card_type": item.get("card_type"),
            "already_forwarded": bool(item.get("already_forwarded")),
        }
        for item in candidates[:5]
    ]

    actionable = [item for item in candidates if not item.get("already_forwarded")]
    if dry_run:
        return stats

    for item in actionable[: max(max_forwards, 0)]:
        stats["attempted"] += 1
        try:
            if not 打开转发弹层(driver, str(item.get("card_id") or "").strip()):
                raise RuntimeError("未能打开转发弹层")
            if not 填写附加文案(driver, append_text):
                raise RuntimeError("未能写入附加文案")
            if not 发送转发(driver):
                raise RuntimeError("点击发送后弹层未正常关闭")
            写入转发状态(state, item, append_text)
            stats["forwarded"] += 1
            time.sleep(1.0)
        except Exception:
            stats["errors"] += 1
            关闭转发弹层(driver)
            continue

    return stats


def 构建参数解析器() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按关键词自动转发指定 QQ 的列表动态。")
    parser.add_argument("--watch-qq", action="append", default=[], help="目标 QQ 号，可重复传入；留空时扫描全部动态")
    parser.add_argument("--keyword", default="转发", help="正文必须包含的关键词；留空时不按关键词筛选")
    parser.add_argument("--append-text", default="测试内容", help="转发时附加的文案")
    parser.add_argument("--include-forwarded-feeds", action="store_true", help="允许转发列表里本身就是转发的动态")
    parser.add_argument("--limit", type=int, default=30, help="最多扫描多少条当前可见动态")
    parser.add_argument("--max-forwards", type=int, default=1, help="本次最多执行多少次真实转发")
    parser.add_argument("--dry-run", action="store_true", help="只扫描命中项，不真正发送")
    parser.add_argument("--debugger-address", help="覆盖 Chrome 调试地址")
    parser.add_argument("--driver-path", help="覆盖 ChromeDriver 路径")
    return parser


def 主程序(argv: list[str] | None = None) -> int:
    parser = 构建参数解析器()
    args = parser.parse_args(argv)

    try:
        from mz_core.mz import CONFIG, 应用持久化配置, 连接浏览器, 尝试恢复点赞流
    except ModuleNotFoundError:
        from mz import CONFIG, 应用持久化配置, 连接浏览器, 尝试恢复点赞流

    config = 应用持久化配置(CONFIG)
    if args.debugger_address:
        config.browser.debugger_address = args.debugger_address
    if args.driver_path:
        config.browser.driver_path = args.driver_path

    try:
        driver = 连接浏览器(config.browser)
        尝试恢复点赞流(driver, config, require_praise=False)
        stats = 执行自动转发候选动态(
            driver=driver,
            watch_uins=args.watch_qq,
            keyword=args.keyword,
            append_text=args.append_text,
            include_forwarded_feeds=bool(args.include_forwarded_feeds),
            scan_limit=max(args.limit, 0),
            max_forwards=max(args.max_forwards, 0),
            dry_run=bool(args.dry_run),
        )
        print(
            f"自动转发扫描完成：扫描 {stats['scanned']}，命中 {stats['matched']}，"
            f"已转发 {stats['forwarded']}，已跳过 {stats['already_forwarded']}，错误 {stats['errors']}"
        )
        for item in stats.get("preview", []):
            print(
                f"- {item.get('actor_name') or '(未知作者)'} "
                f"actor={item.get('actor_uin') or '-'} "
                f"[{item.get('card_type')}] "
                f"{'(已转发过)' if item.get('already_forwarded') else ''}"
            )
            print(f"  内容: {item.get('content_text') or '(空)'}")
        return 0
    except Exception as exc:
        print(f"自动转发失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(主程序())
