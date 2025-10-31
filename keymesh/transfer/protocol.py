"""Streaming protocol helpers for KeyMesh file transfers.

Protocol frames are exchanged using the length-prefixed JSON helpers from
:mod:`keymesh.net.framing`. Chunk frames are immediately followed by the
binary payload bytes described by the ``size`` field.

Example frames::

    {"type": "FILE_REQ", "file": "docs/report.pdf", "size": 1048576, "mode": "push"}
    {"type": "FILE_META", "status": "ok", "resume_offset": 524288}
    {"type": "CHUNK", "file": "docs/report.pdf", "chunk": 5, "offset": 524288,
     "size": 4096, "hash": "sha256:..."}
    {"type": "CHUNK_ACK", "file": "docs/report.pdf", "chunk": 5, "status": "ok"}
    {"type": "FILE_END", "file": "docs/report.pdf", "hash": "sha256:...",
     "bytes": 1048576}
"""

from __future__ import annotations

import asyncio  # asyncio StreamReader/StreamWriter 用于网络 I/O
import time  # time.perf_counter 用于统计耗时
from hashlib import sha256  # 整体哈希验证
from pathlib import Path  # Path 提供路径处理能力
from typing import Awaitable, Callable, Iterable  # Iterable 用于类型提示

from ..net.framing import ProtocolError as FramingProtocolError, recv_json, send_json
from .chunker import verify_chunk


class ChecksumError(Exception):
    """Raised when a chunk or file checksum does not match."""


class ProtocolError(Exception):
    """Raised when transfer protocol state machine is violated."""


async def _read_exact(reader: asyncio.StreamReader, size: int) -> bytes:
    """Read exactly ``size`` bytes or raise :class:`ProtocolError`."""

    try:
        data = await reader.readexactly(size)
    except asyncio.IncompleteReadError as exc:  # 捕获读取不足的情况
        raise ProtocolError("unexpected EOF while reading chunk payload") from exc
    return data


