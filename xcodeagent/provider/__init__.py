"""Provider 工厂，根据配置创建对应的 LLM Provider 实例。"""

from xcodeagent.config import AppConfig
from xcodeagent.provider.anthropic import AnthropicProvider
from xcodeagent.provider.base import BaseLLMProvider
from xcodeagent.provider.openai import OpenAIProvider


def create_provider(config: AppConfig) -> BaseLLMProvider:
    """根据 AppConfig 创建对应的 Provider 实例。"""
    if config.protocol == "anthropic":
        return AnthropicProvider(
            api_key=config.api_key,
            base_url=config.base_url,
        )
    elif config.protocol == "openai":
        return OpenAIProvider(
            api_key=config.api_key,
            base_url=config.base_url,
        )
    else:
        raise ValueError(f"不支持的 protocol: {config.protocol}")
