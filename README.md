# XCodeAgent

命令行 AI 编程助手 —— 一个基于 ReAct 模式的智能编程 Agent，可以自主推理、调用工具、多轮循环完成编程任务。

由 **蟑螂恶霸** 开发，Apache 2.0 协议开源。

## 功能特性

- **ReAct 自主推理**：Agent 会先思考 → 调用工具 → 拿到结果 → 再思考，最多支持 20 轮循环
- **六大工具**：读文件（ReadFile）、写文件（WriteFile）、编辑文件（EditFile）、执行命令（Bash）、搜索文件（Glob）、搜索内容（Grep）
- **多 LLM 后端**：同时支持 Anthropic Claude 和 OpenAI 两种协议
- **流式输出**：实时显示 AI 的思考过程和文本生成
- **Extended Thinking**：支持 Claude Extended Thinking，展示深度推理过程
- **五层权限安全系统**：高危命令拦截 → 路径沙箱 → 规则引擎 → 权限模式 → 确认对话框
- **Plan-only 模式**：只读不写，安全审查 AI 的修改计划
- **终端 TUI**：基于 Rich 的终端界面，支持斜杠命令和 Esc 取消
- **跨平台**：Windows / macOS / Linux 均可使用

## 项目结构

```
XCodeAgent/
├── pyproject.toml              # 项目配置、依赖、入口脚本
├── config.example.json         # 配置文件模板
├── README.md                   # 项目说明（本文件）
├── LICENSE                     # Apache 2.0 许可证
├── CLAUDE.md                   # Claude Code 指引文档
├── uv.lock                     # 依赖锁文件
├── xcodeagent/                 # 主包
│   ├── __init__.py
│   ├── main.py                 # 入口：参数解析、组装模块、启动 TUI
│   ├── config.py               # JSON 配置加载 → AppConfig 数据类
│   ├── chat.py                 # ChatSession：消息历史管理
│   ├── agent.py                # ReAct Agent 主循环
│   ├── events.py               # AgentEvent 事件类型体系
│   ├── tui.py                  # 基于 Rich 的终端界面
│   ├── input_ui.py             # 输入 UI 模块
│   ├── command_analyzer.py     # 命令分析器
│   ├── client.py               # 客户端占位文件
│   ├── provider/               # LLM 供应商抽象层
│   │   ├── __init__.py         # 工厂函数 create_provider()
│   │   ├── base.py             # 抽象基类 + Delta 类型
│   │   ├── anthropic.py        # Anthropic Messages API
│   │   └── openai.py           # OpenAI Chat Completions API
│   ├── tools/                  # 工具子系统
│   │   ├── __init__.py         # 工厂函数 create_tool_executor()
│   │   ├── base.py             # BaseTool 抽象基类 + ToolResult
│   │   ├── registry.py         # ToolRegistry 工具注册中心
│   │   ├── executor.py         # ToolExecutor 执行引擎
│   │   ├── read_file.py        # ReadFile 读文件工具
│   │   ├── write_file.py       # WriteFile 写文件工具
│   │   ├── edit_file.py        # EditFile 精确编辑工具
│   │   ├── bash_.py            # Bash 命令执行工具
│   │   ├── glob_.py            # Glob 文件搜索工具
│   │   └── grep_.py            # Grep 内容搜索工具
│   └── permission/             # 权限安全系统
│       ├── __init__.py         # 公开 API + 配置数据类
│       ├── manager.py          # PermissionManager 权限管理器
│       ├── modes.py            # PermissionMode 权限模式
│       ├── rules.py            # 规则引擎
│       ├── path_sandbox.py     # 路径沙箱
│       └── dangerous_cmds.py   # 高危命令黑名单
└── prompt/                     # 开发规划文档（gitignored）
    ├── ch02/ ~ ch06/           # 各阶段 spec / tasks / checklist
```

## 环境要求

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)（推荐包管理器）

## 安装与启动

### 1. 克隆项目

```bash
git clone https://github.com/balalaxiaomochuan/XCodeAgent.git
cd XCodeAgent
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 生成配置文件

```bash
# 在当前目录生成 config.json
uv run xcodeagent --init

