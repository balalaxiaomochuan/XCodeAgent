# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

XCodeAgent 是一个命令行 AI 编程助手，通过 Provider 抽象层同时支持 Anthropic Claude 和 OpenAI 两种后端。Apache 2.0 协议开源。

## 构建 / 运行 / 测试

```bash
# 安装依赖（含开发依赖）
uv sync

# 启动应用
uv run xcodeagent

# 在当前目录生成默认配置文件 config.json
uv run xcodeagent --init

# 运行测试
uv run pytest
```

## 架构

```
xcodeagent/
├── main.py          # 入口：参数解析、组装各模块、asyncio.run(tui.run())
├── config.py        # JSON 配置 → AppConfig 数据类（protocol/model/base_url/api_key）
├── chat.py          # ChatSession：消息历史管理 + send()纯文本 + send_with_tools()工具调用
├── agent.py         # [V4] Agent：ReAct 主循环，结果自动回填，多轮自主推理
├── events.py        # [V4] AgentEvent 事件类型体系，循环与 UI 之间的契约
├── tui.py           # [V4] 基于 Rich 终端界面，消费 Agent.run() 事件流，支持 Esc 取消
├── provider/
│   ├── base.py      # 抽象基类 BaseLLMProvider + TextDelta/ThinkingDelta/ToolCall 类型
│   ├── anthropic.py # Anthropic Messages API：chat_stream() + chat_with_tools() + ThinkingDelta
│   ├── openai.py    # OpenAI Chat Completions：chat_stream() + chat_with_tools()
│   └── __init__.py  # 工厂函数：create_provider(config) → BaseLLMProvider
└── tools/
    ├── base.py      # BaseTool 抽象基类 + ToolResult + category 属性（read/write）
    ├── registry.py  # ToolRegistry：注册、查找、生成 Anthropic tool use 定义
    ├── executor.py  # ToolExecutor：execute() + execute_batch() 分批并发执行
    ├── read_file.py # ReadFile：读取文件，返回带行号前缀的文本（支持 offset/limit）
    ├── write_file.py# WriteFile：创建或覆盖写入文件
    ├── edit_file.py # EditFile：多段编辑，old_string 严格唯一匹配，整体回滚
    ├── bash_.py     # Bash：子进程执行 shell 命令，超时后 SIGTERM→SIGKILL
    ├── glob_.py     # Glob：Path.rglob 文件模式匹配，排除 .git/.venv 等
    ├── grep_.py     # Grep：正则搜索文件内容，返回 文件:行号: 匹配行
    └── __init__.py  # 工厂函数：create_tool_executor(project_root) → ToolExecutor
```

**数据流（V4 ReAct Agent）：** `main.py` 加载 `config.json` → `AppConfig` → `create_provider()` + `create_tool_executor()` → `ChatSession` + `Agent` + `TUI`。用户输入经 `TUI._handle_chat()` → `Agent.run()` → `provider.chat_with_tools()` → 流式文本渲染 + 工具调用分批执行 + 结果回填 LLM → 下一轮 → ... → 最终回复。每轮通过 `AgentEvent` 事件流对外暴露。Esc 键可随时取消循环。

**消息格式：** 内部存储使用 Anthropic content block 格式。Assistant 消息的 `content` 为 `[{type: "text"/"tool_use", ...}]` 数组；工具结果用 `[{type: "tool_result", tool_use_id, content}]` 包装在 `role: "user"` 消息中。Anthropic Provider 直接透传；OpenAI Provider 在 `_convert_messages()` 中双向转换。

**工具调用 delta 类型：** `chat_with_tools()` 返回 `AsyncIterator[TextDelta | ThinkingDelta | ToolCall]`。TextDelta 用于流式文本 token，ThinkingDelta 用于 Extended Thinking 过程（仅 Anthropic），ToolCall 是完整解析的工具调用（含 id/name/input）。

**配置：** `config.json` 位于项目根目录（已 gitignore）。模板文件为 `config.example.json`。支持字段：`protocol`（anthropic / openai）、`model`、`base_url`、`api_key`，以及可选的 `extended_thinking`。

**TUI 斜杠命令：** `/exit`、`/quit`、`/clear`、`/help`、`/plan-on`、`/plan-off`。快捷键：Esc 取消当前 Agent 循环，Ctrl+C 兜底中断。

## 六个工具

| 工具 | 分类 | 用途 | 关键行为 |
|------|------|------|---------|
| ReadFile | read | 读取文件，带行号 | offset 从 1 开始，limit 上限 2000 |
| WriteFile | write | 创建/覆盖文件 | 自动建父目录，完整覆盖非追加 |
| EditFile | write | 多段精确替换 | old_string 严格唯一匹配，整体回滚 |
| Bash | write | 执行 shell 命令 | 子进程隔离，默认 120s 超时 |
| Glob | read | 通配符搜文件 | Path.rglob，排除 .git/.venv，按时间排序 |
| Grep | read | 正则搜内容 | re 模块，排除二进制和忽略目录 |

## 备注

- `xcodeagent/client.py` 为空占位文件，未被任何模块引用。
- `prompt/` 目录已 gitignore，用于存放开发规划文档。ch02/ 为 V1 纯对话阶段文档，ch04/ 为 V4 ReAct Agent 阶段文档。
- Windows：`main.py` 启动时自动将标准输入输出切换为 UTF-8 编码，以支持 emoji 显示。
