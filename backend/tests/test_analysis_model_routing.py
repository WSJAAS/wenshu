"""章节分析模型路由回归测试。

## 在测什么

需求：**章节分析与撰写正文允许使用不同模型**。机制已存在于
`app/api/settings.py:282` 的 `get_user_ai_service_from_db_by_usage(usage="chapter_analysis")`
—— 它读用户在设置页配置的「章节内容分析专用 API 预设」
（`Settings.preferences` 的 `api_presets` + `chapter_analysis_preset_id`），
**配了就用预设，没配回退默认配置**。

`app/api/chapters.py` 的 `_analyze_chapter_background_impl` 里那段：

    if ai_service is None:
        ai_service = await get_user_ai_service_from_db_by_usage(..., usage="chapter_analysis")

是唯一的「按用途取模型」入口。也就是说：**调用方传 ai_service 就等于绕过用户预设**，
不传才会走 by_usage。

## 缺陷形态

`_run_chapter_generation_bg`（「写完自动分析」这条路径）曾把**写作模型**显式传进分析：

    analysis_success = await analyze_chapter_background(..., ai_service=ai_service)

于是「写完自动分析」永远用写作模型，用户配的分析预设被静默绕过。
本次修复删掉该实参，让该调用点与其它入口一致（不传 → impl 内部走 by_usage）。

## 为什么用「源码静态断言」打底

`_analyze_chapter_background_impl` 是重函数（真 DB 会话、PlotAnalyzer、伏笔服务、
向量记忆、终态事务），端到端跑通成本高且脆弱。而本缺陷是**纯参数传递**问题：
「这个调用点有没有把 ai_service 传下去」在 AST 上一读便知，且不依赖运行时环境。
参照 `backend/tests/test_migration_safety.py` 的既有做法（读源码 / AST 断言）。

`_run_chapter_generation_bg` 在 `chapters.py` 里**定义了两次**（后者覆盖前者），
所以按函数名收集时会拿到两个节点；本测试两个都查，避免修了被覆盖的那个。

## 两道断言各管一头

| 用例组 | 管什么 |
| :--- | :--- |
| `test_*_call_site*` / `test_*_source*` | 静态：4 个调用点里谁传了 `ai_service` —— 防回归的重锤 |
| `test_impl_*` | 行为：`ai_service=None` → 真的调 by_usage，且 `usage="chapter_analysis"`；传了 → 根本不调 by_usage |

静态断言保证「没传」，行为断言保证「没传之后确实走预设」。
只测一头都可能漏：删掉实参但 impl 的 by_usage 被改动，静态用例照样绿。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

import app.api.chapters as chapters_mod
from app.models.analysis_task import AnalysisTask
from app.models.chapter import Chapter


BACKEND_DIR = Path(__file__).resolve().parent.parent
CHAPTERS_PY = BACKEND_DIR / "app" / "api" / "chapters.py"

ANALYSIS_FN = "analyze_chapter_background"
IMPL_FN = "_analyze_chapter_background_impl"
GENERATION_BG_FN = "_run_chapter_generation_bg"
SEQUENTIAL_ANALYSIS_FN = "_run_batch_analysis_in_sequence"

# 「一键分析顺序执行」这条路径**有意**自己解析一次模型再传下去（见
# _run_batch_analysis_in_sequence 的 ai_service 形参，调用点传的是 None）。
# 它不在本次需求范围内，这里显式点名，防止把「有意为之」误判成回归。
EXPECTED_FORWARDING_OWNER = SEQUENTIAL_ANALYSIS_FN


# ============================================================
#  1. 源码静态断言：哪些调用点把 ai_service 传了下去
# ============================================================
def _parse_chapters() -> ast.Module:
    """读 chapters.py 并解析。

    **只读不 import**：这个模块顶层会建 APIRouter、拉起服务对象，
    import 有副作用；本组用例要的就是「不执行也能查」。
    """
    return ast.parse(
        CHAPTERS_PY.read_text(encoding="utf-8"), filename=str(CHAPTERS_PY)
    )


def _top_level_defs(tree: ast.Module, name: str):
    """取顶层同名 def 的全部节点（`_run_chapter_generation_bg` 有两个）。"""
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _analysis_calls(node: ast.AST):
    """收集某颗子树里所有 `analyze_chapter_background(...)` 调用节点。"""
    calls = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id == ANALYSIS_FN:
            calls.append(sub)
        elif isinstance(func, ast.Attribute) and func.attr == ANALYSIS_FN:
            calls.append(sub)
    return calls


def _forwards_ai_service(call: ast.Call) -> bool:
    """该调用是否显式传了 `ai_service=` 实参。"""
    return any(kw.arg == "ai_service" for kw in call.keywords)


def _enclosing_def(tree: ast.Module, call: ast.Call):
    """调用点所属的顶层函数名（用于在断言里点名）。"""
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= call.lineno <= (node.end_lineno or node.lineno):
            return node.name
    return None


@pytest.fixture(scope="module")
def chapters_tree() -> ast.Module:
    return _parse_chapters()


@pytest.fixture(scope="module")
def all_analysis_calls(chapters_tree: ast.Module):
    return _analysis_calls(chapters_tree)


def test_analysis_call_site_count_is_four(all_analysis_calls):
    """chapters.py 里恰好 4 个 analyze_chapter_background 调用点。

    数量变化说明有人新增/删除了入口 —— 新入口同样要遵守「分析不继承写作模型」，
    所以这里数死，逼改动者回来读本文件。
    """
    assert len(all_analysis_calls) == 4, (
        f"期望 4 个 analyze_chapter_background 调用点，实际 {len(all_analysis_calls)}："
        f"{[c.lineno for c in all_analysis_calls]}。新增入口请确认它不显式传 ai_service。"
    )


@pytest.mark.parametrize("index", [0, 1])
def test_generation_bg_call_sites_do_not_forward_ai_service(
    chapters_tree: ast.Module, index: int
):
    """**核心回归**：`_run_chapter_generation_bg` 体内不得把 ai_service 传给分析。

    - index=1 是真正生效的那个定义（后者覆盖前者），也就是「写完自动分析」；
    - index=0 是被覆盖的旧定义，一并查，免得修错对象还以为绿了。

    不传 → impl 内部 `if ai_service is None:` 走
    `get_user_ai_service_from_db_by_usage(usage="chapter_analysis")` → 尊重用户预设。
    """
    defs = _top_level_defs(chapters_tree, GENERATION_BG_FN)
    assert len(defs) == 2, (
        f"期望 {GENERATION_BG_FN} 有两个定义（后者覆盖前者），实际 {len(defs)} —— "
        "函数结构变了，请重新确认哪个定义生效"
    )

    node = defs[index]
    calls = _analysis_calls(node)
    assert len(calls) == 1, (
        f"{GENERATION_BG_FN}@{node.lineno} 内期望 1 个分析调用点，实际 {len(calls)}"
    )

    call = calls[0]
    forwarded = [kw for kw in call.keywords if kw.arg == "ai_service"]
    assert not forwarded, (
        f"{CHAPTERS_PY.name}:{call.lineno}（{GENERATION_BG_FN}@{node.lineno} 内）"
        "仍显式传递 ai_service —— 这会让「写完自动分析」用写作模型，"
        "绕过用户在设置页配置的分析专用预设。删掉 ai_service= 实参即可"
        "（impl 内部会走 by_usage(\"chapter_analysis\")）。"
    )


def test_only_one_call_site_forwards_ai_service_and_it_is_the_sequential_analyzer(
    chapters_tree: ast.Module, all_analysis_calls
):
    """4 个调用点里只剩「一键顺序分析」一处显式传 ai_service，且它传的是 None。

    `_run_batch_analysis_in_sequence` 的形参默认 None、真实调用方（一键分析
    端点）也不传 → 传下去等价于不传，行为上仍走 by_usage。这里把它钉住，
    是为了让「谁传了」这件事有意义地只剩一处，而不是散落多处难以审计。
    """
    forwarding = [
        call for call in all_analysis_calls if _forwards_ai_service(call)
    ]
    assert len(forwarding) == 1, (
        "期望恰好 1 个调用点显式传 ai_service，实际 "
        f"{[(c.lineno, _enclosing_def(chapters_tree, c)) for c in forwarding]}"
    )

    call = forwarding[0]
    assert _enclosing_def(chapters_tree, call) == EXPECTED_FORWARDING_OWNER, (
        f"显式传 ai_service 的调用点应在 {EXPECTED_FORWARDING_OWNER}，"
        f"实际在 {_enclosing_def(chapters_tree, call)}（line {call.lineno}）"
    )


def test_impl_resolves_by_usage_when_ai_service_is_absent(chapters_tree: ast.Module):
    """impl 内的按用途取模型逻辑仍在，且 usage 写死 "chapter_analysis"。

    静态用例只保证「调用点没传」；这一条保证「没传之后确实去读了分析预设」。
    否则删掉实参只是把 ai_service 变成 None，分析端会拿到空实现。
    """
    defs = _top_level_defs(chapters_tree, IMPL_FN)
    assert len(defs) == 1, f"期望 {IMPL_FN} 只有一个定义，实际 {len(defs)}"

    impl = defs[0]

    # 定位 `if ai_service is None:` 的守卫
    guards = [
        node
        for node in ast.walk(impl)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "ai_service"
    ]
    assert guards, f"{IMPL_FN} 内找不到 `if ai_service is None:` 守卫"

    # 守卫体内必须调 by_usage，且 usage="chapter_analysis"
    by_usage_names = {
        "get_user_ai_service_from_db_by_usage",
        "get_user_ai_service_from_db",
    }
    found_calls = []
    for guard in guards:
        for sub in ast.walk(guard):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in by_usage_names:
                found_calls.append(sub)

    assert found_calls, (
        f"{IMPL_FN} 的 `if ai_service is None:` 分支里没有调用 "
        "get_user_ai_service_from_db_by_usage —— 分析端拿不到用户预设"
    )

    usage_values = [
        kw.value.value
        for call in found_calls
        for kw in call.keywords
        if kw.arg == "usage" and isinstance(kw.value, ast.Constant)
    ]
    assert "chapter_analysis" in usage_values, (
        "by_usage 的 usage 必须是 \"chapter_analysis\"，实际 "
        f"{usage_values or '（未以字面量给出）'}"
    )


def test_public_wrapper_keeps_optional_ai_service_signature(chapters_tree: ast.Module):
    """`analyze_chapter_background` 的 ai_service 仍必须有 None 默认值。

    本次只改调用点、不改签名：别的调用方（含 app/services 里的一处）仍可能传值，
    把该形参删掉会连带打断它们。
    """
    defs = _top_level_defs(chapters_tree, ANALYSIS_FN)
    assert len(defs) == 1, f"期望 {ANALYSIS_FN} 只有一个定义，实际 {len(defs)}"

    fn = defs[0]
    args = fn.args.args
    defaults = fn.args.defaults
    # defaults 对齐到 args 的尾部
    default_map = {
        arg.arg: default
        for arg, default in zip(args[len(args) - len(defaults):], defaults)
    }

    assert "ai_service" in default_map, (
        f"{ANALYSIS_FN} 的 ai_service 失去了默认值 —— 调用方必须显式传参才能调用"
    )
    assert isinstance(default_map["ai_service"], ast.Constant), (
        f"{ANALYSIS_FN} 的 ai_service 默认值应为字面量 None"
    )
    assert default_map["ai_service"].value is None, (
        f"{ANALYSIS_FN} 的 ai_service 默认值应为 None，"
        f"实际 {default_map['ai_service'].value!r}"
    )


def test_fixed_call_site_carries_rationale_comment(chapters_tree: ast.Module):
    """修复处留了注释说明「为什么故意不传」。

    这条是防「好心回归」的：后人看到调用点少个参数，很容易顺手补回去。
    注释是唯一能在原地劝住他的东西。
    """
    source = CHAPTERS_PY.read_text(encoding="utf-8")
    lines = source.splitlines()

    defs = _top_level_defs(chapters_tree, GENERATION_BG_FN)
    live = defs[1]  # 生效的那个定义

    calls = _analysis_calls(live)
    assert len(calls) == 1
    call = calls[0]

    # 取调用点前若干行做注释检查
    head = lines[max(0, call.lineno - 6) : call.lineno - 1]
    block = "\n".join(head)

    assert "不传 ai_service" in block, (
        f"{CHAPTERS_PY.name}:{call.lineno} 上方的注释未说明「不传 ai_service」的原因。"
        "注释应点名 by_usage(\"chapter_analysis\") 与用户分析预设。"
    )
    assert "chapter_analysis" in block, (
        f"{CHAPTERS_PY.name}:{call.lineno} 上方的注释应点名 usage=\"chapter_analysis\""
    )


# ============================================================
#  2. 行为断言：impl 真的按「传没传」分流
# ============================================================
class _FakeResult:
    """极简 Result：只实现 impl 用到的那几个取值方法。"""

    def __init__(self, scalar: Any = None, items: list | None = None):
        self._scalar = scalar
        self._items = items or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeAnalyzer:
    """替身 PlotAnalyzer：记录拿到的 ai_service，其余返回最小可用结果。"""

    def __init__(self, ai_service):
        self.ai_service = ai_service
        _FAKE_ANALYZER_SEEN["ai_service"] = ai_service

    async def analyze_chapter(self, **kwargs):
        return {"plot_stage": "发展", "scores": {}}

    def extract_memories_from_analysis(self, **kwargs):
        return []

    def generate_analysis_summary(self, *args, **kwargs):
        return ""


class _FakeForeshadowService:
    """伏笔服务替身：impl 会在几处调用它，全部返回空结构。"""

    async def get_planted_foreshadows_for_analysis(self, **kwargs):
        return []

    async def clean_chapter_analysis_foreshadows(self, **kwargs):
        return {"cleaned_count": 0}

    async def auto_plant_pending_foreshadows(self, **kwargs):
        return {"planted_count": 0}

    async def update_foreshadow_status(self, **kwargs):
        return 0


class _FakeSession:
    """分析会话替身：按语句实体分派，返回最小可用对象。

    只需要撑过 impl 的开头（取任务 → 取章节 → 这里就是关键分流点），
    后续步骤一律返回空结构，避免依赖真实库。
    """

    def __init__(self):
        self.task = AnalysisTask(
            id="t1", chapter_id="c1", user_id="u1",
            project_id="p1", status="pending", progress=0,
        )
        self.chapter = Chapter(
            id="c1", project_id="p1", chapter_number=1,
            title="测试章", content="正文内容", word_count=4,
        )
        self.commits = 0
        self.closed = False
        self.added: list = []

    async def execute(self, statement):
        try:
            entity = statement.column_descriptions[0].get("entity")
        except Exception:
            entity = None

        if entity is AnalysisTask:
            return _FakeResult(scalar=self.task)
        if entity is Chapter:
            return _FakeResult(scalar=self.chapter)
        return _FakeResult(scalar=None, items=[])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj=None):
        return None

    async def rollback(self):
        return None

    async def close(self):
        self.closed = True


_FAKE_ANALYZER_SEEN: dict[str, Any] = {}


def _build_session_factory(session: _FakeSession):
    """伪装 async_sessionmaker(engine, class_=..., expire_on_commit=...) 的调用形式。

    注意两层调用：`async_sessionmaker(...)` 先返回一个**会话工厂**，
    impl 再 `db_session = AsyncSessionLocal()` 取会话。所以这里返回的函数
    还必须再返回一个可调用对象，不能直接返回 session 本身。
    """

    def _async_sessionmaker(*args, **kwargs):
        def _session_local(*a, **k):
            return session

        return _session_local

    return _async_sessionmaker


class _FakePresetService:
    """标记用的"分析预设服务"替身；只用来确认「取出来的就是这个」。"""

    def __init__(self, tag: str):
        self.tag = tag


async def _run_impl(monkeypatch, ai_service):
    """在最小替身环境里跑一次 _analyze_chapter_background_impl。

    返回 (impl_return, by_usage_usages, analyzer_ai_service)。
    impl 的返回值不参与断言 —— 后续步骤被替身后它可能提前 return False，
    但**分流决策发生在更早的位置**，标志位此时已经记录完毕。
    """
    session = _FakeSession()
    by_usage_calls: list[str] = []

    async def _fake_by_usage(user_id, db, usage="default"):
        by_usage_calls.append(usage)
        return _FakePresetService("analysis-preset")

    async def _fake_get_db_write_lock(user_id):
        return asyncio.Lock()

    async def _fake_build_characters_info(**kwargs):
        return ""

    async def _fake_terminal_state(*args, **kwargs):
        return True

    async def _fake_get_engine(user_id):
        return object()

    _FAKE_ANALYZER_SEEN.clear()

    monkeypatch.setattr(chapters_mod, "get_user_ai_service_from_db_by_usage", _fake_by_usage)
    monkeypatch.setattr(chapters_mod, "get_db_write_lock", _fake_get_db_write_lock)
    monkeypatch.setattr(
        chapters_mod, "build_characters_info_with_careers", _fake_build_characters_info
    )
    monkeypatch.setattr(
        chapters_mod, "_set_analysis_task_terminal_state", _fake_terminal_state
    )
    monkeypatch.setattr(chapters_mod, "get_engine", _fake_get_engine)
    monkeypatch.setattr(chapters_mod, "PlotAnalyzer", _FakeAnalyzer)
    monkeypatch.setattr(chapters_mod, "foreshadow_service", _FakeForeshadowService())

    # impl 内部是 `from sqlalchemy.ext.asyncio import async_sessionmaker`（本地 import），
    # 所以补丁要打在 sqlalchemy.ext.asyncio 上，而不是 chapters 模块属性上。
    import sqlalchemy.ext.asyncio as sa_async

    monkeypatch.setattr(
        sa_async, "async_sessionmaker", _build_session_factory(session)
    )
    monkeypatch.setattr(chapters_mod, "async_sessionmaker", _build_session_factory(session))

    result = await chapters_mod._analyze_chapter_background_impl(
        chapter_id="c1",
        user_id="u1",
        project_id="p1",
        task_id="t1",
        ai_service=ai_service,
        progress_callback=None,
    )
    return result, by_usage_calls, _FAKE_ANALYZER_SEEN.get("ai_service")


def test_impl_uses_analysis_preset_when_ai_service_is_none(monkeypatch):
    """ai_service=None → 必须调 by_usage，且 usage 恰为 "chapter_analysis"。

    这是「写完自动分析走用户分析预设」的最小可执行证据：
    删掉调用点实参之后，真正决定用哪个模型的就是这条分支。
    """
    result, by_usage_calls, analyzer_service = asyncio.run(_run_impl(monkeypatch, None))

    assert by_usage_calls == ["chapter_analysis"], (
        "ai_service=None 时应恰好按 chapter_analysis 用途取一次模型，实际调用 "
        f"{by_usage_calls}（result={result!r}）"
    )
    assert isinstance(analyzer_service, _FakePresetService), (
        f"取到的分析预设服务应被交给 PlotAnalyzer，实际 {analyzer_service!r}"
    )


def test_impl_honors_explicit_ai_service_and_skips_presets(monkeypatch):
    """显式传入 ai_service → 不得再调 by_usage（调用点自己决定了模型）。

    这条锁住「分流」的另一半：如果 impl 无条件覆盖传入值，
    写作路径传进来的模型会被悄悄换掉，反向也是 bug。
    """
    sentinel = object()
    result, by_usage_calls, analyzer_service = asyncio.run(
        _run_impl(monkeypatch, sentinel)
    )

    assert by_usage_calls == [], (
        "显式传入 ai_service 时不应再去读用户预设，实际 by_usage 调用 "
        f"{by_usage_calls}（result={result!r}）"
    )
    assert analyzer_service is sentinel, (
        f"传入的 ai_service 应原样交给 PlotAnalyzer，实际 {analyzer_service!r}"
    )
