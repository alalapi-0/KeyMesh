"""Chunking helpers used by the transfer engine.

Each chunk is produced with the following JSON header example::

    {"type": "CHUNK", "file": "example.bin", "chunk": 1, "hash": "sha256:...", "size": 4096}

The binary payload immediately follows the JSON header when transported
by :mod:`keymesh.transfer.protocol`.
"""

from __future__ import annotations

from hashlib import sha256  # 哈希函数用于校验数据块完整性
from pathlib import Path  # Path 类型便于跨平台文件访问
from typing import Generator, Tuple  # 类型提示让接口更清晰

# 默认分块大小设置为 4MB，兼顾吞吐与内存占用
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


def chunk_file(
    path: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Generator[Tuple[int, int, bytes, str], None, None]:
    """Yield file chunks with per-chunk SHA256 checksums.

    Parameters
    ----------
    path:
        Path of the file to read.
    chunk_size:
        Size of each chunk in bytes. Defaults to four megabytes.

    Yields
    ------
    tuple
        ``(chunk_id, offset, data, hash_hex)`` for each chunk. ``chunk_id`` is
        a zero-based integer and ``hash_hex`` is the hexadecimal SHA256 digest.

    Notes
    -----
    The generator reads sequentially and can be restarted from an arbitrary
    offset by seeking the file before iterating. Consumers that implement
    resume semantics should ``seek`` the file handle prior to iterating.
    """

    # 将路径转换为绝对路径以避免歧义
    file_path = Path(path).expanduser().resolve()
    # 打开文件为二进制读取模式
    with file_path.open("rb") as handle:
        # 初始化块编号和偏移量
        chunk_id = 0
        offset = 0
        # 循环读取直到文件末尾
        while True:
            # 从当前位置读取一块数据
            data = handle.read(chunk_size)
            # 若读取结果为空字节串则表示结束
            if not data:
                break
            # 计算数据块的 SHA256 校验
            digest = sha256(data).hexdigest()
            # 组合带前缀的哈希字符串
            hash_value = f"sha256:{digest}"
            # 产出当前块的信息
            yield chunk_id, offset, data, hash_value
            # 更新块编号与偏移量为下一轮准备
            chunk_id += 1
            offset += len(data)


def verify_chunk(data: bytes, expected_hash: str) -> bool:
    """Verify the SHA256 checksum of a chunk.

    Parameters
    ----------
    data:
        Raw chunk bytes.
    expected_hash:
        Hash string produced by :func:`chunk_file`.

    Returns
    -------
    bool
        ``True`` if the hash matches, otherwise ``False``.
    """

    # 预期的哈希值应当以 "sha256:" 前缀开头
    if not expected_hash.startswith("sha256:"):
        return False
    # 取出实际的十六进制哈希部分
    _, _, hex_digest = expected_hash.partition(":")
    # 使用同样的算法计算输入数据的哈希
    calculated = sha256(data).hexdigest()
    # 返回比较结果
    return calculated == hex_digest
