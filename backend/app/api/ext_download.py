"""整本下载 API —— 交给外部下载器整本抓取，产出 EPUB / TXT。

## 定位

**可关闭的可选模块。** 它和书源一样依赖外部站点（番茄小说）与外部工具，
因此复用同一个开关 `FANQIE_ENABLED`：关闭时所有接口返回 403。

## 与书源的分工

| | 书源（`/api/source`） | 整本下载（`/api/ext-download`） |
| :--- | :--- | :--- |
| 抓取者 | 后端逐章直连 | 外部 Rust 工具 |
| 适合 | 挑几章、看榜单 | **整本**几百章 |
| 正文 | 未登录时锁定章只有试读片段 | 实测 550/550 章全文 |
| 产出 | JSON 逐章结果 → TXT | EPUB 文件 → 按需转 TXT |

两边互补，不是替代：快而浅的用书源，整本要全的用这里。

## 任务状态的两种用法

薄封装接口（`/start` `/status/{job_id}` `/result/{job_id}`）**现场去问工具**，
不做第二份真相 —— 两边各记一份进度，迟早会不一致。

下载管理器接口（`/jobs` 系列）则把任务**落库**（`download_jobs` 表）：
用户要的是「多任务列表 + 历史 + 交接拆书」，不持久化的话页面一刷新
就什么都不剩。落库后数据库不再是「现场真相」而是「最后一次同步的快照」，
真相仍在外部下载器里 —— 所以每次读列表都会**惰性同步**一次，
把外部状态拉回库里。两套接口并存，互不干扰。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Base, get_db
from app.logger import get_logger
from app.services.ext_downloader import epub_to_txt, ext_downloader_service

router = APIRouter(prefix="/ext-download", tags=["整本下载"])
logger = get_logger(__name__)


# ============================================================
#  持久化模型（下载管理器）
# ============================================================
class DownloadJob(Base):
    """整本下载任务。持久化，重启后靠惰性同步恢复真实状态。

    与 SourceTask 的区别在于执行者：取源任务的抓取协程跑在本后端进程里，
    后端一重启协程就死，因此重启时要标 paused；而整本下载的执行者是
    **独立的外部下载器进程**，本后端重启不影响它继续跑。
    所以本表在重启时**不改任何状态**，留在 running 的任务会在下一次
    读列表时被惰性同步拉回真实进度。只有同步时外部查无此 ext_job_id
    （外部下载器自己重启过、内存态丢了）才标 lost。
    """

    __tablename__ = "download_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    book_id = Column(String(32), nullable=False, index=True)
    # 外部下载器的任务号。它的 id 可能是 int，统一存 str，免得拼接 URL 时再转。
    ext_job_id = Column(String(64), default="", index=True)

    state = Column(String(20), nullable=False, default="queued", index=True)
    # queued / running / done / error / cancelled / lost
    message = Column(String(500), default="")
    error = Column(Text, default="")

    book_title = Column(String(300), default="")
    book_author = Column(String(200), default="")
    chapter_total = Column(Integer, default=0)
    saved_chapters = Column(Integer, default=0)
    group_done = Column(Integer, default=0)
    group_total = Column(Integer, default=0)

    epub_path = Column(String(500), default="")
    epub_bytes = Column(Integer, default=0)
    # TXT 是现场重转的（不缓存文件），字数只在这里留一份，
    # 让列表页不必每次都转一遍 120 万字的文本。
    txt_chars = Column(Integer, default=0)

    # ---------- 与拆书模块的衔接（与 source_tasks 同款三件套）----------
    import_task_id = Column(String(36), default="")
    import_status = Column(String(20), default="")      # "" / handed / failed
    import_message = Column(String(500), default="")
    imported_at = Column(DateTime, nullable=True)
    auto_import = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# 终态集合：同步器只对非终态任务发问，终态不再打扰外部下载器
_JOB_TERMINAL_STATES = ("done", "error", "cancelled", "lost")


# ============================================================
#  请求 / 响应
# ============================================================
class StartDownloadRequest(BaseModel):
    """开始整本下载。

    书名与作者只是**给界面用的提示**，下载器自己会解析真实书名
    （`GET /api/jobs` 返回的 title/author）。填错了不影响下载，
    但会影响 `find_output_epub` 的兜底查找，因此能填就填。
    """

    book_id: str = Field(..., description="书籍 ID（必填）")
    title: str = Field(default="", description="书名（便于定位产出的 EPUB）")
    author: str = Field(default="", description="作者（仅作记录）")


class CreateDownloadJobRequest(BaseModel):
    """下载管理器：创建任务。"""

    book_id: str = Field(..., description="书籍 ID 或番茄链接")
    title: str = Field(default="", description="书名（便于定位产出的 EPUB）")
    author: str = Field(default="", description="作者（仅作记录）")
    auto_import: bool = Field(default=True, description="下载完成后是否自动交给拆书模块")


class DownloadJobSummary(BaseModel):
    """DownloadJob 的全字段摘要，列表与详情共用同一份口径。"""

    id: str
    book_id: str
    ext_job_id: str = ""
    state: str
    message: str = ""
    error: str = ""
    book_title: str = ""
    book_author: str = ""
    chapter_total: int = 0
    saved_chapters: int = 0
    group_done: int = 0
    group_total: int = 0
    epub_path: str = ""
    epub_bytes: int = 0
    txt_chars: int = 0
    import_task_id: str = ""
    import_status: str = ""
    import_message: str = ""
    imported_at: Optional[datetime] = None
    auto_import: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FromDownloadRequest(BaseModel):
    """拆书侧「从下载管理器导入」的请求体。"""

    download_job_id: str = Field(..., description="下载管理器任务 ID")
    extract_mode: str = Field(default="full", description="解析范围：full=整本 / tail=截取末章")
    tail_chapter_count: int = Field(default=10, description="extract_mode=tail 时截取末尾章节数")


# ============================================================
#  内部
# ============================================================
def _ext_enabled() -> bool:
    """是否启用。与书源共用开关 —— 同一个外部依赖门类。

    单独再设一个开关的坏处：用户关掉书源以为已经断开了外部依赖，
    整本下载却还在闷头往外发请求。
    """
    return bool(getattr(settings, "FANQIE_ENABLED", False))


def _require_enabled() -> None:
    if not _ext_enabled():
        raise HTTPException(
            status_code=403,
            detail="整本下载模块已关闭。启用请设置环境变量 FANQIE_ENABLED=true",
        )


def _job_title(job: dict, fallback: str = "") -> str:
    """取书名：下载器解析出来的优先，其次用调用方给的。"""
    return (job.get("title") or "").strip() or (fallback or "").strip()


def _locate_epub(job: dict, title: str) -> Path | None:
    """找这本书的产出 EPUB。

    书名优先用下载器解析出来的 —— 用户传的可能是错的或者空的。
    下载器没解析出书名时（任务还在排队），退回用户传的值。
    """
    candidates = [_job_title(job), title]
    for t in candidates:
        if not t:
            continue
        p = ext_downloader_service.find_output_epub(t)
        if p is not None:
            return p
    return None


# ============================================================
#  下载管理器：同步 / 交接
# ============================================================
def _job_to_summary(j: DownloadJob) -> DownloadJobSummary:
    return DownloadJobSummary(
        id=j.id,
        book_id=j.book_id,
        ext_job_id=j.ext_job_id or "",
        state=j.state,
        message=j.message or "",
        error=j.error or "",
        book_title=j.book_title or "",
        book_author=j.book_author or "",
        chapter_total=j.chapter_total or 0,
        saved_chapters=j.saved_chapters or 0,
        group_done=j.group_done or 0,
        group_total=j.group_total or 0,
        epub_path=j.epub_path or "",
        epub_bytes=j.epub_bytes or 0,
        txt_chars=j.txt_chars or 0,
        import_task_id=j.import_task_id or "",
        import_status=j.import_status or "",
        import_message=j.import_message or "",
        imported_at=j.imported_at,
        auto_import=bool(j.auto_import),
        created_at=j.created_at,
        updated_at=j.updated_at,
    )


async def _sync_jobs(db: AsyncSession, jobs: list[DownloadJob]) -> None:
    """把外部下载器的最新状态惰性拉回数据库。

    只在读接口里调用（列表/详情/建完任务后）。终态任务不再同步 ——
    它们的故事已经讲完，没必要再花 HTTP 请求去问。

    同步频率由前端轮询驱动：外部下载器没有单任务接口，
    `get_job()` 每次都是拉一次 `/api/jobs` 列表再筛。活跃任务数量小
    （用户同时下载的书也就几本），逐任务一次列表请求的开销可接受；
    真到几十本并发时再考虑批量拉一次列表共享。
    """
    for j in jobs:
        if j.state in _JOB_TERMINAL_STATES or not j.ext_job_id:
            continue

        try:
            # get_job 是同步阻塞 HTTP，放线程里跑，别卡事件循环
            ext = await asyncio.to_thread(ext_downloader_service.get_job, j.ext_job_id)
        except Exception as e:
            # 下载器临时不可达不等于任务丢了 —— 保留原状态，
            # 只在 message 里留一句，下次轮询还会再试。
            logger.warning("同步下载任务 %s（外部 %s）失败：%s", j.id, j.ext_job_id, e)
            continue

        if ext is None:
            # 外部查无此任务：它的内存态带 done_retention_ms，重启就全丢。
            # 文件若已落盘仍可导出/交接，所以标 lost 而不是 error。
            j.state = "lost"
            j.message = "外部下载器已重启或任务已过期被清理，任务状态丢失；若文件已落盘仍可尝试导出/交接"
            await db.commit()
            continue

        old_state = j.state
        new_state = ext.get("state", "") or j.state
        j.state = new_state

        progress = ext.get("progress") or {}
        j.chapter_total = int(progress.get("chapter_total") or 0)
        j.saved_chapters = int(progress.get("saved_chapters") or 0)
        j.group_done = int(progress.get("group_done") or 0)
        j.group_total = int(progress.get("group_total") or 0)

        # 书名/作者以下载器解析出来的为准：用户传的可能是错的或空的
        ext_title = (ext.get("title") or "").strip()
        if ext_title:
            j.book_title = ext_title
        ext_author = (ext.get("author") or "").strip()
        if ext_author:
            j.book_author = ext_author

        if new_state == "done":
            j.message = "下载完成"
            j.error = ""
            # 完成了才去找产物文件；中途找只会落空
            epub = _locate_epub(ext, j.book_title)
            if epub is not None:
                j.epub_path = str(epub)
                try:
                    j.epub_bytes = epub.stat().st_size
                except OSError:
                    j.epub_bytes = 0
        elif new_state == "error":
            # 失败原因字段名实测是 message（规格写的是 error），两个都读
            err = ext.get("message") or ext.get("error") or ""
            j.state = "error"
            j.error = str(err)
            j.message = f"下载失败：{err}"[:500] if err else "下载失败"
        else:
            j.message = ext.get("message") or j.message or ""

        await db.commit()

        # ---------- 自动交接 ----------
        # 触发点严格限定在「同步把状态翻成 done」这一刻：
        # 若建任务时就是 done（重复下载），POST /jobs 那次同步也会走这里；
        # 若任务早已 done 但 import_status 非空（交过），不重复交。
        if (
            old_state != "done"
            and new_state == "done"
            and bool(j.auto_import)
            and not (j.import_status or "")
        ):
            asyncio.create_task(_auto_handoff(j.id, j.user_id))


async def _auto_handoff(job_id: str, user_id: str) -> None:
    """后台交接：下载完成后自动把 EPUB 交给拆书管线。

    自开 session（模式照 source._run_task）：调用它的 _sync_jobs 用完的是
    请求生命周期里的会话，请求一结束会话就关，后台协程不能搭便车。

    交接成功后盯拆书终态并回写 import_* —— 交接成功 ≠ 拆书成功，
    不盯的话用户端看到的就是「交了之后没反应」。
    """
    from app.api.source import _await_book_import_result
    from app.database import get_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    engine = await get_engine("system")
    maker = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    try:
        async with maker() as db:
            job = await db.get(DownloadJob, job_id)
            if job is None:
                return
            try:
                res = await _hand_job_to_book_import(db, job, user_id)
            except HTTPException as e:
                job.import_status = "failed"
                job.import_message = str(e.detail)[:500]
                await db.commit()
                logger.warning("下载任务 %s 自动交接失败：%s", job_id, e.detail)
                return
            except Exception as e:
                job.import_status = "failed"
                job.import_message = f"自动交接失败：{type(e).__name__}: {e}"[:500]
                await db.commit()
                logger.warning("下载任务 %s 自动交接失败：%s", job_id, e)
                return

            bi_status = await _await_book_import_result(
                res["import_task_id"], user_id,
            )
            if bi_status is not None:
                # 重新取一次：盯结果花了几十秒，期间可能有手动交接改了字段
                job = await db.get(DownloadJob, job_id)
                if job is None:
                    return
                # 盯结果期间若有人重新交接，import_task_id 已换，
                # 这次的终态就不该再覆盖新任务的记录
                if job.import_task_id != res["import_task_id"]:
                    return
                if bi_status.status == "failed":
                    job.import_status = "failed"
                    job.import_message = (
                        f"拆书解析失败：{bi_status.error or bi_status.message or '未知原因'}"
                    )[:500]
                else:
                    job.import_message = (
                        (job.import_message or "")
                        + f" 拆书解析完成（{bi_status.progress}%）"
                    )[:500]
                await db.commit()
    except Exception as e:
        logger.error("下载任务 %s 自动交接协程异常：%s", job_id, e)


async def _hand_job_to_book_import(
    db: AsyncSession,
    job: DownloadJob,
    user_id: str,
    *,
    extract_mode: str = "full",
    tail_chapter_count: int = 10,
) -> dict:
    """把下载产物交给拆书导入。返回交接结果，调用方负责提交。

    幂等：import_status 已是 handed 且拆书任务还活着（未终态）时，
    直接返回现有的 import_task_id，不重复建任务 —— 用户连点两下
    「交给拆书」不该得到两本一样的书。
    """
    from app.services.book_import_service import book_import_service

    # 幂等检查：已交接过且拆书任务未终态 → 直接复用
    if job.import_status == "handed" and job.import_task_id:
        try:
            st = await book_import_service.get_task_status(
                task_id=job.import_task_id, user_id=user_id,
            )
            if st.status not in ("completed", "failed", "cancelled"):
                return {
                    "ok": True,
                    "import_task_id": job.import_task_id,
                    "txt_chars": job.txt_chars or 0,
                    "reused": True,
                    "note": "该下载任务已交接给拆书模块，直接复用现有拆书任务。",
                }
        except Exception:
            # 拆书任务查不到（内存态丢失，如服务重启）→ 允许重新交接
            pass

    epub: Path | None = None
    if job.epub_path:
        p = Path(job.epub_path)
        if p.is_file():
            epub = p
    if epub is None:
        # 库里没记路径或文件已被挪走：按书名再找一次。
        # lost 状态的任务也可能走到这里 —— 状态丢了但文件还在。
        epub = ext_downloader_service.find_output_epub(job.book_title or "")
    if epub is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"找不到《{job.book_title or job.book_id}》的 EPUB 文件"
                f"（查找目录：{', '.join(str(d) for d in ext_downloader_service.search_dirs())}）。"
            ),
        )

    try:
        txt = await asyncio.to_thread(epub_to_txt, epub)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"EPUB 转 TXT 失败：{type(e).__name__}: {e}",
        )
    if not txt.strip():
        raise HTTPException(status_code=500, detail=f"EPUB 里没有解析出正文：{epub}")

    bi_task = await book_import_service.create_task(
        user_id=user_id,
        filename=f"{job.book_title or job.book_id}.txt",
        file_content=txt.encode("utf-8"),
        project_id=None,
        create_new_project=True,
        import_mode="append",
        extract_mode=extract_mode,
        tail_chapter_count=tail_chapter_count,
    )
    bi_id = getattr(bi_task, "task_id", "") or ""

    # 产物事实顺手回写：字数只在交接时算一次，列表页直接读库
    job.txt_chars = len(txt)
    job.epub_path = str(epub)
    try:
        job.epub_bytes = epub.stat().st_size
    except OSError:
        pass
    job.import_task_id = bi_id
    job.import_status = "handed"
    job.import_message = f"已交接：{len(txt)} 字"
    job.imported_at = datetime.utcnow()

    return {
        "ok": True,
        "import_task_id": bi_id,
        "txt_chars": len(txt),
        "reused": False,
        "book_import_task": bi_task.model_dump() if hasattr(bi_task, "model_dump") else bi_task,
        "note": "已交给拆书导入。后续在该任务的预览里确认并导入。",
    }


# ============================================================
#  接口
# ============================================================
@router.post("/start", summary="开始整本下载")
async def start_download(payload: StartDownloadRequest):
    """把一本书交给外部下载器，**立即返回**，不等待下载完成。

    整本几百章要跑十几分钟，同步等会顶爆 HTTP 超时与浏览器等待。
    这里只负责排上队，后续用 `/status/{job_id}` 轮询进度。

    book_id 必填：下载器工作在「书 ID」级，给它 ID 它自己抓目录和全部正文。
    """
    _require_enabled()

    book_id = (payload.book_id or "").strip()
    if not book_id:
        raise HTTPException(status_code=400, detail="book_id 不能为空")

    try:
        res = ext_downloader_service.download_book(book_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # 启动失败 / 端口不通：把可读原因原样带出去，
        # 只说「失败」的话用户没有任何线索可查。
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "ok": True,
        "job_id": res.get("job_id"),
        "state": res.get("state", ""),
        "book_id": res.get("book_id", book_id),
        "title": payload.title,
        "author": payload.author,
        "addr": ext_downloader_service.addr,
        "note": (
            "任务已交给外部下载器。整本抓取耗时较长，"
            f"用 /api/ext-download/status/{res.get('job_id')} 轮询进度。"
        ),
    }


# ============================================================
#  下载管理器接口（任务落库，与上面的薄封装并存）
# ============================================================
@router.post("/jobs", response_model=DownloadJobSummary, summary="创建整本下载任务（落库）")
async def create_download_job(
    payload: CreateDownloadJobRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """登记一本要下载的书：落库 → 交给外部下载器 → 立即同步一次。

    与 `/start` 的区别：`/start` 是即用即弃的薄封装，任务号只在响应里
    出现一次；这里把任务持久化，之后用 `/jobs` 列表随时回看。
    """
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"

    # 与书源同一套规范化：链接、带 query 的地址都收
    from app.api.source import _normalize_book_id
    bid = _normalize_book_id(payload.book_id)

    job = DownloadJob(
        user_id=user_id,
        book_id=bid,
        state="queued",
        book_title=(payload.title or "").strip(),
        book_author=(payload.author or "").strip(),
        auto_import=payload.auto_import,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        res = await asyncio.to_thread(ext_downloader_service.download_book, bid)
    except ValueError as e:
        job.state = "error"
        job.error = str(e)
        await db.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # 启动失败 / 端口不通：留痕到任务里，方便事后排查
        job.state = "error"
        job.error = str(e)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(e))

    ext_id = res.get("job_id")
    if ext_id is None:
        job.state = "error"
        job.error = "外部下载器未返回任务号"
        await db.commit()
        raise HTTPException(status_code=502, detail="外部下载器未返回任务号")

    job.ext_job_id = str(ext_id)
    await db.commit()

    # 建完立即同步一次：外部若瞬间完成（重复下载已缓存的书），
    # 这里就能把 done 与自动交接一并触发，不用等下一轮轮询
    await _sync_jobs(db, [job])
    await db.refresh(job)
    return _job_to_summary(job)


@router.get("/jobs", response_model=list[DownloadJobSummary], summary="下载任务列表")
async def list_download_jobs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=200),
):
    """列出本用户的下载任务（新的在前），读之前先惰性同步一轮。"""
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"
    rows = (await db.execute(
        select(DownloadJob)
        .where(DownloadJob.user_id == user_id)
        .order_by(DownloadJob.created_at.desc())
        .limit(limit)
    )).scalars().all()
    await _sync_jobs(db, list(rows))
    return [_job_to_summary(j) for j in rows]


@router.get("/jobs/{job_id}", response_model=DownloadJobSummary, summary="单个下载任务（含同步）")
async def get_download_job(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    await _sync_jobs(db, [job])
    await db.refresh(job)
    return _job_to_summary(job)


@router.post("/jobs/{job_id}/cancel", response_model=DownloadJobSummary, summary="取消下载任务")
async def cancel_download_job(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """取消任务。外部取消是尽力而为：取消失败也把本地标 cancelled，
    因为用户表达的意图是「别再盯它了」；外部那边跑完也没人看。"""
    _require_enabled()
    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")

    if job.state in _JOB_TERMINAL_STATES:
        return _job_to_summary(job)

    if job.ext_job_id:
        await asyncio.to_thread(ext_downloader_service.cancel_job, job.ext_job_id)

    job.state = "cancelled"
    job.message = "用户取消"
    await db.commit()
    await db.refresh(job)
    return _job_to_summary(job)


@router.post("/jobs/{job_id}/to-book-import", summary="把下载产物交给拆书导入")
async def handoff_download_job(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    extract_mode: str = Query(default="full", description="解析范围：full=整本 / tail=截取末章"),
    tail_chapter_count: int = Query(default=10, description="tail 时截取末尾章节数"),
):
    """手动交接。允许 state=lost 的任务尝试 —— 状态丢了不代表文件没了。

    重复调用是安全的：已交接且拆书任务未终态时直接复用现有任务，
    不会重复创建（见 `_hand_job_to_book_import`）。
    """
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"

    job = await db.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")

    if job.state not in ("done", "lost"):
        raise HTTPException(
            status_code=409,
            detail=f"任务尚未下载完成（当前状态：{job.state}），不能交接。",
        )

    result = await _hand_job_to_book_import(
        db, job, user_id,
        extract_mode=extract_mode,
        tail_chapter_count=tail_chapter_count,
    )
    await db.commit()

    # 新建交接后同样在后台盯拆书终态（复用自动交接的盯梢逻辑，
    # 但不动它的交接部分 —— 这里只盯不写新的交接记录）
    if not result.get("reused"):
        asyncio.create_task(_watch_handoff_result(job.id, user_id, result["import_task_id"]))

    return result


async def _watch_handoff_result(job_id: str, user_id: str, import_task_id: str) -> None:
    """盯一次手动交接的拆书终态，把失败如实写回下载任务。

    与 `_auto_handoff` 共用一段盯梢语义，但拆书任务已经建好，
    这里不再交接 —— 只等结果。
    """
    from app.api.source import _await_book_import_result
    from app.database import get_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    engine = await get_engine("system")
    maker = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    try:
        bi_status = await _await_book_import_result(import_task_id, user_id)
        if bi_status is None:
            return
        async with maker() as db:
            job = await db.get(DownloadJob, job_id)
            if job is None or job.import_task_id != import_task_id:
                return
            if bi_status.status == "failed":
                job.import_status = "failed"
                job.import_message = (
                    f"拆书解析失败：{bi_status.error or bi_status.message or '未知原因'}"
                )[:500]
            else:
                job.import_message = (
                    (job.import_message or "")
                    + f" 拆书解析完成（{bi_status.progress}%）"
                )[:500]
            await db.commit()
    except Exception as e:
        logger.warning("盯下载任务 %s 的拆书终态异常：%s", job_id, e)


@router.get("/status/{job_id}", summary="整本下载进度")
async def download_status(job_id: str, title: str = Query(default="", description="书名，用于定位产出文件")):
    """查任务进度。

    下载器没有单任务接口，只能拉列表筛出目标。任务被它自己清理掉
    （`done_retention_ms` 到期）后返回 404，这是预期行为而非错误。
    """
    _require_enabled()

    try:
        job = ext_downloader_service.get_job(job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"下载器里没有任务 {job_id}。任务可能已过期被清理，或服务重启过。",
        )

    state = job.get("state", "")
    progress = job.get("progress") or {}

    # 失败原因：实测该接口用的字段名是 `message`（任务正常时为 null），
    # 而不是规格里写的 `error`。两个都读，任一版本都不会漏掉原因。
    err = job.get("message") or job.get("error") or ""

    result = {
        "ok": True,
        "job_id": job.get("id"),
        "state": state,
        "book_id": job.get("book_id", ""),
        "title": _job_title(job, title),
        "author": job.get("author", ""),
        "error": err,
        "progress": {
            "chapter_total": progress.get("chapter_total", 0),
            "saved_chapters": progress.get("saved_chapters", 0),
            "group_done": progress.get("group_done", 0),
            "group_total": progress.get("group_total", 0),
            "save_phase": progress.get("save_phase", ""),
        },
    }

    # 只有完成了才有必要去找文件。下载中途去找，EPUB 还没落盘。
    if state == "done":
        epub = _locate_epub(job, title)
        if epub is not None:
            result["epub_path"] = str(epub)
            result["epub_bytes"] = epub.stat().st_size
            result["txt_ready"] = True
        else:
            # 完成了却找不到文件，必须如实说 —— 猜一个路径塞回去，
            # 用户点下载只会拿到 404。
            result["epub_path"] = None
            result["epub_bytes"] = 0
            result["txt_ready"] = False
            result["note"] = (
                "任务已完成，但在本机没找到对应的 EPUB。"
                "若下载器是本模块之外启动的实例，产出会落在它的工作目录下。"
            )

    return result


def _attachment_header(filename: str) -> str:
    """拼一个能装中文文件名的 Content-Disposition。

    HTTP 头只能装 latin-1，书名是中文时直接写 `filename="惹金枝.txt"`
    会在构造响应时抛 `UnicodeEncodeError: 'latin-1' codec can't encode`。
    按 RFC 5987 处理：同时给一个纯 ASCII 的 `filename=` 兜底
    （老客户端用），和一个百分号编码的 `filename*=UTF-8''`（现代浏览器用）。
    """
    from urllib.parse import quote

    fallback = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in filename)
    # 引号与反斜杠会破坏头结构
    fallback = fallback.replace('"', "_").replace("\\", "_") or "novel"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quoted}"


@router.get("/result/{job_id}", summary="下载整本 TXT")
async def download_result(
    job_id: str,
    title: str = Query(default="", description="书名，用于定位产出文件"),
):
    """把下载好的 EPUB 转成 TXT，直接返回给浏览器下载。

    为什么在服务端转：EPUB 是 zip + 一堆 xhtml，用户拿到手还得自己拆。
    转成 TXT 后可以直接喂给拆书导入，中间没必要多一步。

    未完成时返回 409 并带上当前状态 —— 用 409 而不是 404，
    是因为「还没跑完」和「没有这个任务」是两件事，
    前端据此决定是「继续等」还是「提示任务不存在」。
    """
    _require_enabled()

    try:
        job = ext_downloader_service.get_job(job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"下载器里没有任务 {job_id}。任务可能已过期被清理，或服务重启过。",
        )

    state = job.get("state", "")
    if state != "done":
        raise HTTPException(
            status_code=409,
            detail=f"任务尚未完成（当前状态：{state}）。请稍后重试。",
        )

    name = _job_title(job, title) or str(job.get("book_id", job_id))
    epub = _locate_epub(job, name)
    if epub is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"任务已完成，但没找到《{name}》的 EPUB 文件"
                f"（查找目录：{', '.join(str(d) for d in ext_downloader_service.search_dirs())}）。"
            ),
        )

    try:
        txt = epub_to_txt(epub)
    except Exception as e:
        logger.warning("EPUB 转 TXT 失败：%s（%s）", epub, e)
        raise HTTPException(
            status_code=500,
            detail=f"EPUB 转 TXT 失败：{type(e).__name__}: {e}",
        )

    if not txt.strip():
        raise HTTPException(
            status_code=500,
            detail=f"EPUB 里没有解析出正文：{epub}",
        )

    logger.info("整本下载任务 %s 导出 TXT：%s 字（源文件 %s）", job_id, len(txt), epub)

    return PlainTextResponse(
        content=txt,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": _attachment_header(f"{name}.txt"),
        },
    )
