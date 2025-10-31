"""Utility helpers to compute hashes according to KeyMesh policies."""

from __future__ import annotations

from hashlib import sha256  # hashlib 提供跨平台哈希实现
from pathlib import Path  # Path 用于处理文件系统路径
from typing import Any, BinaryIO  # Any/BinaryIO 类型便于类型检查

import aiofiles  # aiofiles 支持异步读取文件

try:  # 优先尝试快速的 xxhash
    import xxhash  # type: ignore  # noqa: F401
except ImportError:  # 如果依赖缺失则在运行时回退到 SHA-256
    xxhash = None

# 固定盐值，确保不同版本的哈希不会互相混淆
_HASH_SALT = b"KeyMesh::hash::v1"
# 读取文件时使用的块大小，4 MiB 在大文件上性能较好
_READ_CHUNK_SIZE = 4 * 1024 * 1024


def _new_hasher() -> tuple[str, Any]:
    """创建新的哈希上下文并返回算法名称。"""

    if xxhash is not None:
        return "xxh64", xxhash.xxh64()
    return "sha256", sha256()


def _update_with_salt(hasher: Any, data: bytes) -> None:
    """将数据与盐写入哈希器。"""

    hasher.update(_HASH_SALT)
    hasher.update(data)


def _iter_file_chunks(handle: BinaryIO, *, limit_bytes: int | None = None) -> bytes:
    """生成器：按块读取文件，可选限制总字节数。"""

    remaining = limit_bytes
    while True:
        if remaining is not None and remaining <= 0:
            return
        to_read = _READ_CHUNK_SIZE if remaining is None else min(_READ_CHUNK_SIZE, remaining)
        chunk = handle.read(to_read)
        if not chunk:
            return
        yield chunk
        if remaining is not None:
            remaining -= len(chunk)


def compute_file_hash(path: Path, sample_mb: int | None = None) -> str:
    """计算文件内容哈希。

    Args:
        path: 目标文件的绝对路径。
        sample_mb: 若提供，仅读取前 N MB。

    Returns:
        字符串形式的哈希值，格式如 ``xxh64:deadbeef``。
    """

    algo, hasher = _new_hasher()
    limit = None if sample_mb is None else sample_mb * 1024 * 1024
    with path.open("rb") as handle:
        for chunk in _iter_file_chunks(handle, limit_bytes=limit):
            _update_with_salt(hasher, chunk)
    return f"{algo}:{hasher.hexdigest()}"


async def compute_file_hash_async(path: Path, sample_mb: int | None = None) -> str:
    """异步计算文件内容哈希，使用 aiofiles 逐块读取。

    Args:
        path: 目标文件的绝对路径。
        sample_mb: 若提供，仅读取前 N MB。

    Returns:
        形如 ``xxh64:deadbeef`` 的哈希字符串。
    """

    algo, hasher = _new_hasher()
    limit = None if sample_mb is None else sample_mb * 1024 * 1024
    async with aiofiles.open(path, "rb") as handle:
        remaining = limit
        while True:
            if remaining is not None and remaining <= 0:
                break
            to_read = _READ_CHUNK_SIZE if remaining is None else min(_READ_CHUNK_SIZE, remaining)
            chunk = await handle.read(to_read)
            if not chunk:
                break
            _update_with_salt(hasher, chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return f"{algo}:{hasher.hexdigest()}"


def quick_hash_metadata(path: Path) -> str:
    """基于文件元数据生成快速哈希。

    Args:
        path: 目标文件路径。

    Returns:
        仅依赖文件名、大小与修改时间的哈希结果。
    """

    stat_result = path.stat()
    hasher = sha256()
    payload = f"{path.name}|{stat_result.st_size}|{int(stat_result.st_mtime)}".encode("utf-8")
    _update_with_salt(hasher, payload)
    return f"sha256:{hasher.hexdigest()}"