# 或生成到全局目录 ~/.xcodeagent/config.json
uv run xcodeagent --init --global
```

### 4. 编辑配置

打开生成的 `config.json`，填入你的 API Key：

```json
{
  "protocol": "anthropic",
  "model": "claude-sonnet-4-6",
  "base_url": "https://api.anthropic.com",
  "api_key": "sk-ant-你的API密钥",
  "show_thinking": true,
  "extended_thinking": {
    "enabled": false,
    "budget_tokens": 4000
  },
  "permissions": {
    "mode": "default",
    "confirm_timeout": 0,
    "rules": [],
    "prepend_rules": false,
    "dangerous_commands_extra": []
  }
}
```

| 字段 | 说明 |
|------|------|
| `protocol` | `"anthropic"` 或 `"openai"` |
| `model` | 模型名称，如 `claude-sonnet-4-6`、`gpt-4o` |
| `base_url` | API 地址（可替换为代理地址） |
| `api_key` | 你的 API 密钥 |
| `show_thinking` | 是否显示 AI 思考过程 |
| `extended_thinking` | Claude Extended Thinking 配置 |
| `permissions.mode` | 权限模式：`default` / `acceptEdits` / `plan` |

### 5. 启动

```bash
uv run xcodeagent
```

## 在其他项目中使用

下载本项目后，你可以通过以下两种方式在任何其他项目中使用 XCodeAgent：

### 方式一：全局安装（推荐）

```bash
# 在本项目根目录下执行全局安装
uv tool install .
```

安装后，`xcodeagent` 命令将在系统全局可用。然后在你的目标项目目录下：

```bash
# 进入任意项目目录
cd /path/to/your-project

# 生成配置文件（如果还没有）
xcodeagent --init

# 编辑 config.json 填入 API Key 后启动
xcodeagent
```

Agent 会自动将当前目录作为项目根目录，所有文件操作都在该目录内进行。

### 方式二：通过 uv run 指定路径

```bash
# 在任意项目目录下，通过 uv run 带上 XCodeAgent 路径运行
cd /path/to/your-project
uv run --directory /path/to/XCodeAgent xcodeagent
```

或者先配置本地 config.json，XCodeAgent 会自动从当前工作目录加载配置：

```bash
cd /path/to/your-project
xcodeagent --init          # 生成 config.json
# 编辑 config.json 后
/path/to/XCodeAgent/.venv/Scripts/python -m xcodeagent.main
```

### 方式三：pip 可编辑安装

```bash
# 在 XCodeAgent 目录下
pip install -e .

# 然后在任意目录下
cd /path/to/your-project
xcodeagent --init
xcodeagent
```

## 配置文件查找顺序

XCodeAgent 启动时会按以下顺序查找配置文件：

1. `-c` 参数显式指定的路径
2. 当前目录下的 `config.json`
3. 全局 `~/.xcodeagent/config.json`

这意味着你可以在不同项目中拥有不同的配置（模型、权限规则等），同时在全局保留一个默认配置作为兜底。

## 六大工具

| 工具 | 分类 | 用途 | 关键行为 |
|------|------|------|---------|
| ReadFile | read | 读取文件，带行号 | offset 从 1 开始，limit 上限 2000 |
| WriteFile | write | 创建/覆盖文件 | 自动建父目录，完整覆盖非追加 |
| EditFile | write | 多段精确替换 | old_string 严格唯一匹配，整体回滚 |
| Bash | write | 执行 shell 命令 | 子进程隔离，默认 120s 超时 |
| Glob | read | 通配符搜文件 | Path.rglob，排除 .git/.venv |
| Grep | read | 正则搜内容 | re 模块，排除二进制文件 |

Agent 会自动分组执行：读工具（ReadFile/Glob/Grep）并发执行，写工具（WriteFile/EditFile/Bash）串行执行，每个写操作经过权限检查。

## TUI 命令

启动后在终端界面中可使用以下斜杠命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/clear` | 清除对话历史 |
| `/exit` 或 `/quit` | 退出程序 |
| `/plan-on` | 开启 Plan-only 模式（只读不写） |
| `/plan-off` | 关闭 Plan-only 模式 |

快捷键：

- **Esc**：取消当前 Agent 循环
- **Ctrl+C**：兜底中断程序

## 权限安全系统

五层递进式防线：

| 层级 | 名称 | 说明 |
|------|------|------|
| Layer 1 | 高危命令拦截 | 不可绕过的命令黑名单（如 rm -rf /） |
| Layer 2 | 路径沙箱 | 文件操作限定在项目根目录内 |
| Layer 3 | 规则引擎 | 用户自定义细粒度权限规则 |
| Layer 4 | 权限模式 | default / acceptEdits / plan 三种模式 |
| Layer 5 | 确认对话框 | 敏感操作弹出确认提示 |

权限模式：

- **default**：对写操作弹出确认对话框
- **acceptEdits**：自动接受文件编辑，其他写操作仍需确认
- **plan**：只读模式，所有写操作被拦截

## 架构数据流

```
用户输入 → TUI → Agent.run() → provider.chat_with_tools()
    → 流式渲染文本/思考过程
    → 工具调用分组（读并发、写串行）
    → 权限检查（写工具）
    → 执行工具
    → 结果回填 LLM
    → 下一轮循环（最多 20 轮）
    → 最终回复
```

## 开发相关

```bash
# 运行测试
uv run pytest

# 安装开发依赖
uv sync --group dev
```

## 许可证

Apache License 2.0 © 蟑螂恶霸
