"""迁移安全护栏回归测试（docs/待办.md TODO-010 / 审计 01 P0-37）。

## 在测什么

`source_tasks` / `download_jobs` 是**副轨表**：定义在 `app/api/*.py`、由
lifespan `create(checkfirst=True)` 建表，不在 `app/models/` 里，因此
`alembic/*/env.py` 的 `target_metadata` 看不见它们。autogenerate 以 metadata
为准算差 → 判成「库里多出来的表」→ 生成并执行 `op.drop_table` → 删库。

三道防线各有测试：

| 防线 | 被测对象 | 用例 |
| :--- | :--- | :--- |
| `include_object` 谓词 | `app.migration_safety.include_object` | `test_include_object_*` |
| **env.py 是否真的挂上了钩子**（4 个 configure 调用点） | `alembic/{sqlite,postgres}/env.py` 源码 | `test_both_env_py_wire_...` |
| autogenerate 实际 diff 结果 | `alembic.autogenerate.compare_metadata` | `test_compare_metadata_*` |
| `auto` 子命令的 drop_table 闸门 | `find_drop_table_targets` 等 | `test_find_drop_table_*` |

## 为什么不 import env.py

`alembic/*/env.py` 顶层**直接执行** `run_migrations_offline()` /
`run_migrations_online()` —— import 它等于跑一次迁移。所以它的「有没有挂钩子」
只能读源码（AST）验证，**不能** import。这也正是把 `include_object` 函数体
放到共享模块 `app.migration_safety.py` 的原因。

## 为什么临时库是「最小库」

沿用 `test_download_manager.py` 的既有做法：库建在 `tmp_path` 下，
**绝不碰真实库**（`runtime/data/wenshu.db`）。这里甚至不需要 metadata 全量，
只要「metadata 有主轨表、库里有主轨表 + 两张副轨表」这个最小差集形态，
就足以复现并验证本缺陷。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from app.migration_safety import (
    SIDE_TRACK_TABLES,
    collect_drop_table_targets,
    find_drop_table_targets,
    include_object,
    new_migration_files,
    snapshot_migration_files,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PY_FILES = [
    BACKEND_DIR / "alembic" / "sqlite" / "env.py",
    BACKEND_DIR / "alembic" / "postgres" / "env.py",
]


# ============================================================
#  夹具：最小副轨库
# ============================================================
SIDE_TRACK_DDL = {
    "source_tasks": (
        "CREATE TABLE source_tasks ("
        "  id VARCHAR(36) PRIMARY KEY,"
        "  user_id VARCHAR(100) NOT NULL,"
        "  book_id VARCHAR(32) NOT NULL,"
        "  status VARCHAR(20) NOT NULL DEFAULT 'pending'"
        ")"
    ),
    "download_jobs": (
        "CREATE TABLE download_jobs ("
        "  id VARCHAR(36) PRIMARY KEY,"
        "  user_id VARCHAR(100) NOT NULL,"
        "  book_id VARCHAR(32) NOT NULL,"
        "  state VARCHAR(20) NOT NULL DEFAULT 'queued'"
        ")"
    ),
}

# 主轨对照表：进 metadata，库里也有 → 任何一侧都不该产生 diff 操作。
MAIN_TABLE_DDL = (
    "CREATE TABLE projects ("
    "  id VARCHAR(36) PRIMARY KEY,"
    "  name VARCHAR(200) NOT NULL"
    ")"
)

# 干扰项：库里多出来、但**不在**副轨名单 → 钩子必须放行，
# 用它证明过滤是**定点**的而不是一刀切。
OTHER_STRAY_DDL = (
    "CREATE TABLE legacy_scratch (id INTEGER PRIMARY KEY, note TEXT)"
)


def _metadata_with_main_table() -> MetaData:
    """构造一份「只含主轨表」的 metadata，形态与 env.py 的 target_metadata 同构。

    刻意不用 `app.database.Base.metadata`：那是全局单例，别的测试 import
    `app.api.ext_download` 后就会把 `download_jobs` 注册进去（declarative
    Base 在类定义时即登记），会让「无钩子必须产出 remove_table」这个对照
    断言随测试顺序飘。这里用局部 MetaData 保证确定性 —— 钳住的正是
    env.py 的关键事实：**副轨表不在 metadata 里**。
    """
    md = MetaData()
    Table(
        "projects", md,
        Column("id", String(36), primary_key=True),
        Column("name", String(200), nullable=False),
    )
    return md


@pytest.fixture()
def side_track_db(tmp_path):
    """建一个含「主轨表 + 两张副轨表 + 一个干扰表」的最小 SQLite 库。"""
    db_path = tmp_path / "side_track.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        conn.exec_driver_sql(MAIN_TABLE_DDL)
        for ddl in SIDE_TRACK_DDL.values():
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(OTHER_STRAY_DDL)
    yield engine
    engine.dispose()


def _run_compare_metadata(engine, metadata, include_object_hook=None):
    """跑一次 alembic 的 compare_metadata。

    这是 autogenerate 的核心比较函数：`alembic revision --autogenerate`
    内部同样走它。`include_object` 通过 `MigrationContext.configure(opts=...)`
    传入 —— 与 env.py 里 `context.configure(..., include_object=...)` 等价
    （EnvironmentContext.configure 最终就是把它塞进 opts）。
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    opts: dict = {"compare_type": True, "compare_server_default": True}
    if include_object_hook is not None:
        opts["include_object"] = include_object_hook

    with engine.connect() as conn:
        ctx = MigrationContext.configure(connection=conn, opts=opts)
        return compare_metadata(ctx, metadata)


