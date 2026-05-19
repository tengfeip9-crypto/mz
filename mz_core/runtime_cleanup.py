from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


KEEP_LATEST_SNAPSHOTS = 12
KEEP_LATEST_EXPORTS = 24
KEEP_LATEST_COMPARE_LOGS = 24
KEEP_LATEST_FORWARD_HISTORY = 120


@dataclass
class CleanupStats:
    files_deleted: int = 0
    dirs_deleted: int = 0
    bytes_freed: int = 0

    def add_file(self, size: int) -> None:
        self.files_deleted += 1
        self.bytes_freed += max(0, int(size))

    def add_dir(self) -> None:
        self.dirs_deleted += 1

    def merge(self, other: "CleanupStats") -> "CleanupStats":
        self.files_deleted += other.files_deleted
        self.dirs_deleted += other.dirs_deleted
        self.bytes_freed += other.bytes_freed
        return self

    def summary_text(self) -> str:
        freed_mb = self.bytes_freed / (1024 * 1024)
        return (
            f"删除文件 {self.files_deleted} 个，"
            f"删除目录 {self.dirs_deleted} 个，"
            f"释放约 {freed_mb:.2f} MB"
        )


def _remove_file(path: Path) -> CleanupStats:
    stats = CleanupStats()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    try:
        path.unlink()
    except FileNotFoundError:
        return stats

    stats.add_file(size)
    return stats


def _remove_tree(path: Path) -> CleanupStats:
    stats = CleanupStats()
    if not path.exists():
        return stats

    for item in path.rglob("*"):
        if item.is_file():
            try:
                stats.add_file(item.stat().st_size)
            except OSError:
                stats.add_file(0)

    shutil.rmtree(path, ignore_errors=False)
    stats.add_dir()
    return stats


def _sorted_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        [path for path in directory.glob(pattern) if path.is_file()],
        key=lambda path: path.name,
    )


def prune_directory(directory: Path, *, keep: int, pattern: str = "*") -> CleanupStats:
    stats = CleanupStats()
    files = _sorted_files(directory, pattern)
    if keep < 0 or len(files) <= keep:
        return stats

    delete_count = len(files) if keep == 0 else len(files) - keep
    for path in files[:delete_count]:
        stats.merge(_remove_file(path))
    return stats


def prune_friend_data_history(friend_data_dir: Path) -> CleanupStats:
    stats = CleanupStats()
    stats.merge(prune_directory(friend_data_dir / "snapshots", keep=KEEP_LATEST_SNAPSHOTS, pattern="*.json"))
    stats.merge(prune_directory(friend_data_dir / "exports", keep=KEEP_LATEST_EXPORTS, pattern="*"))
    stats.merge(prune_directory(friend_data_dir / "logs", keep=KEEP_LATEST_COMPARE_LOGS, pattern="*.txt"))
    return stats


def prune_auto_forward_history(run_log_dir: Path) -> CleanupStats:
    stats = CleanupStats()
    stats.merge(
        prune_directory(
            run_log_dir / "auto_forward" / "history",
            keep=KEEP_LATEST_FORWARD_HISTORY,
            pattern="*.json",
        )
    )
    return stats


def clear_runtime_history(friend_data_dir: Path, run_log_dir: Path) -> CleanupStats:
    stats = CleanupStats()

    for directory in ("snapshots", "exports", "logs"):
        for path in _sorted_files(friend_data_dir / directory, "*"):
            stats.merge(_remove_file(path))

    for path in (
        friend_data_dir / "latest_snapshot.json",
        friend_data_dir / "state" / "compare_state.json",
        run_log_dir / "auto_forward" / "forward_state.json",
        run_log_dir / "auto_forward" / "forward_append_text_log.txt",
        run_log_dir / "auto_forward" / "forward_reason_log.txt",
    ):
        if path.is_file():
            stats.merge(_remove_file(path))

    for path in _sorted_files(run_log_dir / "auto_forward" / "history", "*"):
        stats.merge(_remove_file(path))

    return stats


def clear_build_artifacts(project_root: Path) -> CleanupStats:
    stats = CleanupStats()

    build_dir = project_root / "build"
    if build_dir.exists():
        stats.merge(_remove_tree(build_dir))

    for cache_dir in sorted(project_root.rglob("__pycache__")):
        if cache_dir.is_dir():
            stats.merge(_remove_tree(cache_dir))

    return stats
