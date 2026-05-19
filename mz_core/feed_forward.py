from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests
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
    from mz_core.friend_storage import (
        LATEST_SNAPSHOT_PATH,
        load_json,
        now_iso,
        now_timestamp,
        save_json,
        snapshot_to_map,
    )
    from mz_core.runtime_cleanup import prune_auto_forward_history
except ModuleNotFoundError:
    from friend_storage import (
        LATEST_SNAPSHOT_PATH,
        load_json,
        now_iso,
        now_timestamp,
        save_json,
        snapshot_to_map,
    )
    from runtime_cleanup import prune_auto_forward_history


动态卡片选择器 = "li.f-single.f-s-s"
转发弹层选择器 = ".qz_dialog_layer"
转发输入框选择器 = ".qz_dialog_layer .retweet_bd_box [contenteditable='true'].textinput.textarea.c_tx2"
转发发送按钮选择器 = ".qz_dialog_layer .retweet_op input.gb_bt.gb_bt2[value='发送']"
转发关闭按钮选择器 = ".qz_dialog_layer .qz_dialog_btn_close"
自动转发目录 = RUN_LOG_DIR / "auto_forward"
转发状态路径 = 自动转发目录 / "forward_state.json"
转发日志目录 = 自动转发目录 / "history"
转发文案汇总路径 = 自动转发目录 / "forward_append_text_log.txt"
转发理由汇总路径 = 自动转发目录 / "forward_reason_log.txt"
最新好友快照路径 = Path(str(LATEST_SNAPSHOT_PATH))
_EMOJI_BASE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2300, 0x23FF),
)
_好友备注缓存修改时间: float | None = None
_好友备注缓存: dict[str, str] = {}