def _removed_tables(diff) -> set:
    """从 diff 里抽出被 `remove_table` 的表名集合。"""
    return {
        getattr(op[1], "name", op[1])
        for op in diff
        if op[0] == "remove_table"
    }


# ============================================================
#  1. include_object 谓词
# ============================================================
def test_include_object_blocks_side_track_tables():
    """副轨表必须返回 False（被排除），且不区分反射侧/元数据侧。"""
    for table in sorted(SIDE_TRACK_TABLES):
        assert include_object(None, table, "table", True, None) is False, (
            f"{table} 来自库反射、metadata 里没有 → 必须被排除，否则会 drop"
        )
        assert include_object(None, table, "table", False, None) is False


def test_include_object_passes_everything_else():
    """非副轨对象必须放行 —— 钩子不能变成一刀切。"""
    # 普通表
    assert include_object(None, "projects", "table", True, None) is True
    assert include_object(None, "legacy_scratch", "table", True, None) is True
    # 副轨表的索引/列：不按表名过滤（表级过滤已连带跳过其子对象）
    assert include_object(None, "ix_source_tasks_user_id", "index", True, None) is True
    assert include_object(None, "status", "column", True, None) is True
    # 无名字对象不应炸
    assert include_object(None, None, "table", True, None) is True


def test_side_track_table_list_is_what_todo_010_names():
    """名单必须恰好是 TODO-010 点名的两张，防止误删/误加。"""
    assert SIDE_TRACK_TABLES == frozenset({"source_tasks", "download_jobs"})


