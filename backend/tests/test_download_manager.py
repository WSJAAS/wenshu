"""下载管理器后端测试。

## 为什么用独立 SQLite + 依赖覆盖

本工程真实库（runtime/data/wenshu.db）是线上数据，测试绝不能碰。
因此用 tmp_path 下的临时 SQLite 建一套**只含 download_jobs** 的最小库，
用 `app.dependency_overrides[get_db]` 换掉会话来源，同时把
`ext_downloader_service` 的 HTTP/进程行为全部 monkeypatch 成内存假实现
——测试要验的是「我们的状态机与幂等逻辑」，不是外部 Rust 工具。

## 为什么不走 AuthMiddleware

中间件在无会话 Cookie 时会把 user_id 兜回本地默认用户，
测试要覆盖 401/403 这类「没有或不对的用户」分支，
所以在 dependency 层直接把 request.state.user_id 摆成想要的样子 ——
测的是端点本身的校验逻辑，不是中间件。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import get_db


# ============================================================
#  假的外部下载器
# ============================================================
class _FakeExtJob:
    """外部下载器眼里一个任务的样子（照 /api/jobs 列表元素的字段）。"""

    def __init__(self, job_id: int, book_id: str, title: str = "", author: str = ""):
        self.id = job_id
        self.book_id = book_id
        self.title = title
        self.author = author
        self.state = "queued"
        self.message: str | None = None
        self.error: str | None = None
        self.progress = {
            "chapter_total": 0,
            "saved_chapters": 0,
            "group_done": 0,
            "group_total": 0,
        }


class FakeExtDownloader:
    """内存版外部下载器。

    复刻它的几个关键语义，不然测试验的东西会失真：
      - `get_job` 找不到返回 None（驱动 lost 分支）
      - `download_book` 立即返回一个递增的整数 job_id
      - `cancel_job` 尽力而为、不抛错
      - `find_output_epub` 按书名找 EPUB，找不到返回 None
    """

    def __init__(self, epub_dir: Path):
        self._epub_dir = epub_dir
        self._jobs: dict[str, _FakeExtJob] = {}
        self._next_id = 1
        self.addr = "http://127.0.0.1:18423"

    def download_book(self, book_id: str, range_start=None, range_end=None) -> dict[str, Any]:
        bid = str(book_id or "").strip()
        if not bid:
            raise ValueError("book_id 不能为空")
        jid = self._next_id
        self._next_id += 1
        self._jobs[str(jid)] = _FakeExtJob(jid, bid)
        return {"job_id": jid, "state": "queued", "book_id": bid}

    def get_job(self, job_id: Any) -> dict[str, Any] | None:
        job = self._jobs.get(str(job_id))
        if job is None:
            return None
        return {
            "id": job.id,
            "state": job.state,
            "book_id": job.book_id,
            "title": job.title,
            "author": job.author,
            "message": job.message,
            "error": job.error,
            "progress": dict(job.progress),
        }

    def cancel_job(self, job_id: Any) -> bool:
        return True

    def find_output_epub(self, title: str) -> Path | None:
        name = (title or "").strip()
        if not name:
            return None
        p = self._epub_dir / f"{name}.epub"
        return p if p.is_file() else None

    def search_dirs(self) -> list[Path]:
        return [self._epub_dir]

    # ---------- 测试辅助 ----------
    def set_state(
        self,
        job_id: Any,
        state: str,
        *,
        title: str = "",
        author: str = "",
        progress: dict[str, int] | None = None,
        message: str | None = None,
    ) -> None:
        job = self._jobs[str(job_id)]
        job.state = state
        if title:
            job.title = title
        if author:
            job.author = author
        if progress:
            job.progress.update(progress)
        if message is not None:
            job.message = message


def _make_epub(path: Path) -> None:
    """写最小合法 EPUB（一个 zip + 一章 xhtml）。

    `epub_to_txt` 只认 `OEBPS/chapter_\\d+.xhtml`，所以只要造出
    这种命名的一个章节文件，就能走通真实的 EPUB→TXT 链路，
    不必 mock 掉转换 —— 转换逻辑本身也在被间接验证。
    """
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    chapter_xhtml = (
        "<html><head><title>第1章 开始</title></head>"
        "<body><h1>第1章 开始</h1><p>正文一段。</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("OEBPS/chapter_00001.xhtml", chapter_xhtml)


# ============================================================
#  夹具：临时库 + 假下载器 + 可指定 user_id 的 TestClient
# ============================================================
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """每个用例一套独立环境：临时 SQLite + 假下载器 + 独立 app 实例。

    为什么不用全局 `app` 而自建实例：
      - 要在进路由前钉死 `request.state.user_id`，就得叠中间件；
        而 Starlette 不允许 app 启动后再 `add_middleware`，
        用全局 app 做测试会跨用例累积中间件、互相污染。
      - 每用例新建一个轻量 app（只挂被测的两个 router），
        中间件想怎么叠就怎么叠，互不干扰。
    """
    from fastapi import FastAPI
    from app.api import ext_download as ext_mod
    from app.api import book_import as bi_mod

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init_schema():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: ext_mod.DownloadJob.__table__.create(c, checkfirst=True)
            )

    asyncio.run(_init_schema())

    fake = FakeExtDownloader(epub_dir=tmp_path / "epubs")
    monkeypatch.setattr(ext_mod, "ext_downloader_service", fake)

    # 后台盯梢协程在函数体内 `from app.database import get_engine` 延迟引入，
    # 所以要 patch 的是 app.database 上的名字，不是 ext_download 的。
    # 不指到临时库的话，协程会去摸真实库。
    import app.database as db_mod

    async def _fake_get_engine(user_id: str = "system"):
        return engine

    monkeypatch.setattr(db_mod, "get_engine", _fake_get_engine)

    async def _override_get_db():
        async with maker() as s:
            yield s

    test_app = FastAPI()
    test_app.include_router(ext_mod.router, prefix="/api")
    test_app.include_router(bi_mod.router, prefix="/api")
    test_app.dependency_overrides[get_db] = _override_get_db

    class NS:
        pass

    ns = NS()
    ns.engine = engine
    ns.maker = maker
    ns.fake = fake
    ns.ext_mod = ext_mod
    ns.bi_mod = bi_mod
    ns.monkeypatch = monkeypatch
    ns.tmp_path = tmp_path
    ns.app = test_app
    return ns


@pytest.fixture()
def make_client(env):
    """给一个能钉死 request.state.user_id 的客户端工厂。

    在每个用例自己的 app 上叠一层 BaseHTTPMiddleware，
    进路由前把 `request.state.user_id` 摆成指定值（None 则显式写 None，
    以覆盖 AuthMiddleware 兜底出的本地用户 —— 401 分支必须有它）。
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    def _build(user_id: str | None) -> TestClient:
        class _SetUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user_id = user_id
                return await call_next(request)

        env.app.add_middleware(_SetUser)
        return TestClient(env.app)

    return _build


