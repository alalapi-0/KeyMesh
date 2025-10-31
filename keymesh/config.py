"""Configuration loading and validation for KeyMesh."""

from __future__ import annotations

from dataclasses import dataclass, field  # dataclass 用于定义结构化配置对象
from pathlib import Path  # Path 提供跨平台路径处理
from typing import List

import yaml  # PyYAML 用于解析配置文件

from .constants import DEFAULT_CONFIG_FILE  # 默认配置文件名
from .utils.pathing import ensure_within, normalize_path  # 路径归一化与越权检测


@dataclass(slots=True)
class ShareConfig:
    """描述单个共享域。"""

    name: str  # 共享域名称
    path: Path  # 共享域路径（归一化）
    delete_propagation: bool  # 删除是否向对端传播
    ignore_file: str | None = None  # 可选的忽略文件名


@dataclass(slots=True)
class PeerAccess:
    """描述 peer 对共享域的访问模式。"""

    share: str  # 共享域名称
    mode: str  # 访问模式，只能是 ro 或 rw


@dataclass(slots=True)
class PeerConfig:
    """单个 peer 的配置。"""

    id: str  # 节点 ID
    addr: str  # 网络地址
    cert_fingerprint: str  # 证书指纹
    shares_access: List[PeerAccess] = field(default_factory=list)  # 允许访问的共享列表


@dataclass(slots=True)
class NodeConfig:
    """本地节点配置。"""

    id: str  # 节点 ID
    listen_port: int  # 监听端口
    bind_host: str = "0.0.0.0"  # 绑定地址，默认 0.0.0.0


@dataclass(slots=True)
class SecurityConfig:
    """证书与密钥路径配置。"""

    ca_cert: Path  # CA 证书路径
    cert: Path  # 节点证书路径
    key: Path  # 节点私钥路径
    fingerprint_whitelist: List[str] = field(default_factory=list)  # 允许的指纹列表


@dataclass(slots=True)
class ConnectivityConfig:
    """网络层连接参数。"""

    heartbeat_sec: int  # 心跳间隔秒
    connect_timeout_ms: int  # TLS 连接超时毫秒
    backoff: List[int] = field(default_factory=list)  # 重连退避序列


@dataclass(slots=True)
class StatusHttpConfig:
    """状态页 HTTP 服务配置。"""

    enabled: bool  # 是否启用状态页
    host: str  # 监听地址
    port: int  # 监听端口


@dataclass(slots=True)
class TransferConfig:
    """传输行为配置。"""

    chunk_mb: int  # 单块大小
    concurrent_files: int  # 并发文件数
    concurrent_chunks_per_file: int  # 每个文件并发块
    rate_limit_mbps: int  # 限速，0 表示不限


@dataclass(slots=True)
class LoggingConfig:
    """日志配置。"""

    level: str  # 日志级别
    file: Path | None  # 日志文件


@dataclass(slots=True)
class IndexingConfig:
    """索引扫描配置。"""

    small_threshold_mb: int
    sample_mb: int
    hash_policy: str
    ignore_hidden: bool
    max_workers: int


@dataclass(slots=True)
class KeyMeshConfig:
    """聚合所有配置段的顶层对象。"""

    node: NodeConfig  # 本地节点
    security: SecurityConfig  # 安全配置
    peers: List[PeerConfig]  # 对端列表
    shares: List[ShareConfig]  # 共享定义
    transfer: TransferConfig  # 传输配置
    logging: LoggingConfig  # 日志配置
    indexing: IndexingConfig  # 索引配置
    connectivity: ConnectivityConfig  # 网络连接配置
    status_http: StatusHttpConfig  # 状态页配置


def _load_yaml(path: Path) -> dict:
    """辅助函数：读取 YAML 文件并返回字典。"""
    with path.open("r", encoding="utf-8") as f:  # 打开文件，使用 UTF-8 编码
        data = yaml.safe_load(f) or {}  # 安全解析 YAML，空文件回退为空字典
    if not isinstance(data, dict):  # 若顶层不是 dict 则抛错
        raise ValueError("Configuration root must be a mapping")  # 提示错误结构
    return data  # 返回解析结果