# ============================================================
#  2. env.py 是否真的把钩子挂到了每一个 configure 调用点
# ============================================================
def _parse_env_py(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _configure_calls(tree):
    """找出所有 `context.configure(...)` 调用节点。"""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "configure":
            calls.append(node)
    return calls


def _keyword_name(kw: ast.keyword) -> str | None:
    """取 keyword 的值若是裸名字则返回其 id，否则 None。"""
    if isinstance(kw.value, ast.Name):
        return kw.value.id
    return None


def test_both_env_py_files_exist():
    for path in ENV_PY_FILES:
        assert path.is_file(), f"缺少 {path}"


@pytest.mark.parametrize("env_py", ENV_PY_FILES, ids=lambda p: p.parent.name)
def test_env_py_wires_include_object_into_every_configure_call(env_py):
    """每个 `context.configure(...)` 都必须带 `include_object=include_object`。

    sqlite 版两处（`run_migrations_offline` / `do_run_migrations`），
    postgres 版同样两处。**只补一处等于没补** —— 漏掉的那条路径照样删库，
    所以这里逐个数、不取「至少一处」。
    """
    tree = _parse_env_py(env_py)
    calls = _configure_calls(tree)
    assert len(calls) == 2, (
        f"{env_py.name} 期望 2 个 context.configure 调用点，实际 {len(calls)} —— "
        "新增调用点时请一并挂上 include_object"
    )

    for call in calls:
        wired = [
            kw for kw in call.keywords
            if kw.arg == "include_object" and _keyword_name(kw) == "include_object"
        ]
        assert wired, (
            f"{env_py}:{call.lineno} 的 context.configure 没有 "
            "include_object=include_object —— 该路径仍会生成 drop_table"
        )


@pytest.mark.parametrize("env_py", ENV_PY_FILES, ids=lambda p: p.parent.name)
def test_env_py_imports_include_object_from_shared_module(env_py):
    """钩子必须来自共享模块，而不是就地定义。

    就地定义的话测试无法安全取用（import env.py 即触发迁移），
    而且两侧容易各自漂移。这里是共享位置约束的守门断言。
    """
    tree = _parse_env_py(env_py)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.migration_safety":
            imported |= {alias.name for alias in node.names}
    assert "include_object" in imported, (
        f"{env_py.name} 未从 app.migration_safety 导入 include_object"
    )


# ============================================================
#  3. compare_metadata 端到端 diff（核心用例）
# ============================================================
def test_compare_metadata_control_without_hook_produces_remove_table(side_track_db):
    """对照组：**不挂**钩子时必须复现出 remove_table。

    没有这个用例，下面的「挂钩子后没有 remove_table」可能是假绿
    （比如库根本没建起来、或 compare_metadata 压根没跑）。它同时是
    TODO-010 证据链的可执行版本。
    """
    metadata = _metadata_with_main_table()
    diff = _run_compare_metadata(side_track_db, metadata)

    removed = _removed_tables(diff)
    assert "source_tasks" in removed, f"未复现 source_tasks 的 remove_table；diff={diff}"
    assert "download_jobs" in removed, f"未复现 download_jobs 的 remove_table；diff={diff}"
    # 干扰表本来就不在 metadata 里，理应也被判为「多余」
    assert "legacy_scratch" in removed


def test_compare_metadata_with_hook_has_no_remove_table_for_side_track(side_track_db):
    """挂上钩子后，diff 里**不得**出现这两张表的 remove_table。"""
    metadata = _metadata_with_main_table()
    diff = _run_compare_metadata(side_track_db, metadata, include_object)
    removed = _removed_tables(diff)

    assert "source_tasks" not in removed, f"source_tasks 仍被判删除；diff={diff}"
    assert "download_jobs" not in removed, f"download_jobs 仍被判删除；diff={diff}"


def test_hook_does_not_silence_unrelated_stray_table(side_track_db):
    """钩子是定点的：非副轨的多余表仍会被正常报出，不被顺手掩盖。"""
    metadata = _metadata_with_main_table()
    diff = _run_compare_metadata(side_track_db, metadata, include_object)
    removed = _removed_tables(diff)

    assert "legacy_scratch" in removed, (
        "钩子把非副轨表也一起静音了 —— 过滤范围过宽，会掩盖真正的 schema 漂移"
    )


def test_main_track_table_not_flagged_as_removed(side_track_db):
    """主轨表在 metadata 与库里都有 → 不该出现 remove_table。"""
    metadata = _metadata_with_main_table()
    diff = _run_compare_metadata(side_track_db, metadata, include_object)
    assert "projects" not in _removed_tables(diff)


# ============================================================
#  4. auto 子命令的 drop_table 静态扫描
# ============================================================
def test_find_drop_table_targets_detects_single_quotes(tmp_path):
    f = tmp_path / "mig1.py"
    f.write_text(
        "from alembic import op\n\n\n"
        "def upgrade():\n"
        "    op.drop_table('source_tasks')\n",
        encoding="utf-8",
    )
    assert find_drop_table_targets(f) == ["source_tasks"]


def test_find_drop_table_targets_detects_double_quotes_and_kwargs(tmp_path):
    """多行 + 带 kwargs 的写法也要认出：op.drop_table(\"x\", schema=\"public\")。"""
    f = tmp_path / "mig2.py"
    f.write_text(
        "from alembic import op\n\n\n"
        "def upgrade():\n"
        "    op.drop_table(\n"
        '        "agent_messages",\n'
        '        schema="public",\n'
        "    )\n"
        "    op.drop_table('download_jobs')\n",
        encoding="utf-8",
    )
    assert find_drop_table_targets(f) == ["agent_messages", "download_jobs"]


def test_find_drop_table_targets_dedupes_and_keeps_order(tmp_path):
    f = tmp_path / "mig3.py"
    f.write_text(
        "from alembic import op\n\n\n"
        "def upgrade():\n"
        "    op.drop_table('b')\n"
        "    op.drop_table('a')\n"
        "    op.drop_table('b')\n",
        encoding="utf-8",
    )
    assert find_drop_table_targets(f) == ["b", "a"]


def test_find_drop_table_targets_ignores_safe_migration(tmp_path):
    """只有 create_table / add_column 的迁移不应被拦。"""
    f = tmp_path / "mig_safe.py"
    f.write_text(
        "from alembic import op\n"
        "import sqlalchemy as sa\n\n\n"
        "def upgrade():\n"
        "    op.create_table('foo', sa.Column('id', sa.String(36)))\n"
        "    op.add_column('bar', sa.Column('baz', sa.Integer()))\n\n\n"
        "def downgrade():\n"
        "    op.drop_column('bar', 'baz')\n",
        encoding="utf-8",
    )
    assert find_drop_table_targets(f) == []


def test_find_drop_table_ignores_comment_mentioning_drop_table(tmp_path):
    """注释里的 'drop_table' 走不到 op.drop_table() 调用，不该误报。"""
    f = tmp_path / "mig_comment.py"
    f.write_text(
        "# 注意：本文件绝不能出现 drop_table\n"
        "def upgrade():\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert find_drop_table_targets(f) == []


def test_collect_drop_table_targets_only_reports_hits(tmp_path):
    safe = tmp_path / "safe.py"
    safe.write_text("def upgrade():\n    pass\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.drop_table('source_tasks')\n",
        encoding="utf-8",
    )

    hits = collect_drop_table_targets([safe, bad])
    assert list(hits) == [str(bad)]
    assert hits[str(bad)] == ["source_tasks"]


# ============================================================
#  5. 「只扫本次新增文件」的快照逻辑
# ============================================================
def test_snapshot_and_new_migration_files(tmp_path):
    """autogenerate 前后对比，只挑出新增的迁移文件。

    为什么不能扫全目录：历史迁移文件的 `downgrade()` 里本来就全是
    `op.drop_table`（如 `20251226_..._初始化sqlite数据库.py`），
    全目录扫描会**永远**命中、闸门永远误拦。
    """
    versions = tmp_path / "versions"
    versions.mkdir()

    old = versions / "20250101_0000_aaaa_old.py"
    old.write_text("def upgrade():\n    pass\n", encoding="utf-8")
    # __init__.py 之类不该进快照
    (versions / "__init__.py").write_text("", encoding="utf-8")
    # 非 .py 也不该进
    (versions / "notes.txt").write_text("x", encoding="utf-8")

    before = snapshot_migration_files([versions])
    assert str(old.resolve()) in before
    assert len(before) == 1

    new = versions / "20260101_0000_bbbb_new.py"
    new.write_text(
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.drop_table('source_tasks')\n",
        encoding="utf-8",
    )

    created = new_migration_files([versions], before)
    assert [p.name for p in created] == [new.name]

    # 新增文件确实能被扫出 drop_table —— 闸门闭合
    assert collect_drop_table_targets(created) == {str(new.resolve()): ["source_tasks"]}


def test_snapshot_tolerates_missing_dir(tmp_path):
    """目录不存在时返回空集而不是抛错（postgres 侧可能没有 versions 目录）。"""
    assert snapshot_migration_files([tmp_path / "nope"]) == set()
    assert new_migration_files([tmp_path / "nope"], set()) == []


# ============================================================
#  6. scripts/migrate.py 确实把闸门接进了 auto 流程
# ============================================================
def test_migrate_py_auto_flow_uses_safety_gate():
    """AST 级检查：`auto_migrate` 里必须依次出现备份、扫描、确认闸门。

    这些是行为断言不出来的接线点 —— 函数都测过了，但可能压根没被调用。
    """
    migrate_py = BACKEND_DIR / "scripts" / "migrate.py"
    tree = ast.parse(migrate_py.read_text(encoding="utf-8"), filename=str(migrate_py))

    auto_fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "auto_migrate"),
        None,
    )
    assert auto_fn is not None, "scripts/migrate.py 里找不到 auto_migrate"

    called: set = set()
    for node in ast.walk(auto_fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)

    for required in (
        "resolve_alembic_config",
        "snapshot_migration_files",
        "new_migration_files",
        "backup_before_migrate",
        "collect_drop_table_targets",
        "confirm_dangerous_migration",
        "upgrade_database",
    ):
        assert required in called, f"auto_migrate 未调用 {required}"

    # 生成后**先**扫、再决定是否 upgrade：确认闸门必须早于 upgrade
    src = ast.get_source_segment(
        migrate_py.read_text(encoding="utf-8"), auto_fn
    ) or ""
    assert src.index("confirm_dangerous_migration") < src.index("upgrade_database("), (
        "确认闸门必须在 upgrade 之前 —— 否则仍是「生成即执行」"
    )


