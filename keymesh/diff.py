"""Manifest comparison helpers."""

from __future__ import annotations

from typing import Any


def _entry_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """将 manifest entries 转换为字典索引。

    Args:
        manifest: manifest 字典。

    Returns:
        以路径为键的字典，便于差异计算。
    """

    entries = manifest.get("entries", [])
    return {item.get("path", ""): item for item in entries if item.get("path")}


def compare_manifests(local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """比较两个 manifest 并返回差异摘要。

    Args:
        local: 本地 manifest。
        remote: 远端 manifest。

    Returns:
        包含 added/modified/deleted 列表与 summary 统计的字典。
    """

    local_map = _entry_map(local)
    remote_map = _entry_map(remote)
    local_paths = set(local_map)
    remote_paths = set(remote_map)
    added_paths = sorted(local_paths - remote_paths)
    deleted_paths = sorted(remote_paths - local_paths)
    modified_paths: list[str] = []
    for path in sorted(local_paths & remote_paths):
        local_entry = local_map[path]
        remote_entry = remote_map[path]
        local_hash = local_entry.get("hash")
        remote_hash = remote_entry.get("hash")
        if local_hash and remote_hash and local_hash != remote_hash:
            modified_paths.append(path)
            continue
        if local_hash or remote_hash:
            if local_hash != remote_hash:
                modified_paths.append(path)
                continue
        local_mtime = int(local_entry.get("mtime", 0))
        remote_mtime = int(remote_entry.get("mtime", 0))
        if local_mtime > remote_mtime:
            modified_paths.append(path)
    summary = {
        "added": len(added_paths),
        "modified": len(modified_paths),
        "deleted": len(deleted_paths),
        "delta": len(added_paths) + len(modified_paths) + len(deleted_paths),
    }
    return {
        "added": added_paths,
        "modified": modified_paths,
        "deleted": deleted_paths,
        "summary": summary,
    }
