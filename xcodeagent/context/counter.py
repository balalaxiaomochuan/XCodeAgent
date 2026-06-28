"""Token 计数工具：保守近似估算，不依赖外部 tokenizer 库。

采用保守高估策略：
    - 所有文本按 0.5 token/字符估算（纯英文约 0.3，中文约 0.5-0.7）
    - 每条消息加 4 token 结构开销
    - 每个 tool_use 块加 10 token 函数定义开销
"""

from __future__ import annotations

import json


def estimate_tokens(messages: list[dict]) -> int:
    """保守估算消息列表的总 token 数。

    Args:
        messages: 消息列表，每项含 role/content。

    Returns:
        估算的 token 数（整数，保守高估）。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if content is None:
            content = ""

        if isinstance(content, str):
            # 纯文本消息
            total += int(len(content) * 0.5)
        elif isinstance(content, list):
            # content block 数组格式
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_str = json.dumps(block, ensure_ascii=False)
                total += int(len(block_str) * 0.5)
                # tool_use 块有额外的函数定义开销
                if block.get("type") == "tool_use":
                    total += 10
        else:
            # 兜底：转为字符串计数
            total += int(len(str(content)) * 0.5)

        # 每条消息的结构开销（role 字段、JSON 格式等）
        total += 4

    return total


def estimate_single(msg: dict) -> int:
    """估算单条消息的 token 数。

    可用于增量计算（对比前后差异）。

    Args:
        msg: 单条消息字典。

    Returns:
        估算的 token 数。
    """
    return estimate_tokens([msg])
