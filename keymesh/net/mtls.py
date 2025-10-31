"""mTLS context helpers for KeyMesh."""

from __future__ import annotations

import hashlib  # hashlib 提供指纹计算
import logging  # logging 输出调试信息
import ssl  # ssl 构建 TLS 上下文
from typing import Iterable  # Iterable 用于类型提示

from ..config import KeyMeshConfig  # 配置对象

LOGGER = logging.getLogger(__name__)  # 模块级日志记录器


def _apply_strict_tls_settings(context: ssl.SSLContext) -> None:
    """对上下文启用严格的 TLS 选项。"""

    # 禁用 TLS 压缩，防御 CRIME
    context.options |= ssl.OP_NO_COMPRESSION
    # 禁用旧协议版本，确保使用 TLS1.2+
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # 若环境支持则优先启用 TLS1.3
    try:
        context.maximum_version = ssl.TLSVersion.TLSv1_3
    except ValueError:
        # 某些旧平台不支持设置 maximum_version，忽略即可
        pass
    # 设置推荐的安全套件
    context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")


def build_server_context(cfg: KeyMeshConfig) -> ssl.SSLContext:
    """根据配置构造服务端 TLS 上下文。"""

    # 使用系统缺省参数创建面向客户端认证的上下文
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # 装载信任的 CA 证书
    context.load_verify_locations(cafile=str(cfg.security.ca_cert))
    # 加载本节点的证书链与私钥
    context.load_cert_chain(certfile=str(cfg.security.cert), keyfile=str(cfg.security.key))
    # 要求客户端必须提供证书
    context.verify_mode = ssl.CERT_REQUIRED
    # 服务端不检查主机名，依赖指纹与配置校验
    context.check_hostname = False
    # 应用统一的安全配置
    _apply_strict_tls_settings(context)
    # 记录调试信息便于排障
    LOGGER.debug("Server TLS context initialized with cert=%s", cfg.security.cert)
    # 返回构建好的上下文
    return context


def build_client_context(cfg: KeyMeshConfig) -> ssl.SSLContext:
    """根据配置构造客户端 TLS 上下文。"""

    # 创建一个验证服务器证书的上下文
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    # 加载受信任的 CA 证书
    context.load_verify_locations(cafile=str(cfg.security.ca_cert))
    # 客户端同样使用双向认证证书
    context.load_cert_chain(certfile=str(cfg.security.cert), keyfile=str(cfg.security.key))
    # 强制校验服务器证书
    context.verify_mode = ssl.CERT_REQUIRED
    # 我们基于指纹校验，不使用主机名匹配
    context.check_hostname = False
    # 应用严格 TLS 设定
    _apply_strict_tls_settings(context)
    # 输出调试信息
    LOGGER.debug("Client TLS context initialized with cert=%s", cfg.security.cert)
    # 返回上下文
    return context


def extract_peer_fingerprint(ssl_object: ssl.SSLObject | ssl.SSLSocket) -> str:
    """从 SSL 对象中提取 SHA-256 指纹。"""

    # 获取对端证书的二进制 DER 表示
    peer_cert = ssl_object.getpeercert(binary_form=True)
    if not peer_cert:
        # 若对端未提供证书，按照协议视为错误
        raise ssl.SSLError("peer certificate missing")
    # 计算 SHA-256 摘要
    digest = hashlib.sha256(peer_cert).hexdigest()
    # 标准化输出格式
    fingerprint = f"sha256:{digest.lower()}"
    # 返回结果
    return fingerprint


def fingerprint_in_whitelist(fingerprint: str, whitelist: Iterable[str]) -> bool:
    """指纹是否匹配白名单。"""

    # 统一小写比较
    normalized = fingerprint.lower()
    # 遍历白名单并检查匹配
    return any(normalized == (entry or "").strip().lower() for entry in whitelist)
