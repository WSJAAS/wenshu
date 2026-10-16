#!/usr/bin/env python3
"""
数据库自动迁移脚本
用于开发和生产环境的数据库迁移管理

## 安全闸门（见 docs/待办.md TODO-010）

`auto` 子命令曾经是「生成迁移 → 立即 upgrade」一条龙，而本工程存在**副轨表**
（`source_tasks` / `download_jobs` 定义在 API 模块里、由 lifespan 建表，
不进 Alembic metadata）。autogenerate 以 metadata 为准算差，会把它们判成
「不该存在的表」→ 生成并执行 `op.drop_table` → 删掉用户任务数据。

现在的 `auto` 分三道闸：

1. **执行前备份**：SQLite 用 `VACUUM INTO`（库是 WAL 模式，直接复制文件会丢
   最新事务），PostgreSQL 用 `pg_dump`（不可用则警告并继续，不因此中断）。
2. **生成后先扫描、不直接执行**：扫新增迁移文件里的 `op.drop_table`，
   命中就醒目警告、列出表名并**询问确认**；非交互环境（无 tty）默认中止。
3. **只有确认后才 upgrade**。

副轨表名单与 `include_object` 钩子在 `app/migration_safety.py`（共享位置，
两个 `alembic/*/env.py` 都从这里取）。
"""
import configparser
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.config import settings  # noqa: E402
from app.logger import get_logger  # noqa: E402
from app.migration_safety import (  # noqa: E402
    collect_drop_table_targets,
    new_migration_files,
    snapshot_migration_files,
)

logger = get_logger(__name__)

# 显式指定 alembic 配置文件（相对 project_root 或绝对路径）。
# 不设则按 DATABASE_URL 的方言自动挑 alembic-{sqlite,postgres}.ini。
ALEMBIC_CONFIG_ENV = "ALEMBIC_CONFIG"

# 备份文件命名：wenshu_backup_pre_migrate_{timestamp}.db
BACKUP_PREFIX = "wenshu_backup_pre_migrate_"


