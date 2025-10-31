"""Asynchronous transfer engine coordinating file sync tasks."""

from __future__ import annotations

import asyncio  # 异步调度
import contextlib  # 安全关闭连接
import json  # 持久化队列快照
import logging  # 日志记录
import time  # 时间戳
from dataclasses import dataclass, field  # 数据类定义任务状态
from pathlib import Path  # 路径处理
from typing import Any, Dict  # 类型提示

from ..app import AppContext
from ..config import ShareConfig
from ..proto import handshake
from ..utils.pathing import ensure_within
from ..net.mtls import build_client_context
from ..net.framing import recv_json, send_json
from .audit import log_event
from .protocol import send_file
from .session import TransferSession


@dataclass(slots=True)
class TransferTaskState:
    """Track runtime information for a transfer job."""

    task_id: int
    peer_id: str
    share: str
    relative_path: str
    absolute_path: Path
    mode: str
    total_bytes: int
    status: str = "queued"
    retries: int = 0
    error: str | None = None
    bytes_done: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def mark(self, status: str, *, error: str | None = None) -> None:
        """Update status and timestamp."""

        self.status = status
        self.error = error
        self.updated_at = time.time()


class TransferEngine:
    """Queue-based transfer coordinator with resume and retry support."""

    def __init__(self, app_ctx: AppContext) -> None:
        self.app_ctx = app_ctx
        self.logger = logging.getLogger("keymesh.transfer")
        cfg = app_ctx.cfg.transfer
        self.chunk_size = int(cfg.chunk_size_mb) * 1024 * 1024
        self.max_concurrent = int(cfg.max_concurrent_per_peer)
        self.retry_backoff = [float(v) for v in cfg.retry_backoff_sec]
        self.max_retries = int(cfg.max_retries)
        rate_limit = int(cfg.rate_limit_mb_s)
        self.rate_limit_bytes = rate_limit * 1024 * 1024 if rate_limit > 0 else None
        self.sessions_dir = Path(cfg.sessions_dir).expanduser()
        self.audit_dir = Path(cfg.audit_log_dir).expanduser()
        self.share_map: Dict[str, ShareConfig] = {share.name: share for share in app_ctx.cfg.shares}
        self._task_seq = 0
        self._queues: Dict[str, asyncio.Queue[TransferTaskState]] = {}
        self._tasks: Dict[int, TransferTaskState] = {}
        self._workers: set[asyncio.Task[object]] = set()
        self._stop_event = asyncio.Event()
        self._ssl_context = None
        self._state_lock = asyncio.Lock()

    def _get_queue(self, peer_id: str) -> asyncio.Queue[TransferTaskState]:
        queue = self._queues.get(peer_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[peer_id] = queue
        return queue

    def _next_task_id(self) -> int:
        self._task_seq += 1
        return self._task_seq

    def _resolve_file(self, share_name: str, entry: dict[str, Any]) -> tuple[Path, str, int]:
        share = self.share_map.get(share_name)
        if share is None:
            raise ValueError(f"unknown share {share_name}")
        candidate = entry.get("path") or entry.get("absolute") or entry.get("file")
        if not candidate:
            raise ValueError("file_entry must include path")
        absolute = ensure_within(share.path, candidate)
        try:
            relative = str(absolute.relative_to(share.path))
        except ValueError as exc:
            raise ValueError(f"file {absolute} outside share {share_name}") from exc
        if not absolute.exists():
            raise FileNotFoundError(f"source file not found: {absolute}")
        size = int(entry.get("size") or absolute.stat().st_size)
        return absolute, relative, size

    async def enqueue(
        self,
        peer_id: str,
        share_name: str,
        file_entry: dict,
        mode: str = "push",
    ) -> TransferTaskState:
        absolute, relative, size = self._resolve_file(share_name, file_entry)
        task_id = self._next_task_id()
        state = TransferTaskState(
            task_id=task_id,
            peer_id=peer_id,
            share=share_name,
            relative_path=relative,
            absolute_path=absolute,
            mode=mode,
            total_bytes=size,
        )
        session = TransferSession(
            peer_id,
            share_name,
            absolute,
            mode,
            sessions_dir=self.sessions_dir,
        )
        progress = session.load_progress()
        state.bytes_done = progress.get("bytes_done", 0)
        state.updated_at = time.time()
        self._tasks[state.task_id] = state
        queue = self._get_queue(peer_id)
        await queue.put(state)
        await self._persist_states()
        self.logger.info(
            "enqueued transfer task id=%s peer=%s share=%s path=%s", task_id, peer_id, share_name, relative
        )
        return state

    async def worker(self, peer_id: str) -> None:
        queue = self._get_queue(peer_id)
        while not self._stop_event.is_set():
            try:
                state = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if state.status == "cancelled":
                queue.task_done()
                continue
            flag_path = self._cancel_flag(state.task_id)
            if flag_path.exists():
                with contextlib.suppress(FileNotFoundError):
                    flag_path.unlink()
                state.mark("cancelled")
                await self._persist_states()
                queue.task_done()
                continue
            await self._run_task(state)
            queue.task_done()

    async def _run_task(self, state: TransferTaskState) -> None:
        session = TransferSession(
            state.peer_id,
            state.share,
            state.absolute_path,
            state.mode,
            sessions_dir=self.sessions_dir,
        )
        progress = session.load_progress()
        resume_bytes = min(int(progress.get("bytes_done", 0)), state.total_bytes)
        base_chunk = int(progress.get("chunk_id", 0))
        state.mark("running")
        await self._persist_states()
        flag_path = self._cancel_flag(state.task_id)
        if flag_path.exists():
            with contextlib.suppress(FileNotFoundError):
                flag_path.unlink()
            state.mark("cancelled")
            await self._persist_states()
            return
        peer_cfg = self.app_ctx.get_peer_config(state.peer_id)
        if peer_cfg is None:
            state.mark("failed", error="peer not configured")
            await self._persist_states()
            return
        try:
            result = await self._send_once(state, peer_cfg.addr, resume_bytes, base_chunk, session)
        except asyncio.CancelledError:
            state.mark("cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            state.retries += 1
            state.mark("failed", error=str(exc))
            await self._persist_states()
            self.logger.error(
                "transfer task %s failed (%s/%s): %s",
                state.task_id,
                state.retries,
                self.max_retries,
                exc,
            )
            if state.retries <= self.max_retries:
                delay = self.retry_backoff[min(state.retries - 1, len(self.retry_backoff) - 1)] if self.retry_backoff else 0
                if delay:
                    await asyncio.sleep(delay)
                state.mark("queued")
                await self._persist_states()
                await self._get_queue(state.peer_id).put(state)
            else:
                log_event(
                    state.peer_id,
                    state.share,
                    state.relative_path,
                    "send",
                    "failed",
                    state.bytes_done,
                    0.0,
                    base_dir=self.audit_dir,
                )
                await self._persist_states()
            return
        state.bytes_done = result["bytes"]
        session.finalize()
        state.mark("success")
        await self._persist_states()
        log_event(
            state.peer_id,
            state.share,
            state.relative_path,
            "send",
            "success",
            result["bytes"],
            result["elapsed"],
            base_dir=self.audit_dir,
        )

    async def _send_once(
        self,
        state: TransferTaskState,
        addr: str,
        resume_bytes: int,
        base_chunk: int,
        session: TransferSession,
    ) -> dict:
        host, port = AppContext.parse_peer_address(addr)
        if self._ssl_context is None:
            self._ssl_context = build_client_context(self.app_ctx.cfg)
        reader, writer = await asyncio.open_connection(host=host, port=port, ssl=self._ssl_context)
        try:
            allowed = self.app_ctx.get_allowed_shares_for_peer(state.peer_id)
            hello = handshake.build_hello(self.app_ctx.cfg, allowed)
            await send_json(writer, hello)
            ack_raw = await recv_json(reader)
            ack = handshake.validate_ack(ack_raw)
            if not ack["ok"]:
                raise RuntimeError(ack.get("reason", "handshake rejected"))
            if ack.get("peer_id") and ack["peer_id"] != state.peer_id:
                raise RuntimeError("peer id mismatch during transfer handshake")
            allowed_remote = ack.get("capabilities", {}).get("shares", [])
            if state.share not in allowed_remote:
                raise RuntimeError(f"share {state.share} not permitted by remote")
            start_chunk = base_chunk

            async def _progress(delta: int, chunks: int, bytes_total: int) -> None:
                state.bytes_done = bytes_total
                session.save_progress(start_chunk + chunks, bytes_total)

            result = await send_file(
                reader,
                writer,
                state.absolute_path,
                state.share,
                state.relative_path,
                chunk_size=self.chunk_size,
                resume_offset=resume_bytes,
                rate_limit_bytes_per_sec=self.rate_limit_bytes,
                max_retries=self.max_retries,
                retry_backoff=self.retry_backoff,
                progress_cb=_progress,
            )
            return result
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def run_forever(self) -> None:
        if self._workers:
            return
        for peer in self.app_ctx.cfg.peers:
            for _ in range(max(1, self.max_concurrent)):
                worker_task = asyncio.create_task(self.worker(peer.id))
                self._workers.add(worker_task)
                self.app_ctx.register_task(worker_task)
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        for task in list(self._workers):
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def list_tasks(self) -> list[TransferTaskState]:
        return list(self._tasks.values())

    async def cancel(self, task_id: int) -> bool:
        state = self._tasks.get(task_id)
        if not state:
            return False
        state.mark("cancelled")
        await self._persist_states()
        flag = self._cancel_flag(task_id)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("cancelled", encoding="utf-8")
        return True

    async def _persist_states(self) -> None:
        async with self._state_lock:
            payload = [
                {
                    "task_id": state.task_id,
                    "peer": state.peer_id,
                    "share": state.share,
                    "file": state.relative_path,
                    "status": state.status,
                    "bytes_done": state.bytes_done,
                    "total_bytes": state.total_bytes,
                    "retries": state.retries,
                    "error": state.error,
                    "mode": state.mode,
                }
                for state in sorted(self._tasks.values(), key=lambda item: item.task_id)
            ]
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            snapshot = self.sessions_dir / "queue.json"
            snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cancel_flag(self, task_id: int) -> Path:
        return self.sessions_dir / f"cancel_{task_id}.flag"
