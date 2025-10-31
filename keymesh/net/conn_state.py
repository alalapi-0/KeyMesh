"""Peer connection state tracking."""

from __future__ import annotations

import asyncio  # asyncio 提供锁与事件
from dataclasses import dataclass, field  # dataclass 便于定义数据结构
from typing import Any, Dict, List, Optional  # 类型提示


@dataclass(slots=True)
class PeerInfo:
    """描述单个 peer 的运行时状态。"""

    id: str  # peer ID
    addr: str  # 目标地址
    allowed_shares: List[str] = field(default_factory=list)  # 允许的共享列表
    connected: bool = False  # 当前是否处于连接状态
    last_error: Optional[str] = None  # 最近错误信息
    last_hello_ts: Optional[float] = None  # 最近收到 HELLO 时间戳
    last_ack_ts: Optional[float] = None  # 最近发送/收到 ACK 时间戳
    last_heartbeat_ts: Optional[float] = None  # 最近心跳时间戳
    fingerprint: Optional[str] = None  # 已验证的证书指纹
    remote_capabilities: Optional[Dict[str, Any]] = None  # 对端能力声明

    def __post_init__(self) -> None:
        # 每个 PeerInfo 拥有独立的异步锁，确保并发更新安全
        self._lock = asyncio.Lock()
        # 记录是否曾经完成握手
        self._handshake_event = asyncio.Event()

    async def mark_handshake(
        self,
        *,
        hello_ts: float,
        ack_ts: float,
        fingerprint: str,
        allowed_shares: List[str],
        remote_capabilities: Dict[str, Any],
    ) -> None:
        """标记握手成功。"""

        async with self._lock:
            self.connected = True
            self.last_error = None
            self.last_hello_ts = hello_ts
            self.last_ack_ts = ack_ts
            self.last_heartbeat_ts = ack_ts
            self.fingerprint = fingerprint
            self.allowed_shares = list(allowed_shares)
            self.remote_capabilities = dict(remote_capabilities)
            self._handshake_event.set()

    async def mark_heartbeat(self, timestamp: float) -> None:
        """更新心跳时间。"""

        async with self._lock:
            self.last_heartbeat_ts = timestamp

    async def mark_error(self, message: str) -> None:
        """记录错误并标记断开。"""

        async with self._lock:
            self.last_error = message
            self.connected = False

    async def mark_disconnected(self) -> None:
        """记录断开事件。"""

        async with self._lock:
            self.connected = False

    async def wait_handshake(self) -> None:
        """等待首次握手成功。"""

        await self._handshake_event.wait()

    async def to_dict(self) -> Dict[str, Any]:
        """导出状态字典。"""

        async with self._lock:
            return {
                "id": self.id,
                "addr": self.addr,
                "connected": self.connected,
                "last_error": self.last_error,
                "last_hello_ts": self.last_hello_ts,
                "last_ack_ts": self.last_ack_ts,
                "last_heartbeat_ts": self.last_heartbeat_ts,
                "allowed_shares": list(self.allowed_shares),
                "fingerprint": self.fingerprint,
                "remote_capabilities": dict(self.remote_capabilities or {}),
            }
