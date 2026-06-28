"""第二层压缩：LLM 对话摘要。

当上下文接近窗口上限时，调用 LLM 将早期对话压缩为结构化摘要。
采用两阶段生成（analysis → summary），保证前缀稳定和关键信息不丢失。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xcodeagent.provider.base import BaseLLMProvider

logger = logging.getLogger(__name__)

# ── 摘要 Prompt 模板 ───────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = """\
You are a conversation summarizer. Your task is to compress a long conversation \
history into a structured summary that preserves all critical information.

【ABSOLUTE RULE — READ CAREFULLY】
You MUST NOT call any tools, functions, or perform any actions.
You MUST output ONLY plain text — no code execution, no file operations.
Your sole output is a structured summary in the format specified below.

【Summary Structure】
First output an <analysis> block to organize your thoughts, then output the \
<summary> block. The <analysis> block will be discarded; only <summary> is kept.

<analysis>
(Organize key information from the conversation: main goals, technical concepts \
discussed, files touched, errors encountered, decisions made. List everything \
that must be preserved in the summary.)
</analysis>

<summary>
1. Main Requests and Intentions — What the user ultimately wants to achieve. \
Be specific and complete.

2. Key Technical Concepts — Important technical points, frameworks, libraries, \
or patterns discussed. Include version numbers and configuration details if mentioned.

3. Files and Code Sections — Which files were involved, what was read, what was \
modified. Preserve critical code snippets, function signatures, and file paths exactly.

4. Errors and Fixes — What errors were encountered, how they were diagnosed, \
and what solutions were applied. Include exact error messages if they are important.

5. Problem-Solving Process — The reasoning and methodology used to approach \
problems. Why certain decisions were made.

6. All User Messages — Preserve ALL user messages VERBATIM (word for word). \
Do NOT paraphrase or summarize user input. Every user message must be included \
in its original form, including code snippets and file paths the user typed.

7. Pending Tasks — What has NOT been completed yet. Unresolved issues, \
work-in-progress items, and explicitly stated future work.

8. Current Work — What was being done most recently. Describe in detail the \
latest few exchanges, current state of the workspace, and what is being worked on RIGHT NOW.

9. Possible Next Steps — What should logically happen next based on the current \
state and user's intentions.
</summary>

【REMINDER: You MUST NOT call any tools. Output ONLY plain text with the two \
XML blocks above. No tool calls, no code execution, no file access.】\
"""

_SUMMARY_USER_PROMPT = """\
Please summarize the conversation history above following the structured format.

