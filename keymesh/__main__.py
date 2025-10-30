"""Command line entry point for KeyMesh."""

from __future__ import annotations

import sys  # sys 用于访问 argv 与退出状态

from .config import load_config  # 触发懒加载以便 CLI 使用
from .logging_setup import init_logging  # 初始化日志
from .cli import main as cli_main  # CLI 主函数


def main(argv: list[str] | None = None) -> int:
    """入口函数，供 python -m keymesh 调用。"""
    config_path = "config.yaml"  # 默认配置文件路径
    try:
        cfg = load_config(config_path, check_files=False)  # 尝试加载配置用于日志设定
        log_level = cfg.logging.level  # 从配置读取日志级别
        log_file = str(cfg.logging.file) if cfg.logging.file else None  # 若配置了文件则转换为字符串
    except Exception:
        log_level = "INFO"  # 若配置加载失败则使用默认日志级别
        log_file = None  # 不使用日志文件
    init_logging(log_level, log_file)  # 初始化日志系统
    return cli_main(argv)  # 委托给 CLI 模块并返回状态码


if __name__ == "__main__":
    sys.exit(main())  # 将返回值作为进程退出码
