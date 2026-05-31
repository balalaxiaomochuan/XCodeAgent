"""XCodeAgent 入口：加载配置 → 创建 Provider → 创建工具 → 启动 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from xcodeagent.chat import ChatSession
from xcodeagent.config import ConfigError, create_default_config, load_config
from xcodeagent.provider import create_provider
from xcodeagent.tools import create_tool_executor
from xcodeagent.tui import TUI


def _fix_windows_encoding() -> None:
    """Windows 下将标准输入/输出流编码设为 UTF-8。

    Windows 终端默认编码为 GBK，无法正确渲染 emoji 和一些 Unicode 字符。
    此函数在程序启动时自动将 stdin/stdout/stderr 切换到 UTF-8，
    使得用户无需手动设置 PYTHONIOENCODING 环境变量即可正常使用。

    在 macOS / Linux 上此函数不做任何操作（默认已是 UTF-8）。
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # 非控制台场景（管道重定向等），保持默认


def main():
    _fix_windows_encoding()
    parser = argparse.ArgumentParser(
        prog="xcodeagent",
        description="命令行 AI 对话助手",
    )
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="配置文件路径（默认: config.json）",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="在当前目录生成配置文件模板 config.json",
    )
    args = parser.parse_args()

    if args.init:
        if Path("config.json").exists():
            print("config.json 已存在，覆盖吗？[y/N] ", end="")
            if input().strip().lower() != "y":
                print("已取消。")
                return
        create_default_config("config.json")
        print("已生成 config.json，请编辑填入 API Key 后启动。")
        return

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    provider = create_provider(config)

    project_root = Path(os.getcwd())
    tool_executor = create_tool_executor(project_root=project_root)

    system_prompt = None
    chat_session = ChatSession(
        provider=provider,
        model=config.model,
        system_prompt=system_prompt,
        extended_thinking=config.extended_thinking,
    )

    tui = TUI(chat_session, config, tool_executor)

    try:
        asyncio.run(tui.run())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
