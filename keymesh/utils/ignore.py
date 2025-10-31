"""Utilities for parsing .keymeshignore files and evaluating patterns."""

from __future__ import annotations

from fnmatch import fnmatch  # fnmatch 提供 Unix shell 风格的匹配
from pathlib import Path  # Path 用于跨平台路径处理


def load_ignore_patterns(path: Path) -> list[str]:
    """读取忽略文件并返回有效模式列表。

    Args:
        path: 共享目录下 .keymeshignore 的路径。

    Returns:
        去除空行与注释后的 glob 模式列表。
    """

    if not path.exists():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def should_ignore(path: Path | str, patterns: list[str]) -> bool:
    """判断给定相对路径是否匹配忽略模式。

    Args:
        path: 待检测的相对路径，可为 Path 或 str。
        patterns: glob 模式列表。

    Returns:
        若任意模式命中则返回 True。
    """

    if not patterns:
        return False
    candidate = path.as_posix() if isinstance(path, Path) else path.replace("\\", "/")
    return any(fnmatch(candidate, pattern) for pattern in patterns)
