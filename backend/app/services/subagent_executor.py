"""子代理执行模块 —— 有界并发、只读、可取消。

职责边界：
- **不持有写能力**：子任务只拿到主流程固化的文本快照，没有 db 会话、
  没有项目写工具、没有 MCP 工具。
- **不直接保存**：只 yield 事件与结构化结果，由主流程写库与转发 SSE。
- **受预算约束**：并发数、任务数、token、超时都由 SubagentLimits 强制。

模型调用显式关闭工具自动加载与执行：
`generate_text(..., tools=None, tool_choice="none", auto_mcp=False,
handle_tool_calls=False)` —— 因为这两个选项默认是开启的。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

from app.logger import get_logger
from app.services.subagent_contract import (
    BudgetError,
    DEFAULT_LIMITS,
    Finding,
    SourceRef,
    SubagentLimits,
    SubagentResult,
    SubagentTask,
    TASK_TYPE_LABELS,
    TurnBudget,
    validate_task,
)

logger = get_logger(__name__)


# ============================================================
#  提示词
# ============================================================
CONSISTENCY_SYSTEM_PROMPT = """你是一名小说一致性检查助手。你只做检查与报告，不修改任何内容。

严格遵守：
1. 只依据用户提供的材料判断，不得引入材料之外的情节或设定。
2. 每条发现必须注明依据：出自哪一章、哪段原文。无法定位依据的推测不得作为发现提出。
3. 材料不足以判断时，把该项列入 unchecked（未检查项），而不是猜测。
4. 只返回 JSON，不要返回任何解释性文字或 Markdown 代码块标记。

返回格式：
{
  "findings": [
    {
      "summary": "一句话说明问题",
      "severity": "info|warning|error",
      "detail": "补充说明（可空）",
      "source_refs": [
        {"kind": "chapter", "id": "<章节ID>", "label": "第N章 标题", "excerpt": "支撑该结论的原文片段"}
      ]
    }
  ],
  "unchecked": ["未能检查的项目及原因"]
}

