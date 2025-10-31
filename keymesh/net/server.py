"""Asyncio TLS server for KeyMesh."""

from __future__ import annotations

import asyncio  # asyncio 驱动事件循环
import logging  # logging 输出运行日志
import time  # time 用于时间戳
from typing import Optional

from .conn_state import PeerInfo
from .framing import ProtocolError, recv_json, send_json
from .mtls import build_server_context, extract_peer_fingerprint, fingerprint_in_whitelist
from ..app import AppContext
from ..proto import handshake

LOGGER = logging.getLogger(__name__)


async def _handle_client(app_ctx: AppContext, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """处理单个入站连接。"""

    peername = writer.get_extra_info("peername")  # 记录对端地址
    ssl_object = writer.get_extra_info("ssl_object")  # 获取底层 SSL 对象
    if ssl_object is None:  # 缺失 SSL 表示握手异常
        LOGGER.error("incoming connection missing SSL context from %s", peername)
        writer.close()  # 主动关闭连接
        await writer.wait_closed()
        return
    try:
        fingerprint = extract_peer_fingerprint(ssl_object)  # 计算证书指纹
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("failed to extract fingerprint from %s: %s", peername, exc)
        writer.close()
        await writer.wait_closed()
        return
    LOGGER.info("accepted TLS connection from %s fingerprint=%s", peername, fingerprint)
    peer_cfg = app_ctx.get_peer_by_fingerprint(fingerprint)  # 通过指纹匹配配置
    allowed_by_whitelist = fingerprint_in_whitelist(fingerprint, app_ctx.cfg.security.fingerprint_whitelist)  # 白名单检查
    peer_state: Optional[PeerInfo] = None
    if peer_cfg:  # 若找到配置则取出对应状态
        peer_state = app_ctx.peer_states.get(peer_cfg.id)
    handshake_deadline = app_ctx.cfg.connectivity.connect_timeout_ms / 1000.0  # 握手超时（秒）
    try:
        hello_raw = await asyncio.wait_for(recv_json(reader), timeout=handshake_deadline)  # 读取对端 HELLO
        hello = handshake.validate_hello(hello_raw)  # 校验并标准化 HELLO
    except (ProtocolError, asyncio.TimeoutError) as exc:
        LOGGER.warning("HELLO failed from %s: %s", peername, exc)
        await send_json(writer, handshake.build_ack(app_ctx.cfg, False, "invalid HELLO", []))
        writer.close()
        await writer.wait_closed()
        return
    remote_id = hello["node_id"]  # 提取对端节点 ID
    remote_version = hello["version"]  # 对端协议版本
    remote_caps = hello["capabilities"]  # 对端宣称能力
    if not handshake.PROTO_VERSION.split(".")[0] == remote_version.split(".")[0]:  # 仅接受相同主版本
        await send_json(writer, handshake.build_ack(app_ctx.cfg, False, "incompatible version", []))
        if peer_state is not None:
            await peer_state.mark_error(f"version mismatch remote={remote_version}")  # 记录错误信息
        writer.close()
        await writer.wait_closed()
        LOGGER.warning("version mismatch: local=%s remote=%s", handshake.PROTO_VERSION, remote_version)
        return
    if peer_cfg is None:  # 指纹未匹配时，根据 node_id 再次查找配置
        peer_cfg = app_ctx.get_peer_config(remote_id)
        if peer_cfg:
            peer_state = app_ctx.peer_states.get(peer_cfg.id)
    if peer_cfg is None:  # 仍未找到配置
        if not allowed_by_whitelist:  # 白名单也未授权则直接拒绝
            await send_json(writer, handshake.build_ack(app_ctx.cfg, False, "unknown peer", []))
            writer.close()
            await writer.wait_closed()
            LOGGER.warning("connection rejected unknown peer %s fingerprint=%s", remote_id, fingerprint)
            return
        await send_json(writer, handshake.build_ack(app_ctx.cfg, False, "peer not configured", []))
        writer.close()
        await writer.wait_closed()
        LOGGER.warning("connection rejected unconfigured peer %s (whitelist matched)", remote_id)
        return
    elif peer_cfg.cert_fingerprint and peer_cfg.cert_fingerprint != fingerprint:  # 配置指纹不一致
        await send_json(writer, handshake.build_ack(app_ctx.cfg, False, "fingerprint mismatch", []))
        if peer_state is not None:
            await peer_state.mark_error("fingerprint mismatch")
        writer.close()
        await writer.wait_closed()
        LOGGER.warning("fingerprint mismatch for %s expected=%s got=%s", peer_cfg.id, peer_cfg.cert_fingerprint, fingerprint)
        return
    allowed_shares = app_ctx.get_allowed_shares_for_peer(peer_cfg.id if peer_cfg else remote_id)  # 计算授权共享
    ack_message = handshake.build_ack(app_ctx.cfg, True, None, allowed_shares)  # 生成成功 ACK
    await send_json(writer, ack_message)  # 发送 ACK
    LOGGER.info("handshake ACK sent to %s", remote_id)
    if peer_state is None and peer_cfg:  # 若状态缺失则初始化
        peer_state = app_ctx.peer_states.setdefault(peer_cfg.id, PeerInfo(id=peer_cfg.id, addr=peer_cfg.addr))
    if peer_state is not None:
        now = time.time()  # 记录当前时间
        await peer_state.mark_handshake(
            hello_ts=now,
            ack_ts=now,
            fingerprint=fingerprint,
            allowed_shares=allowed_shares,
            remote_capabilities=remote_caps,
        )
    heartbeat_timeout = app_ctx.cfg.connectivity.heartbeat_sec * 3  # 心跳超时阈值
    error_message: Optional[str] = None  # 记录可能的异常
    try:
        while True:
            try:
                message = await asyncio.wait_for(recv_json(reader), timeout=heartbeat_timeout)  # 等待下一条消息
            except asyncio.TimeoutError:
                LOGGER.warning("heartbeat timeout from %s", remote_id)
                raise
            msg_type = message.get("type")  # 解析消息类型
            if msg_type == handshake.MSG_HEARTBEAT:
                hb = handshake.validate_heartbeat(message)  # 校验心跳结构
                if peer_state is not None:
                    await peer_state.mark_heartbeat(time.time())  # 更新心跳时间
                LOGGER.debug("heartbeat received from %s ts=%s", remote_id, hb["ts"])
                continue
            LOGGER.warning("unexpected message type=%s from %s", msg_type, remote_id)
    except asyncio.CancelledError:
        LOGGER.info("connection handler cancelled for %s", remote_id)
        raise
    except Exception as exc:  # noqa: BLE001
        error_message = str(exc)
        LOGGER.info("connection with %s closed: %s", remote_id, exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        if peer_state is not None:
            if error_message:
                await peer_state.mark_error(error_message)
            else:
                await peer_state.mark_disconnected()
        LOGGER.info("peer %s disconnected", remote_id)


async def serve_forever(app_ctx: AppContext) -> None:
    """启动 TLS 服务器并持续运行。"""

    ssl_context = build_server_context(app_ctx.cfg)  # 构建服务器 TLS 上下文

    async def _client_factory(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(_handle_client(app_ctx, reader, writer))  # 为每个连接创建任务
        app_ctx.register_task(task)  # 统一登记以便关闭

    server = await asyncio.start_server(
        _client_factory,
        host=app_ctx.cfg.node.bind_host,
        port=app_ctx.cfg.node.listen_port,
        ssl=ssl_context,
    )
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])  # 收集监听地址
    LOGGER.info("server listening on %s", sockets)
    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        LOGGER.info("server shutdown requested")
        server.close()
        await server.wait_closed()
        raise
