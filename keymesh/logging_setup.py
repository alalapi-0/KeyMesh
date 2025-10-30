"""Logging helper used by the KeyMesh CLI."""

from __future__ import annotations

import logging  # 标准库 logging 提供灵活的日志框架
from pathlib import Path  # Path 便于跨平台处理文件路径
from typing import Optional  # Optional 用于类型提示

from rich.logging import RichHandler  # RichHandler 提供彩色控制台输出


def init_logging(level: str, logfile: Optional[str] = None) -> None:
    """初始化 KeyMesh 的日志系统。"""
    # 将日志级别字符串转为大写，确保兼容大小写输入
    resolved_level = level.upper()
    # 如果日志级别字符串非法，则回退到 INFO 并给出警告
    if resolved_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        # 使用 INFO 作为安全的默认值
        resolved_level = "INFO"
        # 直接通过 logging.basicConfig 之前发出一次简单警告
        logging.getLogger(__name__).warning("Unsupported log level, fallback to INFO")
    # 构建基础的 logging 配置，包括 Rich 控制台处理器
    handlers = [
        # RichHandler 提供时间戳与彩色输出
        RichHandler(rich_tracebacks=True, show_time=True)
    ]
    # 如果用户指定了日志文件，则创建文件处理器
    if logfile:
        # 使用 Path 处理并确保父目录存在
        log_path = Path(logfile)
        # 创建父目录但在已存在时不报错
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 添加文件处理器，编码使用 utf-8 以兼容多语言日志
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    # 使用 basicConfig 设置全局日志级别与格式
    logging.basicConfig(
        level=getattr(logging, resolved_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,  # force 确保多次调用时覆盖旧配置
    )
