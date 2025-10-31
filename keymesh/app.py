"""Application context for KeyMesh runtime."""

from __future__ import annotations

import asyncio  # asyncio 用于任务管理
import logging  # logging 提供日志对象
from typing import Dict, Iterable, List, Optional, Tuple

from .indexer import build_manifest  # manifest 构建函数
from .utils.ignore import load_ignore_patterns  # 忽略规则加载

from .config import KeyMeshConfig, PeerConfig
from .net.conn_state import PeerInfo


class AppContext:
    """封装运行时共享资源。"""

    def __init__(
        self,
        cfg: KeyMeshConfig,
        logger: Optional[logging.Logger] = None,
        *,
        build_runtime: bool = True,
    ) -> None:
        # 保存配置与日志实例
        self.cfg = cfg
        self.logger = logger or logging.getLogger("keymesh")
        # 构建 peer 配置索引，便于快速查询
        self._peer_by_id: Dict[str, PeerConfig] = {peer.id: peer for peer in cfg.peers}
        self._peer_by_fingerprint: Dict[str, PeerConfig] = {
            peer.cert_fingerprint: peer for peer in cfg.peers if peer.cert_fingerprint
        }
        # 初始化运行时状态（可选）
        if build_runtime:
            self.peer_states: Dict[str, PeerInfo] = {
                peer.id: PeerInfo(
                    id=peer.id,
                    addr=peer.addr,
                    allowed_shares=self.get_allowed_shares_for_peer(peer.id),
                )
                for peer in cfg.peers
            }
        else:
            self.peer_states = {}
        # 跟踪活跃任务，方便统一关闭
        self.tasks: set[asyncio.Task[object]] = set()
        # 缓存最近构建的 manifest，避免重复扫描
        self.manifests: Dict[str, dict] = {}

    def get_peer_config(self, peer_id: str) -> PeerConfig | None:
        """根据 ID 获取 peer 配置。"""

        return self._peer_by_id.get(peer_id)

    def get_peer_by_fingerprint(self, fingerprint: str) -> PeerConfig | None:
        """根据证书指纹匹配 peer。"""

        return self._peer_by_fingerprint.get(fingerprint.lower())

    def get_allowed_shares_for_peer(self, peer_id: str) -> List[str]:
        """返回指定 peer 可访问的共享列表。"""

        peer = self._peer_by_id.get(peer_id)
        if not peer:
            return []
        return [access.share for access in peer.shares_access]

    def list_peer_ids(self) -> Iterable[str]:
        """列出所有已配置 peer ID。"""

        return self._peer_by_id.keys()

    def register_task(self, task: asyncio.Task[object]) -> None:
        """登记后台任务，便于统一取消。"""

        self.tasks.add(task)
        task.add_done_callback(lambda finished: self.tasks.discard(finished))

    async def gather_tasks(self) -> None:
        """等待所有登记任务结束。"""

        tasks_snapshot = list(self.tasks)
        if tasks_snapshot:
            await asyncio.gather(*tasks_snapshot, return_exceptions=True)

    async def cancel_all_tasks(self) -> None:
        """取消所有后台任务。"""

        tasks_snapshot = list(self.tasks)
        for task in tasks_snapshot:
            task.cancel()
        if tasks_snapshot:
            await asyncio.gather(*tasks_snapshot, return_exceptions=True)

    async def wait_all_handshakes(self) -> None:
        """等待全部 peer 至少握手一次。"""

        await asyncio.gather(
            *(state.wait_handshake() for state in self.peer_states.values()),
            return_exceptions=False,
        )

    async def get_manifest(self, share_name: str, refresh: bool = False) -> dict:
        """返回指定共享域的 manifest，可选刷新缓存。"""

        if not refresh and share_name in self.manifests:
            return self.manifests[share_name]
        share = next((item for item in self.cfg.shares if item.name == share_name), None)
        if share is None:
            raise ValueError(f"unknown share: {share_name}")
        ignore_patterns = (
            load_ignore_patterns(share.path / share.ignore_file)
            if share.ignore_file
            else []
        )
        manifest = await build_manifest(
            share.name,
            str(share.path),
            ignore_patterns,
            self.cfg.indexing.hash_policy,
            small_threshold_mb=self.cfg.indexing.small_threshold_mb,
            sample_mb=self.cfg.indexing.sample_mb,
            ignore_hidden=self.cfg.indexing.ignore_hidden,
            max_workers=self.cfg.indexing.max_workers,
        )
        self.manifests[share_name] = manifest
        return manifest

    @staticmethod
    def parse_peer_address(addr: str) -> Tuple[str, int]:
        """解析 host:port 字符串。"""

        if ":" not in addr:
            raise ValueError(f"peer addr must be host:port, got {addr!r}")
        host, port_str = addr.rsplit(":", 1)
        port = int(port_str)
        if not host:
            raise ValueError("peer addr host missing")
        if port <= 0 or port > 65535:
            raise ValueError("peer addr port out of range")
        return host, port
