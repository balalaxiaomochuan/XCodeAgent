"""Provider 抽象基类 — 定义统一的 LLM 调用接口。"""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class ProviderError(Exception):
    """LLM API 调用相关错误（认证失败、网络超时、API 错误等）。"""
    pass


class BaseLLMProvider(ABC):
    """所有 LLM 后端的统一抽象接口。

    消息格式约定：统一使用 OpenAI 格式
    [{"role": "user"|"assistant"|"system", "content": "..."}]
    """

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式调用 LLM，逐 token yield 文本。

        Args:
            messages: 对话消息列表（OpenAI 格式）。
            model: 模型 ID。
            **kwargs: 后端特定参数（如 extended thinking 配置）。

        Yields:
            文本块（通常每次一个或几个 token）。
        """
        ...

    @abstractmethod
    def supports_extended_thinking(self) -> bool:
        """当前后端是否支持 extended thinking。"""
        ...