# ============================================================
#  1. 建表 / 补列幂等
# ============================================================
def test_create_and_ensure_columns_idempotent(tmp_path):
    """checkfirst 建表与补列函数重复跑都不应出错、也不应重复加列。"""
    from sqlalchemy import create_engine, inspect as sa_inspect
    from app.api.ext_download import DownloadJob
    from app.main import _ensure_download_job_columns

    db = tmp_path / "idem.db"
    sync_engine = create_engine(f"sqlite:///{db}")

    with sync_engine.begin() as conn:
        DownloadJob.__table__.create(conn, checkfirst=True)
        _ensure_download_job_columns(conn)

    # 再跑一遍：幂等，不应抛错
    with sync_engine.begin() as conn:
        DownloadJob.__table__.create(conn, checkfirst=True)
        _ensure_download_job_columns(conn)

    with sync_engine.connect() as conn:
        cols = {c["name"] for c in sa_inspect(conn).get_columns("download_jobs")}

    # 交接三件套 + auto_import 是本表存在的意义，必须在场
    assert {
        "id", "user_id", "book_id", "ext_job_id", "state",
        "epub_path", "epub_bytes", "txt_chars",
        "import_task_id", "import_status", "import_message",
        "imported_at", "auto_import", "created_at", "updated_at",
    } <= cols

    # 模拟「旧库缺列」：手工建一张只有主干列的旧表，
    # 跑补列后应把缺口都填上。
    db2 = tmp_path / "old.db"
    old_engine = create_engine(f"sqlite:///{db2}")
    with old_engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE download_jobs (id VARCHAR(36) PRIMARY KEY, "
            "user_id VARCHAR(100) NOT NULL, book_id VARCHAR(32) NOT NULL)"
        )
        _ensure_download_job_columns(conn)

    with old_engine.connect() as conn:
        cols2 = {c["name"] for c in sa_inspect(conn).get_columns("download_jobs")}
    assert "import_task_id" in cols2
    assert "auto_import" in cols2
    assert "txt_chars" in cols2