Remember:
1. Output <analysis> first, then <summary>
2. Preserve ALL user messages verbatim in section 6
3. Make section 8 (Current Work) the most detailed
4. DO NOT call any tools — plain text output only
"""

# 两次 compact 之间最少间隔的轮数（防抖动）
MIN_COMPACT_INTERVAL: int = 2


# ── Summarizer ─────────────────────────────────────────────────────


class Summarizer:
    """对话摘要生成器。

    调用 LLM 对早期对话消息生成 9 部分结构化摘要，
    然后重写消息列表为 system + 摘要 + 近期原文。
    """

    def __init__(self, provider: "BaseLLMProvider", model: str) -> None:
        """
        Args:
            provider: LLM provider 实例（复用，不新建）。
            model: 模型 ID。
        """
        self._provider = provider
        self._model = model
        self._last_compact_round: int = -1  # 上次压缩的轮次（防抖）

    @property
    def last_compact_round(self) -> int:
        """上次压缩发生的轮次。"""
        return self._last_compact_round

    def can_compact(self, current_round: int) -> bool:
        """检查是否满足防抖要求（距上次压缩至少 MIN_COMPACT_INTERVAL 轮）。"""
        if self._last_compact_round < 0:
            return True
        return (current_round - self._last_compact_round) >= MIN_COMPACT_INTERVAL

    async def generate_summary(self, messages: list[dict]) -> str:
        """调用 LLM 生成结构化摘要。

        Args:
            messages: 需要被摘要的消息列表。

        Returns:
            摘要文本（<summary> 标签内容，或降级为全部输出）。

        Raises:
            Exception: LLM 调用失败时向上抛出。
        """
        # 构建摘要请求消息
        summary_messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            *messages,
            {"role": "user", "content": _SUMMARY_USER_PROMPT},
        ]

        # 调用 chat_stream（不传工具定义，模型无法调用工具）
        full_output = ""
        try:
            async for token in self._provider.chat_stream(
                messages=summary_messages,
                model=self._model,
            ):
                full_output += token
        except Exception:
            logger.exception("[CONTEXT] 摘要生成 API 调用失败")
            raise

        if not full_output.strip():
            raise RuntimeError("LLM 返回了空的摘要内容")

        # 提取 <summary> 内容
        summary = self._extract_tag(full_output, "summary")
        if summary is not None:
            logger.info("[CONTEXT] 成功提取 <summary> 块 (%d 字符)", len(summary))
            return summary.strip()

        # 降级：没有 <summary> 标签，取全部输出
        logger.warning(
            "[CONTEXT] LLM 输出中未找到 <summary> 标签，使用全部输出作为摘要"
        )
        return full_output.strip()

    def build_compacted_messages(
        self,
        messages: list[dict],
        summary: str,
    ) -> list[dict]:
        """用摘要重写消息列表。

        规则:
            - system prompt（第一条）始终保留
            - 其余早期消息替换为一条 user 角色的摘要消息
            - 从尾部向前保留至少 5 条原文
            - 不切断 tool_use / tool_result 配对

        Args:
            messages: 原始消息列表。
            summary: 生成的摘要文本。

        Returns:
            新的消息列表。
        """
        if not messages:
            return messages

        # ── 1. 保留 system prompt ──
        system_msg = None
        start_idx = 0
        if messages[0].get("role") == "system":
            system_msg = messages[0]
            start_idx = 1

        if start_idx >= len(messages):
            # 只有 system prompt，无需压缩
            return messages

        # ── 2. 计算保留窗口（从尾部向前） ──
        cutoff_idx = self._find_cutoff_index(messages, start_idx)

        # ── 3. 构建新消息列表 ──
        compacted: list[dict] = []

        if system_msg is not None:
            compacted.append(system_msg)

        # 插入摘要
        compacted.append({
            "role": "user",
            "content": (
                "[对话历史摘要]\n"
                "以下是对之前对话的结构化摘要，请基于此继续对话：\n\n"
                + summary
            ),
        })

        # 保留尾部原文
        compacted.extend(messages[cutoff_idx:])

        return compacted

    def _find_cutoff_index(
        self,
        messages: list[dict],
        start_idx: int,
    ) -> int:
        """从尾部向前扫描，找到安全的截断位置。

        约束:
            - 尾部至少保留 5 条消息
            - 保留窗口内的 tool_use 和 tool_result 配对完整

        Args:
            messages: 消息列表。
            start_idx: 起始索引（system prompt 之后）。

        Returns:
            截断索引（保留从该索引开始的消息）。
        """
        n = len(messages)

        # 从尾部向前，至少保留 5 条
        cutoff_idx = max(start_idx, n - 5)

        # ── 配对完整性检查 ──
        # 从尾部向前扫描，收集需要配对的 tool_use_id
        need_tool_use: set[str] = set()

        for i in range(n - 1, cutoff_idx - 1, -1):
            msg = messages[i]
            content = msg.get("content")

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "tool_result":
                        # 发现 tool_result → 需要对应的 tool_use
                        tid = block.get("tool_use_id", "")
                        if tid:
                            need_tool_use.add(tid)
                    elif block_type == "tool_use":
                        # 发现 tool_use → 移除对应的需求
                        tid = block.get("id", "")
                        if tid in need_tool_use:
                            need_tool_use.discard(tid)

        # 如果有未配对的 tool_result，需要向前扩展保留窗口
        if need_tool_use:
            for i in range(cutoff_idx - 1, start_idx - 1, -1):
                msg = messages[i]
                content = msg.get("content")

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        tid = ""
                        if block_type == "tool_result":
                            tid = block.get("tool_use_id", "")
                            if tid and tid in need_tool_use:
                                need_tool_use.discard(tid)
                        elif block_type == "tool_use":
                            tid = block.get("id", "")
                            if tid in need_tool_use:
                                need_tool_use.discard(tid)

                if not need_tool_use:
                    cutoff_idx = i
                    break
            else:
                # 走完循环仍未配对完整 → 从 start_idx 全保留
                cutoff_idx = start_idx

        return cutoff_idx

    # ── 内部工具 ─────────────────────────────────────────────────

    @staticmethod
    def _extract_tag(text: str, tag: str) -> str | None:
        """从文本中提取 XML 标签内容。

        大小写不敏感。

        Args:
            text: 完整文本。
            tag: 标签名（不带尖括号）。

        Returns:
            标签内容字符串，未找到返回 None。
        """
        # 大小写不敏感的标签匹配
        pattern = rf"<{tag}>\s*(.*?)\s*</{tag}>"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def record_compact(self, round_num: int) -> None:
        """记录一次压缩的发生（用于防抖）。"""
        self._last_compact_round = round_num
