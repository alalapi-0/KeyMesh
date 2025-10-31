"""Transfer session bookkeeping utilities."""

from __future__ import annotations

import json  # JSON 用于持久化会话状态
import time  # time.time 记录更新时间
from pathlib import Path  # Path 处理路径
from typing import Any  # 类型提示


class TransferSession:
    """Manage resume metadata for a single file transfer."""

    def __init__(
        self,
        peer_id: str,
        share_name: str,
        file_path: Path,
        mode: str,
        *,
        sessions_dir: Path,
    ) -> None:
        # 保存初始化参数
        self.peer_id = peer_id
        self.share_name = share_name
        self.file_path = Path(file_path)
        self.mode = mode
        self.sessions_dir = Path(sessions_dir)
        # 构造会话文件路径，使用安全名称替换分隔符
        sanitized = (
            str(self.file_path)
            .replace("/", "__")
            .replace("\\", "__")
            .replace(":", "_")
        )
        self.record_path = self.sessions_dir / f"{peer_id}__{share_name}__{sanitized}.json"
        # 未完成传输使用 .part 后缀
        self.partial_path = self.file_path.with_suffix(self.file_path.suffix + ".part")
        # 确保会话目录存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def load_progress(self) -> dict[str, Any]:
        """Load persisted resume metadata."""

        if not self.record_path.exists():
            return {"bytes_done": 0, "chunk_id": 0}
        raw = json.loads(self.record_path.read_text(encoding="utf-8"))
        bytes_done = int(raw.get("bytes_done", 0))
        chunk_id = int(raw.get("chunk_id", 0))
        return {"bytes_done": bytes_done, "chunk_id": chunk_id}

    def save_progress(self, chunk_id: int, bytes_done: int) -> None:
        """Persist current progress to disk."""

        payload = {
            "peer": self.peer_id,
            "share": self.share_name,
            "file": str(self.file_path),
            "mode": self.mode,
            "chunk_id": int(chunk_id),
            "bytes_done": int(bytes_done),
            "updated": time.time(),
        }
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def finalize(self) -> None:
        """Mark the transfer as completed and atomically rename files."""

        if self.record_path.exists():
            self.record_path.unlink()
        if self.partial_path.exists():
            self.partial_path.replace(self.file_path)
