"""Skill 注入长度护栏回归测试（docs/待办.md TODO-011 / 审计 06 G11-1）。

## 在测什么

`skill_loader.load_skills()` 会把 `references/` 下全部 .md 整份拼进 `content`
（`skill_loader.py` 的 `full_content = body + ref_section`）。实测：

```
SKILL_STORY_LONG_WRITE    361,412 字符   ← 报告点名
SKILL_STORY_SHORT_WRITE   180,173 字符   ← 报告遗漏
SKILL_STORY_SHORT_ANALYZE 149,908 字符   ← 报告遗漏
```

中文 1.5–2 token/字 → 22 万–72 万 token，**选它们必然超上下文窗口**。
而 4 处注入点（`api/skills.py` 1 处 + `api/chapters.py` 3 处）原先都是
`skill["content"]` 原样注入，**无任何截断**；唯一的截断先例反而在隔壁
`project_agent_service.py`（`str(...)[:30000]`）。

## 三道防线各有测试

| 防线 | 被测对象 | 用例 |
| :--- | :--- | :--- |
| 截断函数本身 | `truncate_for_injection` | `test_truncate_*` |
| **4 处注入点是否真的走了它** | skills.py / chapters.py 源码（AST） | `test_injection_*` |
| 保存侧长度校验（防未来 1MB skill） | `_validate_skill_metadata` / `_validate_reference_sizes` | `test_validate_*` |

## 为什么注入点用 AST 而不是行为断言

`api/chapters.py` 的三个注入点分别在三个大函数体内（单章生成 / 后台生成 /
批量生成），行为断言需要拉起 DB、项目、上下文构建器才能走到那一行 ——
成本极高且脆弱。而"这一行是否被替换成截断调用"是纯**接线**事实，
AST 足以钉死（沿用 `test_migration_safety.py` 对 `env.py` 的同一套做法）。

## 为什么不是「断言 content ≤ 阈值」

治本方案（references 按需加载）另行排期；本次是**止血**：
`load_skills()` 返回的 `content` 仍可以是 361KB（Skill 管理页要显示全文），
**被限制的是注入 system_prompt 的那份**。所以断言点是 `truncate_for_injection`
的输出，而不是 `load_skills` 的原始 content。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services import skill_loader
from app.services.skill_loader import (
    SKILL_BODY_CHAR_LIMIT,
    SKILL_INJECTION_CHAR_LIMIT,
    SKILL_REFERENCE_CHAR_LIMIT,
    truncate_for_injection,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
SKILLS_API_PY = BACKEND_DIR / "app" / "api" / "skills.py"
CHAPTERS_API_PY = BACKEND_DIR / "app" / "api" / "chapters.py"

# 截断标记必须包含的可辨识片段（决策要求「被裁剪可感知」）。
NOTICE_MARKERS = ("⚠️", "已截断", "参考资料库未完整载入")


def _parse(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(node: ast.AST) -> str | None:
    """取调用表达式的函数名：truncate_for_injection(...) → "truncate_for_injection"。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _assignments_matching(path: Path, target_name: str, call_name: str):
    """找出 `target_name = call_name(...)` 形式的赋值，返回 (lineno, source) 列表。"""
    tree = _parse(path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if _call_name(node.value) != call_name:
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == target_name for t in node.targets
        ):
            continue
        hits.append(node.lineno)
    return hits


# ============================================================
#  1. truncate_for_injection 本体
# ============================================================
def test_default_limit_matches_existing_precedent():
    """阈值必须对齐 project_agent_service.py 的既有先例 30,000。

    两处若各取各的，「同一份 skill 在创作助手里被截、在章节生成里不被截」
    这个行为矛盾就会以新数值的形式复发。
    """
    assert SKILL_INJECTION_CHAR_LIMIT == 30_000


def test_truncate_short_content_is_untouched():
    """未超限必须原样返回 —— 不能顺手加标记、不能 strip、不能改字。"""
    text = "【⚡ Skill 工作流：短篇写作】\n\n正文若干，未超限。"
    assert truncate_for_injection(text) == text
    assert truncate_for_injection(text, limit=10_000) == text


def test_truncate_at_exact_boundary_does_not_truncate():
    """恰好等于 limit：不截断、无标记（边界是「≤」而不是「<」）。"""
    text = "偏" * SKILL_INJECTION_CHAR_LIMIT
    out = truncate_for_injection(text)
    assert out == text
    assert "已截断" not in out


def test_truncate_over_limit_caps_length_and_adds_notice():
    """超长：输出 ≤ limit，且尾部带醒目标记（D5：可感知，不静默降级）。"""
    text = "偏" * (SKILL_INJECTION_CHAR_LIMIT + 1)
    out = truncate_for_injection(text)

    assert len(out) <= SKILL_INJECTION_CHAR_LIMIT, (
        f"截断后仍超阈值：{len(out)} > {SKILL_INJECTION_CHAR_LIMIT}"
    )
    for marker in NOTICE_MARKERS:
        assert marker in out, f"截断标记缺少 {marker!r}，用户无法感知内容被裁剪"

    # 标记必须真的在**尾部**，而不是被前言吞掉
    assert out.rstrip().endswith("】")


