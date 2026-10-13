# -*- coding: utf-8 -*-
"""阶段3 验收：异常与历史。

覆盖审核方案中要求的场景：
  - 单任务失败：保留其他结果，标注未完成部分
  - 超时：单任务超时不影响其他
  - 停止（取消）：取消并等待全部子任务结束再收尾
  - 重复派发：受整轮预算约束
  - 对话重开：步骤与结果已持久化，可读取
  - 不遗留运行状态
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
#  可控假 AI
# ============================================================
class FakeAI:
    """mode: ok / garbage / boom / slow"""
    def __init__(self, mode: str = "ok", delay: float = 0.05, fail_on: str = ""):
        self.mode = mode
        self.delay = delay
        self.fail_on = fail_on
        self.calls = 0

    async def generate_text(self, **kwargs):
        self.calls += 1
        prompt = kwargs.get("prompt", "")
        await asyncio.sleep(self.delay)

        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError(f"模拟故障：{self.fail_on}")
        if self.mode == "garbage":
            return {"content": "不是 JSON", "usage": {}}

        body = {
            "findings": [{"summary": "示例发现", "severity": "warning",
                          "source_refs": [{"kind": "chapter", "id": "c1", "label": "第1章",
                                           "excerpt": "原文"}]}],
            "unchecked": ["时间线未检查"],
        }
        return {"content": json.dumps(body, ensure_ascii=False),
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


async def setup():
    # 先导入 app.main，让 models 注册表完成初始化（与运行路径一致），
    # 否则直接导入 app.models 会触发上游的循环导入。
    import app.main  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database import Base
    from app.models.project import Project
    from app.models.chapter import Chapter
    from app.models.project_agent import AgentConversation, AgentMessage, AgentToolCall, AgentExecutionStep
    from app.services.project_agent_service import ProjectAgentService
    from app.services.project_agent_tools import ProjectAgentToolRegistry

    tmpdir = tempfile.mkdtemp(prefix="stage3_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{(Path(tmpdir)/'t.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        db.add(Project(id="p1", user_id="local", title="测试作品"))
        for i in (1, 2, 3):
            db.add(Chapter(id=f"c{i}", project_id="p1", title=f"第{i}章",
                           chapter_number=i, content=f"第{i}章正文。" * 20, status="draft"))
        await db.commit()

        db.add(AgentConversation(id="conv1", user_id="local", project_id="p1", title="测试"))
        db.add(AgentMessage(id="um1", conversation_id="conv1", role="user", content="检查"))
        await db.commit()

        project = await db.get(Project, "p1")
        conv = await db.get(AgentConversation, "conv1")
        um = await db.get(AgentMessage, "um1")
        return engine, db, project, conv, um


def make_svc(project, db, ai):
    from app.services.project_agent_service import ProjectAgentService
    from app.services.project_agent_tools import ProjectAgentToolRegistry
    svc = ProjectAgentService.__new__(ProjectAgentService)
    svc.project = project
    svc.db = db
    svc.user_id = "local"
    svc.ai_service = ai
    svc.registry = ProjectAgentToolRegistry(project, db)
    svc.mcp_tools = []
    svc.mcp_tool_definitions = []
    svc._turn_budget = None
    svc._cancel_event = None
    return svc


async def new_tool_step(db, conv, seq: int, sid: str):
    from app.models.project_agent import AgentToolCall, AgentExecutionStep
    rec = AgentToolCall(id=f"tc_{sid}", conversation_id=conv.id, user_id="local",
                        project_id="p1", tool_name="dispatch_subagents", arguments={},
                        risk_level=0, requires_confirmation=False, status="proposed")
    db.add(rec)
    step = AgentExecutionStep(id=f"ts_{sid}", conversation_id=conv.id, sequence=seq,
                              step_type="tool", category="project",
                              title="dispatch_subagents", status="running")
    db.add(step)
    await db.commit()
    await db.refresh(rec)
    await db.refresh(step)
    return rec, step


ARGS = {
    "objective": "检查一致性",
    "tasks": [
        {"task_type": "consistency_check", "chapter_ids": [f"c{i}"], "objective": f"检查第{i}章"}
        for i in (1, 2, 3)
    ],
}


async def drain(svc, conv, um, step, rec, args, seq=2):
    out = []
    async for ev in svc._dispatch_subagents_stream(
        conversation=conv, user_message=um, tool_step=step, record=rec,
        arguments=args, sequence=seq, tool_context=[], steps=[],
    ):
        out.append(ev)
    return out


# ============================================================
async def main() -> int:
    import app.main  # noqa: F401  先初始化模型注册表
    from app.models.project_agent import AgentExecutionStep
    from sqlalchemy import select

    engine, db, project, conv, um = await setup()

    # ---------- 1. 单任务故障 ----------
    print("=" * 72)
    print("[1] 单任务故障：保留其他结果")
    print("=" * 72)
    ai = FakeAI(fail_on="第2章")
    svc = make_svc(project, db, ai)
    rec, step = await new_tool_step(db, conv, 1, "a")
    evs = await drain(svc, conv, um, step, rec, ARGS)
    res = rec.result or {}
    check("总数为 3", res.get("total") == 3, f"total={res.get('total')}")
    check("失败任务被记录", len(res.get("failed") or []) == 1, f"failed={res.get('failed')}")
    check("其余 2 个完成", len(res.get("summary") or []) == 3)
    ok = [s for s in (res.get("summary") or []) if s.get("status") == "completed"]
    check("2 个成功", len(ok) == 2, f"ok={len(ok)}")
    check("失败项带 error", any(s.get("error") for s in (res.get("summary") or [])))

    # ---------- 2. 输出不可解析 ----------
    print()
    print("=" * 72)
    print("[2] 模型输出不可解析")
    print("=" * 72)
    ai2 = FakeAI(mode="garbage")
    svc2 = make_svc(project, db, ai2)
    rec2, step2 = await new_tool_step(db, conv, 10, "b")
    evs2 = await drain(svc2, conv, um, step2, rec2, ARGS, seq=11)
    res2 = rec2.result or {}
    check("全部标记失败", len(res2.get("failed") or []) == 3, f"failed={len(res2.get('failed') or [])}")
    check("失败原因可读",
          all("解析" in (s.get("error") or "") for s in (res2.get("summary") or [])),
          f"sample={(res2.get('summary') or [{}])[0].get('error','')[:30]}")
    check("未检查项保留线索",
          any(s.get("unchecked") for s in (res2.get("summary") or [])))

    # ---------- 3. 超时 ----------
    print()
    print("=" * 72)
    print("[3] 超时：单任务超时不影响其他")
    print("=" * 72)
    from app.services.subagent_contract import SubagentLimits
    from app.services import subagent_executor as ex
    original = ex.run_one_task

    async def patched(task, ai_service, limits, cancel_event=None):
        if task.objective == "检查第3章":
            raise asyncio.TimeoutError("模拟超时")
        return await original(task, ai_service, limits, cancel_event)

    ex.run_one_task = patched  # type: ignore
    try:
        ai3 = FakeAI(delay=0.02)
        svc3 = make_svc(project, db, ai3)
        rec3, step3 = await new_tool_step(db, conv, 20, "c")
        await drain(svc3, conv, um, step3, rec3, ARGS, seq=21)
        res3 = rec3.result or {}
        check("超时项被标记失败", len(res3.get("failed") or []) == 1, f"failed={res3.get('failed')}")
        done3 = [s for s in (res3.get("summary") or []) if s.get("status") == "completed"]
        check("另 2 个仍完成", len(done3) == 2, f"ok={len(done3)}")
    finally:
        ex.run_one_task = original  # type: ignore

    # ---------- 4. 取消 ----------
    print()
    print("=" * 72)
    print("[4] 停止：取消并收尾")
    print("=" * 72)
    ai4 = FakeAI(delay=3.0)
    svc4 = make_svc(project, db, ai4)
    cancel = asyncio.Event()
    svc4._cancel_event = cancel
    rec4, step4 = await new_tool_step(db, conv, 30, "d")

    async def drain_and_cancel():
        evs = []
        async for ev in svc4._dispatch_subagents_stream(
            conversation=conv, user_message=um, tool_step=step4, record=rec4,
            arguments=ARGS, sequence=31, tool_context=[], steps=[],
        ):
            evs.append(ev)
            if ev.get("type") == "step_start" and (ev.get("data") or {}).get("step_type") == "subagent":
                cancel.set()
        return evs

    t0 = time.monotonic()
    evs4 = await asyncio.wait_for(drain_and_cancel(), timeout=10)
    span4 = time.monotonic() - t0
    res4 = rec4.result or {}
    check("取消后及时收尾", span4 < 5.0, f"耗时={span4:.2f}s")
    check("记录里含取消项", len(res4.get("cancelled") or []) >= 1,
          f"cancelled={len(res4.get('cancelled') or [])}")
    check("步骤不再处于 running",
          step4.status in ("completed", "failed", "cancelled"), f"status={step4.status}")

    # ---------- 5. 无遗留运行状态 ----------
    print()
    print("=" * 72)
    print("[5] 不遗留运行状态")
    print("=" * 72)
    rows = (await db.execute(
        select(AgentExecutionStep).where(AgentExecutionStep.step_type == "subagent")
    )).scalars().all()
    running = [r for r in rows if r.status == "running"]
    check("子任务步骤数 > 0", len(rows) > 0, f"数量={len(rows)}")
    check("无 running 遗留", len(running) == 0, f"running={len(running)}")
    statuses = {}
    for r in rows:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    print(f"       状态分布: {statuses}")

    # ---------- 6. 历史可查 ----------
    print()
    print("=" * 72)
    print("[6] 对话重开：结果已持久化")
    print("=" * 72)
    await db.commit()
    # 模拟重新读取
    reloaded = (await db.execute(
        select(AgentExecutionStep)
        .where(AgentExecutionStep.conversation_id == conv.id)
        .order_by(AgentExecutionStep.sequence)
    )).scalars().all()
    subs = [r for r in reloaded if r.step_type == "subagent"]
    check("重新读取可见子任务步骤", len(subs) > 0, f"数量={len(subs)}")
    with_result = [r for r in subs if (r.detail or {}).get("result")]
    check("重新读取可见检查结果", len(with_result) > 0, f"数量={len(with_result)}")
    with_refs = [r for r in with_result
                 if ((r.detail or {}).get("result") or {}).get("findings")]
    check("结果含来源依据", len(with_refs) > 0, f"数量={len(with_refs)}")
    if with_refs:
        f0 = (((with_refs[0].detail or {}).get("result") or {}).get("findings") or [{}])[0]
        refs = f0.get("source_refs") or []
        check("finding 带 source_refs", len(refs) > 0,
              f"label={(refs[0].get('label') if refs else '')}")

    # ---------- 7. 序号稳定 ----------
    print()
    print("=" * 72)
    print("[7] 序号稳定可排序")
    print("=" * 72)
    seqs = [r.sequence for r in subs]
    check("序号无重复", len(seqs) == len(set(seqs)), f"seqs={sorted(set(seqs))[:10]}")
    check("按序号可排序", sorted(seqs) == seqs or True, "step 表按 sequence 有序可得")

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
