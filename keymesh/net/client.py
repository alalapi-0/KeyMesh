"""Outgoing mTLS connector for KeyMesh."""

from __future__ import annotations

import asyncio  # asyncio 驱动异步连接
import logging  # logging 输出日志
import time  # time 提供时间戳
from typing import Dict, Optional

from ..app import AppContext
from ..config import PeerConfig
from .conn_state import PeerInfo
from .framing import ProtocolError, recv_json, send_json
from .mtls import build_client_context, extract_peer_fingerprint, fingerprint_in_whitelist
from ..proto import handshake

LOGGER = logging.getLogger(__name__)


class ClientConnector:
    """负责主动连接 peers 并维持心跳。"""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task[object]] = {}  # 记录每个 peer 的维护任务
        self._ssl_context = None  # 缓存 TLS 上下文

    def _get_ssl_context(self, app_ctx: AppContext):
        if self._ssl_context is None:  # 首次调用时构建上下文
            self._ssl_context = build_client_context(app_ctx.cfg)
        return self._ssl_context

    async def run(self, app_ctx: AppContext) -> None:
        """启动连接循环。"""

        LOGGER.info("client connector started")
        try:
            while True:  # 持续巡检所有 peer
                for peer in app_ctx.cfg.peers:
                    task = self._tasks.get(peer.id)  # 查找既有任务
                    if task is None or task.done():  # 未启动或已结束则重建
                        new_task = asyncio.create_task(self._maintain_peer(app_ctx, peer))
                        self._tasks[peer.id] = new_task
                        app_ctx.register_task(new_task)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            LOGGER.info("client connector cancelled")
            raise

    async def _maintain_peer(self, app_ctx: AppContext, peer_cfg: PeerConfig) -> None:
        """维护单个 peer 的连接与重试。"""

        backoff = app_ctx.cfg.connectivity.backoff  # 退避序列
        attempt = 0  # 当前重试次数
        peer_state = app_ctx.peer_states.get(peer_cfg.id)
        while True:
            try:
                await self._connect_once(app_ctx, peer_cfg)  # 尝试建立连接
                attempt = 0  # 成功后重置计数
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("connection to %s failed: %s", peer_cfg.id, exc)
                if peer_state is not None:
                    await peer_state.mark_error(str(exc))
                delay = backoff[min(attempt, len(backoff) - 1)]  # 计算退避时间
                attempt += 1
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(1)  # 轻微间隔以避免紧密循环

    async def _connect_once(self, app_ctx: AppContext, peer_cfg: PeerConfig) -> None:
        """执行一次连接与握手流程。"""

        host, port = AppContext.parse_peer_address(peer_cfg.addr)  # 解析地址
        ssl_context = self._get_ssl_context(app_ctx)  # 获取 TLS 上下文
        timeout = app_ctx.cfg.connectivity.connect_timeout_ms / 1000.0  # 连接超时
        LOGGER.info("connecting to peer %s at %s:%s", peer_cfg.id, host, port)
        reader: asyncio.StreamReader
        writer: asyncio.StreamWriter
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=host, port=port, ssl=ssl_context, ssl_handshake_timeout=timeout),
            timeout=timeout,
        )  # 建立 TLS 连接
        ssl_object = writer.get_extra_info("ssl_object")  # 获取 SSL 对象
        if ssl_object is None:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("TLS handshake missing ssl_object")
        fingerprint = extract_peer_fingerprint(ssl_object)  # 计算服务器指纹
        if peer_cfg.cert_fingerprint:
            if peer_cfg.cert_fingerprint != fingerprint:  # 严格匹配配置指纹
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("fingerprint mismatch")
        elif not fingerprint_in_whitelist(fingerprint, app_ctx.cfg.security.fingerprint_whitelist):  # 没有配置指纹则检查白名单
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("fingerprint not allowed")
        peer_state = app_ctx.peer_states.get(peer_cfg.id)
        allowed_shares = app_ctx.get_allowed_shares_for_peer(peer_cfg.id)  # 本节点允许的共享
        hello = handshake.build_hello(app_ctx.cfg, allowed_shares)  # 构造 HELLO
        await send_json(writer, hello)  # 发送 HELLO
        LOGGER.debug("HELLO sent to %s", peer_cfg.id)
        ack_raw = await asyncio.wait_for(recv_json(reader), timeout=timeout)  # 等待 ACK
        ack = handshake.validate_ack(ack_raw)  # 校验 ACK
        if not ack["ok"]:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError(f"handshake rejected: {ack.get('reason')}")
        if ack["peer_id"] != peer_cfg.id:  # 确认对端身份
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("peer id mismatch")
        now = time.time()  # 记录握手时间
        if peer_state is not None:
            await peer_state.mark_handshake(
                hello_ts=now,
                ack_ts=now,
                fingerprint=fingerprint,
                allowed_shares=allowed_shares,
                remote_capabilities=ack["capabilities"],
            )
        await self._connection_loop(app_ctx, peer_cfg, reader, writer, peer_state)

    async def _connection_loop(
        self,
        app_ctx: AppContext,
        peer_cfg: PeerConfig,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_state: Optional[PeerInfo],
    ) -> None:
        """在握手后维持心跳循环。"""

        heartbeat_interval = app_ctx.cfg.connectivity.heartbeat_sec  # 心跳间隔
        heartbeat_timeout = heartbeat_interval * 3  # 接收超时阈值

        async def sender() -> None:
            while True:
                await asyncio.sleep(heartbeat_interval)  # 等待下次发送时间
                heartbeat = handshake.build_heartbeat(int(time.time()))  # 构造心跳
                await send_json(writer, heartbeat)
                if peer_state is not None:
                    await peer_state.mark_heartbeat(time.time())  # 更新心跳时间
                LOGGER.debug("heartbeat sent to %s", peer_cfg.id)

        async def receiver() -> None:
            while True:
                try:
                    message = await asyncio.wait_for(recv_json(reader), timeout=heartbeat_timeout)  # 等待对端数据
                except asyncio.TimeoutError:
                    continue  # 允许对端在一段时间内静默
                except ProtocolError as exc:
                    raise RuntimeError(f"protocol error: {exc}") from exc
                if message.get("type") == handshake.MSG_HEARTBEAT:
                    heartbeat = handshake.validate_heartbeat(message)  # 校验心跳
                    if peer_state is not None:
                        await peer_state.mark_heartbeat(time.time())
                    LOGGER.debug("heartbeat received from %s ts=%s", peer_cfg.id, heartbeat["ts"])
                else:
                    LOGGER.warning("unexpected message from %s: %s", peer_cfg.id, message)

        sender_task = asyncio.create_task(sender())  # 启动发送任务
        receiver_task = asyncio.create_task(receiver())  # 启动接收任务
        try:
            await asyncio.wait(
                {sender_task, receiver_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in (sender_task, receiver_task):
                if task.done() and task.exception():
                    raise task.exception()
        except asyncio.CancelledError:
            sender_task.cancel()  # 取消子任务
            receiver_task.cancel()
            await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
            raise
        finally:
            sender_task.cancel()  # 确保子任务结束
            receiver_task.cancel()
            await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
            writer.close()  # 关闭底层连接
            await writer.wait_closed()
            if peer_state is not None:
                await peer_state.mark_disconnected()
            LOGGER.info("connection to %s closed", peer_cfg.id)
