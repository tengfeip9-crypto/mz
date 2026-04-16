from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

from project_config import (  # noqa: E402
    CHROMEDRIVER_PATH as DEFAULT_CHROMEDRIVER_PATH,
    DEBUGGER_ADDRESS,
    QQ_NUMBER,
    require_qq_number,
)

# ========= 配置 =========
CHROME_DRIVER_PATH = os.fspath(DEFAULT_CHROMEDRIVER_PATH)
内容 = "「白百合は死者への贈り物」「白百合是赠予逝者的礼物」自动说说 此条在mz在"
等待秒数 = 60
默认图片路径列表: list[str] = []
发表后删除 = True

# 可见范围:
# all / qq / part / self / blacklist
权限模式 = "blacklist"

# 仅在 part / blacklist 下使用
目标好友分组 = "2"
指定好友UIN列表: list[str] = []

权限映射 = {
    "all": 1,
    "qq": 4,
    "part": 16,
    "self": 64,
    "blacklist": 128,
}

图片上传回退地址 = ",".join(
    (
        "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image",
        "http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
    )
)


@dataclass(frozen=True)
class 已上传图片:
    albumid: str
    lloc: str
    sloc: str
    photo_type: int
    width: int
    height: int
    origin_uuid: str
    origin_width: int
    origin_height: int
    pre_url: str
    url: str
    origin_url: str

    def 生成_richval片段(self) -> str:
        return ",".join(
            [
                "",
                self.albumid,
                self.lloc,
                self.sloc,
                str(self.photo_type),
                str(self.height),
                str(self.width),
                self.origin_uuid,
                str(self.origin_height),
                str(self.origin_width),
            ]
        )

    def 生成_pic_bo片段(self) -> str:
        bos = [value for value in (提取_bo参数(self.pre_url), 提取_bo参数(self.url)) if value]
        return "\t".join(bos)


def 连接浏览器():
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
    service = Service(CHROME_DRIVER_PATH)
    return webdriver.Chrome(service=service, options=chrome_options)


def 获取cookie字典(driver):
    return {c["name"]: c["value"] for c in driver.get_cookies()}


def 获取_g_tk(cookie字典):
    p_skey = cookie字典.get("p_skey", "")
    h = 5381

    for c in p_skey:
        h += (h << 5) + ord(c)

    return h & 2147483647


def 获取_qzonetoken(driver) -> str:
    script = """
    return (
        window.g_qzonetoken
        || window.g_qzonetoken
        || (window.QZONE && QZONE.FP && QZONE.FP.getQzoneConfig && QZONE.FP.getQzoneConfig('g_qzonetoken'))
        || ''
    );
    """
    try:
        return str(driver.execute_script(script) or "")
    except Exception:
        return ""


def 新建会话():
    session = requests.Session()
    session.trust_env = False
    return session


def 解析回调脚本(text):
    text = text.strip()

    for marker in ("frameElement.callback(", "_Callback(", "callback("):
        index = text.rfind(marker)
        if index != -1:
            start = index + len(marker)
            end = text.rfind(")")
            if end > start:
                payload = text[start:end].strip().rstrip(";").strip()
                return json.loads(payload)

    match = re.search(r"^\s*(\{.*\})\s*$", text, re.S)
    if match is not None:
        return json.loads(match.group(1))

    raise RuntimeError(f"接口返回格式不符合预期: {text[:300]}")


def 获取feedversion(driver):
    script = """
    return window.QZONE && QZONE.FP && QZONE.FP.getFeedVersion
      ? QZONE.FP.getFeedVersion()
      : 1;
    """
    return driver.execute_script(script) or 1


def 获取好友列表数据(cookie字典, g_tk):
    session = 新建会话()
    headers = {
        "Referer": f"https://user.qzone.qq.com/{QQ_NUMBER}",
        "User-Agent": "Mozilla/5.0",
    }
    params = {
        "uin": QQ_NUMBER,
        "follow_flag": 0,
        "groupface_flag": 0,
        "fupdate": 1,
        "g_tk": g_tk,
    }
    url = "https://h5.qzone.qq.com/proxy/domain/r.qzone.qq.com/cgi-bin/tfriend/friend_show_qqfriends.cgi"

    resp = session.get(url, headers=headers, cookies=cookie字典, params=params, timeout=20)
    resp.raise_for_status()

    result = 解析回调脚本(resp.text)
    data = result.get("data", {})
    return data.get("items", []), data.get("gpnames", [])


