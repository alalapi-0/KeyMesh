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

from rich.console import Console
from rich.table import Table

from .app import AppContext
from .config import KeyMeshConfig, load_config  # 导入配置加载逻辑
from .constants import DEFAULT_CONFIG_FILE, DEFAULT_CONFIG_SAMPLE  # 默认常量
from .diff import compare_manifests
from .manifest_store import load_manifest, load_previous_manifest, save_manifest
from .net import server as net_server
from .net.client import ClientConnector
from .status_http import run_status_http

LOGGER = logging.getLogger(__name__)  # 获取模块级日志记录器
CONSOLE = Console()


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


def _resolve_share_names(cfg: KeyMeshConfig, requested: str | None) -> list[str]:
    """根据用户输入解析共享域列表。"""

    if requested:
        for share in cfg.shares:
            if share.name == requested:
                return [share.name]
        raise ValueError(f"share {requested!r} not defined in config")
    return [share.name for share in cfg.shares]


async def _collect_manifests(app_ctx: AppContext, share_names: list[str], refresh: bool) -> dict[str, dict]:
    """批量获取 manifest。"""

    results: dict[str, dict] = {}
    for name in share_names:
        results[name] = await app_ctx.get_manifest(name, refresh=refresh)
    return results


def _load_peer_manifest(peer_id: str, share_name: str) -> dict | None:
    """尝试加载指定 peer 的 manifest。"""

    candidates = [
        f"{peer_id}_{share_name}",
        f"{peer_id}-{share_name}",
        f"{peer_id}/{share_name}",
    ]
    for key in candidates:
        manifest = load_manifest(key)
        if manifest is not None:
            return manifest
    peer_dir = Path("out/manifests") / peer_id
    alt = peer_dir / f"{share_name}_latest.json"
    if alt.exists():
        return json.loads(alt.read_text(encoding="utf-8"))
    return None


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


