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
├── chat.py          # ChatSession：消息历史管理 + 流式 LLM 调用封装
├── tui.py           # 基于 Rich 的终端界面，流式显示 + 状态指示器
└── provider/
    ├── base.py      # 抽象基类 BaseLLMProvider：chat_stream() + supports_extended_thinking()
    ├── anthropic.py # Anthropic Messages API 流式调用（内部将 OpenAI 格式转为 Anthropic 格式）
    ├── openai.py    # OpenAI Chat Completions 流式调用
    └── __init__.py  # 工厂函数：create_provider(config) → BaseLLMProvider
```

**数据流：** `main.py` 加载 `config.json` → `AppConfig` → `create_provider()` → `ChatSession` + `TUI`。用户输入经 `TUI._handle_chat()` → `ChatSession.send()` → `provider.chat_stream()` → 通过 Rich 逐 token 流式渲染。

**消息格式：** 内部统一使用 OpenAI 格式 `[{"role": "...", "content": "..."}]`。Anthropic Provider 会将 system 消息提取为顶层 `system` 参数，其余消息直接映射 role。

**配置：** `config.json` 位于项目根目录（已 gitignore）。模板文件为 `config.example.json`。支持字段：`protocol`（anthropic / openai）、`model`、`base_url`、`api_key`，以及可选的 `extended_thinking`。

**TUI 斜杠命令：** `/exit`、`/quit`、`/clear`、`/help`。

## 备注

- `xcodeagent/client.py` 为空占位文件，未被任何模块引用。
- `prompt/` 目录已 gitignore，用于存放开发规划文档。
- Windows：`main.py` 启动时自动将标准输入输出切换为 UTF-8 编码，以支持 emoji 显示。