# ============================================================
#  2. 惰性同步
# ============================================================
def test_sync_done_writes_epub_fields(env, make_client):
    """外部 done 时，列表接口要把状态翻成 done 并补上 epub_path/epub_bytes。"""
    client = make_client("u1")
    try:
        r = client.post("/api/ext-download/jobs", json={
            "book_id": "12345",
            "title": "测试书",
            "author": "某人",
            "auto_import": False,   # 这个用例只验同步，关掉自动交接
        })
        assert r.status_code == 200, r.text
        job = r.json()

        env.fake.set_state(
            job["ext_job_id"], "done",
            title="测试书", author="某人",
            progress={"chapter_total": 3, "saved_chapters": 3,
                      "group_done": 1, "group_total": 1},
        )
        _make_epub(env.tmp_path / "epubs" / "测试书.epub")

        r2 = client.get("/api/ext-download/jobs")
        assert r2.status_code == 200, r2.text
        rows = [j for j in r2.json() if j["id"] == job["id"]]
        assert rows, "列表里应有刚建的任务"
        got = rows[0]
        assert got["state"] == "done"
        assert got["chapter_total"] == 3
        assert got["saved_chapters"] == 3
        assert got["book_title"] == "测试书"
        assert got["epub_path"].endswith("测试书.epub")
        assert got["epub_bytes"] > 0
    finally:
        client.close()


def test_sync_lost_when_external_missing(env, make_client):
    """外部查无此任务（下载器重启丢了内存态）→ state=lost。"""
    client = make_client("u1")
    try:
        r = client.post("/api/ext-download/jobs", json={
            "book_id": "999", "auto_import": False,
        })
        assert r.status_code == 200, r.text
        job = r.json()

        env.fake._jobs.clear()

        r2 = client.get(f"/api/ext-download/jobs/{job['id']}")
        assert r2.status_code == 200, r2.text
        got = r2.json()
        assert got["state"] == "lost"
        assert "丢失" in got["message"] or "重启" in got["message"]
    finally:
        client.close()


# ============================================================
#  3. 交接幂等
# ============================================================
def test_to_book_import_idempotent(env, make_client, monkeypatch):
    """已 handed 且拆书任务未终态时，第二次交接必须复用同一个 task_id。"""
    created: list[str] = []

    class _FakeBITask:
        def __init__(self, task_id: str):
            self.task_id = task_id
            self.status = "pending"

        def model_dump(self):
            return {"task_id": self.task_id, "status": self.status}

    class _FakeBIStatus:
        def __init__(self, status: str):
            self.status = status
            self.progress = 0
            self.error = None
            self.message = ""

    class FakeBookImportService:
        async def create_task(self, user_id, filename, file_content, **kw):
            tid = f"bi-{len(created) + 1}"
            created.append(tid)
            return _FakeBITask(tid)

        async def get_task_status(self, task_id, user_id):
            return _FakeBIStatus("running")  # 未终态

    fake_bi = FakeBookImportService()
    # ext_download 是在函数体内 `from app.services.book_import_service import ...`
    # 延迟引入的，要 patch 的是那个模块的属性，不是 ext_download 的名字。
    import app.services.book_import_service as bis_mod
    monkeypatch.setattr(bis_mod, "book_import_service", fake_bi)

    client = make_client("u1")
    try:
        r = client.post("/api/ext-download/jobs", json={
            "book_id": "777", "auto_import": False,
        })
        assert r.status_code == 200, r.text
        job = r.json()
        env.fake.set_state(job["ext_job_id"], "done", title="交接书")
        _make_epub(env.tmp_path / "epubs" / "交接书.epub")

        # 先同步一次把状态翻成 done（否则 409）
        r0 = client.get(f"/api/ext-download/jobs/{job['id']}")
        assert r0.json()["state"] == "done"

        r1 = client.post(f"/api/ext-download/jobs/{job['id']}/to-book-import")
        assert r1.status_code == 200, r1.text
        first = r1.json()
        assert first["ok"] is True
        assert first["import_task_id"]
        assert first["reused"] is False

        r2 = client.post(f"/api/ext-download/jobs/{job['id']}/to-book-import")
        assert r2.status_code == 200, r2.text
        second = r2.json()
        assert second["import_task_id"] == first["import_task_id"]
        assert second["reused"] is True

        assert len(created) == 1
    finally:
        client.close()


