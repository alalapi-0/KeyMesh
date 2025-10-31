"""Command line interface for KeyMesh."""

from __future__ import annotations

import argparse  # argparse 用于解析命令行参数
import asyncio  # asyncio 用于运行网络服务
import json  # json 用于格式化状态输出
import logging  # logging 提供日志支持
import shutil  # shutil 负责复制文件
from pathlib import Path  # Path 便于处理文件系统
from typing import Iterable  # Iterable 类型提示
from urllib import error as urlerror  # urllib 错误处理
from urllib import request as urlrequest  # urllib 调用状态页

from .app import AppContext
from .config import KeyMeshConfig, load_config  # 导入配置加载逻辑
from .constants import DEFAULT_CONFIG_FILE, DEFAULT_CONFIG_SAMPLE  # 默认常量
from .net import server as net_server
from .net.client import ClientConnector
from .status_http import run_status_http

LOGGER = logging.getLogger(__name__)  # 获取模块级日志记录器


def _read_post_init_note() -> str:
    """读取 post-init 提示文件。"""

    note_path = Path("scripts/post-init-note.txt")  # 提示文件路径
    if note_path.exists():  # 若文件存在
        return note_path.read_text(encoding="utf-8")  # 返回文本内容
    return "(post-init note missing)"  # 否则返回占位文本


def _ensure_share_directories(cfg: KeyMeshConfig) -> list[str]:
    """确保共享目录存在，并返回提示信息。"""

    messages: list[str] = []  # 收集输出消息
    for share in cfg.shares:  # 遍历每个共享域
        share.path.mkdir(parents=True, exist_ok=True)  # 创建共享目录（若不存在）
        messages.append(f"share ready: {share.name} -> {share.path}")  # 记录提示
        if share.ignore_file:  # 如果配置了忽略文件
            ignore_path = share.path / share.ignore_file  # 计算忽略文件路径
            if not ignore_path.exists():  # 若文件不存在
                ignore_path.write_text("# KeyMesh ignore patterns\n", encoding="utf-8")  # 写入示例内容
                messages.append(f"created ignore file: {ignore_path}")  # 添加提示
    return messages  # 返回消息列表


def command_init(args: argparse.Namespace) -> int:
    """处理 init 子命令。"""

    config_target = Path(DEFAULT_CONFIG_FILE)  # 目标配置文件路径
    sample_path = Path(DEFAULT_CONFIG_SAMPLE)  # 示例配置文件路径
    if not sample_path.exists():  # 确保示例存在
        LOGGER.error("config.sample.yaml not found")  # 记录错误
        return 1  # 返回失败
    if config_target.exists() and not args.force:  # 若目标已存在且未指定覆盖
        LOGGER.warning("config.yaml already exists; use --force to overwrite")  # 给出警告
    else:
        shutil.copy2(sample_path, config_target)  # 复制示例到目标
        LOGGER.info("config.yaml generated from sample")  # 记录成功信息
    try:
        cfg = load_config(sample_path)  # 加载示例配置以获取共享路径
    except Exception as exc:  # 捕获异常
        LOGGER.error("failed to parse sample config: %s", exc)  # 记录错误
        return 1  # 返回失败
    messages = _ensure_share_directories(cfg)  # 确保共享目录存在
    for message in messages:  # 输出每条提示
        LOGGER.info(message)
    note = _read_post_init_note()  # 读取后续提示
    print(note)  # 打印提示信息
    return 0  # 返回成功


def command_check(args: argparse.Namespace) -> int:
    """处理 check 子命令。"""

    try:
        cfg = load_config(args.config, check_files=True)  # 加载配置并检查证书存在
    except FileNotFoundError as exc:  # 配置或证书缺失
        LOGGER.error(str(exc))  # 输出错误
        return 1  # 失败
    except Exception as exc:  # 其他校验错误
        LOGGER.error("configuration error: %s", exc)  # 输出错误
        return 1  # 失败
    messages = _ensure_share_directories(cfg)  # 确保共享目录存在
    for message in messages:  # 输出目录处理信息
        LOGGER.info(message)
    print(f"Node {cfg.node.id} listening on {cfg.node.bind_host}:{cfg.node.listen_port}")  # 打印节点信息
    print(f"Peers configured: {[peer.id for peer in cfg.peers]}")  # 打印 peer 列表
    print("Configuration check passed.")  # 打印成功信息
    return 0  # 返回成功