def test_migrate_py_non_interactive_defaults_to_abort():
    """无 tty 时必须返回 False（默认中止），不能阻塞在 input()。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415 - 需要先补 sys.path

    import io

    class _NotATty(io.StringIO):
        def isatty(self) -> bool:
            return False

    real_stdin = sys.stdin
    sys.stdin = _NotATty("yes\n")
    try:
        ok = migrate.confirm_dangerous_migration(
            {"migrations/xyz_danger.py": ["source_tasks"]}
        )
    finally:
        sys.stdin = real_stdin

    assert ok is False, "非交互环境下必须默认中止，绝不能因为管道里恰好有 yes 就放行"


def test_migrate_py_interactive_yes_passes():
    """交互环境下明确输入 yes 才放行；其它输入一律中止。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    import io

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    real_stdin = sys.stdin
    try:
        for answer, expected in (("yes\n", True), ("y\n", True),
                                 ("no\n", False), ("\n", False), ("随便\n", False)):
            sys.stdin = _Tty(answer)
            got = migrate.confirm_dangerous_migration({"m.py": ["download_jobs"]})
            assert got is expected, f"输入 {answer!r} 期望 {expected}，实际 {got}"
    finally:
        sys.stdin = real_stdin


def test_migrate_py_resolves_config_by_dialect(monkeypatch):
    """配置文件按 DATABASE_URL 方言选择，覆盖两侧 alembic ini。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    monkeypatch.delenv(migrate.ALEMBIC_CONFIG_ENV, raising=False)

    monkeypatch.setattr(migrate.settings, "database_url", "sqlite+aiosqlite:///tmp/x.db")
    assert migrate.resolve_alembic_config().name == "alembic-sqlite.ini"

    monkeypatch.setattr(
        migrate.settings, "database_url",
        "postgresql+asyncpg://u:p@localhost:5432/db",
    )
    assert migrate.resolve_alembic_config().name == "alembic-postgres.ini"


def test_migrate_py_sqlite_backup_uses_vacuum_into(tmp_path, monkeypatch):
    """SQLite 备份必须用 VACUUM INTO 产出可打开的库文件。

    WAL 模式下裸复制 .db 会丢最新事务，所以这里顺带验证：
    数据在 WAL 未 checkpoint 时也能进备份。
    """
    import sqlite3

    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    db_path = tmp_path / "live.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('刚从 WAL 写进去的')")
        conn.commit()

        out_dir = tmp_path / "runtime_data"
        monkeypatch.setattr(migrate, "BACKUP_DIR", out_dir)
        monkeypatch.setattr(
            migrate.settings, "database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}"
        )

        ok = migrate.backup_before_migrate()
        assert ok is True

        backups = list(out_dir.glob(f"{migrate.BACKUP_PREFIX}*.db"))
        assert len(backups) == 1, f"未产出备份文件: {list(out_dir.iterdir())}"

        # 备份必须是可打开且含最新事务的完整库
        bconn = sqlite3.connect(backups[0])
        try:
            rows = bconn.execute("SELECT v FROM t").fetchall()
        finally:
            bconn.close()
        assert rows == [("刚从 WAL 写进去的",)]
    finally:
        conn.close()


def test_migrate_py_sqlite_backup_missing_db_is_noop(tmp_path, monkeypatch):
    """库文件还不存在（首次部署）时不该中止迁移。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    monkeypatch.setattr(migrate, "BACKUP_DIR", tmp_path / "runtime_data")
    monkeypatch.setattr(
        migrate.settings, "database_url",
        f"sqlite+aiosqlite:///{(tmp_path / 'absent.db').as_posix()}",
    )
    assert migrate.backup_before_migrate() is True


