"""Routing helpers that translate diff results into transfer plans."""

from __future__ import annotations

from typing import Any  # 类型提示


def plan_transfers(
    diff_result: dict[str, Any],
    peer_cfg: Any,
    allowed_shares: list[str],
) -> list[dict[str, Any]]:
    """Generate transfer tasks from manifest differences.

    Parameters
    ----------
    diff_result:
        Mapping of ``share -> diff`` as produced by :func:`keymesh.diff.compare_manifests`.
    peer_cfg:
        Peer configuration object, only used for debugging and future routing decisions.
    allowed_shares:
        List of share names that the peer may access.

    Returns
    -------
    list
        Task descriptors that can be enqueued by :class:`keymesh.transfer.engine.TransferEngine`.
    """

    tasks: list[dict[str, Any]] = []
    for share_name, share_diff in diff_result.items():
        if share_name not in allowed_shares:
            continue
        candidate_paths = []
        candidate_paths.extend(share_diff.get("added", []))
        candidate_paths.extend(share_diff.get("modified", []))
        for rel_path in candidate_paths:
            tasks.append(
                {
                    "share": share_name,
                    "relative_path": rel_path,
                    "mode": "push",
                    "peer_id": getattr(peer_cfg, "id", None),
                }
            )
    return tasks