def 查找分组名(group_list, group_id):
    for item in group_list:
        if str(item.get("gpid")) == str(group_id):
            return (item.get("gpname") or "").strip() or f"分组{group_id}"
    return f"分组{group_id}"


def 按分组获取好友uins(friend_items, group_id):
    group_id = str(group_id)
    uins = []

    for item in friend_items:
        uin = str(item.get("uin") or "").strip()
        if not uin or uin == QQ_NUMBER:
            continue

        item_group = str(item.get("groupid"))
        if group_id == "-1" or item_group == group_id:
            uins.append(uin)

    return uins


def 构建权限配置(driver):
    if 权限模式 not in 权限映射:
        raise RuntimeError(f"未识别的权限模式: {权限模式}")

    config = {
        "authorize_type": 权限模式,
        "ugc_right": 权限映射[权限模式],
        "feedversion": 获取feedversion(driver),
        "group_id": None,
        "group_text": None,
        "uins": [],
    }

    if 权限模式 not in {"part", "blacklist"}:
        return config

    if 指定好友UIN列表:
        config["uins"] = [str(uin).strip() for uin in 指定好友UIN列表 if str(uin).strip()]
        config["group_id"] = "custom"
        config["group_text"] = "自定义名单"
        if not config["uins"]:
            raise RuntimeError("指定好友UIN列表为空，无法设置权限")
        return config

    cookie字典 = 获取cookie字典(driver)
    g_tk = 获取_g_tk(cookie字典)
    friend_items, group_list = 获取好友列表数据(cookie字典, g_tk)
    uins = 按分组获取好友uins(friend_items, 目标好友分组)

    if not uins:
        raise RuntimeError(f"没有在分组 {目标好友分组} 中读取到好友，已停止发表")

    config["group_id"] = str(目标好友分组)
    config["group_text"] = 查找分组名(group_list, 目标好友分组)
    config["uins"] = uins
    return config