def test_truncate_notice_reports_real_original_length():
    """标记里的原始字符数必须是真的原长，否则用户无法判断损失规模。"""
    original = SKILL_INJECTION_CHAR_LIMIT + 12_345
    out = truncate_for_injection("偏" * original)
    assert str(original) in out


def test_truncate_keeps_prefix_of_original_content():
    """保留的是**前** N 字符 —— 工作流正文在 content 头部，必须优先保住。"""
    body = "【工作流指令开始】"
    text = body + "参" * (SKILL_INJECTION_CHAR_LIMIT * 2)
    out = truncate_for_injection(text)
    assert out.startswith(body), "截断把工作流正文也吃掉了"


def test_truncate_tiny_limit_still_marks_truncation():
    """limit 小于标记自身长度时，宁可只留标记，也不能静默返回空串。

    这是形状约束而非长度约束：标记比预算还长时无法同时满足「≤ limit」
    与「可感知」，此时以「可感知」优先。
    """
    out = truncate_for_injection("偏" * 1000, limit=10)
    assert "已截断" in out


def test_truncate_tolerates_none_and_empty():
    """content 缺失时不该炸（注入点拿到的可能是 None）。"""
    assert truncate_for_injection("") == ""
    assert truncate_for_injection(None) == ""  # type: ignore[arg-type]


def test_real_skills_are_actually_over_limit():
    """证据链的可执行版本：仓库里的真实 skill 确实超限，不是空谈。

    若哪天 references 被拆分到全部达标，这个用例会红 —— 那正是治本完成的
    信号，届时应当重写本文件而不是放宽断言。
    """
    skills = skill_loader.load_skills()
    assert skills, "未加载到任何 skill，测试前提不成立"

    over = {
        s["template_key"]: len(s["content"])
        for s in skills
        if len(s["content"]) > SKILL_INJECTION_CHAR_LIMIT
    }
    assert over, "已无超限 skill —— 若为治本完成请更新本测试，勿直接删除"
    # 报告只点名了 LONG_WRITE，这里把实测的另外两个一并锁住
    assert "SKILL_STORY_LONG_WRITE" in over


def test_every_skill_fits_after_truncation():
    """TOKEN-011 验收标准：任意 skill 被选中时，注入的 system_prompt 都不超阈值。"""
    for skill in skill_loader.load_skills():
        injected = truncate_for_injection(skill["content"])
        assert len(injected) <= SKILL_INJECTION_CHAR_LIMIT, (
            f"{skill['template_key']} 截断后仍为 {len(injected)} 字符"
        )


def test_truncation_keeps_workflow_body_intact_for_real_skills():
    """D1-A 的核心补偿性断言：丢的是 references，**工作流正文必须完整**。

    `content = body + ref_section`，而 body 在头部 —— 只要截断是「保留前缀」，
    5458 字符的工作流指令就完整活着，代价仅落在参考资料上（约 93% 被丢）。
    这正是当初选 A 而非「skill 直接失效」的判断依据，必须可回归验证。
    """
    checked = 0
    for skill in skill_loader.load_skills():
        skill_dir = Path(skill_loader.SKILLS_DIR) / skill["name"]
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        body = skill_loader._get_skill_body(skill_md.read_text(encoding="utf-8"))
        injected = truncate_for_injection(skill["content"])
        assert injected.startswith(body), (
            f"{skill['template_key']} 截断后工作流正文被截断/改写 —— "
            "截断必须只吃掉尾部 references"
        )
        checked += 1
    assert checked > 0, "没有任何 SKILL.md 被检查到，断言未真正生效"


# ============================================================
#  2. 4 处注入点是否真的接上了截断（AST 接线断言）
# ============================================================
def test_skills_api_injects_truncated_content():
    """注入点 1：api/skills.py 的 `system_prompt = skill["content"]`。"""
    hits = _assignments_matching(SKILLS_API_PY, "system_prompt", "truncate_for_injection")
    assert hits, (
        "api/skills.py 未找到 `system_prompt = truncate_for_injection(...)` —— "
        "Skill 聊天仍会把 361KB 全量注入"
    )
    assert not _raw_skill_content_assignments(SKILLS_API_PY, "system_prompt"), (
        "api/skills.py 仍有未截断的 skill['content'] 直接赋值"
    )