def command_list_shares(args: argparse.Namespace) -> int:
    """列出共享域列表。"""

    try:
        cfg = load_config(args.config, check_files=False)  # 仅加载配置
    except Exception as exc:  # 捕获异常
        LOGGER.error("failed to load config: %s", exc)  # 记录错误
        return 1  # 返回失败
    for share in cfg.shares:  # 遍历共享域
        print(f"{share.name}: {share.path}")  # 打印共享信息
    return 0  # 返回成功


async def _run_service(args: argparse.Namespace) -> int:
    """异步执行 run 子命令。"""

    try:
        cfg = load_config(args.config, check_files=True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    if args.bind_host:
        cfg.node.bind_host = args.bind_host
    if args.status_port is not None:
        cfg.status_http.port = args.status_port
        cfg.status_http.enabled = True
    messages = _ensure_share_directories(cfg)
    for message in messages:
        LOGGER.info(message)
    app_ctx = AppContext(cfg, logging.getLogger("keymesh.app"))
    server_task = asyncio.create_task(net_server.serve_forever(app_ctx))
    app_ctx.register_task(server_task)
    connector = ClientConnector()
    client_task = asyncio.create_task(connector.run(app_ctx))
    app_ctx.register_task(client_task)
    if cfg.status_http.enabled:
        status_task = asyncio.create_task(run_status_http(app_ctx, host=cfg.status_http.host, port=cfg.status_http.port))
        app_ctx.register_task(status_task)
    LOGGER.info(
        "KeyMesh node %s listening on %s:%s",
        cfg.node.id,
        cfg.node.bind_host,
        cfg.node.listen_port,
    )
    forever: asyncio.Future[None] | None = None
    try:
        if args.once_handshake:
            LOGGER.info("waiting for all peers to complete handshake once")
            await app_ctx.wait_all_handshakes()
            LOGGER.info("all configured peers have completed handshake; exiting")
        else:
            forever = asyncio.get_running_loop().create_future()
            await forever
    except asyncio.CancelledError:
        if forever is not None:
            forever.cancel()
        raise
    finally:
        if forever is not None:
            forever.cancel()
        await app_ctx.cancel_all_tasks()
    return 0


def command_run(args: argparse.Namespace) -> int:
    """处理 run 子命令。"""

    try:
        return asyncio.run(_run_service(args))
    except KeyboardInterrupt:
        LOGGER.info("received keyboard interrupt, shutting down")
        return 0


def command_peers(args: argparse.Namespace) -> int:
    """访问状态页并输出 peers 状态。"""

    try:
        cfg = load_config(args.config, check_files=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    port = args.port or cfg.status_http.port
    host = cfg.status_http.host
    url = f"http://{host}:{port}/peers"
    try:
        with urlrequest.urlopen(url, timeout=5) as response:
            payload = json.load(response)
    except urlerror.URLError as exc:  # 连接失败
        LOGGER.error("failed to query %s: %s", url, exc)
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def command_placeholder(name: str) -> int:
    """输出占位符提示。"""

    print(f"{name} 命令将在后续版本实现。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建顶层解析器。"""

    parser = argparse.ArgumentParser(prog="keymesh", description="KeyMesh CLI scaffold")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="initialize sample config and directories")
    init_parser.add_argument("--force", action="store_true", help="overwrite existing config.yaml")
    init_parser.set_defaults(func=command_init)
    check_parser = subparsers.add_parser("check", help="validate config.yaml and environment")
    check_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    check_parser.set_defaults(func=command_check)
    list_parser = subparsers.add_parser("list-shares", help="list configured shares")
    list_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    list_parser.set_defaults(func=command_list_shares)
    run_parser = subparsers.add_parser("run", help="execute the KeyMesh service")
    run_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    run_parser.add_argument("--status-port", type=int, help="override status HTTP port")
    run_parser.add_argument("--bind-host", help="override bind host for listener")
    run_parser.add_argument("--once-handshake", action="store_true", help="exit after all peers handshake once")
    run_parser.set_defaults(func=command_run)
    peers_parser = subparsers.add_parser("peers", help="show peer connection status")
    peers_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    peers_parser.add_argument("--port", type=int, help="status HTTP port override")
    peers_parser.set_defaults(func=command_peers)
    add_peer_parser = subparsers.add_parser("add-peer", help="add a peer definition (placeholder)")
    add_peer_parser.set_defaults(func=lambda args: command_placeholder("add-peer"))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """CLI 主入口。"""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)
