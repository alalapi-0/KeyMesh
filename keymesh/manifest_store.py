"""Helpers to persist manifest snapshots on disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_share_name(name: str) -> str:
    """将共享名转换为文件系统友好的字符串。"""

    return name.replace("/", "-")


def _sanitize_timestamp(timestamp: str) -> str:
    """将 ISO8601 时间戳转换为文件名片段。"""

    sanitized = timestamp.replace(":", "").replace("-", "")
    if "." in sanitized:
        main, _, rest = sanitized.partition(".")
        rest = rest.rstrip("Z")
        return f"{main}{rest}Z"
    return sanitized


def save_manifest(share_name: str, manifest: dict[str, Any], out_dir: str = "out/manifests") -> Path:
    """将 manifest 写入磁盘并返回保存路径。

    Args:
        share_name: 共享域名称或远端标识。
        manifest: 待写入的 manifest 字典。
        out_dir: 存储目录，默认 ``out/manifests``。

    Returns:
        时间戳版本文件的绝对路径。

    Raises:
        ValueError: manifest 缺少 ``generated_at`` 字段时抛出。
    """

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    timestamp = manifest.get("generated_at")
    if not timestamp:
        raise ValueError("manifest missing generated_at timestamp")
    filename = f"{_safe_share_name(share_name)}_{_sanitize_timestamp(timestamp)}.json"
    target = out_path / filename
    with target.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    latest = out_path / f"{_safe_share_name(share_name)}_latest.json"
    with latest.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target


def load_manifest(share_name: str, out_dir: str = "out/manifests") -> dict[str, Any] | None:
    """读取最新 manifest，如不存在则返回 None。

    Args:
        share_name: 共享域名称或远端标识。
        out_dir: 存放 manifest 的目录。

    Returns:
        manifest 字典或 ``None``。
    """

    latest = Path(out_dir) / f"{_safe_share_name(share_name)}_latest.json"
    if not latest.exists():
        return None
    with latest.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_previous_manifest(share_name: str, out_dir: str = "out/manifests") -> dict[str, Any] | None:
    """加载倒数第二个 manifest 版本，若不足两个版本返回 ``None``。

    Args:
        share_name: 共享域名称或远端标识。
        out_dir: 存储目录。

    Returns:
        倒数第二个 manifest 字典或 ``None``。
    """

    base = Path(out_dir)
    candidates = sorted(
        p for p in base.glob(f"{_safe_share_name(share_name)}_*.json") if not p.name.endswith("_latest.json")
    )
    if len(candidates) < 2:
        return None
    with candidates[-2].open("r", encoding="utf-8") as handle:
        return json.load(handle)
