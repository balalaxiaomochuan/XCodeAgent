"""配置模块：从 JSON 文件加载 LLM 供应商配置。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


def load_config(path: str | Path) -> AppConfig:
    """从 JSON 文件加载配置。

    Args:
        path: 配置文件路径。

    Returns:
        AppConfig 实例。

    Raises:
        ConfigError: 配置缺失、格式错误或文件不存在。
    """
    path = Path(path)

    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

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

    return AppConfig(
        protocol=protocol,
        model=data["model"],
        base_url=data["base_url"],
        api_key=data["api_key"],
        extended_thinking=ext_thinking,
    )


def create_default_config(path: str | Path) -> None:
    """生成一份配置模板文件。"""
    template = {
        "protocol": "anthropic",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-xxxxxxxxxxxx",
        "extended_thinking": {
            "enabled": False,
            "budget_tokens": 4000,
        },
    }
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
