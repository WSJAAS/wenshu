"""迁移安全护栏 —— 副轨表保护与迁移文件风险扫描。

## 为什么需要这个模块

本工程的表分两条轨演进：

| 轨 | 建表方式 | 是否进 Alembic metadata |
| :--- | :--- | :--- |
| 主轨 | `app/models/` + Alembic 迁移 | ✅ |
| **副轨** | **API 模块内定义 + lifespan `create(checkfirst=True)`** | ❌ |

Alembic 的 autogenerate 是「以 metadata 为准算差」的：库里有、metadata 里没有
= 「这个表不该存在」→ 生成 `op.drop_table` 并执行 → **删掉用户任务数据**。
`source_tasks` / `download_jobs` 正是两张副轨表（定义在 `app/api/source.py`、
`app/api/ext_download.py`），而 `alembic/{sqlite,postgres}/env.py` 的
`target_metadata` 是显式白名单式导入 `app.models`，天然看不见它们。

详见 `docs/待办.md` TODO-010（审计 01 P0-37，数据毁灭级）。

## 两条防线

1. `include_object` 钩子 —— 在 autogenerate 比较阶段就跳过副轨表，
   两侧 `env.py` 的**每一个** `context.configure(...)` 调用点都要挂上。
2. `find_drop_table_targets()` —— 迁移文件生成后、执行前的静态扫描，
   作为最后一道闸门（`scripts/migrate.py auto` 用它拦下危险迁移）。

新增副轨表时，**必须**在这里的 `SIDE_TRACK_TABLES` 补一项；
`backend/tests/test_migration_safety.py` 有断言盯着这份名单。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Sequence

# ============================================================
#  副轨表名单
# ============================================================
# 由应用 lifespan 动态建表（app/main.py），不走 alembic 迁移。
# autogenerate 时必须跳过，否则会生成 drop_table 毁掉用户数据
# （见 docs/待办.md TODO-010）。
SIDE_TRACK_TABLES: frozenset[str] = frozenset({
    "source_tasks",    # app/api/source.py:53   SourceTask
    "download_jobs",   # app/api/ext_download.py:56   DownloadJob
})


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Alembic `include_object` 钩子：把副轨表排除在 autogenerate diff 之外。

    返回值语义（Alembic 约定）：``True`` 表示纳入比较，``False`` 表示跳过。

    只拦 ``type_ == "table"`` 且表名命中副轨名单的项。表被跳过后，
    Alembic 不会再走该表的列/索引/约束比较，因此既不会生成 `drop_table`，
    也不会因为副轨表自带的后加列（`_ensure_*_columns` 补的那些）而生成
    列级 diff —— 这是本钩子的第二个隐性收益。

    :param obj: 被比较的 schema 对象（Table / Index / Constraint / …）
    :param name: 对象名，table 类型时即表名
    :param type_: 对象类型，Alembic 传 ``"table"`` / ``"column"`` / ``"index"`` 等
    :param reflected: 该对象是否来自数据库反射（True 即「库里多出来的」，
        也就是可能被 drop 的那一侧）
    :param compare_to: 与之比较的另一侧对象（metadata 项或反射项），可能为 None
    """
    if type_ == "table" and name in SIDE_TRACK_TABLES:
        return False
    return True


# ============================================================
#  迁移文件风险扫描（生成后、执行前的闸门）
# ============================================================
# 匹配 op.drop_table('x') / op.drop_table("x")，容忍多行与 kwargs，
# 例如 op.drop_table("agent_messages", schema="public")。
_DROP_TABLE_RE = re.compile(
    r"""op\s*\.\s*drop_table\s*\(\s*['"](?P<name>[^'"]+)['"]""",
    re.VERBOSE,
)

# 逐行粗筛：先看有没有 drop_table，再解析具体表名。命中才进正则。
_DROP_TABLE_HINT = "drop_table"


def find_drop_table_targets(path: str | Path) -> list[str]:
    """扫描一个迁移文件，返回其中 `op.drop_table` 的目标表名（去重、保序）。

    只读文件文本、不导入模块，因此对语法错误或依赖缺失的迁移文件同样安全。
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    if _DROP_TABLE_HINT not in text:
        return []

    seen: dict[str, None] = {}
    for match in _DROP_TABLE_RE.finditer(text):
        seen.setdefault(match.group("name"), None)
    return list(seen)


def collect_drop_table_targets(paths: Iterable[str | Path]) -> dict[str, list[str]]:
    """批量扫描，返回 ``{迁移文件路径: [表名, ...]}``，只保留有 drop_table 的文件。"""
    found: dict[str, list[str]] = {}
    for p in paths:
        targets = find_drop_table_targets(p)
        if targets:
            found[str(p)] = targets
    return found


def new_migration_files(versions_dirs: Sequence[str | Path], before: set[str]) -> list[Path]:
    """在一次 autogenerate 前后对比 versions 目录，找出**新增**的迁移文件。

    :param versions_dirs: 要扫描的 versions 目录（通常每侧一个）
    :param before: 生成前用 `snapshot_migration_files()` 拍的快照
    """
    after = snapshot_migration_files(versions_dirs)
    created = sorted(after - before)
    return [Path(p) for p in created]


def snapshot_migration_files(versions_dirs: Sequence[str | Path]) -> set[str]:
    """给 versions 目录里的所有 .py 迁移文件拍快照（绝对路径字符串集合）。"""
    snapshot: set[str] = set()
    for d in versions_dirs:
        directory = Path(d)
        if not directory.is_dir():
            continue
        for p in directory.glob("*.py"):
            if p.name.startswith("__"):
                continue
            snapshot.add(str(p.resolve()))
    return snapshot