def 转整数(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def 提取_bo参数(url: str) -> str:
    if not url:
        return ""

    parsed = urlsplit(url)
    query = parsed.query or ""
    parsed_query = parse_qs(query, keep_blank_values=True)
    if "bo" in parsed_query and parsed_query["bo"]:
        return unquote(parsed_query["bo"][0])

    match = re.search(r"(?:[?&]|&amp;)bo=([^&#]+)", url)
    if match is not None:
        return unquote(match.group(1))

    return ""


def 标准化图片路径列表(image_paths: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw_path in image_paths or []:
        path = os.path.abspath(os.path.expanduser(str(raw_path).strip()))
        if not path:
            continue
        if path in seen:
            continue
        if not os.path.isfile(path):
            raise RuntimeError(f"图片不存在: {path}")
        seen.add(path)
        normalized.append(path)

    if len(normalized) > 9:
        raise RuntimeError("QQ 空间单条说说最多只能上传 9 张图片")

    return normalized


def 上传图片(driver, image_path: str) -> 已上传图片:
    cookie字典 = 获取cookie字典(driver)
    g_tk = 获取_g_tk(cookie字典)
    referer = f"https://user.qzone.qq.com/{QQ_NUMBER}/infocenter"
    upload_url = f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={g_tk}&"

    with open(image_path, "rb") as fh:
        image_base64 = base64.b64encode(fh.read()).decode("ascii")

    session = 新建会话()
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
    }
    data = {
        "filename": os.path.basename(image_path) or "image",
        "uin": QQ_NUMBER,
        "skey": cookie字典.get("skey", ""),
        "zzpaneluin": QQ_NUMBER,
        "zzpanelkey": "",
        "p_uin": QQ_NUMBER,
        "p_skey": cookie字典.get("p_skey", ""),
        "qzonetoken": 获取_qzonetoken(driver),
        "uploadtype": "1",
        "albumtype": "7",
        "exttype": "0",
        "refer": "shuoshuo",
        "output_type": "jsonhtml",
        "charset": "utf-8",
        "output_charset": "utf-8",
        "upload_hd": "1",
        "hd_width": "2048",
        "hd_height": "10000",
        "hd_quality": "96",
        "backUrls": 图片上传回退地址,
        "url": upload_url.rstrip("&"),
        "base64": "1",
        "jsonhtml_callback": "callback",
        "picfile": image_base64,
    }

    resp = session.post(upload_url, headers=headers, cookies=cookie字典, data=data, timeout=60)
    resp.raise_for_status()

    result = 解析回调脚本(resp.text)
    if 转整数(result.get("ret"), default=-1) != 0:
        raise RuntimeError(f"图片上传失败: {result}")

    photo_data = result.get("data", {}) or {}
    if not photo_data.get("albumid") or not photo_data.get("lloc") or not photo_data.get("sloc"):
        raise RuntimeError(f"图片上传返回缺少关键字段: {photo_data}")

    return 已上传图片(
        albumid=str(photo_data.get("albumid") or ""),
        lloc=str(photo_data.get("lloc") or ""),
        sloc=str(photo_data.get("sloc") or ""),
        photo_type=转整数(photo_data.get("type"), default=22),
        width=转整数(photo_data.get("width"), default=0),
        height=转整数(photo_data.get("height"), default=0),
        origin_uuid=str(photo_data.get("origin_uuid") or ""),
        origin_width=转整数(photo_data.get("origin_width"), default=转整数(photo_data.get("width"), default=0)),
        origin_height=转整数(photo_data.get("origin_height"), default=转整数(photo_data.get("height"), default=0)),
        pre_url=str(photo_data.get("pre") or ""),
        url=str(photo_data.get("url") or ""),
        origin_url=str(photo_data.get("origin_url") or photo_data.get("url") or ""),
    )


def 批量上传图片(driver, image_paths: Iterable[str]) -> list[已上传图片]:
    uploaded: list[已上传图片] = []
    for image_path in 标准化图片路径列表(image_paths):
        print(f"开始上传图片: {image_path}")
        photo = 上传图片(driver, image_path)
        uploaded.append(photo)
        print(
            "图片上传成功:",
            image_path,
            f"size={photo.width}x{photo.height}",
            f"album={photo.albumid}",
        )
    return uploaded


def 构建图片发布数据(uploaded_photos: Iterable[已上传图片]) -> dict[str, str]:
    richval_list: list[str] = []
    pic_bo_list: list[str] = []

    for photo in uploaded_photos:
        richval_list.append(photo.生成_richval片段())
        pic_bo = photo.生成_pic_bo片段()
        if pic_bo:
            pic_bo_list.append(pic_bo)

    if not richval_list:
        return {
            "pic_template": "",
            "richtype": "",
            "richval": "",
            "subrichtype": "",
            "pic_bo": "",
        }

    return {
        "pic_template": "",
        "richtype": "1",
        "richval": "\t".join(richval_list),
        "subrichtype": "1",
        "pic_bo": "\t".join(pic_bo_list),
    }


def 发表说说(driver, content, 权限配置, image_paths: Iterable[str] | None = None):
    cookie字典 = 获取cookie字典(driver)
    g_tk = 获取_g_tk(cookie字典)
    referer = f"https://user.qzone.qq.com/{QQ_NUMBER}/infocenter"

    session = 新建会话()
    headers = {
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
    }
    url = f"https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk={g_tk}"

    uploaded_photos = 批量上传图片(driver, image_paths)
    image_post_data = 构建图片发布数据(uploaded_photos)

    data = {
        "syn_tweet_verson": "1",
        "paramstr": "1",
        "pic_template": image_post_data["pic_template"],
        "richtype": image_post_data["richtype"],
        "richval": image_post_data["richval"],
        "special_url": "",
        "subrichtype": image_post_data["subrichtype"],
        "who": "1",
        "con": content,
        "feedversion": str(权限配置.get("feedversion") or 1),
        "ver": "1",
        "ugc_right": str(权限配置["ugc_right"]),
        "to_sign": "0",
        "hostuin": QQ_NUMBER,
        "code_version": "1",
        "format": "fs",
        "qzreferrer": referer,
    }

    if image_post_data["pic_bo"]:
        data["pic_bo"] = image_post_data["pic_bo"]
    else:
        data["to_tweet"] = "0"

    if 权限配置["authorize_type"] in {"part", "blacklist"}:
        uins = 权限配置.get("uins") or []
        if not uins:
            raise RuntimeError(f"{权限配置['authorize_type']} 模式下没有读取到好友列表，已停止发表以避免权限错误")
        data["allow_uins"] = "|".join(uins)

    resp = session.post(url, headers=headers, cookies=cookie字典, data=data, timeout=60)
    resp.raise_for_status()

    result = 解析回调脚本(resp.text)
    if result.get("code") != 0 or result.get("subcode", 0) != 0:
        raise RuntimeError(f"发表失败: {result}")

    return result


def 提取说说tid(发表结果: dict) -> str | None:
    for key in ("tid", "topicId", "topicid"):
        value = 发表结果.get(key)
        if value:
            return str(value)

    feedinfo = str(发表结果.get("feedinfo") or "")
    for pattern in (
        r"fct_\d+_311_0_(\d+)_0_1",
        r'data-tid="(\d+)"',
        r"\btid[=:\"']+(\d+)",
    ):
        match = re.search(pattern, feedinfo)
        if match is not None:
            return match.group(1)

    return None


def 删除说说(driver, tid):
    cookie字典 = 获取cookie字典(driver)
    g_tk = 获取_g_tk(cookie字典)

    session = 新建会话()
    headers = {
        "Referer": f"https://user.qzone.qq.com/{QQ_NUMBER}/311",
        "User-Agent": "Mozilla/5.0",
    }
    url = f"https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6?g_tk={g_tk}"
    data = {
        "hostuin": QQ_NUMBER,
        "tid": tid,
        "t1_source": "1",
        "code_version": "1",
        "format": "fs",
        "qzreferrer": f"https://user.qzone.qq.com/{QQ_NUMBER}/311",
    }

    resp = session.post(url, headers=headers, cookies=cookie字典, data=data, timeout=20)
    resp.raise_for_status()

    result = 解析回调脚本(resp.text)
    if result.get("code") == 0 and result.get("subcode", 0) == 0:
        return result

    raise RuntimeError(f"删除失败: {result}")


def 执行自动说说任务(
    *,
    content: str,
    image_paths: Iterable[str] | None = None,
    wait_seconds: float = 等待秒数,
    delete_after_post: bool = 发表后删除,
) -> int:
    global QQ_NUMBER
    QQ_NUMBER = require_qq_number()
    driver = 连接浏览器()
    time.sleep(1)

    权限配置 = 构建权限配置(driver)
    normalized_image_paths = 标准化图片路径列表(image_paths)
    print(
        "当前可见范围:",
        权限配置["authorize_type"],
        "group=",
        权限配置.get("group_text") or "(无分组)",
        "uins=",
        len(权限配置.get("uins") or []),
        "images=",
        len(normalized_image_paths),
    )

    发表结果 = 发表说说(driver, content, 权限配置, normalized_image_paths)
    新tid = 提取说说tid(发表结果)

    print("发表成功")
    print("新tid:", 新tid or "(接口未直接返回)")

    if not delete_after_post:
        print("按当前参数保留该条说说，不执行删除。")
        return 0

    if not 新tid:
        print("未能从返回结果中提取 tid，已跳过自动删除。")
        return 0

    print(f"等待{wait_seconds}秒供刷访")
    time.sleep(wait_seconds)

    删除结果 = 删除说说(driver, 新tid)
    print("删除成功")
    print("删除接口返回:", 删除结果)
    return 0


def 解析命令行参数():
    parser = argparse.ArgumentParser(description="自动发表 QQ 空间说说，支持配图。")
    parser.add_argument("--content", default=内容, help="说说正文")
    parser.add_argument(
        "--image",
        action="append",
        dest="images",
        default=None,
        help="要上传的图片路径，可重复传入多次以支持多图",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=等待秒数,
        help="发表后等待多少秒再删除，仅在未开启 --keep 时生效",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="发表后保留说说，不自动删除",
    )
    return parser.parse_args()


def main():
    args = 解析命令行参数()
    image_paths = args.images if args.images is not None else 默认图片路径列表

    try:
        return 执行自动说说任务(
            content=args.content,
            image_paths=image_paths,
            wait_seconds=args.wait_seconds,
            delete_after_post=not args.keep and 发表后删除,
        )
    except Exception as exc:
        print(f"自动说说失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
