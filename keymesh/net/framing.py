"""Length-prefixed JSON framing helpers."""

from __future__ import annotations

import json  # json 负责序列化
import struct  # struct 处理二进制长度

import asyncio  # asyncio 支持异步 I/O


class ProtocolError(Exception):
    """表示帧协议解析错误。"""


async def send_json(writer: asyncio.StreamWriter, obj: dict) -> None:
    """发送单帧 JSON 对象。"""

    # 序列化对象为紧凑的 JSON 字符串
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    # 编码为 UTF-8 字节序列
    data = payload.encode("utf-8")
    # 计算长度并打包为 4 字节大端整数
    header = struct.pack(">I", len(data))
    # 写入长度头
    writer.write(header)
    # 写入正文
    writer.write(data)
    # 等待缓冲区刷新
    await writer.drain()


async def recv_json(reader: asyncio.StreamReader, max_size: int = 8 * 1024 * 1024) -> dict:
    """接收并解析单帧 JSON。"""

    try:
        # 读取长度头
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        # 流过早结束
        raise ProtocolError("unexpected EOF while reading frame length") from exc
    # 解包得到正文长度
    (length,) = struct.unpack(">I", header)
    # 长度为零或过大视为协议错误
    if length <= 0 or length > max_size:
        raise ProtocolError(f"invalid frame length: {length}")
    try:
        # 读取指定长度的数据
        data = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        # 正文未完整读取
        raise ProtocolError("unexpected EOF while reading frame payload") from exc
    try:
        # 解码 UTF-8 并解析 JSON
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # 解析失败
        raise ProtocolError("invalid JSON payload") from exc
    # 仅接受字典消息
    if not isinstance(obj, dict):
        raise ProtocolError("frame payload must be an object")
    # 返回解析结果
    return obj