# ============================================================
#  4. from-download 的 404 / 403 / 409 / 401
# ============================================================
def _seed_job(client, env, *, user_id: str, state: str) -> str:
    """直接往库里塞一个 DownloadJob，绕过外部下载器，专测 book_import 侧的校验。

    必须借 TestClient 的 blocking portal 跑：aiosqlite 连接绑定事件循环，
    而 client 的请求都跑在 portal 的循环里；自己 `asyncio.run` 会新开循环，
    同一连接跨循环串门会报「attached to a different loop」。
    """
    from app.api.ext_download import DownloadJob

    async def _insert() -> str:
        async with env.maker() as s:
            j = DownloadJob(user_id=user_id, book_id="1", state=state)
            s.add(j)
            await s.commit()
            return j.id

    return client.portal.call(_insert)


def test_from_download_404(env, make_client):
    client = make_client("u1")
    try:
        r = client.post("/api/book-import/tasks/from-download", json={
            "download_job_id": "不存在的id",
            "extract_mode": "full",
            "tail_chapter_count": 10,
        })
        assert r.status_code == 404, r.text
    finally:
        client.close()


def test_from_download_403(env, make_client):
    # portal 只有在 client 进入 with 后才活着，seed 也必须放进去
    with make_client("u1") as client:
        job_id = _seed_job(client, env, user_id="别人", state="done")
        r = client.post("/api/book-import/tasks/from-download", json={
            "download_job_id": job_id,
            "extract_mode": "full",
            "tail_chapter_count": 10,
        })
        assert r.status_code == 403, r.text


def test_from_download_409(env, make_client):
    with make_client("u1") as client:
        job_id = _seed_job(client, env, user_id="u1", state="running")
        r = client.post("/api/book-import/tasks/from-download", json={
            "download_job_id": job_id,
            "extract_mode": "full",
            "tail_chapter_count": 10,
        })
        assert r.status_code == 409, r.text
        # detail 里要如实带上当前 state，前端据此提示
        assert "running" in r.json()["detail"]


def test_from_download_401(env, make_client):
    """没有 user_id 时必须 401 —— 归属都还没确认，什么都不能做。"""
    client = make_client(None)
    try:
        r = client.post("/api/book-import/tasks/from-download", json={
            "download_job_id": "whatever",
            "extract_mode": "full",
            "tail_chapter_count": 10,
        })
        assert r.status_code == 401, r.text
    finally:
        client.close()


# ============================================================
#  5. FANQIE_ENABLED=false 时新端点 403
# ============================================================
def test_jobs_endpoints_blocked_when_disabled(env, make_client, monkeypatch):
    """模块开关关闭时，/jobs 系列必须整体 403，不能漏出任何一条。"""
    monkeypatch.setattr(
        env.ext_mod.settings, "FANQIE_ENABLED", False, raising=False,
    )
    client = make_client("u1")
    try:
        assert client.get("/api/ext-download/jobs").status_code == 403
        assert client.post("/api/ext-download/jobs", json={
            "book_id": "1",
        }).status_code == 403
        assert client.get("/api/ext-download/jobs/x").status_code == 403
        assert client.post("/api/ext-download/jobs/x/cancel").status_code == 403
        assert client.post(
            "/api/ext-download/jobs/x/to-book-import",
        ).status_code == 403
    finally:
        client.close()