def test_chapters_api_all_three_sites_inject_truncated_content():
    """注入点 2/3/4：单章生成 / 后台生成 / 批量生成，**三处都要改**。

    只补一处等于没补 —— 漏掉的那条路径照样 361KB 全量注入。
    """
    hits = _assignments_matching(CHAPTERS_API_PY, "skill_content", "truncate_for_injection")
    assert len(hits) == 3, (
        f"期望 3 处 `skill_content = truncate_for_injection(...)`，实际 {len(hits)} 处 "
        f"（行号 {hits}）—— 单章/后台/批量三条路径必须一致"
    )
    assert not _raw_skill_content_assignments(CHAPTERS_API_PY, "skill_content"), (
        "chapters.py 仍有未截断的 skill['content'] 直接赋值"
    )


def _raw_skill_content_assignments(path: Path, target_name: str):
    """找出 `target_name = <something>["content"]` 形式的裸注入。"""
    tree = _parse(path)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == target_name for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant):
            if value.slice.value == "content":
                hits.append(node.lineno)
    return hits


def test_truncate_is_imported_at_the_injection_sites():
    """注入点必须从 skill_loader 导入该函数（而不是各写一遍切片）。"""
    for path in (SKILLS_API_PY, CHAPTERS_API_PY):
        tree = _parse(path)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.skill_loader":
                imported |= {alias.name for alias in node.names}
        assert "truncate_for_injection" in imported, (
            f"{path.name} 未从 app.services.skill_loader 导入 truncate_for_injection"
        )


# ============================================================
#  3. 保存侧长度校验（S9：防未来再塞进 1MB 的 skill）
# ============================================================
def _valid_kwargs(**overrides):
    kwargs = dict(
        name="demo-skill",
        display_name="演示 Skill",
        category="Skill",
        description="用于测试的 Skill 描述。",
        triggers=["/demo-skill"],
        body="正文",
    )
    kwargs.update(overrides)
    return kwargs


def test_validate_metadata_accepts_normal_body():
    """正常长度必须放行 —— 校验不能变成一刀切拦死所有写入。"""
    skill_loader._validate_skill_metadata(**_valid_kwargs())


def test_validate_metadata_rejects_overlong_body():
    """body 超上限必须拒绝，并给出人话错误（含实际长度与上限）。"""
    body = "偏" * (SKILL_BODY_CHAR_LIMIT + 1)
    with pytest.raises(ValueError) as exc:
        skill_loader._validate_skill_metadata(**_valid_kwargs(body=body))
    msg = str(exc.value)
    assert str(len(body)) in msg
    assert str(SKILL_BODY_CHAR_LIMIT) in msg


def test_validate_metadata_accepts_body_at_limit():
    """边界：恰好等于上限要放行（约束是「≤」）。"""
    skill_loader._validate_skill_metadata(
        **_valid_kwargs(body="偏" * SKILL_BODY_CHAR_LIMIT)
    )


def test_validate_references_rejects_overlong_single_file():
    """单个 reference 超上限必须拒绝，错误里要点名是哪个文件。"""
    refs = {"style-modules": "偏" * (SKILL_REFERENCE_CHAR_LIMIT + 1)}
    with pytest.raises(ValueError) as exc:
        skill_loader._validate_reference_sizes(refs)
    msg = str(exc.value)
    assert "style-modules" in msg
    assert str(SKILL_REFERENCE_CHAR_LIMIT) in msg


def test_validate_references_allows_many_files_under_limit():
    """每份都在限内时放行 —— 上限是**单份**，不是总量（总量由注入截断兜住）。"""
    refs = {f"ref-{i}": "偏" * 1000 for i in range(20)}
    skill_loader._validate_reference_sizes(refs)
    skill_loader._validate_reference_sizes(None)
    skill_loader._validate_reference_sizes({})


def test_create_skill_files_rejects_overlong_body(tmp_path, monkeypatch):
    """端到端闸门：create_skill_files 也走同一校验，且**拒绝时不落盘**。"""
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        skill_loader.create_skill_files(
            name="huge-skill",
            description="超大 Skill。",
            body="偏" * (SKILL_BODY_CHAR_LIMIT + 1),
        )

    assert list(tmp_path.iterdir()) == [], "校验失败时不应创建任何目录/文件"


def test_create_skill_files_rejects_overlong_reference(tmp_path, monkeypatch):
    """同上，针对 references 侧；同样要求拒绝前不落盘。"""
    monkeypatch.setattr(skill_loader, "SKILLS_DIR", str(tmp_path))

    with pytest.raises(ValueError) as exc:
        skill_loader.create_skill_files(
            name="ref-heavy-skill",
            description="参考资料超大的 Skill。",
            body="正文",
            references={"big-ref": "偏" * (SKILL_REFERENCE_CHAR_LIMIT + 1)},
        )
    assert "big-ref" in str(exc.value)

    assert list(tmp_path.iterdir()) == [], "校验失败时不应创建任何目录/文件"
