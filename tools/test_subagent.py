# -*- coding: utf-8 -*-
"""子代理模块的离线自检（不依赖模型）。

覆盖：契约校验、预算账本、结果解析、有界并发、取消、失败隔离。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

from app.services.subagent_contract import (  # noqa: E402
    BudgetError,
    SubagentTask,
    TurnBudget,
    validate_task,
)
from app.services.subagent_executor import parse_result, run_batch  # noqa: E402

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


# ============================================================
#  假 AI 服务
# ============================================================
class FakeAI:
    """模拟模型：可控延迟、可控失败、可控输出。"""

    def __init__(self, delay: float = 0.05, mode: str = "ok"):
        self.delay = delay
        self.mode = mode
        self.calls: list[float] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.kwargs_seen: list[dict] = []

    async def generate_text(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        import time
        self.calls.append(time.monotonic())
        try:
            await asyncio.sleep(self.delay)
            name = kwargs.get("prompt", "")[:40]
            if self.mode == "garbage":
                return {"content": "这不是 JSON", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            if self.mode == "boom":
                raise RuntimeError("模拟模型故障")
            body = {
                "findings": [
                    {
                        "summary": f"发现于{name}",
                        "severity": "warning",
                        "source_refs": [
                            {"kind": "chapter", "id": "c1", "label": "第1章", "excerpt": "原文片段"}
                        ],
                    }
                ],
                "unchecked": ["时间线未检查"],
            }
            return {
                "content": json.dumps(body, ensure_ascii=False),
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        finally:
            self.concurrent -= 1


def mk_task(i: int) -> SubagentTask:
    return SubagentTask(
        task_id=f"t{i}",
        task_type="consistency_check",
        objective=f"检查第{i}章一致性",
        sources=[{"kind": "chapter", "id": f"c{i}", "label": f"第{i}章", "text": f"第{i}章正文内容"}],
        sequence=i,
    )


async def collect(tasks, ai, **kw):
    events = []
    async for ev in run_batch(tasks, ai, **kw):
        events.append(ev)
    return events


# ============================================================
def main() -> int:
    print("=" * 70)
    print("[1] 契约与预算")
    print("=" * 70)
    t = mk_task(1)
    validate_task(t)
    check("合法任务通过", True)

    b = TurnBudget()
    b.reserve(2)
    check("预留 2 个", b.remaining == 1, f"remaining={b.remaining}")
    try:
        b.reserve(2)
        check("超限被拦截", False)
    except BudgetError:
        check("超限被拦截", True)
    b.commit()
    check("提交后计数", b.dispatched == 2, f"dispatched={b.dispatched}")

    print()
    print("=" * 70)
    print("[2] 结果解析")
    print("=" * 70)
    r = parse_result(t, json.dumps({
        "findings": [{"summary": "年龄不符", "severity": "warning",
                      "source_refs": [{"kind": "chapter", "id": "c1", "label": "第1章",
                                       "excerpt": "他今年二十岁"}]}],
        "unchecked": ["时间线未检查"],
    }, ensure_ascii=False))
    check("正常 JSON 解析", r.status == "completed", f"status={r.status}")
    check("提取 findings", len(r.findings) == 1)
    check("提取 unchecked", r.unchecked == ["时间线未检查"])
    check("汇总来源引用", len(r.source_refs) == 1)

    # 反引号包裹（用 chr 避免任何转义干扰）
    fence = chr(96) * 3
    wrapped = f"{fence}json\n" + json.dumps({"findings": [], "unchecked": ["材料不足"]}, ensure_ascii=False) + f"\n{fence}"
    r2 = parse_result(t, wrapped)
    check("去代码块包裹", r2.status == "completed" and r2.unchecked == ["材料不足"],
          f"status={r2.status}")

    r3 = parse_result(t, "不是 JSON 的文本")
    check("非法输出标记失败而非抛错", r3.status == "failed", f"error={r3.error[:30]}")

    print()
    print("=" * 70)
    print("[3] 有界并发")
    print("=" * 70)
    ai = FakeAI(delay=0.1)
    tasks = [mk_task(i) for i in range(1, 4)]
    events = asyncio.run(collect(tasks, ai))

    starts = [e for e in events if e["kind"] == "start"]
    dones = [e for e in events if e["kind"] in ("done", "error")]
    all_done = [e for e in events if e["kind"] == "all_done"]

    check("3 个任务各发 start", len(starts) == 3, f"starts={len(starts)}")
    check("3 个任务各发结果", len(dones) == 3, f"dones={len(dones)}")
    check("发出 all_done", len(all_done) == 1)
    # 延迟 0.1 × 3 串行需 0.3s；并发应显著短于串行
    import time as _t
    span = ai.calls[-1] - ai.calls[0] if len(ai.calls) > 1 else 0
    check("确实并发执行", span < 0.15, f"跨度={span:.3f}s（串行应≈0.2s）")
    check("并发未超上限", ai.max_concurrent <= 3, f"峰值={ai.max_concurrent}")

    print()
    print("=" * 70)
    print("[4] 模型调用参数（必须显式关闭工具）")
    print("=" * 70)
    k = ai.kwargs_seen[0] if ai.kwargs_seen else {}
    check("tools 显式为 None", k.get("tools") is None, f"tools={k.get('tools')}")
    check("tool_choice 为 none", k.get("tool_choice") == "none", f"={k.get('tool_choice')}")
    check("auto_mcp 关闭", k.get("auto_mcp") is False, f"={k.get('auto_mcp')}")
    check("handle_tool_calls 关闭", k.get("handle_tool_calls") is False,
          f"={k.get('handle_tool_calls')}")
    check("有 max_tokens 上限", isinstance(k.get("max_tokens"), int) and k["max_tokens"] > 0,
          f"={k.get('max_tokens')}")

    print()
    print("=" * 70)
    print("[5] 单任务失败不影响其他")
    print("=" * 70)
    mixed = FakeAI(mode="ok", delay=0.05)
    orig = mixed.generate_text

    async def selective(**kw):
        if "第2章" in kw.get("prompt", ""):
            return {"content": "坏输出", "usage": {}}
        return await orig(**kw)

    mixed.generate_text = selective  # type: ignore
    events = asyncio.run(collect([mk_task(i) for i in (1, 2, 3)], mixed))
    all_done = [e for e in events if e["kind"] == "all_done"][0]
    check("失败任务被标记", all_done["failed"] == ["t2"], f"failed={all_done['failed']}")
    check("其余任务仍完成", all_done["total"] == 3, f"total={all_done['total']}")
    ok_tasks = [e for e in events if e["kind"] == "done"]
    check("2 个成功", len(ok_tasks) == 2, f"ok={len(ok_tasks)}")

    print()
    print("=" * 70)
    print("[6] 取消行为")
    print("=" * 70)
    slow = FakeAI(delay=5.0)
    cancel = asyncio.Event()

    async def run_then_cancel():
        evs = []
        async for ev in run_batch([mk_task(i) for i in (1, 2)], slow, cancel_event=cancel):
            evs.append(ev)
            if ev["kind"] == "start" and ev["task_id"] == "t2":
                cancel.set()
        return evs

    evs = asyncio.run(run_then_cancel())
    all_done = [e for e in evs if e["kind"] == "all_done"]
    check("取消后仍收尾", len(all_done) == 1)

    print()
    print("=" * 70)
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
    print("=" * 70)
    for f in FAILS:
        print("  - " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