def _resolve_backup_dir() -> Path:
    """备份落盘目录。

    要求备份**落在代码目录之外**的运行时数据目录，本工程惯例是
    `<repo>/runtime/data`（见 `backend/.env` 的 `MUMU_DATA_DIR`，
    `.gitignore` 也已忽略 `runtime/`）：

      - 设了 `MUMU_DATA_DIR` → 用它（本机即 `runtime\\data`）；
      - 未设 → 显式回退 `<repo>/runtime/data`，而不是 `backend/data`。
        Docker 镜像里跑迁移时，`/app/runtime/data` 才是那个可外挂卷的位置。
    """
    override = os.getenv("MUMU_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (project_root.parent / "runtime" / "data").resolve()


# 备份目录（模块级常量，便于测试替换）
BACKUP_DIR = _resolve_backup_dir()


# ============================================================
#  命令执行
# ============================================================
def _alembic_base_cmd() -> list:
    """alembic 命令前缀。

    用 `sys.executable -m alembic` 而不是裸 `alembic`：脚本可能被 venv 里的
    python 直接调用而 PATH 未激活，裸命令会找不到；模块形式与 README 的
    `..\\.venv\\Scripts\\python.exe -m alembic ...` 用法一致。
    """
    return [sys.executable, "-m", "alembic"]


def run_command(cmd: list, description: str) -> bool:
    """运行命令并返回是否成功"""
    try:
        logger.info(f"🚀 {description}...")
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            logger.info(f"✅ {description}成功")
            if result.stdout:
                print(result.stdout)
            return True
        else:
            logger.error(f"❌ {description}失败")
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return False
    except Exception as e:
        logger.error(f"❌ {description}异常: {e}")
        return False


# ============================================================
#  alembic 配置定位（决定 -c 参数与 versions 目录）
# ============================================================
def resolve_alembic_config() -> Optional[Path]:
    """定位本次要用的 alembic 配置文件。

    顺序：`ALEMBIC_CONFIG` 环境变量 → 按 `DATABASE_URL` 方言挑
    `alembic-sqlite.ini` / `alembic-postgres.ini` → 退回 `alembic.ini`
    （Docker 镜像里就是这个布局：`COPY backend/alembic-postgres.ini ./alembic.ini`）。
    都不存在返回 None，此时不传 `-c`，交给 alembic 自己找。
    """
    explicit = os.getenv(ALEMBIC_CONFIG_ENV)
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = project_root / p
        if p.is_file():
            return p
        logger.warning(f"⚠️ {ALEMBIC_CONFIG_ENV}={explicit} 指向的文件不存在，改按方言自动选择")

    url = (settings.database_url or "").lower()
    if url.startswith("postgres"):
        candidate = project_root / "alembic-postgres.ini"
    elif url.startswith("sqlite"):
        candidate = project_root / "alembic-sqlite.ini"
    else:
        candidate = None

    if candidate is not None and candidate.is_file():
        return candidate

    plain = project_root / "alembic.ini"
    if plain.is_file():
        return plain

    return None


def resolve_versions_dirs(config_path: Optional[Path]) -> list:
    """找出待扫描的 `versions` 目录。

    有配置文件时按其 `script_location`（与 alembic 一致：相对 cwd，即
    project_root）以及可选的 `version_locations` 推导；都没有则回退扫描
    `alembic/*/versions`。

    为什么要读 `version_locations`：配了它之后，新迁移可能被写到那里而不是
    `script_location/versions`。漏读就会让「本次新增了哪些文件」算空集 →
    drop_table 闸门**静默失效**（这正是本子项要防的失败模式）。

    `script_location` 是脚本目录（其下才是 `versions`），而
    `version_locations` 直接指向 versions 目录，两者的解析方式不同。
    """
    script_location_raw = ""
    version_locations_raw = ""
    separator = ""

    if config_path is not None:
        # 注意：`strict` 是 ConfigParser 的**构造参数**，不是 read() 的参数。
        # 误传给 read() 会抛 TypeError，被下面的 except 吞掉后静默退回 glob ——
        # 正是本模块要防的「静默失效」。所以这里容错要窄、日志要响。
        parser = configparser.ConfigParser(strict=False)
        try:
            parser.read(config_path, encoding="utf-8")
            script_location_raw = parser.get("alembic", "script_location", fallback="").strip()
            version_locations_raw = parser.get("alembic", "version_locations", fallback="").strip()
            separator = parser.get("alembic", "version_path_separator", fallback="").strip()
        except Exception as e:  # pragma: no cover - 配置损坏时退回目录扫描
            logger.warning(f"⚠️ 读取 {config_path} 失败（{e}），改为扫描 alembic/*/versions")
            script_location_raw = ""
            version_locations_raw = ""

    # `version_locations` 的分隔符由 `version_path_separator` 决定，取值语义
    # 照抄 alembic（`alembic/script/base.py`，对照本机 1.14.0）：
    #   None（未设）→ legacy：按「逗号/空白」切分
    #   'os'        → os.pathsep（Windows ';'，其它 ':'）
    #   ':' / ';'   → 字面量
    #   'space' / 'newline' → 对应字符
    # 这里不照抄会导致「新迁移写到别处 → 新增文件集合算空 → 闸门静默失效」。
    def _split_version_locations(raw: str) -> list:
        key = separator.strip().lower()
        if key == "":
            parts = re.split(r", *| +", raw)          # legacy：空格/逗号
        elif key == "os":
            parts = raw.split(os.pathsep)
        elif key == "space":
            parts = raw.split(" ")
        elif key == "newline":
            parts = raw.split("\n")
        elif key in (":", ";"):
            parts = raw.split(key)
        else:
            # alembic 会在此抛 ValueError；这里保守回退 legacy 切分并告警，
            # 免得一个拼错的配置项让备份+闸门整体失效。
            logger.warning(f"⚠️ 无法识别的 version_path_separator={separator!r}，按 legacy 空格/逗号切分")
            parts = re.split(r", *| +", raw)
        return [p.strip() for p in parts if p.strip()]

    def _abs(raw: str) -> Path:
        location = Path(raw)
        return location if location.is_absolute() else project_root / location

    candidates: list = []
    if script_location_raw:
        candidates.append(_abs(script_location_raw) / "versions")
    if version_locations_raw:
        for raw in _split_version_locations(version_locations_raw):
            candidates.append(_abs(raw))

    dirs: list = []
    seen: set = set()
    for versions in candidates:
        if not versions.is_dir():
            continue
        key = str(versions.resolve())
        if key not in seen:
            seen.add(key)
            dirs.append(versions)

    if not dirs:
        for versions in sorted((project_root / "alembic").glob("*/versions")):
            if versions.is_dir():
                dirs.append(versions)

    return dirs


def _with_config(cmd: list, config_path: Optional[Path]) -> list:
    """给 alembic 命令补上 -c 参数。"""
    if config_path is None:
        return cmd
    return cmd[:3] + ["-c", str(config_path)] + cmd[3:]


# ============================================================
#  迁移前备份
# ============================================================
def _backup_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sqlite_backup() -> tuple:
    """SQLite 备份：用 `VACUUM INTO` 产出一份一致的库快照。

    为什么不用复制文件：本库是 WAL 模式，最新事务可能只落在 `-wal` 里，
    裸复制 `.db` 会丢掉它们（见 docs/待办.md TODO-010 附带发现）。

    返回 `(ok, message)`。库文件不存在时视作「无数据可备份」的空操作成功。
    """
    from sqlalchemy.engine import make_url

    db_url = make_url(settings.database_url)
    raw_path = db_url.database
    if not raw_path:
        return False, "无法从 DATABASE_URL 解析出 SQLite 库文件路径"

    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    if not db_path.is_file():
        return True, f"库文件不存在（{db_path}），无数据可备份，跳过"

    backup_path = BACKUP_DIR / f"{BACKUP_PREFIX}{_backup_timestamp()}.db"
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    if backup_path.exists():  # 同秒重跑：让路，不覆盖既有备份
        backup_path = backup_path.with_name(
            f"{backup_path.stem}_{os.getpid()}{backup_path.suffix}"
        )

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            # VACUUM INTO 目标必须不存在；参数绑定可用（SQLite >= 3.27）。
            conn.execute("VACUUM INTO ?", (backup_path.as_posix(),))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return False, f"SQLite 备份失败（VACUUM INTO）：{e}"

    size = backup_path.stat().st_size if backup_path.exists() else 0
    return True, f"SQLite 备份完成：{backup_path}（{size} 字节）"


def _postgres_backup() -> tuple:
    """PostgreSQL 备份：调 `pg_dump`。

    `pg_dump` 不可用时按 TODO-010 的决策**只警告不中断** —— 客户端工具是否
    装在应用容器里不由本脚本决定。真正失败（命令返回非 0）才算失败。
    """
    from sqlalchemy.engine import make_url

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        return True, "未找到 pg_dump（客户端工具不可用），跳过备份并继续 —— 请自行确保有外部备份"

    url = make_url(settings.database_url)
    # pg_dump 只认同步驱动：postgresql+asyncpg → postgresql
    sync_url = url.set(drivername="postgresql").render_as_string(hide_password=False)

    backup_path = BACKUP_DIR / f"{BACKUP_PREFIX}{_backup_timestamp()}.dump"
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [pg_dump, "--dbname", sync_url, "--format", "custom",
             "--file", str(backup_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        return False, f"PostgreSQL 备份失败（pg_dump 调用异常）：{e}"

    if result.returncode != 0:
        err = (result.stderr or "").strip() or "无 stderr 输出"
        return False, f"PostgreSQL 备份失败（pg_dump 退出码 {result.returncode}）：{err}"

    size = backup_path.stat().st_size if backup_path.exists() else 0
    return True, f"PostgreSQL 备份完成：{backup_path}（{size} 字节）"


def backup_before_migrate() -> bool:
    """迁移前自动备份。返回 False 表示应中止迁移。"""
    logger.info("─" * 60)
    logger.info("💾 迁移前备份")
    logger.info("─" * 60)

    url = (settings.database_url or "").lower()
    if url.startswith("sqlite"):
        ok, message = _sqlite_backup()
    elif url.startswith("postgres"):
        ok, message = _postgres_backup()
    else:
        logger.error(f"❌ 不认识的数据库方言，无法备份：{settings.database_url}")
        return False

    if not ok:
        logger.error("❌ 备份失败，已中止迁移（不在无备份的情况下继续）")
        logger.error(f"   原因：{message}")
        return False

    logger.info(f"✅ {message}")
    return True


# ============================================================
#  危险迁移确认闸门
# ============================================================
def _is_interactive() -> bool:
    """是否存在可用的交互终端。

    `sys.stdin` 可能是 None（pythonw / 被当作服务拉起）或缺 tty（管道、CI），
    两种情况都必须走「默认中止」而不是阻塞在 input()。
    """
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:  # pragma: no cover - 极端环境下的保护
        return False


def confirm_dangerous_migration(hits: dict) -> bool:
    """生成后、执行前的确认闸门。返回 True 才允许 upgrade。

    `hits` 形如 `{迁移文件路径: [表名, ...]}`。
    """
    print("")
    print("!" * 72)
    print("⚠️  危险迁移：生成的迁移文件里含 op.drop_table（会删除整张表及其数据）")
    print("!" * 72)
    for path, tables in hits.items():
        print(f"  文件: {path}")
        for table in tables:
            print(f"    - 将 DROP TABLE: {table}")
    print("")
    print("  排查提示：若被删的是 source_tasks / download_jobs，说明 env.py 的")
    print("  include_object 保护没生效（它们由 lifespan 建表、不属于 alembic 管辖）。")
    print("  参考 docs/待办.md TODO-010。")
    print("!" * 72)
    print("")

    if not _is_interactive():
        logger.error("❌ 非交互环境（stdin 无 tty），默认**中止**，不执行 upgrade")
        logger.error("   如确需执行：请手动审阅上述迁移文件，确认无误后单独运行")
        logger.error("   `alembic upgrade head`，或删除含 drop_table 的迁移文件后重跑。")
        return False

    logger.warning("❌ 检测到 drop_table，需要人工确认后才继续")
    try:
        answer = input("确认执行这份迁移吗？输入 yes 继续（其它任何输入都会中止）: ")
    except (EOFError, KeyboardInterrupt):
        print("")
        logger.error("❌ 未取得确认（输入中断），已中止迁移")
        return False

    if answer.strip().lower() in ("yes", "y"):
        logger.warning("⚠️ 已获人工确认，继续执行迁移")
        return True

    logger.error("❌ 未确认，已中止迁移（迁移文件已生成但未执行，请自行审阅）")
    return False


# ============================================================
#  子命令实现
# ============================================================
def create_migration(message: str = None, config_path: Optional[Path] = None):
    """创建新的迁移版本"""
    if not message:
        message = input("请输入迁移描述: ").strip()
        if not message:
            message = "auto_migration"

    cmd = _with_config(_alembic_base_cmd() + ["revision", "--autogenerate", "-m", message], config_path)
    return run_command(cmd, f"生成迁移: {message}")


def upgrade_database(revision: str = "head", config_path: Optional[Path] = None):
    """升级数据库到指定版本"""
    cmd = _with_config(_alembic_base_cmd() + ["upgrade", revision], config_path)
    return run_command(cmd, f"升级数据库到: {revision}")


def downgrade_database(revision: str = "-1", config_path: Optional[Path] = None):
    """降级数据库到指定版本"""
    cmd = _with_config(_alembic_base_cmd() + ["downgrade", revision], config_path)
    return run_command(cmd, f"降级数据库到: {revision}")


def show_current(config_path: Optional[Path] = None):
    """显示当前数据库版本"""
    cmd = _with_config(_alembic_base_cmd() + ["current"], config_path)
    return run_command(cmd, "查看当前版本")


def show_history(config_path: Optional[Path] = None):
    """显示迁移历史"""
    cmd = _with_config(_alembic_base_cmd() + ["history", "--verbose"], config_path)
    return run_command(cmd, "查看迁移历史")


def show_heads(config_path: Optional[Path] = None):
    """显示最新版本"""
    cmd = _with_config(_alembic_base_cmd() + ["heads"], config_path)
    return run_command(cmd, "查看最新版本")


def stamp_database(revision: str = "head", config_path: Optional[Path] = None):
    """标记数据库版本（不执行迁移）"""
    cmd = _with_config(_alembic_base_cmd() + ["stamp", revision], config_path)
    return run_command(cmd, f"标记数据库版本: {revision}")


def auto_migrate():
    """自动迁移：备份 → 生成 → 扫描 drop_table → 确认 → 执行。

    与旧版的区别：**生成与执行之间多了一道闸**。生成完先扫新增的迁移文件，
    命中 `op.drop_table` 就停下来问人；非交互环境下默认中止。
    """
    logger.info("=" * 60)
    logger.info("🔄 开始自动迁移流程")
    logger.info("=" * 60)

    # 0. 定位配置与 versions 目录（决定 -c 参数与扫描范围）
    config_path = resolve_alembic_config()
    if config_path is not None:
        logger.info(f"📄 alembic 配置: {config_path}")
    else:
        logger.warning("⚠️ 未找到 alembic 配置文件，交给 alembic 自行发现")

    versions_dirs = resolve_versions_dirs(config_path)
    if versions_dirs:
        logger.info("📂 扫描迁移目录: " + ", ".join(str(d) for d in versions_dirs))
    else:
        logger.warning("⚠️ 未找到 versions 目录，将无法扫描新迁移文件")

    # 1. 迁移前备份（失败即中止）
    if not backup_before_migrate():
        logger.error("❌ 自动迁移失败：迁移前备份未成功")
        return False

    # 2. 生成前快照，用于事后只挑出「本次新增」的迁移文件
    before = snapshot_migration_files(versions_dirs)

    # 3. 生成迁移
    if not create_migration("auto_migration", config_path):
        logger.error("❌ 自动迁移失败：无法生成迁移")
        return False

    # 4. 扫描新增迁移文件，找 drop_table
    try:
        created = new_migration_files(versions_dirs, before)
    except Exception as e:  # pragma: no cover - 目录异常时保守放行到人工审核
        logger.warning(f"⚠️ 扫描迁移目录异常（{e}），无法自动检查 drop_table")
        created = []

    if not created:
        logger.warning("⚠️ 本次 autogenerate 未产出新的迁移文件（可能无 schema 变更）")
    else:
        logger.info(f"🆕 本次新增迁移文件 {len(created)} 个：")
        for path in created:
            logger.info(f"   - {path.name}")

    hits = collect_drop_table_targets(created) if created else {}

    if hits:
        if not confirm_dangerous_migration(hits):
            logger.error("❌ 自动迁移中止：迁移文件已生成但**未执行**，请人工审阅后再决定")
            return False
    else:
        logger.info("✅ 新增迁移文件未发现 op.drop_table")

    # 5. 执行迁移
    if not upgrade_database(config_path=config_path):
        logger.error("❌ 自动迁移失败：无法执行迁移")
        return False

    logger.info("=" * 60)
    logger.info("✅ 自动迁移完成")
    logger.info("=" * 60)
    return True


def init_database():
    """初始化数据库（首次部署）"""
    logger.info("=" * 60)
    logger.info("🔧 初始化数据库")
    logger.info("=" * 60)

    config_path = resolve_alembic_config()

    # 创建初始迁移
    if not create_migration("initial_migration", config_path):
        logger.warning("⚠️ 无法创建初始迁移，可能已存在")

    # 执行迁移
    if not upgrade_database(config_path=config_path):
        logger.error("❌ 初始化失败")
        return False

    logger.info("=" * 60)
    logger.info("✅ 数据库初始化完成")
    logger.info("=" * 60)
    return True


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("使用方法:")
        print("  python migrate.py create [message]    - 创建新迁移")
        print("  python migrate.py upgrade [revision]  - 升级数据库（默认: head）")
        print("  python migrate.py downgrade [revision] - 降级数据库（默认: -1）")
        print("  python migrate.py current            - 显示当前版本")
        print("  python migrate.py history            - 显示迁移历史")
        print("  python migrate.py heads              - 显示最新版本")
        print("  python migrate.py stamp [revision]   - 标记版本（默认: head）")
        print("  python migrate.py auto               - 自动迁移（备份+生成+检查+执行）")
        print("  python migrate.py init               - 初始化数据库")
        print("")
        print("环境变量:")
        print(f"  {ALEMBIC_CONFIG_ENV}    - 显式指定 alembic 配置文件（默认按 DATABASE_URL 方言选择）")
        sys.exit(1)

    command = sys.argv[1]
    config_path = resolve_alembic_config()

    if command == "create":
        message = sys.argv[2] if len(sys.argv) > 2 else None
        success = create_migration(message, config_path)
    elif command == "upgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "head"
        success = upgrade_database(revision, config_path)
    elif command == "downgrade":
        revision = sys.argv[2] if len(sys.argv) > 2 else "-1"
        success = downgrade_database(revision, config_path)
    elif command == "current":
        success = show_current(config_path)
    elif command == "history":
        success = show_history(config_path)
    elif command == "heads":
        success = show_heads(config_path)
    elif command == "stamp":
        revision = sys.argv[2] if len(sys.argv) > 2 else "head"
        success = stamp_database(revision, config_path)
    elif command == "auto":
        success = auto_migrate()
    elif command == "init":
        success = init_database()
    else:
        logger.error(f"❌ 未知命令: {command}")
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