没有任何问题时返回 {"findings": [], "unchecked": [...]}。
"""


def build_user_prompt(task: SubagentTask) -> str:
    """把任务与来源快照拼成提示词。"""
    parts = [f"检查目标：{task.objective}", "", "可依据的材料："]

    for idx, src in enumerate(task.sources, start=1):
        label = src.get("label") or src.get("id") or f"材料{idx}"
        sid = src.get("id") or ""
        text = str(src.get("text", ""))
        parts.append(f"\n【材料{idx}】{label}（id={sid}）")
        parts.append(text)

    constraints = task.constraints or {}
    if constraints:
        parts.append("")
        parts.append("附加约束：")
        parts.append(json.dumps(constraints, ensure_ascii=False))

    parts.append("")
    parts.append("请按约定的 JSON 格式返回检查结果。")
    return "\n".join(parts)


# ============================================================
#  响应解析
# ============================================================
def _strip_code_fence(text: str) -> str:
    """去掉模型可能加的 ```json 包裹。"""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_result(task: SubagentTask, raw: str) -> SubagentResult:
    """把模型输出解析成结构化结果。

    解析失败不抛异常：标记为 failed 并保留原始输出片段，
    这样一个子任务失败不会拖垮整批。
    """
    result = SubagentResult(task_id=task.task_id)
    cleaned = _strip_code_fence(raw)

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        result.status = "failed"
        result.error = "模型输出无法解析为约定 JSON"
        result.unchecked = [f"原始输出片段：{cleaned[:200]}"]
        return result

    if not isinstance(data, dict):
        result.status = "failed"
        result.error = "模型输出不是 JSON 对象"
        return result

    findings: list[Finding] = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        refs: list[SourceRef] = []
        for r in item.get("source_refs") or []:
            if not isinstance(r, dict):
                continue
            refs.append(SourceRef(
                kind=str(r.get("kind") or "unknown"),
                id=str(r.get("id") or ""),
                label=str(r.get("label") or ""),
                excerpt=str(r.get("excerpt") or "")[:500],
            ))
        findings.append(Finding(
            summary=str(item.get("summary") or "").strip() or "（未说明）",
            severity=str(item.get("severity") or "info"),
            detail=str(item.get("detail") or ""),
            source_refs=refs,
        ))

    unchecked = [str(u) for u in (data.get("unchecked") or []) if str(u).strip()]

    result.status = "completed"
    result.findings = findings
    result.unchecked = unchecked
    # 顶层来源引用汇总，便于前端直接展示
    seen: set[tuple[str, str]] = set()
    for f in findings:
        for r in f.source_refs:
            key = (r.kind, r.id)
            if key not in seen:
                seen.add(key)
                result.source_refs.append(r)
    return result


# ============================================================
#  单个任务执行
# ============================================================
async def run_one_task(
    task: SubagentTask,
    ai_service: Any,
    limits: SubagentLimits,
    cancel_event: asyncio.Event | None = None,
) -> SubagentResult:
    """执行单个子任务。

    - 显式关闭工具自动加载与执行（这两个选项默认开启，必须显式关掉）
    - 受单任务 token 与超时约束
    - 失败重试至 limits.max_retries 次
    """
    result = SubagentResult(task_id=task.task_id)
    prompt = build_user_prompt(task)
    attempts = max(1, limits.max_retries + 1)
    last_error = ""

    for attempt in range(attempts):
        if cancel_event is not None and cancel_event.is_set():
            result.status = "cancelled"
            result.error = "任务已取消"
            return result

        try:
            response = await asyncio.wait_for(
                ai_service.generate_text(
                    prompt=prompt,
                    system_prompt=CONSISTENCY_SYSTEM_PROMPT,
                    max_tokens=limits.max_tokens_per_task,
                    tools=None,               # 不注入任何工具
                    tool_choice="none",       # 明确禁止工具选择
                    auto_mcp=False,           # 不自动加载 MCP 工具
                    handle_tool_calls=False,  # 不自动执行工具调用
                ),
                timeout=limits.turn_timeout_seconds,
            )
        except asyncio.TimeoutError:
            last_error = f"第 {attempt + 1} 次尝试超时"
            logger.warning("子任务 %s %s", task.task_id, last_error)
            continue
        except asyncio.CancelledError:
            result.status = "cancelled"
            result.error = "任务已取消"
            raise
        except Exception as exc:
            last_error = f"第 {attempt + 1} 次尝试失败：{exc}"
            logger.warning("子任务 %s %s", task.task_id, last_error)
            continue

        usage = response.get("usage") or {}
        result.usage = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "attempts": attempt + 1,
        }

        parsed = parse_result(task, response.get("content") or "")
        parsed.usage = result.usage
        if parsed.status == "completed":
            return parsed

        last_error = parsed.error
        if parsed.unchecked:
            result.unchecked = parsed.unchecked

    result.status = "failed"
    result.error = last_error or "未知错误"
    return result


# ============================================================
#  批量执行（有界并发）
# ============================================================
async def run_batch(
    tasks: list[SubagentTask],
    ai_service: Any,
    limits: SubagentLimits = DEFAULT_LIMITS,
    budget: TurnBudget | None = None,
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """有界并发执行子任务，逐个 yield 进度事件。

    yield 的事件形态（与主流程现有 SSE 事件区分）：
        {"kind": "start",  "task_id", "objective", "sequence"}
        {"kind": "done",   "task_id", "result": <SubagentResult.to_dict()>}
        {"kind": "error",  "task_id", "error"}
        {"kind": "all_done", "summary": [...]}

    调用方（project_agent_service）负责把 kind 映射成 step_start/step_update。
    """
    if not tasks:
        return

    # --- 预校验 ---
    for task in tasks:
        validate_task(task, limits)

    if budget is not None:
        budget.reserve(len(tasks))

    semaphore = asyncio.Semaphore(max(1, limits.max_concurrency))

    async def guarded(task: SubagentTask) -> tuple[SubagentTask, SubagentResult]:
        async with semaphore:
            try:
                res = await run_one_task(task, ai_service, limits, cancel_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("子任务 %s 异常: %s", task.task_id, exc)
                res = SubagentResult(
                    task_id=task.task_id, status="failed", error=str(exc)
                )
            return task, res

    # --- 全部启动，受 semaphore 限流 ---
    for task in tasks:
        yield {
            "kind": "start",
            "task_id": task.task_id,
            "objective": task.objective,
            "sequence": task.sequence,
            "task_type": task.task_type,
            "task_type_label": TASK_TYPE_LABELS.get(task.task_type, task.task_type),
        }

    futures = [asyncio.create_task(guarded(t)) for t in tasks]
    results: list[tuple[SubagentTask, SubagentResult]] = []

    try:
        for coro in asyncio.as_completed(futures):
            try:
                task, res = await coro
            except asyncio.CancelledError:
                raise
            results.append((task, res))
            yield {
                "kind": "done" if res.status == "completed" else "error",
                "task_id": task.task_id,
                "status": res.status,
                "result": res.to_dict(),
                "error": res.error,
            }
    finally:
        # 取消或异常时，等所有子任务收尾再返回，避免留下悬挂任务
        for fut in futures:
            if not fut.done():
                fut.cancel()
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)

    if budget is not None:
        budget.commit()

    # 汇总：只回传摘要，避免完整结果挤占主上下文
    summary = [res.to_summary() for _, res in results]
    failed = [r.task_id for _, r in results if r.status == "failed"]
    cancelled = [r.task_id for _, r in results if r.status == "cancelled"]

    yield {
        "kind": "all_done",
        "total": len(results),
        "failed": failed,
        "cancelled": cancelled,
        "summary": summary,
        "budget": budget.to_dict() if budget else None,
    }


# ============================================================
#  辅助
# ============================================================
def make_task_id(prefix: str = "sa") -> str:
    """生成任务 ID。"""
    return f"{prefix}_{int(time.time() * 1000):x}_{id(object()):x}"


__all__ = [
    "BudgetError",
    "DEFAULT_LIMITS",
    "SubagentLimits",
    "SubagentTask",
    "SubagentResult",
    "SourceRef",
    "Finding",
    "TurnBudget",
    "build_user_prompt",
    "parse_result",
    "run_one_task",
    "run_batch",
    "make_task_id",
]