def command_manifest(args: argparse.Namespace) -> int:
    """处理 manifest 子命令。"""

    try:
        cfg = load_config(args.config, check_files=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    try:
        share_names = _resolve_share_names(cfg, args.share)
    except ValueError as exc:
        LOGGER.error(str(exc))
        return 1
    if args.out and len(share_names) != 1:
        LOGGER.error("--out can only be used when targeting a single share")
        return 1
    async def _run() -> dict[str, dict]:
        app_ctx = AppContext(cfg, logging.getLogger("keymesh.app"), build_runtime=False)
        return await _collect_manifests(app_ctx, share_names, refresh=True)

    try:
        manifests = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("manifest generation failed: %s", exc)
        return 1
    table = Table(title="Manifest Summary", header_style="bold")
    table.add_column("Share", style="cyan")
    table.add_column("Entries", justify="right")
    table.add_column("Ignored", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Saved Path", overflow="fold")
    export_path: Path | None = None
    for share_name in share_names:
        manifest = manifests[share_name]
        saved_path = save_manifest(share_name, manifest)
        target_path = saved_path
        if args.out:
            export_path = Path(args.out).expanduser().resolve()
            export_path.parent.mkdir(parents=True, exist_ok=True)
            with export_path.open("w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            target_path = export_path
        policy = manifest.get("policy", {})
        table.add_row(
            share_name,
            str(len(manifest.get("entries", []))),
            str(policy.get("ignore_count", 0)),
            str(policy.get("skipped", 0)),
            str(target_path),
        )
    CONSOLE.print(table)
    if export_path:
        CONSOLE.print(f"[green]Manifest exported to {export_path}[/green]")
    return 0


def command_diff(args: argparse.Namespace) -> int:
    """处理 diff 子命令。"""

    try:
        cfg = load_config(args.config, check_files=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    try:
        share_names = _resolve_share_names(cfg, args.share)
    except ValueError as exc:
        LOGGER.error(str(exc))
        return 1
    async def _ensure_local() -> dict[str, dict]:
        app_ctx = AppContext(cfg, logging.getLogger("keymesh.app"), build_runtime=False)
        existing: dict[str, dict] = {}
        for name in share_names:
            manifest = load_manifest(name)
            if manifest is None:
                manifest = await app_ctx.get_manifest(name, refresh=True)
                save_manifest(name, manifest)
            existing[name] = manifest
        return existing

    try:
        local_manifests = asyncio.run(_ensure_local())
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load local manifests: %s", exc)
        return 1
    diffs: dict[str, dict] = {}
    for share_name in share_names:
        remote_manifest = _load_peer_manifest(args.peer, share_name)
        if remote_manifest is None and args.peer in {cfg.node.id, "local"}:
            remote_manifest = load_previous_manifest(share_name)
        if remote_manifest is None:
            LOGGER.warning("no remote manifest found for peer=%s share=%s", args.peer, share_name)
            continue
        diffs[share_name] = compare_manifests(local_manifests[share_name], remote_manifest)
    if not diffs:
        LOGGER.error("unable to compute diff: missing remote manifests")
        return 1
    table = Table(title=f"Diff vs {args.peer}", header_style="bold")
    table.add_column("Share", style="cyan")
    table.add_column("Added", justify="right")
    table.add_column("Modified", justify="right")
    table.add_column("Deleted", justify="right")
    for share_name, diff_result in diffs.items():
        summary = diff_result.get("summary", {})
        table.add_row(
            share_name,
            str(summary.get("added", 0)),
            str(summary.get("modified", 0)),
            str(summary.get("deleted", 0)),
        )
    CONSOLE.print(table)
    if not args.dry_run:
        for share_name, diff_result in diffs.items():
            if diff_result.get("summary", {}).get("delta", 0) == 0:
                continue
            CONSOLE.print(f"[yellow]{share_name} changes:[/yellow]")
            for label in ("added", "modified", "deleted"):
                paths = diff_result.get(label, [])
                if not paths:
                    continue
                CONSOLE.print(f"  [bold]{label.title()}[/bold]: {', '.join(paths)}")
    if args.output and not args.dry_run:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump(diffs, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        CONSOLE.print(f"[green]Diff written to {out_path}[/green]")
    if args.output and args.dry_run:
        LOGGER.info("dry-run enabled; diff result not written to %s", args.output)
    return 0


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
    if app_ctx.transfer_engine is not None:
        engine_task = asyncio.create_task(app_ctx.transfer_engine.run_forever())
        app_ctx.register_task(engine_task)
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
        if app_ctx.transfer_engine is not None:
            await app_ctx.transfer_engine.stop()
        await app_ctx.cancel_all_tasks()
    return 0


def command_run(args: argparse.Namespace) -> int:
    """处理 run 子命令。"""

    try:
        return asyncio.run(_run_service(args))
    except KeyboardInterrupt:
        LOGGER.info("received keyboard interrupt, shutting down")
        return 0


def command_send(args: argparse.Namespace) -> int:
    """手动触发单个文件传输任务。"""

    try:
        cfg = load_config(args.config, check_files=True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1

    async def _run() -> int:
        app_ctx = AppContext(cfg, logging.getLogger("keymesh.app"))
        engine = app_ctx.transfer_engine
        if engine is None:
            raise RuntimeError("transfer engine not initialized")
        runner = asyncio.create_task(engine.run_forever())
        try:
            state = await engine.enqueue(
                args.peer,
                args.share,
                {"path": args.file},
            )
            last_status = None
            last_bytes = -1
            while state.status not in {"success", "failed", "cancelled"}:
                if state.status != last_status or state.bytes_done != last_bytes:
                    percent = 0.0
                    if state.total_bytes:
                        percent = (state.bytes_done / state.total_bytes) * 100
                    CONSOLE.print(
                        f"[{state.status}] #{state.task_id} -> peer={state.peer_id} "
                        f"share={state.share} file={state.relative_path} ({percent:.1f}%)"
                    )
                    last_status = state.status
                    last_bytes = state.bytes_done
                await asyncio.sleep(0.5)
            await engine.stop()
            await asyncio.gather(runner, return_exceptions=True)
            if state.status != "success":
                raise RuntimeError(state.error or "transfer failed")
            CONSOLE.print(
                f"[green]Transfer complete:[/green] {state.relative_path} -> {state.peer_id}"
            )
            return 0
        finally:
            await engine.stop()
            if not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("send command failed: %s", exc)
        return 1


def command_queue(args: argparse.Namespace) -> int:
    """查看当前传输任务队列。"""

    try:
        cfg = load_config(args.config, check_files=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    snapshot = Path(cfg.transfer.sessions_dir) / "queue.json"
    if not snapshot.exists():
        CONSOLE.print("[green]queue empty[/green]")
        return 0
    try:
        tasks = json.loads(snapshot.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.error("failed to parse queue snapshot: %s", exc)
        return 1
    if not tasks:
        CONSOLE.print("[green]queue empty[/green]")
        return 0
    table = Table(title="Transfer Queue", header_style="bold")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("Peer")
    table.add_column("Share")
    table.add_column("File", overflow="fold")
    table.add_column("Progress", justify="right")
    for entry in tasks:
        total = entry.get("total_bytes", 0) or 0
        done = entry.get("bytes_done", 0) or 0
        percent = 0.0
        if total:
            percent = (done / total) * 100
        table.add_row(
            entry.get("status", "unknown"),
            f"#{entry.get('task_id')}",
            entry.get("peer", "?"),
            entry.get("share", "?"),
            entry.get("file", ""),
            f"{percent:.1f}%",
        )
    CONSOLE.print(table)
    return 0


def command_cancel(args: argparse.Namespace) -> int:
    """取消指定任务。"""

    try:
        cfg = load_config(args.config, check_files=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("failed to load config: %s", exc)
        return 1
    flag = Path(cfg.transfer.sessions_dir) / f"cancel_{args.task_id}.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("cancelled", encoding="utf-8")
    CONSOLE.print(f"[yellow]cancel flag written for task {args.task_id}[/yellow]")
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
    manifest_parser = subparsers.add_parser("manifest", help="generate share manifest")
    manifest_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    manifest_parser.add_argument("--share", help="target share name; default is all")
    manifest_parser.add_argument("--out", help="optional export file for single share")
    manifest_parser.set_defaults(func=command_manifest)
    diff_parser = subparsers.add_parser("diff", help="compare manifests with a peer")
    diff_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    diff_parser.add_argument("--peer", required=True, help="peer identifier")
    diff_parser.add_argument("--share", help="target share name; default is all")
    diff_parser.add_argument("--output", help="write diff result to JSON")
    diff_parser.add_argument("--dry-run", action="store_true", help="print summary only")
    diff_parser.set_defaults(func=command_diff)
    run_parser = subparsers.add_parser("run", help="execute the KeyMesh service")
    run_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    run_parser.add_argument("--status-port", type=int, help="override status HTTP port")
    run_parser.add_argument("--bind-host", help="override bind host for listener")
    run_parser.add_argument("--once-handshake", action="store_true", help="exit after all peers handshake once")
    run_parser.set_defaults(func=command_run)
    send_parser = subparsers.add_parser("send", help="enqueue a manual transfer task")
    send_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    send_parser.add_argument("--peer", required=True, help="peer identifier")
    send_parser.add_argument("--share", required=True, help="share name")
    send_parser.add_argument("--file", required=True, help="absolute or share-relative file path")
    send_parser.set_defaults(func=command_send)
    queue_parser = subparsers.add_parser("queue", help="inspect transfer queue snapshot")
    queue_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    queue_parser.set_defaults(func=command_queue)
    cancel_parser = subparsers.add_parser("cancel", help="cancel a queued transfer")
    cancel_parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="path to config file")
    cancel_parser.add_argument("task_id", type=int, help="task identifier")
    cancel_parser.set_defaults(func=command_cancel)
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
