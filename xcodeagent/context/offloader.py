"""第一层压缩：工具结果磁盘卸载 + 决策冻结。

当工具输出超过阈值时，将完整内容存入磁盘文件，
在对话历史中替换为"预览 + 文件路径"。

决策冻结保证 Prompt Cache 前缀稳定性：
已决定的"替换/保留"在整个会话周期内永不改变。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 阈值常量 ──────────────────────────────────────────────────────

# 单个工具结果字符数超过此值触发磁盘卸载
SINGLE_TOOL_THRESHOLD: int = 50_000

# 单轮所有工具结果总字符数超过此值触发聚合卸载
AGGREGATE_THRESHOLD: int = 200_000

# 预览保留的前缀字符数
PREVIEW_LENGTH: int = 500


# ── 格式化工具 ────────────────────────────────────────────────────


def _format_size(char_count: int) -> str:
    """将字符数格式化为人类可读的大小字符串。"""
    if char_count >= 1_000_000:
        return f"{char_count / 1_000_000:.1f}MB"
    elif char_count >= 1_000:
        return f"{char_count / 1_000:.0f}KB"
    else:
        return f"{char_count}B"


# ── DecisionFreezer ────────────────────────────────────────────────


class DecisionFreezer:
    """决策冻结器：保证每个 tool_use_id 的替换/保留决定只做一次。

    已替换的 → 使用缓存的预览字符串（永不重新生成）
    已决定不替换 → 保持原文（永不重新评估）
    新产生的 → 正常评估，做出决策后冻结

    这是 Prompt Cache 稳定性的关键保证。
    """

    def __init__(self) -> None:
        self._replaced_ids: set[str] = set()       # 已决定"替换"
        self._kept_ids: set[str] = set()            # 已决定"保留"
        self._preview_cache: dict[str, str] = {}    # 冻结的预览字符串

    def is_frozen(self, tool_use_id: str) -> bool:
        """该 tool_use_id 是否已有冻结决策。"""
        return tool_use_id in self._replaced_ids or tool_use_id in self._kept_ids

    def get_preview(self, tool_use_id: str) -> str | None:
        """获取冻结的预览字符串。仅对已决定"替换"的 id 有效。"""
        return self._preview_cache.get(tool_use_id)

    def freeze_replace(self, tool_use_id: str, preview: str) -> None:
        """冻结"替换"决策并缓存预览字符串。"""
        self._replaced_ids.add(tool_use_id)
        self._preview_cache[tool_use_id] = preview

    def freeze_keep(self, tool_use_id: str) -> None:
        """冻结"保留"决策。"""
        self._kept_ids.add(tool_use_id)

    def get_stats(self) -> dict:
        """返回决策冻结统计。"""
        return {
            "replaced_count": len(self._replaced_ids),
            "kept_count": len(self._kept_ids),
            "replaced_ids": sorted(self._replaced_ids),
        }


# ── DiskOffloader ──────────────────────────────────────────────────


class DiskOffloader:
    """工具结果磁盘卸载器。

    将超长工具输出写入 .xcodeagent/session/tool-results/ 目录，
    并生成"预览 + 文件路径"替换文本。
    """

    def __init__(self, session_dir: Path | str) -> None:
        """
        Args:
            session_dir: 会话目录根路径（如 .xcodeagent/session/）。
        """
        if isinstance(session_dir, str):
            session_dir = Path(session_dir)
        self._session_dir = session_dir
        self._results_dir = session_dir / "tool-results"
        self._freezer = DecisionFreezer()

    @property
    def results_dir(self) -> Path:
        """工具结果存储目录。"""
        return self._results_dir

    @property
    def freezer(self) -> DecisionFreezer:
        """决策冻结器。"""
        return self._freezer

    def ensure_dir(self) -> None:
        """确保存储目录存在。"""
        try:
            self._results_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("[CONTEXT] 无法创建工具结果目录 %s: %s", self._results_dir, e)

    def save(self, tool_use_id: str, content: str) -> Path | None:
        """将工具结果存入磁盘（独占创建模式）。

        Args:
            tool_use_id: 工具调用唯一 ID。
            content: 完整的工具输出文本。

        Returns:
            写入的文件路径，失败返回 None。
        """
        self.ensure_dir()
        file_path = self._results_dir / f"{tool_use_id}.txt"
        try:
            # 使用独占创建模式：文件已存在则跳过
            with open(file_path, "x", encoding="utf-8") as f:
                f.write(content)
            return file_path
        except FileExistsError:
            # 已存在，不用重复写
            return file_path
        except OSError as e:
            logger.warning("[CONTEXT] 无法写入工具结果文件 %s: %s", file_path, e)
            return None

    def make_preview(self, content: str, file_path: Path) -> str:
        """生成"预览 + 文件路径"替换文本。

        Args:
            content: 原始工具输出文本。
            file_path: 完整内容所在的磁盘文件路径。

        Returns:
            替换用的预览字符串。
        """
        size_label = _format_size(len(content))
        preview = content[:PREVIEW_LENGTH]
        if len(content) > PREVIEW_LENGTH:
            preview += "\n..."

        return (
            f"[输出太大（{size_label}），完整内容已保存到：\n"
            f"{file_path}\n"
            f"--- 预览（前{PREVIEW_LENGTH}字符）---\n"
            f"{preview}"
        )

    def check_single(
        self,
        tool_use_id: str,
        content: str,
    ) -> tuple[str, bool]:
        """对单个工具结果执行第一层检查。

        流程:
            1. 若已冻结 → 返回缓存值或原文
            2. 若 content ≤ 阈值 → 冻结"保留"，返回原文
            3. 若 content > 阈值 → 存盘 → 生成预览 → 冻结"替换"

        Args:
            tool_use_id: 工具调用唯一 ID。
            content: 工具输出文本。

        Returns:
            (output_text, was_offloaded): output_text 为原始内容或预览，
            was_offloaded 表示是否触发了磁盘卸载。
        """
        # ── 已冻结的决策 ──
        if tool_use_id in self._freezer._kept_ids:
            return content, False

        if tool_use_id in self._freezer._replaced_ids:
            cached = self._freezer.get_preview(tool_use_id)
            if cached is not None:
                return cached, True
            # 缓存丢失（异常情况），重新评估
            logger.warning(
                "[CONTEXT] tool_use_id=%s 标记为已替换但缓存丢失，重新评估",
                tool_use_id,
            )

        # ── 新结果：评估是否需要卸载 ──
        if len(content) <= SINGLE_TOOL_THRESHOLD:
            self._freezer.freeze_keep(tool_use_id)
            return content, False

        # 需要卸载
        file_path = self.save(tool_use_id, content)
        if file_path is None:
            # 写入失败 → 保留原文（但仍冻结，避免反复尝试）
            self._freezer.freeze_keep(tool_use_id)
            logger.warning(
                "[CONTEXT] 无法卸载 tool_use_id=%s，保留原文 (%d 字符)",
                tool_use_id, len(content),
            )
            return content, False

        preview = self.make_preview(content, file_path)
        self._freezer.freeze_replace(tool_use_id, preview)
        logger.info(
            "[CONTEXT] 已卸载 tool_use_id=%s → %s (%s)",
            tool_use_id, file_path, _format_size(len(content)),
        )
        return preview, True

    def check_aggregate(
        self,
        messages: list[dict],
    ) -> int:
        """对整个消息历史做聚合大小检查。

        遍历所有 tool_result 块，若总字符数超 AGGREGATE_THRESHOLD，
        从最大的结果开始卸载到预算内。

        已冻结的结果跳过。

        Args:
            messages: 消息历史列表（原地修改）。

        Returns:
            本轮卸载的工具结果数量。
        """
        # ── 收集所有 tool_result 块 ──
        # 格式: [(msg_idx, block_idx, tool_use_id, content_str)]
        results: list[tuple[int, int, str, str]] = []
        total_chars = 0

        for mi, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for bi, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                result_content = block.get("content", "")
                if not isinstance(result_content, str):
                    result_content = str(result_content)
                tool_use_id = block.get("tool_use_id", "")
                results.append((mi, bi, tool_use_id, result_content))
                total_chars += len(result_content)

        if total_chars <= AGGREGATE_THRESHOLD:
            return 0

        # ── 按大小降序排列 ──
        results.sort(key=lambda x: len(x[3]), reverse=True)

        offloaded = 0
        for mi, bi, tool_use_id, content_str in results:
            if total_chars <= AGGREGATE_THRESHOLD:
                break

            # 已冻结的不动
            if self._freezer.is_frozen(tool_use_id):
                continue

            # 卸载这个结果
            file_path = self.save(tool_use_id, content_str)
            if file_path is None:
                continue

            preview = self.make_preview(content_str, file_path)
            self._freezer.freeze_replace(tool_use_id, preview)

            # 原地替换消息中的 tool_result 内容
            messages[mi]["content"][bi]["content"] = preview

            total_chars -= len(content_str) - len(preview)
            offloaded += 1
            logger.info(
                "[CONTEXT] 聚合卸载 tool_use_id=%s (%s)，总字符数降至 %d",
                tool_use_id, _format_size(len(content_str)), total_chars,
            )

        return offloaded
