"""子代理调度的契约与限额。

本模块只负责**定义**，不执行任何任务。

设计边界（首期）：
- 子任务**只读**：不持有数据库会话，不持有写工具，不持有 MCP 工具。
- 主流程读取并校验数据，生成固定的上下文快照交给子任务。
- 子任务只返回结构化结果，由主流程保存与转发事件。

限额由后端强制校验，模型无法绕过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ============================================================
#  任务类型
# ============================================================
TaskType = Literal["consistency_check"]

SUPPORTED_TASK_TYPES: tuple[str, ...] = ("consistency_check",)

TASK_TYPE_LABELS: dict[str, str] = {
    "consistency_check": "一致性检查",
}


# ============================================================
#  限额（后端强制）
# ============================================================
@dataclass(frozen=True)
class SubagentLimits:
    """整轮子代理限额。

    这些上限由后端校验，重复派发与重试**共同消耗**同一份整轮预算，
    避免模型通过反复派发绕过限制。
    """

    # 整轮最多允许多少个子任务（含重试）
    max_tasks_per_turn: int = 3
    # 同时执行的上限
    max_concurrency: int = 3
    # 单个子任务的输出 token 上限
    max_tokens_per_task: int = 4000
    # 单个子任务的输入预算（字符数），防止把整本书塞进上下文
    max_input_chars_per_task: int = 20000
    # 单个子任务最多重试次数（0 表示不重试）
    max_retries: int = 1
    # 整轮总超时（秒）
    turn_timeout_seconds: float = 180.0


DEFAULT_LIMITS = SubagentLimits()


# ============================================================
#  输入快照
# ============================================================
@dataclass
class SourceRef:
    """结果到来源的引用，便于核对与追溯。"""

    kind: str          # chapter / character / outline / ...
    id: str
    label: str = ""    # 例如「第 3 章 雨夜」
    excerpt: str = ""  # 支撑结论的原文片段（截断）

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind, "id": self.id}
        if self.label:
            data["label"] = self.label
        if self.excerpt:
            data["excerpt"] = self.excerpt
        return data


@dataclass
class SubagentTask:
    """一个待执行的子任务。

    注意：`sources` 是**主流程读取后固化的文本快照**，
    子任务不获得数据库会话，因此无法读取快照之外的数据。
    """

    task_id: str
    task_type: str
    objective: str                      # 任务目标（给用户看，也给模型看）
    sources: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0                   # 由主流程分配，保持稳定

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "objective": self.objective,
            "sources": self.sources,
            "constraints": self.constraints,
            "sequence": self.sequence,
        }


# ============================================================
#  结果
# ============================================================
@dataclass
class Finding:
    """一条发现。必须带来源依据。"""

    summary: str
    severity: str = "info"              # info / warning / error
    source_refs: list[SourceRef] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "summary": self.summary,
            "severity": self.severity,
            "source_refs": [r.to_dict() for r in self.source_refs],
        }
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class SubagentResult:
    """子任务结果。

    字段固定为：task_id / status / findings / source_refs / usage / error。
    """

    task_id: str
    status: str = "pending"             # pending / running / completed / failed / cancelled
    findings: list[Finding] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""
    # 未检查部分：明确区分「没问题」与「没看」
    unchecked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "findings": [f.to_dict() for f in self.findings],
            "source_refs": [r.to_dict() for r in self.source_refs],
            "usage": self.usage,
            "error": self.error,
            "unchecked": self.unchecked,
        }

    def to_summary(self, max_findings: int = 5) -> dict[str, Any]:
        """给主 Agent 的摘要视图。

        子任务可能产出很长的结果，全部塞回主上下文会挤占空间，
        因此只回传摘要，完整结果留在步骤记录里。
        """
        return {
            "task_id": self.task_id,
            "status": self.status,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings[:max_findings]],
            "unchecked": self.unchecked,
            "error": self.error,
        }


# ============================================================
#  预算校验
# ============================================================
class BudgetError(ValueError):
    """超出限额。"""


@dataclass
class TurnBudget:
    """整轮的预算账本。

    重复派发与重试共享同一份预算，防止绕过限制。
    """

    limits: SubagentLimits = DEFAULT_LIMITS
    dispatched: int = 0
    _reserved: int = 0

    def reserve(self, count: int) -> None:
        """预留 count 个任务额度，超限则抛错。"""
        if count <= 0:
            raise BudgetError("子任务数量必须大于 0")
        if self.dispatched + self._reserved + count > self.limits.max_tasks_per_turn:
            remaining = self.limits.max_tasks_per_turn - self.dispatched - self._reserved
            raise BudgetError(
                f"超出整轮子任务上限：本批 {count} 个，剩余额度 {max(remaining, 0)} 个"
                f"（上限 {self.limits.max_tasks_per_turn}）"
            )
        self._reserved += count

    def commit(self) -> None:
        """把预留转为已用。"""
        self.dispatched += self._reserved
        self._reserved = 0

    def release(self) -> None:
        """放弃预留。"""
        self._reserved = 0

    @property
    def remaining(self) -> int:
        return max(self.limits.max_tasks_per_turn - self.dispatched - self._reserved, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limits.max_tasks_per_turn,
            "dispatched": self.dispatched,
            "remaining": self.remaining,
        }


def validate_task(task: SubagentTask, limits: SubagentLimits = DEFAULT_LIMITS) -> None:
    """校验单个任务是否合法。"""
    if task.task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(
            f"不支持的任务类型：{task.task_type}；"
            f"首期仅支持 {', '.join(SUPPORTED_TASK_TYPES)}"
        )
    if not task.objective.strip():
        raise ValueError("任务目标不能为空")
    if not task.sources:
        raise ValueError("子任务必须携带来源快照")

    total_chars = sum(len(str(s.get("text", ""))) for s in task.sources)
    if total_chars > limits.max_input_chars_per_task:
        raise ValueError(
            f"输入超出预算：{total_chars} 字符，上限 {limits.max_input_chars_per_task}"
        )
