from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any

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
    from mz_core.friend_storage import now_timestamp, save_json
    from mz_core.mz import (
        CONFIG,
        应用持久化配置,
        连接浏览器,
        尝试恢复点赞流,
        获取动态流状态,
    )
except ModuleNotFoundError:
    from friend_storage import now_timestamp, save_json
    from mz import CONFIG, 应用持久化配置, 连接浏览器, 尝试恢复点赞流, 获取动态流状态


动态卡片选择器 = "li.f-single.f-s-s"
动态探测目录 = RUN_LOG_DIR / "feed_probe"

动态元素说明 = {
    "card_selector": "li.f-single.f-s-s",
    "ad_card_class": "f-single-biz",
    "feed_data_selector": "[name='feed_data']",
    "actor_name_selector": ".f-single-head .f-name",
    "original_author_selector": ".qz_summary .txt-box .nickname.name, .qz_summary .txt-box .nickname",
    "self_content_selector": ".f-info",
    "forward_content_selector": ".qz_summary .txt-box",
    "forward_button_selector": ".qz_retweet_btn[data-type='ForwardingBox']",
    "like_button_selector": ".qz_like_btn_v3",
    "retweet_count_attr": "data-retweetcount / data-totweet",
    "actor_qq_attr": "data-uin",
    "original_qq_attr": "data-origuin",
}


def 规范化文本(value: Any) -> str:
    text = str(value or "")
    return " ".join(text.split()).strip()


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


