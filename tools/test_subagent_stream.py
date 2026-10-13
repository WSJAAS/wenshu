# -*- coding: utf-8 -*-
"""阶段2 验收：子任务进度在任务结束前可见，且多任务交错不串行。

本测试**不调用真实模型**：用一个可控的假 AI 服务替换 AIService，
直接驱动 ProjectAgentService 的流式分支，检查事件序列。

验证点：
  1. 派发后立刻产生 step_start（子任务步骤先建出来）
  2. 在 all_done 之前就能收到 step_update（进度实时可见）
  3. 三个子任务的 update 事件交错出现（并发，不是串行）
  4. step_type 为 subagent，携带 task_id 与 stage
  5. 结果里区分「发现问题」与「未检查」
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


# ============================================================
#  假 AI：可控延迟，让三个任务真正交错
# ============================================================
class FakeAI:
    def __init__(self, delays: list[float]):
        self.delays = delays
        self.idx = 0
        self.timeline: list[tuple[float, str]] = []
        self.kwargs_seen: list[dict] = []

    async def generate_text(self, **kwargs):
        self.kwargs_seen.append(kwargs)
        i = self.idx
        self.idx += 1
        delay = self.delays[i] if i < len(self.delays) else 0.05
        prompt = kwargs.get("prompt", "")
        tag = "?"
        for line in prompt.splitlines():
            if line.startswith("【材料1】"):
                tag = line[6:24]
                break
        self.timeline.append((time.monotonic(), f"start:{tag}"))
        await asyncio.sleep(delay)
        self.timeline.append((time.monotonic(), f"end:{tag}"))
        body = {
            "findings": [{
                "summary": f"{tag} 发现一处问题",
                "severity": "warning",
                "source_refs": [{"kind": "chapter", "id": tag, "label": tag,
                                 "excerpt": "支撑原文"}],
            }],
            "unchecked": [f"{tag} 的时间线未检查"],
        }
        return {"content": json.dumps(body, ensure_ascii=False),
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


# ============================================================
async def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import Base
    from app.models.project import Project
    from app.models.chapter import Chapter
    from app.models.project_agent import AgentConversation, AgentMessage
    from app.services.project_agent_service import ProjectAgentService
    from app.services.project_agent_tools import ProjectAgentToolRegistry

    # ---- 临时库 ----
    tmpdir = tempfile.mkdtemp(prefix="subagent_")
    db_path = Path(tmpdir) / "t.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        project = Project(id="p1", user_id="local", title="测试作品", genre="悬疑")
        db.add(project)
        chapters = []
        for i in (1, 2, 3):
            ch = Chapter(
                id=f"c{i}", project_id="p1", title=f"第{i}章",
                chapter_number=i, content=f"第{i}章的正文内容。" * 20, status="draft",
            )
            db.add(ch)
            chapters.append(ch)
        await db.commit()
        await db.refresh(project)

        conv = AgentConversation(id="conv1", user_id="local", project_id="p1", title="测试")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

        um = AgentMessage(id="um1", conversation_id="conv1", role="user", content="帮我检查三章一致性")
        db.add(um)
        await db.commit()
        await db.refresh(um)

        # ---- 构造服务 ----
        fake = FakeAI([0.30, 0.20, 0.10])  # 故意错开，便于观察交错
        registry = ProjectAgentToolRegistry(project, db)
        svc = ProjectAgentService.__new__(ProjectAgentService)
        svc.project = project
        svc.db = db
        svc.user_id = "local"
        svc.ai_service = fake
        svc.registry = registry
        svc.mcp_tools = []
        svc.mcp_tool_definitions = []
        svc._turn_budget = None
        svc._cancel_event = None

        from app.models.project_agent import AgentExecutionStep, AgentToolCall
        record = AgentToolCall(
            id="tc1", conversation_id="conv1", user_id="local", project_id="p1",
            tool_name="dispatch_subagents", arguments={}, risk_level=0,
            requires_confirmation=False, status="proposed",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        tool_step = AgentExecutionStep(
            id="ts1", conversation_id="conv1", sequence=1, step_type="tool",
            category="project", title="dispatch_subagents", status="running",
        )
        db.add(tool_step)
        await db.commit()
        await db.refresh(tool_step)

        arguments = {
            "objective": "检查三章一致性",
            "tasks": [
                {"task_type": "consistency_check", "chapter_ids": [f"c{i}"], "objective": f"检查第{i}章"}
                for i in (1, 2, 3)
            ],
        }

        # ---- 收集事件 ----
        print("=" * 72)
        print("[1] 通过流式分支派发三个子任务")
        print("=" * 72)
        events = []
        t0 = time.monotonic()
        async for ev in svc._dispatch_subagents_stream(
            conversation=conv, user_message=um, tool_step=tool_step, record=record,
            arguments=arguments, sequence=2, tool_context=[], steps=[],
        ):
            elapsed = time.monotonic() - t0
            events.append((elapsed, ev))
        span = time.monotonic() - t0

        starts = [(t, e) for t, e in events
                  if e.get("type") == "step_start"
                  and (e.get("data") or {}).get("step_type") == "subagent"]
        updates = [(t, e) for t, e in events if e.get("type") == "step_update"]

        check("产生子任务 step_start", len(starts) == 3, f"数量={len(starts)}")
        check("产生 step_update", len(updates) >= 4, f"数量={len(updates)}")
        print(f"       总耗时 {span:.2f}s（延迟和=0.60s，并发应显著短于串行）")
        check("并发执行（短于串行和）", span < 0.55, f"耗时={span:.2f}s")

        # ---- 关键：进度在结束前可见 ----
        print()
        print("=" * 72)
        print("[2] 进度是否在任务结束前出现")
        print("=" * 72)
        # 子任务 step_update 指 data.step_type == 'subagent' 的更新
        child_updates = [(t, e) for t, e in updates
                         if (e.get("data") or {}).get("step_type") == "subagent"]
        check("有子任务进度更新", len(child_updates) >= 3, f"数量={len(child_updates)}")
        if child_updates:
            first_upd = child_updates[0][0]
            print(f"       首个子任务进度出现在 {first_upd:.2f}s")
            check("首个子任务进度早于整体结束", first_upd < span * 0.95,
                  f"{first_upd:.2f}s < {span:.2f}s")

        # ---- 交错验证 ----
        print()
        print("=" * 72)
        print("[3] 三个任务是否交错（非串行）")
        print("=" * 72)
        print("       假 AI 调用时间线：")
        for t, name in fake.timeline:
            print(f"         {t - t0:6.3f}s  {name}")
        # 检查是否存在「第二个 start 早于第一个 end」
        starts_t = [t for t, n in fake.timeline if n.startswith("start:")]
        ends_t = [t for t, n in fake.timeline if n.startswith("end:")]
        interleaved = len(starts_t) >= 2 and len(ends_t) >= 1 and starts_t[1] < ends_t[0]
        check("任务确实交错执行", interleaved,
              f"第2个start={starts_t[1]-t0:.3f}s < 第1个end={ends_t[0]-t0:.3f}s"
              if len(starts_t) >= 2 and ends_t else "数据不足")

        # ---- 步骤身份 ----
        print()
        print("=" * 72)
        print("[4] 步骤身份与内容")
        print("=" * 72)
        if starts:
            d = starts[0][1]["data"]
            check("step_type=subagent", d.get("step_type") == "subagent", f"={d.get('step_type')}")
            check("携带 detail.task_id", bool((d.get("detail") or {}).get("task_id")),
                  f"={((d.get('detail') or {}).get('task_id'))}")
            check("携带 detail.stage", bool((d.get("detail") or {}).get("stage")),
                  f"={((d.get('detail') or {}).get('stage'))}")
            check("携带 task_type_label", bool((d.get("detail") or {}).get("task_type_label")),
                  f"={((d.get('detail') or {}).get('task_type_label'))}")

        # ---- 结果区分 ----
        print()
        print("=" * 72)
        print("[5] 结果区分「发现问题」与「未检查」")
        print("=" * 72)
        done_steps = [(t, e) for t, e in child_updates
                      if (e.get("data") or {}).get("status") in ("completed", "failed")]
        check("有完成的子任务", len(done_steps) >= 3, f"数量={len(done_steps)}")
        if done_steps:
            det = done_steps[-1][1]["data"].get("detail") or {}
            res = det.get("result") or {}
            check("结果含 findings", isinstance(res.get("findings"), list),
                  f"数量={len(res.get('findings') or [])}")
            check("结果含 unchecked", isinstance(res.get("unchecked"), list),
                  f"数量={len(res.get('unchecked') or [])}")

        # ---- 主 Agent 收到的是摘要 ----
        print()
        print("=" * 72)
        print("[6] 回给主 Agent 的是摘要而非全量")
        print("=" * 72)
        check("record.result 已写入", bool(record.result), f"keys={list((record.result or {}).keys())}")
        if record.result:
            check("result 含 total", "total" in record.result, f"total={record.result.get('total')}")
            check("result 含 summary", "summary" in record.result)

        # ---- 预算 ----
        print()
        print("=" * 72)
        print("[7] 预算账本")
        print("=" * 72)
        budget = getattr(svc, "_turn_budget", None)
        check("预算已建立", budget is not None)
        if budget:
            check("已记账 3 个", budget.dispatched == 3, f"dispatched={budget.dispatched}")
            check("剩余额度为 0", budget.remaining == 0, f"remaining={budget.remaining}")

        # ---- 重复派发被拒 ----
        print()
        print("=" * 72)
        print("[8] 重复派发被预算拦截")
        print("=" * 72)
        record2 = AgentToolCall(
            id="tc2", conversation_id="conv1", user_id="local", project_id="p1",
            tool_name="dispatch_subagents", arguments={}, risk_level=0,
            requires_confirmation=False, status="proposed",
        )
        db.add(record2)
        await db.commit()
        await db.refresh(record2)
        ts2 = AgentExecutionStep(
            id="ts2", conversation_id="conv1", sequence=9, step_type="tool",
            category="project", title="dispatch_subagents", status="running",
        )
        db.add(ts2)
        await db.commit()
        await db.refresh(ts2)

        evs2 = []
        async for ev in svc._dispatch_subagents_stream(
            conversation=conv, user_message=um, tool_step=ts2, record=record2,
            arguments=arguments, sequence=20, tool_context=[], steps=[],
        ):
            evs2.append(ev)
        check("第二次派发被拒", record2.status == "failed",
              f"status={record2.status} err={record2.error_message[:50] if record2.error_message else ''}")

    await engine.dispose()

    print()
    print("=" * 72)
    print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
    print("=" * 72)
    for f in FAILS:
        print("  - " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