async def send_file(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    file_path: Path,
    share_name: str,
    relative_path: str,
    *,
    chunk_size: int,
    resume_offset: int = 0,
    rate_limit_bytes_per_sec: int | None = None,
    max_retries: int = 3,
    retry_backoff: Iterable[float] | None = None,
    progress_cb: Callable[[int, int, int], Awaitable[None] | None] | None = None,
) -> dict:
    """Stream a file to the remote peer using the transfer protocol.

    Parameters
    ----------
    reader, writer:
        Connected TLS stream pair.
    file_path:
        Path to the source file.
    chunk_size:
        Chunk size in bytes.
    resume_offset:
        Number of leading bytes that the receiver already stored.
    rate_limit_bytes_per_sec:
        Optional throttling rate. ``None`` disables rate limiting.
    max_retries:
        Number of attempts per chunk when acknowledgements fail.
    retry_backoff:
        Iterable of seconds applied between retries. When exhausted the
        last value is reused.

    Returns
    -------
    dict
        Summary containing ``{"bytes": int, "chunks": int, "elapsed": float}``.
    """

    # 将输入路径正规化
    src = Path(file_path).expanduser().resolve()
    # 获取文件大小用于元数据
    total_size = src.stat().st_size
    # 预先计算整文件哈希，确保断点续传仍能提供统一校验值
    total_hash = sha256()
    with src.open("rb") as checksum_handle:
        while True:
            piece = checksum_handle.read(chunk_size)
            if not piece:
                break
            total_hash.update(piece)
    total_hash_hex = total_hash.hexdigest()
    # 记录传输开始时间
    start_ts = time.perf_counter()
    # 发送 FILE_REQ 帧，包含目标文件、大小与模式
    await send_json(
        writer,
        {
            "type": "FILE_REQ",
            "file": relative_path,
            "size": total_size,
            "mode": "push",
            "resume_offset": resume_offset,
            "hash": f"sha256:{total_hash_hex}",
            "share": share_name,
        },
    )
    # 等待对端返回 FILE_META 响应
    try:
        meta = await recv_json(reader)
    except FramingProtocolError as exc:
        raise ProtocolError("failed to receive FILE_META") from exc
    # 校验响应类型
    if meta.get("type") != "FILE_META":
        raise ProtocolError(f"unexpected response type: {meta}")
    # 如果状态非 ok 则终止
    if meta.get("status") != "ok":
        raise ProtocolError(meta.get("error", "FILE_META rejected"))
    # 对端可能要求使用不同的续传偏移
    remote_resume = int(meta.get("resume_offset", 0))
    # 针对续传偏移调整读取起点
    start_offset = max(resume_offset, remote_resume)
    # 打开文件准备读取
    with src.open("rb") as handle:
        # 跳过对端已存在的字节
        if start_offset:
            handle.seek(start_offset)
        # 初始化统计变量
        sent_bytes = start_offset
        sent_chunks = 0
        # 计算初始块编号
        chunk_index = start_offset // chunk_size
        # 构造退避序列迭代器
        backoff = list(retry_backoff or [])
        # 逐块读取并发送
        while True:
            # 从文件读取一块数据
            data = handle.read(chunk_size)
            # 若无更多数据则跳出
            if not data:
                break
            # 计算当前块的哈希并准备头部
            chunk_hash = sha256(data).hexdigest()
            header = {
                "type": "CHUNK",
                "file": relative_path,
                "share": share_name,
                "chunk": chunk_index,
                "offset": sent_bytes,
                "size": len(data),
                "hash": f"sha256:{chunk_hash}",
            }
            # 初始化重试计数
            attempt = 0
            while True:
                # 发送头部 JSON
                await send_json(writer, header)
                # 发送原始数据块
                writer.write(data)
                await writer.drain()
                # 如需限速则根据速率等待
                if rate_limit_bytes_per_sec:
                    sleep_time = len(data) / rate_limit_bytes_per_sec
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                try:
                    # 等待对端确认
                    ack = await recv_json(reader)
                except FramingProtocolError as exc:
                    ack = {"type": "ERROR", "error": str(exc)}
                # 若收到错误帧或类型不匹配则判断是否需要重试
                if ack.get("type") != "CHUNK_ACK" or ack.get("chunk") != chunk_index:
                    attempt += 1
                    if attempt >= max_retries:
                        raise ProtocolError(
                            f"chunk {chunk_index} ack mismatch: {ack}",
                        )
                    delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                # 如果状态非 ok 也尝试重试
                if ack.get("status") != "ok":
                    attempt += 1
                    if attempt >= max_retries:
                        raise ProtocolError(ack.get("error", "chunk rejected"))
                    delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                # 确认成功，退出重试循环
                break
            # 更新统计变量
            sent_bytes += len(data)
            sent_chunks += 1
            chunk_index += 1
            if progress_cb is not None:
                maybe_coro = progress_cb(len(data), sent_chunks, sent_bytes)
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
    # 全部完成后发送 FILE_END 帧
    await send_json(
        writer,
        {
            "type": "FILE_END",
            "file": relative_path,
            "share": share_name,
            "hash": f"sha256:{total_hash_hex}",
            "bytes": total_size,
        },
    )
    # 等待对端最终确认
    try:
        end_ack = await recv_json(reader)
    except FramingProtocolError as exc:
        raise ProtocolError("failed to receive FILE_END ack") from exc
    if end_ack.get("type") != "FILE_END" or end_ack.get("status") != "ok":
        raise ProtocolError(end_ack.get("error", "transfer failed"))
    # 计算耗时
    elapsed = time.perf_counter() - start_ts
    # 返回总结
    return {"bytes": sent_bytes, "chunks": sent_chunks, "elapsed": elapsed}