def 标准化关键词列表(values: list[str] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in values:
        keyword = str(raw or "").strip()
        if not keyword:
            continue
        lower = keyword.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(keyword)
    return normalized


def 从空间网址提取QQ号(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""

    match = re.search(r"user\.qzone\.qq\.com/(\d+)", text)
    if match:
        return match.group(1)
    return ""


def 判断动态类别(actor_uin: str, original_uin: str, self_uin: str) -> str:
    actor = actor_uin.strip()
    original = original_uin.strip()
    current = self_uin.strip()

    if not actor and not original:
        return "unknown"
    if actor == "0" and original == "0":
        return "ad"
    if not original:
        if actor == current and current:
            return "self_original"
        return "other_original"
    if actor == original:
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
  const likeButton = item.querySelector(".qz_like_btn_v3");
  const foot = item.querySelector(".f-single-foot");
  const infoDetail = item.querySelector(".info-detail");
  const uniqueClasses = Array.from(
    new Set(
      Array.from(item.querySelectorAll("*"))
        .map((el) => typeof el.className === "string" ? el.className.trim() : "")
        .filter(Boolean)
    )
  ).slice(0, 80);

  return {
    index,
    card_id: item.id || "",
    card_classes: item.className || "",
    is_ad: item.classList.contains("f-single-biz"),
    actor_name: actorLink ? (actorLink.textContent || "").trim() : "",
    actor_href: actorLink ? (actorLink.getAttribute("href") || "") : "",
    original_author_name: originalAuthorLink ? (originalAuthorLink.textContent || "").trim() : "",
    original_author_href: originalAuthorLink ? (originalAuthorLink.getAttribute("href") || "") : "",
    actor_uin: feedData ? (feedData.getAttribute("data-uin") || "") : "",
    original_uin: feedData ? (feedData.getAttribute("data-origuin") || "") : "",
    tid: feedData ? (feedData.getAttribute("data-tid") || "") : "",
    original_tid: feedData ? (feedData.getAttribute("data-origtid") || "") : "",
    feed_type: feedData ? (feedData.getAttribute("data-feedstype") || "") : "",
    retweet_count_raw: feedData ? (feedData.getAttribute("data-retweetcount") || "") : "",
    total_tweet_raw: feedData ? (feedData.getAttribute("data-totweet") || "") : "",
    like_count_raw: likeButton ? (likeButton.getAttribute("data-likecnt") || "") : "",
    like_show_count_raw: likeButton ? (likeButton.getAttribute("data-showcount") || "") : "",
    own_content_text: ownContent ? (ownContent.textContent || "").trim() : "",
    forward_content_text: forwardContent ? (forwardContent.textContent || "").trim() : "",
    full_text: (item.innerText || "").replace(/\\s+/g, " ").trim(),
    foot_text: foot ? (foot.innerText || "").replace(/\\s+/g, " ").trim() : "",
    time_text: infoDetail ? (infoDetail.textContent || "").replace(/\\s+/g, " ").trim() : "",
    has_forward_button: Boolean(forwardButton),
    forward_button_cmd: forwardButton ? (forwardButton.getAttribute("data-cmd") || "") : "",
    forward_button_type: forwardButton ? (forwardButton.getAttribute("data-type") || "") : "",
    forward_button_html: forwardButton ? forwardButton.outerHTML : "",
    like_button_html: likeButton ? likeButton.outerHTML : "",
    element_classes: uniqueClasses,
    element_presence: {
      feed_data: Boolean(feedData),
      actor_name: Boolean(actorLink),
      original_author: Boolean(originalAuthorLink),
      own_content: Boolean(ownContent),
      forward_content: Boolean(forwardContent),
      forward_button: Boolean(forwardButton),
      like_button: Boolean(likeButton),
      comments_box: Boolean(item.querySelector(".mod-comments")),
    },
  };
});
"""
    result = driver.execute_script(script, limit, 动态卡片选择器)
    return result if isinstance(result, list) else []


def 丰富动态信息(
    raw_cards: list[dict[str, Any]],
    self_uin: str,
    related_qq: str,
    min_retweet_count: int,
    forwarded_only: bool,
    keywords: list[str],
) -> list[dict[str, Any]]:
    normalized_self = self_uin.strip()
    normalized_related_qq = related_qq.strip()
    normalized_keywords = 标准化关键词列表(keywords)

    enriched: list[dict[str, Any]] = []
    for raw in raw_cards:
        actor_uin = str(raw.get("actor_uin") or "").strip()
        original_uin = str(raw.get("original_uin") or "").strip()
        retweet_count = max(
            安全整数(raw.get("retweet_count_raw")),
            安全整数(raw.get("total_tweet_raw")),
        )
        like_count = 安全整数(raw.get("like_count_raw"))
        like_show_count = 安全整数(raw.get("like_show_count_raw"))
        own_content = 规范化文本(raw.get("own_content_text"))
        forward_content = 规范化文本(raw.get("forward_content_text"))
        full_text = 规范化文本(raw.get("full_text"))
        content_text = own_content or forward_content or full_text
        lower_content = content_text.lower()
        matched_keywords = [keyword for keyword in normalized_keywords if keyword.lower() in lower_content]
        card_type = 判断动态类别(actor_uin, original_uin, normalized_self)
        is_forwarded = bool(actor_uin and original_uin and actor_uin != original_uin)

        qq_matched = True
        if normalized_related_qq:
            qq_matched = normalized_related_qq in {actor_uin, original_uin}

        forwarded_matched = (not forwarded_only) or is_forwarded
        retweet_matched = retweet_count >= min_retweet_count
        keyword_matched = (not normalized_keywords) or bool(matched_keywords)

        item = dict(raw)
        item.update(
            {
                "card_type": card_type,
                "is_forwarded_feed": is_forwarded,
                "is_self_actor": bool(normalized_self and actor_uin == normalized_self),
                "is_self_original": bool(normalized_self and original_uin == normalized_self),
                "retweet_count": retweet_count,
                "like_count": like_count,
                "like_show_count": like_show_count,
                "content_text": content_text,
                "matched_keywords": matched_keywords,
                "filters": {
                    "related_qq": normalized_related_qq or None,
                    "min_retweet_count": min_retweet_count,
                    "forwarded_only": forwarded_only,
                    "keywords": normalized_keywords,
                    "qq_matched": qq_matched,
                    "retweet_matched": retweet_matched,
                    "forwarded_matched": forwarded_matched,
                    "keyword_matched": keyword_matched,
                    "all_matched": qq_matched and retweet_matched and forwarded_matched and keyword_matched,
                },
            }
        )
        enriched.append(item)

    return enriched


def 构建探测报告(
    cards: list[dict[str, Any]],
    self_uin: str,
    related_qq: str,
    min_retweet_count: int,
    forwarded_only: bool,
    keywords: list[str],
    page_state: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "total_cards": len(cards),
        "ad_cards": sum(1 for item in cards if item.get("is_ad")),
        "forwarded_cards": sum(1 for item in cards if item.get("is_forwarded_feed")),
        "matched_cards": sum(1 for item in cards if item.get("filters", {}).get("all_matched")),
        "self_original_cards": sum(1 for item in cards if item.get("card_type") == "self_original"),
        "self_forwarded_cards": sum(1 for item in cards if item.get("card_type") == "self_forwarded_other"),
        "other_original_cards": sum(1 for item in cards if item.get("card_type") == "other_original"),
        "other_forwarded_other_cards": sum(1 for item in cards if item.get("card_type") == "other_forwarded_other"),
        "other_forwarded_self_cards": sum(1 for item in cards if item.get("card_type") == "other_forwarded_self"),
    }
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "self_qq": self_uin.strip() or None,
        "filters": {
            "related_qq": related_qq.strip() or None,
            "min_retweet_count": min_retweet_count,
            "forwarded_only": forwarded_only,
            "keywords": 标准化关键词列表(keywords),
        },
        "page_state": page_state,
        "element_hints": 动态元素说明,
        "summary": summary,
        "cards": cards,
    }


def 打印探测摘要(report: dict[str, Any]) -> None:
    summary = report.get("summary", {})
    page_state = report.get("page_state", {})
    print("动态探测完成")
    print(
        f"页签={page_state.get('tab_text') or '未知'}，"
        f"动态={page_state.get('dynamic_count', 0)}，"
        f"可点赞={page_state.get('praise_count', 0)}"
    )
    print(
        f"总卡片 {summary.get('total_cards', 0)}，"
        f"广告 {summary.get('ad_cards', 0)}，"
        f"疑似转发 {summary.get('forwarded_cards', 0)}，"
        f"命中过滤条件 {summary.get('matched_cards', 0)}"
    )

    matched_cards = [item for item in report.get("cards", []) if item.get("filters", {}).get("all_matched")]
    preview_cards = matched_cards if matched_cards else report.get("cards", [])[:5]
    if not preview_cards:
        print("当前页面没有读取到动态卡片。")
        return

    print("\n样本卡片")
    for item in preview_cards[:5]:
        print(
            f"- #{item.get('index', 0) + 1} "
            f"{item.get('actor_name') or '(未知作者)'} "
            f"[{item.get('card_type')}] "
            f"actor={item.get('actor_uin') or '-'} "
            f"orig={item.get('original_uin') or '-'} "
            f"retweet={item.get('retweet_count', 0)} "
            f"keywords={','.join(item.get('matched_keywords', [])) or '-'}"
        )
        print(f"  内容: {规范化文本(item.get('content_text'))[:120] or '(空)'}")


def 保存探测报告(report: dict[str, Any]) -> Path:
    动态探测目录.mkdir(parents=True, exist_ok=True)
    path = 动态探测目录 / f"feed_probe_{now_timestamp()}.json"
    save_json(report, str(path))
    return path


def 构建参数解析器() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="探测 QQ 空间动态列表元素，只做识别，不执行转发。")
    parser.add_argument("--debugger-address", help="覆盖 Chrome 远程调试地址，例如 127.0.0.1:9222")
    parser.add_argument("--driver-path", help="覆盖 ChromeDriver 路径；未提供时允许回退 Selenium Manager")
    parser.add_argument("--startup-wait-seconds", type=float, default=1.0, help="连接浏览器后的等待秒数")
    parser.add_argument("--limit", type=int, default=20, help="最多探测多少条动态卡片，默认 20")
    parser.add_argument("--related-qq", default="", help="只标记 actor/original QQ 命中的动态")
    parser.add_argument("--min-retweet-count", type=int, default=0, help="只标记转发数不低于该值的动态")
    parser.add_argument("--forwarded-only", action="store_true", help="只标记疑似“转发别人的动态”")
    parser.add_argument("--keyword", action="append", default=[], help="按关键词匹配动态正文，可重复传入")
    parser.add_argument("--no-save", action="store_true", help="只打印摘要，不落地 JSON 报告")
    return parser


def 主程序(argv: list[str] | None = None) -> int:
    parser = 构建参数解析器()
    args = parser.parse_args(argv)

    config = 应用持久化配置(CONFIG)
    if args.debugger_address:
        config.browser.debugger_address = args.debugger_address
    if args.driver_path:
        config.browser.driver_path = args.driver_path

    try:
        driver = 连接浏览器(config.browser)
        time.sleep(max(args.startup_wait_seconds, 0.0))
        尝试恢复点赞流(driver, config, require_praise=False)
        current_url = str(driver.current_url or "").strip()
        self_uin = str(QQ_NUMBER or "").strip() or 从空间网址提取QQ号(current_url)
        page_state_obj = 获取动态流状态(driver, config.selectors)
        raw_cards = 提取动态卡片(driver, max(args.limit, 0))
        cards = 丰富动态信息(
            raw_cards=raw_cards,
            self_uin=self_uin,
            related_qq=args.related_qq,
            min_retweet_count=max(args.min_retweet_count, 0),
            forwarded_only=args.forwarded_only,
            keywords=args.keyword,
        )
        report = 构建探测报告(
            cards=cards,
            self_uin=self_uin,
            related_qq=args.related_qq,
            min_retweet_count=max(args.min_retweet_count, 0),
            forwarded_only=args.forwarded_only,
            keywords=args.keyword,
            page_state={
                "current_url": current_url,
                "tab_text": page_state_obj.tab_text,
                "dynamic_count": page_state_obj.dynamic_count,
                "praise_count": page_state_obj.praise_count,
            },
        )
        打印探测摘要(report)

        if args.no_save:
            print("已按参数跳过 JSON 落盘。")
            return 0

        saved_path = 保存探测报告(report)
        print(f"探测报告已保存: {saved_path}")
        return 0
    except Exception as exc:
        print(f"动态探测失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(主程序())
