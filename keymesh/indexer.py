"""Manifest builder for KeyMesh shares."""

from __future__ import annotations

import asyncio  # asyncio 支持并发调度
import logging  # logging 用于输出进度信息
import os  # os.walk 用于遍历目录
import stat as stat_module  # stat 模块帮助识别文件类型
from datetime import datetime, timezone  # datetime 用于生成时间戳
from pathlib import Path  # Path 提供跨平台路径操作
from typing import Any  # Any 用于类型提示

from .hash_policy import (
    compute_file_hash,
    compute_file_hash_async,
    quick_hash_metadata,
)  # 统一的哈希实现
from .utils.ignore import load_ignore_patterns, should_ignore  # 忽略规则工具

LOGGER = logging.getLogger(__name__)

DEFAULT_SAMPLE_MB = 4
DEFAULT_MAX_WORKERS = 4


async def build_manifest(
    share_name: str,
    share_path: str,
    ignore_patterns: list[str],
    hash_policy: str = "auto",
    *,
    small_threshold_mb: int = 16,
    sample_mb: int = DEFAULT_SAMPLE_MB,
    ignore_hidden: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, Any]:
    """扫描共享目录并生成 manifest。

    Args:
        share_name: 共享域名称，用于写入 manifest 元数据。
        share_path: 共享域根目录。
        ignore_patterns: 预先加载的忽略模式列表。
        hash_policy: 哈希策略，可取 ``auto``/``full``/``sample``/``meta``/``none``。
        small_threshold_mb: 小文件阈值，单位 MB。
        sample_mb: 采样模式读取的大小，单位 MB。
        ignore_hidden: 是否忽略隐藏目录与缓存目录。
        max_workers: 计算哈希时允许的最大并发数。

    Returns:
        manifest 字典，包含 entries 列表与策略摘要。
    """

    root = Path(share_path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"share path {root} not found")
    patterns = list(ignore_patterns)
    ignore_file = root / ".keymeshignore"
    patterns.extend(load_ignore_patterns(ignore_file))
    files: list[tuple[Path, str]] = []
    ignored = 0
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel_dir = Path(dirpath).relative_to(root)
        kept_dirs = []
        for dirname in dirnames:
            rel_path = (rel_dir / dirname).as_posix()
            if ignore_hidden and (dirname.startswith(".") or dirname in {"__pycache__"}):
                ignored += 1
                continue
            if should_ignore(rel_path, patterns) or should_ignore(f"{rel_path}/", patterns):
                ignored += 1
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            rel_path_obj = rel_dir / filename
            rel_path = rel_path_obj.as_posix()
            full_path = Path(dirpath) / filename
            if should_ignore(rel_path, patterns):
                ignored += 1
                continue
            files.append((full_path, rel_path))
    semaphore = asyncio.Semaphore(max(1, max_workers))

    async def _hash_with_policy(full_path: Path, file_size: int) -> str:
        """依据策略选择哈希算法。"""

        if hash_policy == "full":
            return await compute_file_hash_async(full_path)
        if hash_policy == "sample":
            return await compute_file_hash_async(full_path, sample_mb=sample_mb)
        if hash_policy == "auto":
            threshold_bytes = small_threshold_mb * 1024 * 1024
            if file_size <= threshold_bytes:
                return await compute_file_hash_async(full_path)
            return await compute_file_hash_async(full_path, sample_mb=sample_mb)
        raise ValueError(f"unknown hash policy: {hash_policy}")

    async def process_file(full_path: Path, rel_path: str) -> tuple[dict[str, Any] | None, int]:
        """读取单个文件的元数据与哈希。"""

        async with semaphore:
            try:
                stat_result = await asyncio.to_thread(full_path.stat)
            except PermissionError:
                LOGGER.warning("permission denied while indexing %s", full_path)
                return None, 1
            except FileNotFoundError:
                LOGGER.warning("file disappeared during indexing: %s", full_path)
                return None, 0
            if not stat_module.S_ISREG(stat_result.st_mode):
                return None, 0
            if hash_policy == "meta":
                hash_value = quick_hash_metadata(full_path)
            elif hash_policy == "none":
                hash_value = ""
            else:
                hash_value = await _hash_with_policy(full_path, stat_result.st_size)
            entry = {
                "path": rel_path,
                "size": stat_result.st_size,
                "mtime": int(stat_result.st_mtime),
                "hash": hash_value,
            }
            return entry, 0

    tasks = [process_file(path, rel_path) for path, rel_path in files]
    results = await asyncio.gather(*tasks)
    entries = []
    for entry, skip_flag in results:
        if entry:
            entries.append(entry)
        skipped += skip_flag
    entries.sort(key=lambda item: item["path"])
    manifest = {
        "share": share_name,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "entries": entries,
        "policy": {
            "hash": hash_policy,
            "ignore_count": ignored,
            "skipped": skipped,
            "small_threshold_mb": small_threshold_mb,
            "sample_mb": sample_mb,
        },
    }
    LOGGER.info(
        "manifest for %s: %d entries, %d ignored, %d skipped",
        share_name,
        len(entries),
        ignored,
        skipped,
    )
    return manifest


def hash_file(
    path: Path,
    small_threshold_mb: int = 16,
    *,
    sample_mb: int = DEFAULT_SAMPLE_MB,
    policy: str = "auto",
    file_size: int | None = None,
) -> str:
    """按照策略计算文件哈希。

    Args:
        path: 目标文件路径。
        small_threshold_mb: ``auto`` 策略的小文件阈值。
        sample_mb: 采样策略读取的大小。
        policy: 具体哈希策略。
        file_size: 可选的文件大小，避免重复 stat。

    Returns:
        形如 ``xxh64:deadbeef`` 的哈希字符串或空字符串。

    Raises:
        ValueError: 当策略字符串未知时抛出。
    """

    size = file_size if file_size is not None else path.stat().st_size
    threshold_bytes = small_threshold_mb * 1024 * 1024
    if policy == "full":
        return compute_file_hash(path)
    if policy == "sample":
        return compute_file_hash(path, sample_mb=sample_mb)
    if policy == "meta":
        return quick_hash_metadata(path)
    if policy == "none":
        return ""
    if policy != "auto":
        raise ValueError(f"unknown hash policy: {policy}")
    if size <= threshold_bytes:
        return compute_file_hash(path)
    return compute_file_hash(path, sample_mb=sample_mb)