async def receive_file(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    out_path: Path,
    *,
    initial_request: dict | None = None,
    resume_offset: int = 0,
    expected_mode: str = "push",
    rate_limit_bytes_per_sec: int | None = None,
    progress_cb: Callable[[int, int, int], Awaitable[None] | None] | None = None,
) -> dict:
    """Receive a file from the remote peer.

    Parameters
    ----------
    reader, writer:
        Connected TLS stream pair.
    out_path:
        Path to the ``.part`` temporary file.
    resume_offset:
        Number of bytes already present in ``out_path``.
    expected_mode:
        Expected direction, defaults to ``"push"``.
    rate_limit_bytes_per_sec:
        Optional throttling applied while writing.

    Returns
    -------
    dict
        Summary dictionary mirroring :func:`send_file`.
    """

    # 首先接收 FILE_REQ 帧
    if initial_request is None:
        try:
            file_req = await recv_json(reader)
        except FramingProtocolError as exc:
            raise ProtocolError("failed to receive FILE_REQ") from exc
    else:
        file_req = initial_request
    # 验证消息类型
    if file_req.get("type") != "FILE_REQ":
        raise ProtocolError(f"unexpected frame: {file_req}")
    # 校验传输模式
    if file_req.get("mode") != expected_mode:
        raise ProtocolError("unsupported transfer mode")
    share_name = file_req.get("share", "")
    relative_path = file_req.get("file", "")
    # 解析目标文件大小
    remote_size = int(file_req.get("size", 0))
    # 发送 FILE_META 确认续传偏移
    await send_json(
        writer,
        {
            "type": "FILE_META",
            "status": "ok",
            "resume_offset": resume_offset,
        },
    )
    # 确保输出路径可写
    target = Path(out_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_bytes = 0
    file_exists = target.exists()
    # 打开文件并定位到续传位置
    with target.open("r+b" if file_exists else "wb") as handle:
        if resume_offset and file_exists:
            # 读取已有内容用于累计哈希
            handle.seek(0)
            buffered = handle.read(resume_offset)
            existing_bytes = len(buffered)
            whole_hash = sha256(buffered)
            handle.seek(existing_bytes)
        else:
            whole_hash = sha256()
        if resume_offset and existing_bytes < resume_offset:
            # 如果实际文件不足则调整为实际长度
            resume_offset = existing_bytes
            handle.seek(resume_offset)
        else:
            handle.seek(resume_offset)
        # 初始化统计变量
        received_bytes = resume_offset
        received_chunks = 0
        # whole_hash 已根据现有内容初始化
        while True:
            try:
                # 读取下一帧
                header = await recv_json(reader)
            except FramingProtocolError as exc:
                raise ProtocolError("failed to receive frame") from exc
            frame_type = header.get("type")
            # 遇到 FILE_END 表示结束
            if frame_type == "FILE_END":
                claimed_hash = header.get("hash", "")
                computed_hash = f"sha256:{whole_hash.hexdigest()}"
                if computed_hash != claimed_hash:
                    raise ChecksumError("final hash mismatch")
                await send_json(
                    writer,
                    {"type": "FILE_END", "status": "ok", "bytes": received_bytes},
                )
                break
            if frame_type != "CHUNK":
                raise ProtocolError(f"unexpected frame type: {header}")
            size = int(header.get("size", 0))
            expected_hash = header.get("hash", "")
            # 读取对应长度的数据
            payload = await _read_exact(reader, size)
            # 校验数据块哈希
            if not verify_chunk(payload, expected_hash):
                raise ChecksumError(
                    f"chunk hash mismatch at {header.get('chunk')}",
                )
            # 将块写入文件
            handle.write(payload)
            handle.flush()
            # 更新整体哈希
            whole_hash.update(payload)
            # 维护统计信息
            received_bytes += len(payload)
            received_chunks += 1
            # 写入完成后发送确认
            await send_json(
                writer,
                {
                    "type": "CHUNK_ACK",
                    "chunk": header.get("chunk"),
                    "status": "ok",
                },
            )
            # 限速处理
            if rate_limit_bytes_per_sec:
                delay = len(payload) / rate_limit_bytes_per_sec
                if delay > 0:
                    await asyncio.sleep(delay)
            if progress_cb is not None:
                maybe_coro = progress_cb(len(payload), received_chunks, received_bytes)
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
    # 返回统计结果
    return {
        "bytes": received_bytes,
        "chunks": received_chunks,
        "size": remote_size,
        "share": share_name,
        "file": relative_path,
    }
