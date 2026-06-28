"""上下文管理器：编排两层压缩的统一入口。

- 第一层：工具结果磁盘卸载 + 决策冻结
- 第二层：LLM 对话摘要（Auto-Compact）

在每轮 Agent 循环开始前调用 pre_round_check()，
也提供 compact_now() 供 /compact 命令使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from xcodeagent.context.counter import estimate_tokens
from xcodeagent.context.offloader import DiskOffloader
from xcodeagent.context.summarizer import Summarizer

if TYPE_CHECKING:
    from xcodeagent.chat import ChatSession
    from xcodeagent.provider.base import BaseLLMProvider

logger = logging.getLogger(__name__)

# ── 预算常量 ─────────────────────────────────────────────────────

# 摘要输出预留 token 数（摘要 prompt + 输出本身占用的空间）
SUMMARY_BUDGET: int = 20_000

# 安全余量 token 数（防止检查通过后新增内容击穿窗口）
SAFETY_MARGIN: int = 13_000

# 默认上下文窗口 token 数
DEFAULT_CONTEXT_WINDOW: int = 200_000


# ── ContextCheckResult ───────────────────────────────────────────


@dataclass
class ContextCheckResult:
    """pre_round_check / compact_now 的返回类型。"""

    # 本轮第一层卸载统计
    offloaded_count: int = 0
    offloaded_chars: int = 0

    # 第二层压缩统计
    compacted: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    messages_before: int = 0
    messages_after: int = 0

    # 降级标记
    degraded: bool = False
    error_message: str = ""


# ── ContextManager ───────────────────────────────────────────────


class ContextManager:
    """上下文管理器：编排两层压缩。

    使用方式:
        ctx = ContextManager(provider, model, session_dir=Path(".xcodeagent/session"))
        # 每轮开始前
        result = await ctx.pre_round_check(chat_session, round_num, new_tool_results)
        # 手动压缩
        result = await ctx.compact_now(chat_session, round_num)
    """

    def __init__(
        self,
        provider: "BaseLLMProvider",
        model: str,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        session_dir: Path | None = None,
    ):
        """
        Args:
            provider: LLM provider 实例。
            model: 模型 ID。
            context_window: 模型上下文窗口大小（tokens）。
            session_dir: 会话目录，默认 .xcodeagent/session/。
        """
        self._provider = provider
        self._model = model
        self._context_window = context_window

        # 计算压缩阈值
        self._compact_threshold = context_window - SUMMARY_BUDGET - SAFETY_MARGIN

        # 会话目录
        if session_dir is None:
            session_dir = Path(".xcodeagent") / "session"
        elif isinstance(session_dir, str):
            session_dir = Path(session_dir)
        self._session_dir = session_dir

        # 子模块
        self._offloader = DiskOffloader(session_dir)
        self._summarizer = Summarizer(provider, model)

    # ── 属性 ─────────────────────────────────────────────────

    @property
    def compact_threshold(self) -> int:
        """自动压缩触发阈值（token 数）。"""
        return self._compact_threshold

    @property
    def offloader(self) -> DiskOffloader:
        """磁盘卸载器。"""
        return self._offloader

    @property
    def summarizer(self) -> Summarizer:
        """摘要生成器。"""
        return self._summarizer

    # ── 每轮检查 ──────────────────────────────────────────────

    async def pre_round_check(
        self,
        chat_session: "ChatSession",
        round_num: int,
        new_tool_ids: list[str] | None = None,
    ) -> ContextCheckResult:
        """在每轮 Agent 循环开始前执行上下文检查。

        流程:
            1. 对上一轮新产生的 tool_result 做第一层检查 + 决策冻结
            2. 对整个消息历史做聚合大小检查
            3. 估算当前 token 数
            4. 若超过 compact_threshold → 执行第二层 Auto-Compact

        Args:
            chat_session: 当前会话。
            round_num: 当前轮次编号（从 1 开始）。
            new_tool_ids: 上一轮新产生的 tool_use_id 列表。

        Returns:
            ContextCheckResult 包含各层统计信息。
        """
        result = ContextCheckResult()

        try:
            # ── 步骤 1+2: 第一层压缩 ──────────────
            self._offloader.ensure_dir()

            # 对新产生的 tool_result 做检查
            if new_tool_ids:
                for tool_use_id in new_tool_ids:
                    # 在消息历史中查找对应的 tool_result
                    for msg in chat_session.messages:
                        content = msg.get("content")
                        if not isinstance(content, list):
                            continue
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if (block.get("type") == "tool_result"
                                    and block.get("tool_use_id") == tool_use_id):
                                result_content = block.get("content", "")
                                if not isinstance(result_content, str):
                                    result_content = str(result_content)
                                new_content, was_offloaded = (
                                    self._offloader.check_single(
                                        tool_use_id, result_content
                                    )
                                )
                                if was_offloaded:
                                    block["content"] = new_content
                                    result.offloaded_count += 1
                                    result.offloaded_chars += len(result_content)

            # 聚合检查
            agg_count = self._offloader.check_aggregate(chat_session.messages)
            result.offloaded_count += agg_count

            # ── 步骤 3: token 估算 ──────────────
            tokens = estimate_tokens(chat_session.messages)
            result.tokens_before = tokens

            # ── 步骤 4: 第二层 Auto-Compact ──────
            if tokens > self._compact_threshold:
                if self._summarizer.can_compact(round_num):
                    await self._do_compact(chat_session, round_num, result)
                else:
                    logger.info(
                        "[CONTEXT] 超过压缩阈值但距上次压缩不足 %d 轮，跳过",
                        self._summarizer._last_compact_round,
                    )

            result.tokens_after = estimate_tokens(chat_session.messages)

        except Exception as e:
            logger.exception("[CONTEXT] pre_round_check 异常: %s", e)
            result.degraded = True
            result.error_message = str(e)

        return result

    # ── 手动压缩 ──────────────────────────────────────────────

    async def compact_now(
        self,
        chat_session: "ChatSession",
        round_num: int = 0,
    ) -> ContextCheckResult:
        """立即执行全量压缩（供 /compact 命令使用）。

        无视阈值和防抖限制。

        Args:
            chat_session: 当前会话。
            round_num: 当前轮次。

        Returns:
            ContextCheckResult 包含压缩前后统计。
        """
        result = ContextCheckResult()
        result.tokens_before = estimate_tokens(chat_session.messages)
        result.messages_before = len(chat_session.messages)

        try:
            await self._do_compact(chat_session, round_num, result)
        except Exception as e:
            logger.exception("[CONTEXT] compact_now 异常: %s", e)
            result.degraded = True
            result.error_message = str(e)

        result.tokens_after = estimate_tokens(chat_session.messages)
        result.messages_after = len(chat_session.messages)
        return result

    # ── 内部 ──────────────────────────────────────────────────

    async def _do_compact(
        self,
        chat_session: "ChatSession",
        round_num: int,
        result: ContextCheckResult,
    ) -> None:
        """执行第二层 Auto-Compact 的内部实现。"""
        messages = chat_session.messages
        result.messages_before = len(messages)

        logger.info(
            "[CONTEXT] 触发 Auto-Compact: %d tokens, 阈值 %d",
            result.tokens_before or estimate_tokens(messages),
            self._compact_threshold,
        )

        # 调用 LLM 生成摘要
        # 跳过 system prompt（第一条），摘要其余消息
        if len(messages) > 1 and messages[0].get("role") == "system":
            to_summarize = messages[1:]
        else:
            to_summarize = messages

        if not to_summarize:
            logger.info("[CONTEXT] 无可摘要的消息，跳过压缩")
            return

        summary = await self._summarizer.generate_summary(to_summarize)

        # 重写消息列表
        compacted = self._summarizer.build_compacted_messages(
            messages, summary
        )

        # 原地更新 chat_session 的消息列表
        chat_session._messages = compacted

        result.compacted = True
        result.messages_after = len(compacted)
        result.tokens_after = estimate_tokens(compacted)

        # 记录压缩轮次（防抖）
        self._summarizer.record_compact(round_num)

        logger.info(
            "[CONTEXT] Auto-Compact 完成: %d → %d 条消息, "
            "%d → %d tokens",
            result.messages_before, result.messages_after,
            result.tokens_before, result.tokens_after,
        )

    # ── 统计 ──────────────────────────────────────────────────

    def get_stats(self, messages: list[dict] | None = None) -> dict:
        """返回当前上下文统计信息。

        Args:
            messages: 可选的消息列表，不传则只返回配置信息。

        Returns:
            统计信息字典。
        """
        stats: dict = {
            "context_window": self._context_window,
            "compact_threshold": self._compact_threshold,
            "summary_budget": SUMMARY_BUDGET,
            "safety_margin": SAFETY_MARGIN,
            "offloader": self._offloader._freezer.get_stats(),
            "last_compact_round": self._summarizer.last_compact_round,
        }
        if messages is not None:
            stats["total_messages"] = len(messages)
            stats["estimated_tokens"] = estimate_tokens(messages)
        return stats
