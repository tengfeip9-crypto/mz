import sys
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

from project_config import require_qq_number  # noqa: E402

try:
    from mz_core.friend_storage import (
        CHROME_DRIVER_PATH,
        DEBUGGER_ADDRESS,
        LATEST_SNAPSHOT_PATH,
        build_friend_record,
        ensure_friend_dirs,
        export_friends_csv,
        export_friends_xlsx,
        load_json,
        make_snapshot,
        now_timestamp,
        parse_callback_json,
        snapshot_friend_count,
        write_snapshot,
    )
except ModuleNotFoundError:
    from friend_storage import (
        CHROME_DRIVER_PATH,
        DEBUGGER_ADDRESS,
        LATEST_SNAPSHOT_PATH,
        build_friend_record,
        ensure_friend_dirs,
        export_friends_csv,
        export_friends_xlsx,
        load_json,
        make_snapshot,
        now_timestamp,
        parse_callback_json,
        snapshot_friend_count,
        write_snapshot,
    )

# ================= 工具函数 =================

def 连接浏览器():
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", DEBUGGER_ADDRESS)
    return webdriver.Chrome(service=Service(CHROME_DRIVER_PATH), options=chrome_options)


def 获取_g_tk(cookie字典):
    p_skey = cookie字典.get("p_skey", "")
    h = 5381

    for c in p_skey:
        h += (h << 5) + ord(c)

    return h & 2147483647


def 获取cookie字典(driver):
    return {c["name"]: c["value"] for c in driver.get_cookies()}


def 新建会话():
    session = requests.Session()
    session.trust_env = False
    return session


def 获取好友列表(qq号, g_tk, cookie字典):
    url = "https://h5.qzone.qq.com/proxy/domain/r.qzone.qq.com/cgi-bin/tfriend/friend_show_qqfriends.cgi"
    headers = {
        "Referer": f"https://user.qzone.qq.com/{qq号}",
        "User-Agent": "Mozilla/5.0",
    }
    params = {
        "uin": qq号,
        "follow_flag": 0,
        "groupface_flag": 0,
        "fupdate": 1,
        "g_tk": g_tk,
    }

    session = 新建会话()
    resp = session.get(url, headers=headers, cookies=cookie字典, params=params, timeout=20)
    resp.raise_for_status()

    data = parse_callback_json(resp.text)
    code = data.get("code")
    if code not in (None, 0):
        message = data.get("message") or data.get("msg") or "未知错误"
        raise RuntimeError(f"好友接口返回异常 code={code}: {message}")

    items = data.get("data", {}).get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("好友接口返回结构异常，items 不是列表")

    friends = []
    for item in items:
        record = build_friend_record(item)
        if not record["uin"] or record["uin"] == str(qq号):
            continue
        friends.append(record)

    return friends


def 读取上一份快照好友数():
    latest_snapshot = load_json(LATEST_SNAPSHOT_PATH, default=None)
    if latest_snapshot is None:
        return None

    try:
        return snapshot_friend_count(latest_snapshot)
    except Exception:
        return None


def 校验好友列表结果(friends):
    current_count = len(friends)
    previous_count = 读取上一份快照好友数()

    if current_count > 0:
        return True, ""

    if previous_count and previous_count > 0:
        return (
            False,
            f"本次好友列表为空，上一份有效快照仍有 {previous_count} 个好友，"
            "疑似接口或登录态异常，本轮已取消保存。",
        )

    return True, ""


def 保存好友快照(friends):
    ensure_friend_dirs()
    timestamp = now_timestamp()
    snapshot = make_snapshot(friends, qq_number=require_qq_number())
    snapshot_path = write_snapshot(snapshot, timestamp)
    export_csv_path = export_friends_csv(friends, timestamp)
    export_xlsx_path = export_friends_xlsx(friends, timestamp)

    print(f"好友数量: {snapshot['friend_count']}")
    print(f"好友快照已保存: {snapshot_path}")
    print(f"好友导出已保存: {export_csv_path}")
    print(f"好友Excel已保存: {export_xlsx_path}")

    return snapshot_path, export_csv_path, export_xlsx_path

# ================= 主逻辑 =================

def 主程序():
    try:
        qq_number = require_qq_number()
        driver = 连接浏览器()
        cookie字典 = 获取cookie字典(driver)
        if not cookie字典.get("p_skey"):
            print("未获取到 p_skey，当前浏览器登录态可能已失效。")
            return 1

        g_tk = 获取_g_tk(cookie字典)
        friends = 获取好友列表(qq_number, g_tk, cookie字典)

        ok, message = 校验好友列表结果(friends)
        if not ok:
            print(message)
            return 1

        保存好友快照(friends)
        return 0
    except Exception as exc:
        print(f"好友保存失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(主程序())
