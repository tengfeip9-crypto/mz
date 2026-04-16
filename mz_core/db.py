from __future__ import annotations

import os

try:
    from mz_core.friend_storage import (
        COMPARE_STATE_PATH,
        LOG_DIR,
        friend_display_name,
        list_snapshot_paths,
        load_json,
        normalize_snapshot,
        now_iso,
        now_timestamp,
        save_json,
        snapshot_to_map,
    )
except ModuleNotFoundError:
    from friend_storage import (
        COMPARE_STATE_PATH,
        LOG_DIR,
        friend_display_name,
        list_snapshot_paths,
        load_json,
        normalize_snapshot,
        now_iso,
        now_timestamp,
        save_json,
        snapshot_to_map,
    )


def 读取最新两个快照路径() -> tuple[str | None, str | None]:
    snapshot_paths = list_snapshot_paths(skip_empty=True)
    if len(snapshot_paths) < 2:
        return None, None
    return snapshot_paths[-2], snapshot_paths[-1]


def 本轮已经对比过(previous_path: str, latest_path: str) -> bool:
    state = load_json(COMPARE_STATE_PATH, default={}) or {}
    return (
        state.get("previous_snapshot") == os.path.basename(previous_path)
        and state.get("latest_snapshot") == os.path.basename(latest_path)
    )


def 比较好友快照(old_snapshot: dict, new_snapshot: dict):
    old_map = snapshot_to_map(old_snapshot)
    new_map = snapshot_to_map(new_snapshot)

    old_uins = set(old_map)
    new_uins = set(new_map)

    增加 = [new_map[uin] for uin in sorted(new_uins - old_uins)]
    减少 = [old_map[uin] for uin in sorted(old_uins - new_uins)]

    备注变更 = []
    for uin in sorted(old_uins & new_uins):
        old_item = old_map[uin]
        new_item = new_map[uin]

        if (
            old_item.get("remark") != new_item.get("remark")
            or old_item.get("name") != new_item.get("name")
            or old_item.get("group_id") != new_item.get("group_id")
        ):
            备注变更.append(
                {
                    "uin": uin,
                    "old": old_item,
                    "new": new_item,
                }
            )

    return 增加, 减少, 备注变更


def 写入对比日志(previous_path: str, latest_path: str, 增加, 减少, 备注变更) -> str:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = now_timestamp()
    log_path = os.path.join(LOG_DIR, f"好友对比_{timestamp}.txt")

    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("===== 好友快照对比 =====\n")
        fh.write(f"对比时间：{now_iso()}\n")
        fh.write(f"旧快照：{previous_path}\n")
        fh.write(f"新快照：{latest_path}\n")
        fh.write(f"新增：{len(增加)}\n")
        fh.write(f"减少：{len(减少)}\n")
        fh.write(f"信息变化：{len(备注变更)}\n")

        if 增加:
            fh.write("\n[新增好友]\n")
            for item in 增加:
                fh.write(f"{item['uin']}  {friend_display_name(item)}\n")

        if 减少:
            fh.write("\n[减少好友]\n")
            for item in 减少:
                fh.write(f"{item['uin']}  {friend_display_name(item)}\n")

        if 备注变更:
            fh.write("\n[信息变化]\n")
            for item in 备注变更:
                old_name = friend_display_name(item["old"])
                new_name = friend_display_name(item["new"])
                fh.write(f"{item['uin']}  {old_name}  ->  {new_name}\n")

    return log_path


def 更新对比状态(previous_path: str, latest_path: str, 增加, 减少, 备注变更) -> None:
    save_json(
        {
            "previous_snapshot": os.path.basename(previous_path),
            "latest_snapshot": os.path.basename(latest_path),
            "compared_at": now_iso(),
            "added_count": len(增加),
            "removed_count": len(减少),
            "changed_count": len(备注变更),
        },
        COMPARE_STATE_PATH,
    )


def 主程序():
    print("开始执行好友快照对比...")

    previous_path, latest_path = 读取最新两个快照路径()
    if previous_path is None or latest_path is None:
        print("有效快照不足两份，暂时无法对比。请先至少完成两次成功的好友保存。")
        return

    if 本轮已经对比过(previous_path, latest_path):
        print("没有新的好友快照，跳过对比。")
        return

    old_snapshot = normalize_snapshot(load_json(previous_path))
    new_snapshot = normalize_snapshot(load_json(latest_path))
    增加, 减少, 备注变更 = 比较好友快照(old_snapshot, new_snapshot)

    print(
        f"对比完成：新增 {len(增加)}，"
        f"减少 {len(减少)}，"
        f"信息变化 {len(备注变更)}"
    )

    if 增加:
        print("新增好友：", [f"{item['uin']} {friend_display_name(item)}" for item in 增加])
    if 减少:
        print("减少好友：", [f"{item['uin']} {friend_display_name(item)}" for item in 减少])
    if 备注变更:
        print("信息变化：", [f"{item['uin']} {friend_display_name(item['old'])} -> {friend_display_name(item['new'])}" for item in 备注变更])

    log_path = 写入对比日志(previous_path, latest_path, 增加, 减少, 备注变更)
    更新对比状态(previous_path, latest_path, 增加, 减少, 备注变更)
    print(f"对比日志已保存: {log_path}")


if __name__ == "__main__":
    主程序()