def load_config(config_path: str | Path = DEFAULT_CONFIG_FILE, *, check_files: bool = False) -> KeyMeshConfig:
    """加载并校验配置文件，必要时检查文件存在性。"""
    path = Path(config_path).expanduser().resolve()  # 解析配置文件路径
    if not path.exists():  # 若文件不存在
        raise FileNotFoundError(f"Config file {path} not found")  # 抛出文件不存在错误
    raw = _load_yaml(path)  # 读取原始字典
    node_raw = raw.get("node") or {}  # 获取 node 段
    security_raw = raw.get("security") or {}  # 获取 security 段
    peers_raw = raw.get("peers") or []  # 获取 peers 列表
    shares_raw = raw.get("shares") or []  # 获取 shares 列表
    transfer_raw = raw.get("transfer") or {}  # 获取 transfer 段
    logging_raw = raw.get("logging") or {}  # 获取 logging 段
    indexing_raw = raw.get("indexing") or {}  # 获取 indexing 段
    connectivity_raw = raw.get("connectivity") or {}  # 获取 connectivity 段
    status_http_raw = raw.get("status_http") or {}  # 获取状态页配置
    node = NodeConfig(  # 构造 NodeConfig
        id=node_raw.get("id", ""),  # 节点 ID
        listen_port=int(node_raw.get("listen_port", 0)),  # 监听端口
        bind_host=node_raw.get("bind_host", "0.0.0.0"),  # 绑定地址
    )
    security = SecurityConfig(  # 构造 SecurityConfig
        ca_cert=normalize_path(path.parent, security_raw.get("ca_cert", "")),  # CA 证书绝对路径
        cert=normalize_path(path.parent, security_raw.get("cert", "")),  # 节点证书绝对路径
        key=normalize_path(path.parent, security_raw.get("key", "")),  # 私钥绝对路径
        fingerprint_whitelist=[(fp or "").strip().lower() for fp in security_raw.get("fingerprint_whitelist", [])],  # 指纹白名单
    )
    shares: List[ShareConfig] = []  # 初始化 share 容器
    seen_share_names: set[str] = set()  # 记录已出现的共享名称
    for entry in shares_raw:  # 遍历每个共享定义
        name = entry.get("name", "")  # 读取共享名
        if not name:  # 名称不可为空
            raise ValueError("Share name cannot be empty")  # 抛出错误
        if name in seen_share_names:  # 检查重复
            raise ValueError(f"Duplicate share name: {name}")  # 抛错提示重复
        seen_share_names.add(name)  # 记录名称
        path_value = entry.get("path", "")  # 获取路径
        normalized = ensure_within(path.parent, path_value)  # 保证路径未越权
        share = ShareConfig(  # 构造 ShareConfig
            name=name,
            path=normalized,
            delete_propagation=bool(entry.get("delete_propagation", False)),
            ignore_file=entry.get("ignore_file"),
        )
        shares.append(share)  # 添加到列表
    share_names = {share.name for share in shares}  # 创建名称集合便于校验
    peers: List[PeerConfig] = []  # 初始化 peer 列表
    for peer_entry in peers_raw:  # 遍历 peer 定义
        access_list: List[PeerAccess] = []  # 初始化访问列表
        for access in peer_entry.get("shares_access", []):  # 遍历访问权限
            share_name = access.get("share")  # 获取共享名
            if share_name not in share_names:  # 共享必须存在
                raise ValueError(f"Peer references unknown share: {share_name}")  # 抛错
            mode = (access.get("mode") or "").lower()  # 读取访问模式并转小写
            if mode not in {"ro", "rw"}:  # 仅允许 ro/rw
                raise ValueError(f"Invalid share access mode: {mode}")  # 抛错
            access_list.append(PeerAccess(share=share_name, mode=mode))  # 添加访问定义
        peer = PeerConfig(  # 构造 PeerConfig
            id=peer_entry.get("id", ""),
            addr=peer_entry.get("addr", ""),
            cert_fingerprint=((peer_entry.get("cert_fingerprint", "") or "").strip().lower()),
            shares_access=access_list,
        )
        peers.append(peer)  # 将 peer 加入列表
    transfer = TransferConfig(  # 构造 TransferConfig
        chunk_mb=int(transfer_raw.get("chunk_mb", 0)),
        concurrent_files=int(transfer_raw.get("concurrent_files", 0)),
        concurrent_chunks_per_file=int(transfer_raw.get("concurrent_chunks_per_file", 0)),
        rate_limit_mbps=int(transfer_raw.get("rate_limit_mbps", 0)),
    )
    logging_config = LoggingConfig(  # 构造 LoggingConfig
        level=logging_raw.get("level", "info"),
        file=normalize_path(path.parent, logging_raw["file"]) if logging_raw.get("file") else None,
    )
    indexing = IndexingConfig(
        small_threshold_mb=int(indexing_raw.get("small_threshold_mb", 16)),
        sample_mb=int(indexing_raw.get("sample_mb", 4)),
        hash_policy=(indexing_raw.get("hash_policy", "auto") or "auto").lower(),
        ignore_hidden=bool(indexing_raw.get("ignore_hidden", True)),
        max_workers=int(indexing_raw.get("max_workers", 4)),
    )
    connectivity = ConnectivityConfig(  # 构造 ConnectivityConfig
        heartbeat_sec=int(connectivity_raw.get("heartbeat_sec", 20)),
        connect_timeout_ms=int(connectivity_raw.get("connect_timeout_ms", 5000)),
        backoff=[int(x) for x in (connectivity_raw.get("backoff") or [1, 3, 10, 30])],
    )
    status_http = StatusHttpConfig(  # 构造状态页配置
        enabled=bool(status_http_raw.get("enabled", True)),
        host=status_http_raw.get("host", "127.0.0.1"),
        port=int(status_http_raw.get("port", 52180)),
    )
    config = KeyMeshConfig(  # 聚合为整体配置对象
        node=node,
        security=security,
        peers=peers,
        shares=shares,
        transfer=transfer,
        logging=logging_config,
        indexing=indexing,
        connectivity=connectivity,
        status_http=status_http,
    )
    # 校验心跳与退避参数
    if config.connectivity.heartbeat_sec <= 0:
        raise ValueError("heartbeat_sec must be positive")
    if config.connectivity.connect_timeout_ms <= 0:
        raise ValueError("connect_timeout_ms must be positive")
    if not config.connectivity.backoff:
        raise ValueError("backoff must contain at least one value")
    for value in config.connectivity.backoff:
        if value <= 0:
            raise ValueError("backoff values must be positive")
    if config.status_http.port <= 0 or config.status_http.port > 65535:
        raise ValueError("status_http.port must be in 1-65535")
    if config.node.listen_port <= 0 or config.node.listen_port > 65535:
        raise ValueError("node.listen_port must be in 1-65535")
    if config.indexing.small_threshold_mb <= 0:
        raise ValueError("indexing.small_threshold_mb must be positive")
    if config.indexing.sample_mb <= 0:
        raise ValueError("indexing.sample_mb must be positive")
    if config.indexing.max_workers <= 0:
        raise ValueError("indexing.max_workers must be positive")
    if config.indexing.hash_policy not in {"auto", "full", "sample", "meta", "none"}:
        raise ValueError("indexing.hash_policy must be one of auto/full/sample/meta/none")
    if check_files:  # 如果需要检查文件存在性
        missing: List[Path] = []  # 记录缺失路径
        for candidate in [security.ca_cert, security.cert, security.key]:  # 遍历证书文件
            if not candidate.exists():  # 不存在则加入缺失列表
                missing.append(candidate)
        if missing:  # 若有缺失
            missing_str = ", ".join(str(p) for p in missing)  # 格式化缺失列表
            raise FileNotFoundError(f"Missing security material: {missing_str}")  # 抛出异常
    return config  # 返回配置对象
