"""Alembic 环境配置文件 - PostgreSQL"""
import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入应用配置
from app.config import settings

# 导入 Base 和所有模型
from app.database import Base
from app.models import (
    Project, Outline, Character, Chapter, GenerationHistory,
    Settings, WritingStyle, ProjectDefaultStyle,
    RelationshipType, CharacterRelationship, Organization, OrganizationMember,
    StoryMemory, PlotAnalysis, AnalysisTask, BatchGenerationTask,
    RegenerationTask, Career, CharacterCareer, User, MCPPlugin, PromptTemplate,
    BackgroundTask
)

# 副轨表：由应用 lifespan 动态建表，不走 alembic 迁移。
# autogenerate 时必须跳过，否则会生成 drop_table 毁掉用户数据（见 docs/待办.md TODO-010）。
#
# 报告 01 P0-37 只点了 sqlite 侧，但本侧同样中招，且 docker-compose 默认用
# PostgreSQL —— 只修 sqlite 版的话，默认编排的部署照样删库。
#
# 函数体与副轨表名单放在 app/migration_safety.py（共享位置）而不是就地定义：
# env.py 顶层会直接跑 run_migrations_*，**import 本文件即触发迁移**，
# 就地定义的话测试无法安全拿到这个钩子。新增副轨表请改该模块的 SIDE_TRACK_TABLES。
from app.migration_safety import include_object  # noqa: E402

# Alembic Config 对象
config = context.config

# 设置数据库连接字符串（从环境变量读取）
config.set_main_option("sqlalchemy.url", settings.database_url)

# 配置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置 target_metadata 为应用的 Base.metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """在'离线'模式下运行迁移"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,  # 跳过副轨表，见 TODO-010
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """执行迁移的核心函数 - PostgreSQL 专用"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=False,  # PostgreSQL 不需要批处理模式
        include_object=include_object,  # 跳过副轨表，见 TODO-010
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在'在线'模式下运行异步迁移"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url
    
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在'在线'模式下运行迁移"""
    asyncio.run(run_async_migrations())


# 根据上下文选择运行模式
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()