def 规范化文本(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def 字符属于表情基字符(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in _EMOJI_BASE_RANGES)


def 备注名末尾带有表情(remark: Any) -> bool:
    text = str(remark or "").rstrip()
    if not text:
        return False

    saw_keycap = False
    for char in reversed(text):
        codepoint = ord(char)
        if char.isspace():
            continue
        if codepoint == 0x20E3:
            saw_keycap = True
            continue
        if codepoint in {0x200D, 0xFE0E, 0xFE0F}:
            continue
        if 0x1F3FB <= codepoint <= 0x1F3FF:
            continue
        if 0xE0020 <= codepoint <= 0xE007F:
            continue
        if saw_keycap and char in "#*0123456789":
            return True
        return 字符属于表情基字符(char)
    return False


def 载入好友备注映射() -> dict[str, str]:
    global _好友备注缓存修改时间, _好友备注缓存

    if not 最新好友快照路径.is_file():
        _好友备注缓存修改时间 = None
        _好友备注缓存 = {}
        return {}

    try:
        modified_time = 最新好友快照路径.stat().st_mtime
    except OSError:
        _好友备注缓存修改时间 = None
        _好友备注缓存 = {}
        return {}

    if _好友备注缓存修改时间 == modified_time:
        return dict(_好友备注缓存)

    try:
        snapshot = load_json(str(最新好友快照路径), default=None)
        friend_map = snapshot_to_map(snapshot)
    except Exception:
        return {}

    result: dict[str, str] = {}
    for uin, friend in friend_map.items():
        normalized_uin = str(uin or "").strip()
        if not normalized_uin:
            continue
        result[normalized_uin] = str(friend.get("remark") or "").strip()
    _好友备注缓存修改时间 = modified_time
    _好友备注缓存 = result
    return dict(result)


def 标准化关键词列表(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values)

    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        for item in re.split(r"[\r\n,，;；、]+", text):
            keyword = item.strip()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            result.append(keyword)
    return result


def 匹配关键词(content_text: str, keyword_pairs: list[tuple[str, str]]) -> str:
    content_text_lower = 规范化文本(content_text).lower()
    if not content_text_lower:
        return ""
    for raw_keyword, keyword_lower in keyword_pairs:
        if keyword_lower in content_text_lower:
            return raw_keyword
    return ""


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
        quoted_original_text = forward_content if is_forwarded_feed else ""
        content_text = own_content or quoted_original_text or 规范化文本(raw.get("full_text"))
        analysis_parts: list[str] = []
        if own_content:
            analysis_parts.append(f"转发者补充：{own_content}")
        if quoted_original_text:
            analysis_parts.append(f"引用原帖：{quoted_original_text}")
        analysis_text = " ".join(analysis_parts).strip() or content_text

        item = dict(raw)
        item.update(
            {
                "card_type": card_type,
                "is_forwarded_feed": is_forwarded_feed,
                "own_content_text": own_content,
                "quoted_original_text": quoted_original_text,
                "content_text": content_text,
                "analysis_text": analysis_text,
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
        "own_content_text": item.get("own_content_text"),
        "quoted_original_text": item.get("quoted_original_text"),
        "matched_keyword": item.get("matched_keyword"),
        "append_text": append_text,
        "retweet_count": item.get("retweet_count"),
    }
    log_path = 转发日志目录 / f"forward_{now_timestamp()}.json"
    save_json(record, str(log_path))
    prune_auto_forward_history(Path(RUN_LOG_DIR))


def 记录转发文案(append_text: str, item: dict[str, Any]) -> None:
    自动转发目录.mkdir(parents=True, exist_ok=True)
    actor_name = str(item.get("actor_name") or "").strip() or "(未知作者)"
    actor_uin = str(item.get("actor_uin") or "").strip() or "-"
    content_text = 规范化文本(item.get("content_text")) or "(空)"
    own_content_text = 规范化文本(item.get("own_content_text")) or "(空)"
    quoted_original_text = 规范化文本(item.get("quoted_original_text")) or "(空)"
    text = str(append_text or "").strip() or "(空)"
    record = (
        "===== 转发样本开始 =====\n"
        f"记录时间：{now_iso()}\n"
        f"作者：{actor_name}\n"
        f"作者QQ：{actor_uin}\n"
        f"动态内容：{content_text}\n"
        f"转发者补充：{own_content_text}\n"
        f"引用原帖：{quoted_original_text}\n"
        f"转发文案：{text}\n"
        "\n"
        "人工判断（请手动填写）：\n"
        "是否应该转发：\n"
        "误转发 / 正确转发：\n"
        "原因：\n"
        "可训练摘要：\n"
        "\n"
        "===== 转发样本结束 =====\n\n"
    )
    with 转发文案汇总路径.open("a", encoding="utf-8") as fh:
        fh.write(record)


def 记录模型转发理由(item: dict[str, Any]) -> None:
    if not item:
        return

    自动转发目录.mkdir(parents=True, exist_ok=True)
    with 转发理由汇总路径.open("a", encoding="utf-8") as fh:
        actor_name = str(item.get("actor_name") or "").strip() or "(未知作者)"
        actor_uin = str(item.get("actor_uin") or "").strip() or "-"
        own_content_text = 规范化文本(item.get("own_content_text")) or "(空)"
        quoted_original_text = 规范化文本(item.get("quoted_original_text")) or "(空)"
        content_text = 规范化文本(item.get("content_text")) or "(空)"
        model_reason = str(item.get("model_reason") or "").strip() or "(空)"
        record = (
            "===== 模型理由开始 =====\n"
            f"记录时间：{now_iso()}\n"
            f"作者：{actor_name}\n"
            f"作者QQ：{actor_uin}\n"
            f"动态类别：{item.get('card_type') or '-'}\n"
            f"是否二次转发：{'是' if item.get('is_forwarded_feed') else '否'}\n"
            f"动态内容：{content_text}\n"
            f"转发者补充：{own_content_text}\n"
            f"引用原帖：{quoted_original_text}\n"
            "模型判定应否转发：是\n"
            f"模型理由：{model_reason}\n"
            "===== 模型理由结束 =====\n\n"
        )
        fh.write(record)


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
    记录转发文案(append_text, item)
    if str(item.get("model_reason") or "").strip():
        记录模型转发理由(item)


def 筛选候选动态(
    cards: list[dict[str, Any]],
    keyword: str,
    include_forwarded_feeds: bool,
    state: dict[str, Any],
    allow_text_candidates: bool = False,
    only_remark_suffix_emoji: bool = False,
    friend_remarks: dict[str, str] | None = None,
    filter_stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    normalized_keywords = 标准化关键词列表(keyword)
    keyword_pairs = [(item, item.lower()) for item in normalized_keywords]
    result: list[dict[str, Any]] = []
    remarks = friend_remarks or {}
    stats = filter_stats if filter_stats is not None else {}

    for item in cards:
        actor_uin = str(item.get("actor_uin") or "").strip()
        content_text = 规范化文本(item.get("analysis_text") or item.get("content_text"))
        actor_remark = remarks.get(actor_uin, "").strip()
        if item.get("is_ad"):
            continue
        if not item.get("has_forward_button"):
            continue
        if not actor_uin:
            continue
        if only_remark_suffix_emoji and not 备注名末尾带有表情(actor_remark):
            stats["skipped_by_remark_suffix_emoji"] = stats.get("skipped_by_remark_suffix_emoji", 0) + 1
            continue
        if not include_forwarded_feeds and item.get("is_forwarded_feed"):
            continue
        blocked_keyword = 匹配关键词(content_text, keyword_pairs)
        if blocked_keyword:
            continue
        if allow_text_candidates and not content_text:
            continue
        if 已经转发过(state, item):
            item = dict(item)
            item["matched_keyword"] = item.get("matched_keyword")
            item["already_forwarded"] = True
            item["actor_remark"] = actor_remark
            result.append(item)
            continue
        if not allow_text_candidates:
            continue

        item = dict(item)
        item["matched_keyword"] = None
        item["already_forwarded"] = False
        item["actor_remark"] = actor_remark
        result.append(item)

    return result


def 提取JSON内容(text: str) -> Any:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    for left, right in (("[", "]"), ("{", "}")):
        start = raw.find(left)
        end = raw.rfind(right)
        if start != -1 and end > start:
            return json.loads(raw[start : end + 1])

    raise ValueError("模型返回内容不是有效 JSON")


def 标准化模型判断结果(payload: Any) -> dict[str, dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            rows = payload["results"]
        elif isinstance(payload.get("items"), list):
            rows = payload["items"]
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id") if "id" in row else row.get("index")
        item_id = str(raw_id if raw_id is not None else "").strip()
        if not item_id:
            continue
        raw_should_forward = row.get("should_forward")
        if isinstance(raw_should_forward, str):
            should_forward = raw_should_forward.strip().lower() in {"true", "1", "yes", "y", "是", "转发"}
        else:
            should_forward = bool(raw_should_forward)
        result[item_id] = {
            "should_forward": should_forward,
            "reason": str(row.get("reason") or "").strip(),
        }
    return result


def 标准化模型理由结果(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            rows = payload["results"]
        elif isinstance(payload.get("items"), list):
            rows = payload["items"]
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id") if "id" in row else row.get("index")
        item_id = str(raw_id if raw_id is not None else "").strip()
        if not item_id:
            continue
        result[item_id] = str(row.get("reason") or row.get("forward_reason") or "").strip()
    return result


def 请求本地模型JSON(
    endpoint: str,
    model_name: str,
    timeout_seconds: float,
    system_prompt: str,
    user_payload: dict[str, Any],
) -> Any:
    endpoint = str(endpoint or "").strip()
    model_name = str(model_name or "").strip()
    if not endpoint or not model_name:
        raise ValueError("本地模型接口地址或模型名称为空")

    session = requests.Session()
    session.trust_env = False
    response = session.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        json={
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0,
            "stream": False,
        },
        timeout=max(float(timeout_seconds), 1.0),
    )
    response.raise_for_status()
    data = response.json()
    content = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if choices and isinstance(choices, list):
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        else:
            content = str(choices[0].get("text") or "")

    return 提取JSON内容(content)


def 调用本地模型判断转发(
    items: list[dict[str, Any]],
    keyword: str,
    endpoint: str,
    model_name: str,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    if not items:
        return {}

    prompt_items = []
    for index, item in enumerate(items):
        quoted_original_text = 规范化文本(item.get("quoted_original_text"))
        prompt_items.append(
            {
                "id": str(index),
                "author": item.get("actor_name") or "",
                "actor_uin": item.get("actor_uin") or "",
                "card_type": item.get("card_type") or "",
                "is_forwarded_feed": bool(item.get("is_forwarded_feed")),
                "text": 规范化文本(item.get("content_text"))[:1000],
                "actor_comment_text": 规范化文本(item.get("own_content_text"))[:1000],
                "quoted_original_text": quoted_original_text[:1600],
            }
        )

    rule_text = 规范化文本(keyword) or "无固定关键词限制"
    system_prompt = (
        "你是 QQ 空间自动转发判断器。任务是判断这条动态本身现在是否值得帮作者转发。"
        "只能依据当前动态可见文本，不脑补上下文，不把作者昵称、语气词、单个关键词当作充分条件。"
        "如果 is_forwarded_feed=true，必须同时判断转发者补充的话和 quoted_original_text 里的引用原帖内容，不能因为看见二次转发就直接选 true。"
        "二次转发时，如果转发者只说了“帮扩”“捞捞”之类短句，但引用原帖本身不在请求转发当前动态，就选 false。"
        "二次转发时，如果引用原帖本身明确请求转发，或本身就是完整扩条/扩列文，可以选 true。"
        "判断为 should_forward=true 的典型情况："
        "1. 正文明确请求读者转发、帮转、扩散、传播、互转、求扩、帮扩、劳烦转发当前这条动态。"
        "2. 典型扩条/扩列/扩友文，正文是在介绍自己、属性、圈子、地区、喜好、联系方式或招募同好，明显希望被扩散。"
        "3. 抽奖、活动、瓜条、作品发布等内容里，正文明确要求转发当前这条。"
        "判断为 should_forward=false 的典型情况："
        "1. 普通日常、闲聊、吐槽、提问、晒图、发图、发相册、单纯说废话。"
        "2. 只出现了“求”“发一下”“转发”“扩列”等词，但并不是请求别人转发当前动态。"
        "3. 感谢已经帮忙转发的人、通知兑奖、说明后续、提醒先别删、复盘活动结果，这些不是在请求转发当前动态。"
        "4. 提到别人“求帮忙”“求转发”是在吐槽或转述，不代表作者自己在求转发。"
        "5. 只有一句像“什么时候有人带我扩列呢”“传一下相册”“帮我选选呗”这类内容，没有扩条应有的自我介绍或明确求转发意图，不转发。"
        "重要边界："
        "1. “扩列/扩友/扩同好/二刷扩扩/捞我扩”等词只有在明显是扩条或求扩散时才转发；单纯一句口头感叹不转发。"
        "2. “再次拜托转发”“劳烦看到的大人们转发”“帮我转一下”这类直接请求，应转发。"
        "3. “帮我转发瓜条的小宝宝们兑奖”“谢谢你们转发过”这类面向已转发人群的感谢或通知，不转发。"
        "输出必须是 JSON 数组，每个对象包含 id、should_forward、reason；不要输出 Markdown，不要解释规则。"
    )
    user_prompt = {
        "用户配置的屏蔽关键词参考": rule_text,
        "判定提示": [
            "只有当前动态本身在求转发、求扩散，或明显是需要扩散的扩条时才选 true。",
            "不要因为出现单个触发词就选 true，要结合整句语义判断是不是在请求转发当前动态。",
            "reason 用一句短中文说明核心依据。",
        ],
        "参考样例": [
            {
                "text": "捞一波 姐姐们快帮我转一下",
                "should_forward": True,
                "reason": "明确请求帮忙转发当前动态",
            },
            {
                "text": "扩列 cn.xxx 男 07学生 喜欢xxx",
                "should_forward": True,
                "reason": "典型扩条，自我介绍完整，明显希望被扩散",
            },
            {
                "text": "什么时候有热机带我扩列呢",
                "should_forward": False,
                "reason": "只是闲聊感叹，不是扩条，也没请求转发",
            },
            {
                "text": "帮我转发瓜条的小宝宝们带着截图兑奖呀，谢谢你们",
                "should_forward": False,
                "reason": "是在感谢和通知已转发人群，不是在请求转发当前动态",
            },
            {
                "text": "传一下相册",
                "should_forward": False,
                "reason": "普通发图说明，没有求转发意图",
            },
        ],
        "返回格式": [
            {"id": "0", "should_forward": True, "reason": "简短原因"},
            {"id": "1", "should_forward": False, "reason": "简短原因"},
        ],
        "待判断动态": prompt_items,
    }
    parsed = 请求本地模型JSON(
        endpoint=endpoint,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
        user_payload=user_prompt,
    )
    return 标准化模型判断结果(parsed)


def 调用本地模型生成转发理由(
    items: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    endpoint: str,
    model_name: str,
    timeout_seconds: float,
) -> dict[str, str]:
    if not items:
        return {}

    prompt_items = []
    for index, item in enumerate(items):
        decision = decisions.get(str(index), {})
        prompt_items.append(
            {
                "id": str(index),
                "author": item.get("actor_name") or "",
                "card_type": item.get("card_type") or "",
                "is_forwarded_feed": bool(item.get("is_forwarded_feed")),
                "text": 规范化文本(item.get("content_text"))[:1000],
                "actor_comment_text": 规范化文本(item.get("own_content_text"))[:1000],
                "quoted_original_text": 规范化文本(item.get("quoted_original_text"))[:1600],
                "should_forward": bool(decision.get("should_forward")),
                "decision_reason": str(decision.get("reason") or "").strip(),
            }
        )

    system_prompt = (
        "你是 QQ 空间自动转发复盘器。"
        "任务不是重新判定，而是基于给定 should_forward 结果，为每条动态写一句便于后续优化规则的中文理由。"
        "如果 is_forwarded_feed=true，理由必须明确说明依据主要来自转发者补充的话、引用原帖，还是两者都不足。"
        "理由要短，但要指出关键误判边界，例如感谢通知、闲聊感叹、原帖才是真正求扩、原帖并未求转发等。"
        "输出必须是 JSON 数组，每个对象包含 id、reason；不要输出 Markdown。"
    )
    user_payload = {
        "返回格式": [
            {"id": "0", "reason": "一句短中文理由"},
            {"id": "1", "reason": "一句短中文理由"},
        ],
        "待生成理由的动态": prompt_items,
    }
    parsed = 请求本地模型JSON(
        endpoint=endpoint,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
        system_prompt=system_prompt,
        user_payload=user_payload,
    )
    return 标准化模型理由结果(parsed)


def 用本地模型筛选候选动态(
    candidates: list[dict[str, Any]],
    keyword: str,
    endpoint: str,
    model_name: str,
    timeout_seconds: float,
    reason_model_endpoint: str,
    reason_model_name: str,
    reason_model_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actionable = [item for item in candidates if not item.get("already_forwarded")]
    stats = {
        "model_checked": len(actionable),
        "model_selected": 0,
        "model_errors": 0,
        "model_error": "",
        "reason_model_checked": 0,
        "reason_model_errors": 0,
        "reason_model_error": "",
    }
    if not actionable:
        return candidates, stats

    try:
        decisions = 调用本地模型判断转发(
            actionable,
            keyword=keyword,
            endpoint=endpoint,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        stats["model_errors"] = 1
        stats["model_error"] = str(exc)
        return [item for item in candidates if item.get("already_forwarded")], stats

    for index, item in enumerate(actionable):
        item["model_item_id"] = str(index)
        decision = decisions.get(str(index), {})
        item["decision_should_forward"] = bool(decision.get("should_forward"))
        item["decision_reason"] = str(decision.get("reason") or "").strip()

    selected_actionable = [item for item in actionable if item.get("decision_should_forward")]
    stats["reason_model_checked"] = len(selected_actionable)

    try:
        reason_results = 调用本地模型生成转发理由(
            selected_actionable,
            decisions=decisions,
            endpoint=reason_model_endpoint,
            model_name=reason_model_name,
            timeout_seconds=reason_model_timeout_seconds,
        )
    except Exception as exc:
        stats["reason_model_errors"] = 1
        stats["reason_model_error"] = str(exc)
        reason_results = {}

    for item in selected_actionable:
        item_id = str(item.get("model_item_id") or "").strip()
        fallback_reason = str(item.get("decision_reason") or "").strip()
        reason_text = str(reason_results.get(item_id) or "").strip() or fallback_reason
        item["model_reason"] = reason_text

    selected: list[dict[str, Any]] = [item for item in candidates if item.get("already_forwarded")]
    for index, item in enumerate(actionable):
        decision = decisions.get(str(index), {})
        if not decision.get("should_forward"):
            continue
        selected_item = dict(item)
        selected_item["matched_keyword"] = "本地模型"
        selected_item["model_should_forward"] = True
        selected_item["model_reason"] = item.get("model_reason") or str(decision.get("reason") or "").strip()
        selected.append(selected_item)

    stats["model_selected"] = len(selected) - sum(1 for item in selected if item.get("already_forwarded"))
    return selected, stats


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


def 等待转发输入框可用(driver, timeout_seconds: float = 5.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for element in driver.find_elements(By.CSS_SELECTOR, 转发输入框选择器):
            try:
                if element.is_displayed() and element.is_enabled():
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
    if 等待转发弹层出现(driver) is None:
        return False
    return 等待转发输入框可用(driver) is not None


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
    keyword: str,
    append_text: str,
    include_forwarded_feeds: bool = False,
    model_enabled: bool = False,
    model_endpoint: str = "http://127.0.0.1:1234/v1/chat/completions",
    model_name: str = "openai/gpt-oss-20b",
    model_timeout_seconds: float = 60.0,
    reason_model_endpoint: str = "http://127.0.0.1:1234/v1/chat/completions",
    reason_model_name: str = "openai/gpt-oss-20b",
    reason_model_timeout_seconds: float = 60.0,
    only_remark_suffix_emoji: bool = False,
    self_uin: str = "",
    scan_limit: int = 30,
    max_forwards: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
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
        "model_enabled": bool(model_enabled),
        "model_checked": 0,
        "model_selected": 0,
        "model_errors": 0,
        "model_error": "",
        "reason_model_checked": 0,
        "reason_model_errors": 0,
        "reason_model_error": "",
        "remark_emoji_filter_enabled": bool(only_remark_suffix_emoji),
        "remark_snapshot_size": 0,
        "skipped_by_remark_suffix_emoji": 0,
        "consecutive_forward_failures": 0,
        "forwarding_stopped_after_failures": False,
    }

    current_self_uin = str(self_uin or "").strip() or str(QQ_NUMBER or "").strip() or 从空间网址提取QQ号(driver.current_url)
    raw_cards = 提取动态卡片(driver, max(scan_limit, 0))
    cards = 丰富动态信息(raw_cards, current_self_uin)
    state = 载入转发状态()
    friend_remarks = 载入好友备注映射() if only_remark_suffix_emoji else {}
    stats["remark_snapshot_size"] = len(friend_remarks)
    filter_stats = {"skipped_by_remark_suffix_emoji": 0}
    candidates = 筛选候选动态(
        cards,
        keyword,
        include_forwarded_feeds,
        state,
        allow_text_candidates=bool(model_enabled),
        only_remark_suffix_emoji=bool(only_remark_suffix_emoji),
        friend_remarks=friend_remarks,
        filter_stats=filter_stats,
    )
    stats.update(filter_stats)
    if model_enabled:
        candidates, model_stats = 用本地模型筛选候选动态(
            candidates,
            keyword=keyword,
            endpoint=model_endpoint,
            model_name=model_name,
            timeout_seconds=model_timeout_seconds,
            reason_model_endpoint=reason_model_endpoint,
            reason_model_name=reason_model_name,
            reason_model_timeout_seconds=reason_model_timeout_seconds,
        )
        stats.update(model_stats)
        stats["errors"] += int(model_stats.get("model_errors", 0) or 0)
        stats["errors"] += int(model_stats.get("reason_model_errors", 0) or 0)

    stats["scanned"] = len(cards)
    stats["matched"] = len(candidates)
    stats["already_forwarded"] = sum(1 for item in candidates if item.get("already_forwarded"))
    stats["preview"] = [
        {
            "actor_name": item.get("actor_name"),
            "actor_uin": item.get("actor_uin"),
            "actor_remark": item.get("actor_remark"),
            "content_text": 规范化文本(item.get("content_text"))[:120],
            "quoted_original_text": 规范化文本(item.get("quoted_original_text"))[:120],
            "card_type": item.get("card_type"),
            "matched_keyword": item.get("matched_keyword"),
            "model_reason": item.get("model_reason"),
            "already_forwarded": bool(item.get("already_forwarded")),
        }
        for item in candidates[:5]
    ]

    actionable = [item for item in candidates if not item.get("already_forwarded")]
    if dry_run:
        return stats

    consecutive_forward_failures = 0
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
            consecutive_forward_failures = 0
            stats["consecutive_forward_failures"] = 0
            time.sleep(1.0)
        except Exception:
            stats["errors"] += 1
            consecutive_forward_failures += 1
            stats["consecutive_forward_failures"] = consecutive_forward_failures
            关闭转发弹层(driver)
            if consecutive_forward_failures >= 2:
                stats["forwarding_stopped_after_failures"] = True
                break
            continue

    return stats


def 构建参数解析器() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按关键词自动转发当前列表中的动态。")
    parser.add_argument("--keyword", default="转发", help="屏蔽关键词，支持换行/逗号/顿号分隔；命中这些关键词时直接不转发")
    parser.add_argument("--append-text", default="测试内容", help="转发时附加的文案")
    parser.add_argument("--include-forwarded-feeds", action="store_true", help="允许转发列表里本身就是转发的动态")
    parser.add_argument("--only-remark-suffix-emoji", action="store_true", help="仅转发备注名最后带 emoji 表情的好友动态")
    parser.add_argument("--use-local-model", action="store_true", help="用本地模型判断所有含文字候选动态是否转发")
    parser.add_argument("--model-endpoint", default="http://127.0.0.1:1234/v1/chat/completions", help="本地 OpenAI 兼容接口地址")
    parser.add_argument("--model-name", default="openai/gpt-oss-20b", help="本地模型名称")
    parser.add_argument("--model-timeout", type=float, default=60.0, help="本地模型请求超时秒数")
    parser.add_argument("--reason-model-endpoint", default="http://127.0.0.1:1234/v1/chat/completions", help="转发理由模型接口地址")
    parser.add_argument("--reason-model-name", default="openai/gpt-oss-20b", help="转发理由模型名称")
    parser.add_argument("--reason-model-timeout", type=float, default=60.0, help="转发理由模型请求超时秒数")
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
            keyword=args.keyword,
            append_text=args.append_text,
            include_forwarded_feeds=bool(args.include_forwarded_feeds),
            model_enabled=bool(args.use_local_model),
            model_endpoint=args.model_endpoint,
            model_name=args.model_name,
            model_timeout_seconds=max(args.model_timeout, 1.0),
            reason_model_endpoint=args.reason_model_endpoint,
            reason_model_name=args.reason_model_name,
            reason_model_timeout_seconds=max(args.reason_model_timeout, 1.0),
            only_remark_suffix_emoji=bool(args.only_remark_suffix_emoji),
            scan_limit=max(args.limit, 0),
            max_forwards=max(args.max_forwards, 0),
            dry_run=bool(args.dry_run),
        )
        print(
            f"自动转发扫描完成：扫描 {stats['scanned']}，命中 {stats['matched']}，"
            f"已转发 {stats['forwarded']}，已跳过 {stats['already_forwarded']}，错误 {stats['errors']}"
        )
        if stats.get("model_enabled"):
            print(
                "本地模型判断："
                f"送入 {stats.get('model_checked', 0)}，"
                f"选中 {stats.get('model_selected', 0)}，"
                f"错误 {stats.get('model_errors', 0)}"
            )
            if stats.get("model_error"):
                print(f"本地模型错误：{stats.get('model_error')}")
        if stats.get("remark_emoji_filter_enabled"):
            print(
                "备注尾表情限制："
                f"快照 {stats.get('remark_snapshot_size', 0)} 人 / "
                f"跳过 {stats.get('skipped_by_remark_suffix_emoji', 0)}"
            )
        for item in stats.get("preview", []):
            print(
                f"- {item.get('actor_name') or '(未知作者)'} "
                f"remark={item.get('actor_remark') or '-'} "
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