def test_migrate_py_backup_dir_defaults_to_runtime_data(monkeypatch):
    """未设 MUMU_DATA_DIR 时，备份目录默认是 <repo>/runtime/data。

    仓库根不是 backend/ 而是它的父目录 —— 本机 `.env` 把数据放在
    `wenshu/runtime/data`，备份必须跟着落在那个**代码目录之外**的位置。
    """
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    monkeypatch.delenv("MUMU_DATA_DIR", raising=False)
    resolved = migrate._resolve_backup_dir()
    assert resolved == (BACKEND_DIR.parent / "runtime" / "data").resolve(), resolved

    monkeypatch.setenv("MUMU_DATA_DIR", str(tmp_data := (BACKEND_DIR / ".." / "runtime" / "other")))
    assert migrate._resolve_backup_dir() == tmp_data.resolve()


def test_resolve_versions_dirs_follows_script_location(tmp_path, monkeypatch):
    """script_location 相对 project_root 解析，其下才是 versions。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    fake_root = tmp_path / "proj"
    versions = fake_root / "alembic" / "sqlite" / "versions"
    versions.mkdir(parents=True)

    ini = fake_root / "alembic-sqlite.ini"
    ini.write_text(
        "[alembic]\nscript_location = alembic/sqlite\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(migrate, "project_root", fake_root)
    dirs = migrate.resolve_versions_dirs(ini)
    assert [d.resolve() for d in dirs] == [versions.resolve()]


def test_resolve_versions_dirs_reads_version_locations(tmp_path, monkeypatch):
    """配了 version_locations 时也要纳入扫描面。

    漏掉这里的后果是闸门**静默失效**：新迁移写到 version_locations，
    而「本次新增文件」算成空集 → 不问、不拦，直接 upgrade。
    这里按 alembic 的 legacy 语义（未设 version_path_separator → 空格/逗号切分）。
    """
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    fake_root = tmp_path / "proj"
    (fake_root / "alembic" / "sqlite" / "versions").mkdir(parents=True)
    extra = fake_root / "extra_versions"
    extra.mkdir()

    ini = fake_root / "alembic-sqlite.ini"
    ini.write_text(
        "[alembic]\n"
        "script_location = alembic/sqlite\n"
        "version_locations = extra_versions\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(migrate, "project_root", fake_root)
    dirs = {d.resolve() for d in migrate.resolve_versions_dirs(ini)}
    assert extra.resolve() in dirs
    assert (fake_root / "alembic" / "sqlite" / "versions").resolve() in dirs


def test_resolve_versions_dirs_falls_back_to_glob(tmp_path, monkeypatch):
    """没有可用配置时退回扫描 alembic/*/versions。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    fake_root = tmp_path / "proj"
    for side in ("sqlite", "postgres"):
        (fake_root / "alembic" / side / "versions").mkdir(parents=True)

    monkeypatch.setattr(migrate, "project_root", fake_root)
    # versions 目录的父目录即侧名（.../alembic/sqlite/versions → sqlite）
    dirs = {d.parent.name for d in migrate.resolve_versions_dirs(None)}
    assert dirs == {"sqlite", "postgres"}, (
        f"回退扫描应覆盖 alembic/*/versions，实际 {dirs}"
    )


def test_migrate_py_postgres_backup_tolerates_missing_pg_dump(monkeypatch):
    """pg_dump 不可用时只警告并继续 —— 按 TODO-010 决策，不因此中断。"""
    scripts_dir = BACKEND_DIR / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import migrate  # noqa: PLC0415

    monkeypatch.setattr(
        migrate.settings, "database_url",
        "postgresql+asyncpg://u:p@localhost:5432/db",
    )
    monkeypatch.setattr(migrate.shutil, "which", lambda _name: None)

    assert migrate.backup_before_migrate() is True
