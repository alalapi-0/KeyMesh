"""Path normalization utilities for KeyMesh."""

from __future__ import annotations

from pathlib import Path  # Path 提供跨平台路径操作


def normalize_path(base: Path | str, p: Path | str) -> Path:
    """将输入路径规范化并转换为绝对路径。"""
    base_path = Path(base).expanduser().resolve()  # 展开用户目录并转换为绝对路径
    candidate = Path(p).expanduser()  # 先展开用户目录以处理 ~
    if candidate.is_absolute():  # 如果用户提供绝对路径
        return candidate.resolve()  # 直接返回规范化后的绝对路径
    absolute = (base_path / candidate).resolve()  # 对相对路径拼接后再解析
    return absolute  # 返回规范化后的绝对路径


def ensure_within(base: Path | str, p: Path | str) -> Path:
    """确保相对路径位于给定基路径之内，绝对路径则原样返回。"""
    base_path = Path(base).expanduser().resolve()  # 解析基路径
    candidate = Path(p).expanduser()  # 记录原始输入是否为绝对路径
    target = normalize_path(base_path, candidate)  # 复用 normalize_path 获取目标绝对路径
    if candidate.is_absolute():  # 若用户已明确提供绝对路径
        return target  # 直接返回，不做越权判断
    try:
        target.relative_to(base_path)  # 如果 target 不在 base 下会抛出 ValueError
    except ValueError as exc:
        raise ValueError(f"Path {target} escapes base {base_path}") from exc  # 转化异常并提供提示
    return target  # 返回安全路径


if __name__ == "__main__":
    # 简单示例：
    # >>> normalize_path("/tmp", "../tmp2")
    # Path('/tmp2')
    # >>> ensure_within("/tmp", "data")
    # Path('/tmp/data')
    pass
