"""Audit logging helpers for transfer events."""

from __future__ import annotations

import datetime as _dt  # 日期时间格式化
from pathlib import Path  # Path 处理路径


def log_event(
    peer_id: str,
    share: str,
    file: str,
    action: str,
    status: str,
    bytes_transferred: int,
    elapsed: float,
    *,
    base_dir: Path = Path("logs/transfers"),
) -> None:
    """Append an audit entry to the daily transfer log."""

    timestamp = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    log_dir = Path(base_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_dt.date.today().isoformat()}.log"
    line = (
        f"[{timestamp}] peer={peer_id} share={share} file={file} "
        f"action={action} status={status} size={bytes_transferred} time={elapsed:.2f}s\n"
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
