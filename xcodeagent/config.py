"""配置模块：从 JSON 文件加载 LLM 供应商配置和权限配置。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from xcodeagent.permission import PermissionConfig


class ConfigError(Exception):
    """配置相关错误。"""
    pass


@dataclass
class ExtendedThinkingConfig:
    """Claude Extended Thinking 配置。"""
    enabled: bool = False
    budget_tokens: int = 4000


@dataclass
class AppConfig:
    """应用配置。"""
    protocol: str
    model: str
    base_url: str
    api_key: str
    extended_thinking: Optional[ExtendedThinkingConfig] = None
    show_thinking: bool = True
    permissions: PermissionConfig = field(default_factory=PermissionConfig)


def _default_config_path() -> Path:
    """全局默认配置路径：~/.xcodeagent/config.json"""
    return Path.home() / ".xcodeagent" / "config.json"


def _resolve_config_path(path: str | Path) -> Path:
    """解析配置文件路径，支持多级查找。

    查找顺序：
    1. 用户显式指定的路径（-c 参数）
    2. 当前目录下的 config.json
    3. 全局 ~/.xcodeagent/config.json
    """
    path = Path(path)
    if path != Path("config.json"):
        return path
    if path.exists():
        return path
    global_path = _default_config_path()
    if global_path.exists():
        return global_path
    return path


def _load_permission_config(data: dict) -> PermissionConfig:
    """从 JSON 数据中加载权限配置。"""
    perm_data = data.get("permissions", {})
    if not isinstance(perm_data, dict):
        return PermissionConfig()

    config = PermissionConfig(
        mode=perm_data.get("mode", "default"),
        confirm_timeout=float(perm_data.get("confirm_timeout", 0)),
        rules=perm_data.get("rules", []),
        prepend_rules=bool(perm_data.get("prepend_rules", False)),
        dangerous_commands_extra=perm_data.get("dangerous_commands_extra", []),
    )

    errors = config.validate()
    if errors:
        # 不阻止启动，但打印警告
        import sys
        for err in errors:
            print(f"[WARNING] 权限配置: {err}", file=sys.stderr)
        # 对无效 mode 回退到 default
        if config.mode not in ("default", "acceptEdits", "plan"):
            config.mode = "default"

    return config


def load_config(path: str | Path) -> AppConfig:
    """从 JSON 文件加载配置。

    查找顺序：显式路径 → ./config.json → ~/.xcodeagent/config.json

    Args:
        path: 配置文件路径（默认 "config.json"）。

    Returns:
        AppConfig 实例。

    Raises:
        ConfigError: 配置缺失、格式错误或文件不存在。
    """
    path = _resolve_config_path(path)

    if not path.exists():
        raise ConfigError(
            f"配置文件不存在: {path}\n"
            f"请运行 xcodeagent --init 生成配置模板"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"配置文件 JSON 格式错误: {e}")

    # 校验必填字段
    for field_name in ("protocol", "model", "base_url", "api_key"):
        if field_name not in data or not data[field_name]:
            raise ConfigError(f"配置文件缺少必填字段: {field_name}")

    # 校验 protocol 值
    protocol = data["protocol"]
    if protocol not in ("anthropic", "openai"):
        raise ConfigError(
            f"不支持的 protocol: {protocol}，仅支持 anthropic 或 openai"
        )

    # 解析 extended_thinking
    ext_thinking = None
    if "extended_thinking" in data and data["extended_thinking"] is not None:
        et = data["extended_thinking"]
        ext_thinking = ExtendedThinkingConfig(
            enabled=et.get("enabled", False),
            budget_tokens=et.get("budget_tokens", 4000),
        )

    # 解析权限配置
    permissions = _load_permission_config(data)

    return AppConfig(
        protocol=protocol,
        model=data["model"],
        base_url=data["base_url"],
        api_key=data["api_key"],
        extended_thinking=ext_thinking,
        show_thinking=data.get("show_thinking", True),
        permissions=permissions,
    )


def create_default_config(path: str | Path) -> None:
    """生成一份配置模板文件。"""
    template = {
        "protocol": "anthropic",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-xxxxxxxxxxxx",
        "show_thinking": True,
        "extended_thinking": {
            "enabled": False,
            "budget_tokens": 4000,
        },
        "permissions": {
            "mode": "default",
            "confirm_timeout": 0,
            "rules": [],
            "prepend_rules": False,
            "dangerous_commands_extra": [],
        },
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
