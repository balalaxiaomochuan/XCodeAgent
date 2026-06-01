"""XCodeAgent 入口：加载配置 → 创建 Provider → 创建工具 → 启动 Agent → TUI。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from xcodeagent.agent import Agent, AgentConfig
from xcodeagent.chat import ChatSession
from xcodeagent.config import ConfigError, _default_config_path, create_default_config, load_config
from xcodeagent.provider import create_provider
from xcodeagent.tools import create_tool_executor
from xcodeagent.tui import TUI


# ── 系统提示词 ────────────────────────────────────────────────

SYSTEM_PROMPT = """\
你的名字是 XCodeAgent，一个由蟑螂恶霸开发的命令行 AI 编程助手。

## 核心能力
- 你拥有六个工具：ReadFile（读文件）、WriteFile（写文件）、EditFile（编辑文件）、Bash（执行命令）、Glob（搜索文件）、Grep（搜索内容）。
- 你可以自主推理——先读代码了解现状，再决定如何修改，最后执行并验证。

## 行为准则
- 收到任务后先思考：需要哪些信息？需要调用什么工具？按什么顺序？
- 对于代码修改任务：先 ReadFile 了解当前代码，再 EditFile 精确修改，最后 Bash 验证。
- 对于探索性任务：先用 Glob/Grep 定位目标文件，再 ReadFile 深入查看。
- 工具结果出错时，尝试理解错误原因并调整策略，而不是简单放弃。
- 修改完成后主动总结变更内容。

## 交流风格
- 回复简洁、准确、专业。
- 用代码块展示代码片段。
- 不确定时主动说明，不要编造。
- 使用中文与用户交流。
- 打印在终端的内容不能使用markdown格式
"""


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
        description="命令行 AI 编程助手 (ReAct Agent)",
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
    parser.add_argument(
        "-g", "--global",
        action="store_true",
        dest="global_init",
        help="与 --init 配合使用，将配置生成到 ~/.xcodeagent/config.json（全局）",
    )
    args = parser.parse_args()

    if args.init:
        config_path = _default_config_path() if args.global_init else Path("config.json")
        if config_path.exists():
            print(f"{config_path} 已存在，覆盖吗？[y/N] ", end="")
            if input().strip().lower() != "y":
                print("已取消。")
                return
        create_default_config(config_path)
        print(f"已生成 {config_path}，请编辑填入 API Key 后启动。")
        return

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    provider = create_provider(config)

    project_root = Path(os.getcwd())
    tool_executor = create_tool_executor(project_root=project_root)

    chat_session = ChatSession(
        provider=provider,
        model=config.model,
        system_prompt=SYSTEM_PROMPT,
        extended_thinking=config.extended_thinking,
    )

    agent_config = AgentConfig(
        max_rounds=20,
        plan_only=False,
        tool_timeout=120.0,
    )
    agent = Agent(chat_session, tool_executor, agent_config)

    tui = TUI(agent, chat_session, config)

    try:
        asyncio.run(tui.run())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
