"""Handshake message definitions and helpers."""

from __future__ import annotations

from typing import Iterable  # Iterable 便于处理列表

from ..config import KeyMeshConfig  # 导入配置类型

# 协议版本常量，向后兼容时可同时接受旧版本
PROTO_VERSION = "0.2"

# 消息类型常量
MSG_HELLO = "HELLO"
MSG_ACK = "ACK"
MSG_HEARTBEAT = "HEARTBEAT"

# 默认功能标识
DEFAULT_FEATURES = ["mtls", "heartbeat"]


def build_capabilities(allowed_shares: Iterable[str]) -> dict:
    """组装 capabilities 字段。"""

    # 共享列表转换为排序后的唯一集合，方便对比
    share_list = sorted(set(allowed_shares))
    # 返回包含 shares 与 features 的结构
    return {"shares": share_list, "features": list(DEFAULT_FEATURES)}


def build_hello(local_cfg: KeyMeshConfig, allowed_shares_for_peer: Iterable[str]) -> dict:
    """构造 HELLO 消息。"""

    # 调用 build_capabilities 生成能力声明
    capabilities = build_capabilities(allowed_shares_for_peer)
    # 逐字段组装消息体
    message = {
        "type": MSG_HELLO,  # 消息类型
        "node_id": local_cfg.node.id,  # 节点 ID
        "version": PROTO_VERSION,  # 协议版本
        "capabilities": capabilities,  # 能力列表
    }
    # 返回结果
    return message


def build_ack(local_cfg: KeyMeshConfig, ok: bool, reason: str | None, allowed_shares_for_peer: Iterable[str]) -> dict:
    """构造 ACK 消息。"""

    # 组装 capabilities 字段
    capabilities = build_capabilities(allowed_shares_for_peer)
    # 汇总消息字段
    message = {
        "type": MSG_ACK,  # 消息类型
        "ok": bool(ok),  # 是否握手成功
        "reason": reason,  # 失败原因，可为空
        "peer_id": local_cfg.node.id,  # 我方节点 ID
        "capabilities": capabilities,  # 对对端开放的能力
    }
    # 返回 ACK
    return message


def _expect_type(value: object, expected_type: type, field: str) -> None:
    """辅助函数，校验字段类型。"""

    # 检查实例类型，不通过时抛异常
    if not isinstance(value, expected_type):
        raise ValueError(f"{field} must be {expected_type.__name__}")


def _validate_capabilities(obj: object) -> dict:
    """校验 capabilities 结构。"""

    # capabilities 必须是字典
    if not isinstance(obj, dict):
        raise ValueError("capabilities must be an object")
    # shares 字段必须存在且为列表
    shares = obj.get("shares", [])
    if not isinstance(shares, list):
        raise ValueError("capabilities.shares must be a list")
    # 确保 shares 内元素均为字符串
    if not all(isinstance(item, str) for item in shares):
        raise ValueError("capabilities.shares elements must be strings")
    # features 字段可选，缺省为空列表
    features = obj.get("features", [])
    if not isinstance(features, list):
        raise ValueError("capabilities.features must be a list")
    # 元素类型校验
    if not all(isinstance(item, str) for item in features):
        raise ValueError("capabilities.features elements must be strings")
    # 返回副本，避免外部修改
    return {"shares": list(shares), "features": list(features)}


def validate_hello(msg: dict) -> dict:
    """验证 HELLO 消息。"""

    # 确认类型字段正确
    if msg.get("type") != MSG_HELLO:
        raise ValueError("HELLO message missing or invalid type")
    # 校验 node_id 必须为字符串
    node_id = msg.get("node_id")
    _expect_type(node_id, str, "node_id")
    # 校验版本号
    version = msg.get("version")
    _expect_type(version, str, "version")
    # 校验 capabilities 结构
    capabilities = _validate_capabilities(msg.get("capabilities"))
    # 返回标准化后的消息体
    return {
        "type": MSG_HELLO,
        "node_id": node_id,
        "version": version,
        "capabilities": capabilities,
    }


def validate_ack(msg: dict) -> dict:
    """验证 ACK 消息。"""

    # 检查类型
    if msg.get("type") != MSG_ACK:
        raise ValueError("ACK message missing or invalid type")
    # 校验 ok 字段
    ok_value = msg.get("ok")
    if not isinstance(ok_value, bool):
        raise ValueError("ack.ok must be boolean")
    # reason 允许为空或字符串
    reason = msg.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("ack.reason must be string or null")
    # peer_id 必须存在
    peer_id = msg.get("peer_id")
    _expect_type(peer_id, str, "peer_id")
    # capabilities 校验
    capabilities = _validate_capabilities(msg.get("capabilities"))
    # 返回标准化对象
    return {
        "type": MSG_ACK,
        "ok": ok_value,
        "reason": reason,
        "peer_id": peer_id,
        "capabilities": capabilities,
    }


def build_heartbeat(timestamp: int) -> dict:
    """构造 HEARTBEAT 消息。"""

    # 逐字段设置心跳消息
    return {
        "type": MSG_HEARTBEAT,
        "ts": int(timestamp),
    }


def validate_heartbeat(msg: dict) -> dict:
    """验证 HEARTBEAT 消息。"""

    # 检查类型
    if msg.get("type") != MSG_HEARTBEAT:
        raise ValueError("HEARTBEAT message missing or invalid type")
    # 时间戳必须是整数
    ts = msg.get("ts")
    if not isinstance(ts, int):
        raise ValueError("heartbeat.ts must be integer")
    # 返回标准化结构
    return {"type": MSG_HEARTBEAT, "ts": ts}
