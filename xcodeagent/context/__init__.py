"""上下文管理模块：两层压缩机制。

第一层：工具结果磁盘卸载（超长输出存盘替换为文件路径引用）
第二层：Auto-Compact（调用 LLM 将早期对话压缩为结构化摘要）

对外公开:
    - ContextManager: 编排两层压缩的统一入口
    - ContextCheckResult: pre_round_check / compact_now 的返回类型
"""

from xcodeagent.context.manager import ContextCheckResult, ContextManager

__all__ = ["ContextManager", "ContextCheckResult"]
