"""书源 API —— 榜单、详情、取源任务。

## 定位

**可关闭的可选模块。** 依赖外部站点（番茄小说），随时可能失效。
通过配置 `FANQIE_ENABLED` 开关，默认关闭；关闭时所有接口返回 403。

## 与拆书导入的关系

书源负责「拿到原文 TXT」，拆书导入负责「把 TXT 变成项目」。
两者通过 `results_to_txt()` 衔接，拆书管线不改。

## 任务持久化

取源任务**不放在内存里**。整本可能几百章、跑十几分钟，
进程重启就丢会非常糟。任务状态落在 `source_tasks` 表，
重启后可以继续。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Base, get_db
from app.logger import get_logger
from app.services.fanqie_source import (
    FREE_CHAPTER_COUNT,
    BatchFetcher, BlockedError, BookInfo, ChapterContent, ChapterRef,
    FanqieClient, FanqieError, build_missing_report, merge_results,
    results_to_txt,
    cookie_status, save_login_cookie,
)

router = APIRouter(prefix="/source", tags=["书源"])
logger = get_logger(__name__)


# ============================================================
#  持久化模型
# ============================================================
class SourceTask(Base):
    """取源任务。持久化，重启后可继续。"""

    __tablename__ = "source_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    book_id = Column(String(32), nullable=False, index=True)

    status = Column(String(20), nullable=False, default="pending", index=True)
    # pending / running / paused / completed / failed / cancelled
    message = Column(String(500), default="")
    error = Column(Text, default="")

    # 书籍元信息与目录（JSON）
    book_title = Column(String(300), default="")
    book_author = Column(String(200), default="")
    total_chapters = Column(Integer, default=0)

    # 抓取范围与进度
    requested_ranges = Column(Text, default="")     # 用户选的范围说明
    done_count = Column(Integer, default=0)
    ok_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)

    # 逐章结果：{item_id: ChapterContent dict}
    chapters_json = Column(Text, default="{}")
    # 全部正文的内容指纹（SHA256）。用于验证任务隔离、校验迁移一致性。
    chapters_digest = Column(String(64), default="")
    # 目录快照
    catalog_json = Column(Text, default="[]")
    # 书籍信息快照
    book_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================
#  请求 / 响应
# ============================================================
class SourceStatusResponse(BaseModel):
    enabled: bool
    module: str = "书源"
    note: str = ""
    # 登录态。未登录时锁定章只有试读片段，这个状态必须让界面知道。
    login_configured: bool = False
    login_has_session: bool = False
    login_note: str = ""


class BookSearchRequest(BaseModel):
    book_id: str = Field(..., description="书籍 ID 或番茄链接")


class ChapterSelection(BaseModel):
    """选章范围。三选一，优先级：item_ids > range > preset。"""

    preset: str = Field(default="first", description="first=开篇若干章 / volume=指定卷 / all=整本")
    count: int = Field(default=10, ge=1, le=200, description="preset=first 时取多少章")
    volume: str = Field(default="", description="preset=volume 时的卷名")
    item_ids: list[str] = Field(default_factory=list, description="preset=ids 时直接指定")


class CreateSourceTaskRequest(BaseModel):
    book_id: str
    selection: ChapterSelection = Field(default_factory=ChapterSelection)
    # 保守默认值。实测单章 0.2~0.4s，但连续整本的风控代价未知，
    # 因此不按实测速度设默认。
    interval: float = Field(default=1.2, ge=0.2, le=10.0, description="请求间隔（秒）")
    max_chapters: int | None = Field(default=None, ge=1, le=3000, description="本次最多抓多少章")


class TaskSummary(BaseModel):
    task_id: str
    status: str
    message: str = ""
    error: str = ""
    book_id: str = ""
    book_title: str = ""
    total_chapters: int = 0
    done_count: int = 0
    ok_count: int = 0
    failed_count: int = 0
    # 内容指纹：验证任务隔离与迁移一致性
    chapters_digest: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ============================================================
#  内部：任务与客户端
# ============================================================
def _source_enabled() -> bool:
    """书源是否启用。默认关闭 —— 依赖外部站点，让它显式开启。"""
    return bool(getattr(settings, "FANQIE_ENABLED", False))


def _require_enabled() -> None:
    if not _source_enabled():
        raise HTTPException(
            status_code=403,
            detail="书源模块已关闭。启用请设置环境变量 FANQIE_ENABLED=true",
        )


def _normalize_book_id(raw: str) -> str:
    """从链接或纯 ID 中取出 bookId。"""
    s = (raw or "").strip()
    if not s:
        raise HTTPException(status_code=400, detail="书籍 ID 为空")
    # 链接形式：https://fanqienovel.com/page/<id>
    if "/" in s:
        parts = [p for p in s.split("/") if p]
        for i, p in enumerate(parts):
            if p in ("page", "reader") and i + 1 < len(parts):
                s = parts[i + 1]
                break
        else:
            s = parts[-1]
    s = s.split("?")[0].strip()
    if not s.isdigit():
        raise HTTPException(status_code=400, detail=f"无法识别的书籍 ID: {raw}")
    return s


def _select_chapters(all_ch: list[ChapterRef], sel: ChapterSelection) -> list[ChapterRef]:
    """按用户选择筛出要抓的章节。"""
    if sel.item_ids:
        want = set(sel.item_ids)
        picked = [c for c in all_ch if c.item_id in want]
        if not picked:
            raise HTTPException(status_code=400, detail="指定的章节 ID 都不在目录里")
        return picked

    if sel.preset == "all":
        return list(all_ch)

    if sel.preset == "volume":
        if not sel.volume:
            raise HTTPException(status_code=400, detail="preset=volume 时必须指定卷名")
        picked = [c for c in all_ch if c.volume == sel.volume]
        if not picked:
            vols = sorted({c.volume for c in all_ch})
            raise HTTPException(
                status_code=400,
                detail=f"卷名不存在: {sel.volume}。可用卷: {', '.join(vols)}",
            )
        return picked

    # 默认：开篇若干章
    return all_ch[: sel.count]


def _task_to_summary(t: SourceTask) -> TaskSummary:
    return TaskSummary(
        task_id=t.id,
        status=t.status,
        message=t.message or "",
        error=t.error or "",
        book_id=t.book_id,
        book_title=t.book_title or "",
        total_chapters=t.total_chapters or 0,
        done_count=t.done_count or 0,
        ok_count=t.ok_count or 0,
        failed_count=t.failed_count or 0,
        chapters_digest=getattr(t, "chapters_digest", "") or "",
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# ChapterContent 的全部字段。读取时需要校验，避免字段缺失被静默填空。
_CHAPTER_FIELDS = (
    "item_id", "title", "text", "volume", "order",
    "status", "error", "pua_before", "pua_after", "chars",
    # 截断判定所需的两个事实。漏掉它们会让读回的任务丢失依据：
    # 分章接口就没法告诉前端「这章申报了多少字、为何判为试读」。
    "is_locked", "claimed_words",
)
# 缺了这些字段说明数据不可用，必须报错而不是静默补默认值
_CHAPTER_REQUIRED = ("item_id", "status")


def _load_chapters(t: SourceTask) -> dict[str, ChapterContent]:
    """从任务里读回逐章结果。

    严格校验：必填字段缺失时抛错，而不是静默填空。

    早期实现只挑已知字段，缺什么就用 dataclass 的默认值补上。
    那样的后果是：若数据结构变化（比如 text 改名），
    会得到一批「状态 ok 但正文为空」的记录却不报错 ——
    错误被推迟到用户看到空内容时才暴露。

    宁可在这里就明确失败。
    """
    try:
        raw = json.loads(t.chapters_json or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(
            f"任务 {t.id} 的 chapters_json 不是合法 JSON：{e}"
        ) from e

    if not isinstance(raw, dict):
        raise ValueError(
            f"任务 {t.id} 的 chapters_json 应为对象，实际是 {type(raw).__name__}"
        )

    out: dict[str, ChapterContent] = {}
    for key, v in raw.items():
        if not isinstance(v, dict):
            raise ValueError(f"任务 {t.id} 的章节 {key} 不是对象")

        missing = [f for f in _CHAPTER_REQUIRED if not v.get(f)]
        if missing:
            raise ValueError(
                f"任务 {t.id} 的章节 {key} 缺少必填字段 {missing}；"
                f"实际字段为 {sorted(v.keys())}"
            )

        payload = {f: v[f] for f in _CHAPTER_FIELDS if f in v}
        # item_id 以记录键为准（键即 item_id），但保留原值以便发现不一致
        if "item_id" not in payload:
            payload["item_id"] = key
        elif str(payload["item_id"]) != str(key):
            raise ValueError(
                f"任务 {t.id} 的章节键 {key} 与记录内 item_id "
                f"{payload['item_id']} 不一致"
            )

        out[key] = ChapterContent(**payload)

    return out


def _save_chapters(t: SourceTask, results: dict[str, ChapterContent]) -> None:
    """写入逐章结果。

    同时计算内容指纹，让「这个任务的内容没被别的任务污染」可被验证。
    """
    payload = {k: v.to_dict() for k, v in results.items()}
    t.chapters_json = json.dumps(payload, ensure_ascii=False)
    t.chapters_digest = _content_digest(results)


def _content_digest(results: dict[str, ChapterContent]) -> str:
    """对全部正文算一个 SHA256，作为这一批内容的指纹。

    用途：
      - 验证任务之间的隔离（A 的指纹不应出现在 B 里）
      - 迁移时校验搬的是同一份内容
    为了让指纹稳定，按 item_id 排序后再喂给哈希。
    """
    h = hashlib.sha256()
    for item_id in sorted(results.keys()):
        c = results[item_id]
        h.update(item_id.encode("utf-8"))
        h.update(b"\x00")
        h.update((c.text or "").encode("utf-8"))
        h.update(b"\x00")
        h.update((c.status or "").encode("utf-8"))
        h.update(b"\x1e")   # 记录分隔符，避免拼接歧义
    return h.hexdigest()


def _load_catalog(t: SourceTask) -> list[ChapterRef]:
    try:
        raw = json.loads(t.catalog_json or "[]")
    except json.JSONDecodeError:
        return []
    return [ChapterRef(**c) for c in raw if isinstance(c, dict)]


# ============================================================
#  接口
# ============================================================
@router.get("/status", response_model=SourceStatusResponse, summary="书源模块状态")
async def source_status():
    """前端据此决定是否显示书源入口。"""
    enabled = _source_enabled()
    ck = cookie_status()
    return SourceStatusResponse(
        enabled=enabled,
        note=(
            "书源已启用。内容来自番茄小说公开页面，仅作本地研读。"
            if enabled
            else "书源默认关闭。它依赖外部站点，启用需设置 FANQIE_ENABLED=true"
        ),
        login_configured=bool(ck.get("configured")),
        login_has_session=bool(ck.get("has_sessionid")),
        login_note=ck.get("note", ""),
    )


@router.get("/login", summary="查看登录态（不回显 Cookie 全文）")
async def get_login_state():
    """登录态概况。

    未登录时，锁定章只能取到约 200 字符的试读片段 —— 这是
    「抓了 550 章却只有 10 万字」的直接原因。界面必须如实告知。
    """
    _require_enabled()
    return cookie_status()


class LoginCookieRequest(BaseModel):
    cookie: str = Field(default="", description="浏览器里复制的 Cookie 串；空串表示清除")


@router.post("/login", summary="设置或清除登录态")
async def set_login_state(payload: LoginCookieRequest):
    """保存登录 Cookie。

    取值方法：浏览器登录番茄小说后，按 F12 → Network →
    任意请求 → Request Headers → 复制 Cookie 整行。

    只做原样转发，不解密、不校验账号。存放位置在数据目录下，
    不入库、不进 git。
    """
    _require_enabled()
    try:
        path = save_login_cookie(payload.cookie)
    except FanqieError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ck = cookie_status()
    ck["saved_to"] = path
    return ck


@router.get("/rank", summary="榜单（含覆盖范围说明）")
async def get_rank(
    interval: float = Query(default=1.2, ge=0.2, le=10.0),
    enrich: bool = Query(default=True, description="是否补取详情页拿干净书名"),
):
    """取榜单首屏。

    **返回里明确标注覆盖范围**：首屏通常只有 10 本，而平台声明 100 本。
    绝不把部分当全部。
    """
    _require_enabled()
    client = FanqieClient(interval=interval)

    try:
        items, declared_total = await asyncio.to_thread(client.fetch_rank)
    except BlockedError as e:
        raise HTTPException(status_code=429, detail=f"被风控拦截：{e}")
    except FanqieError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # 榜单页书名可能带混淆，补取详情页拿干净的
    if enrich:
        for it in items:
            if not it.get("book_id"):
                continue
            try:
                book, _ = await asyncio.to_thread(client.fetch_book, it["book_id"])
            except Exception as e:
                # 单本补取失败不影响整体。此时书名只能用榜单页的（可能带混淆），
                # 明确标记出来，避免把乱码当正常书名展示。
                it["enriched"] = False
                it["enrich_error"] = str(e)[:120]
                fallback = it.get("title_decoded") or it.get("title_raw") or ""
                it["title"] = fallback
                it["title_may_be_garbled"] = any(
                    0xE000 <= ord(c) <= 0xF8FF for c in fallback
                )
                continue

            it["enriched"] = True
            it["title"] = book.title or it.get("title_decoded") or it.get("title_raw") or ""
            it["title_may_be_garbled"] = False
            it["author"] = book.author or it.get("author", "")
            it["category"] = book.category
            it["tags"] = book.tags
            it["status"] = book.status
            # 详情页的读数更可靠；榜单读数可能是 0（字段缺失）
            it["read_count"] = book.read_count or it.get("read_count", 0)
            it["word_count"] = book.word_count or it.get("word_count", 0)
            it["cover_url"] = book.cover_url or it.get("cover_url", "")
            it["abstract"] = book.abstract or it.get("abstract", "")
            it["last_chapter_title"] = book.last_chapter_title

    enriched_count = sum(1 for it in items if it.get("enriched"))
    garbled_count = sum(1 for it in items if it.get("title_may_be_garbled"))
    failed_count = sum(1 for it in items if it.get("enriched") is False)

    return {
        "items": items,
        "fetched": len(items),
        "declared_total": declared_total,
        "enriched": enriched_count,
        "enrich_failed": failed_count,
        "garbled_titles": garbled_count,
        # 如实说明覆盖范围
        "coverage_note": (
            f"已取得 {len(items)} 本"
            + (f"，平台声明 {declared_total} 本" if declared_total else "")
            + ("。榜单首屏只返回部分条目，未分页取全时不应视为完整榜单。"
               if declared_total and len(items) < declared_total else "")
            + (f" 另有 {failed_count} 本详情补取失败，书名可能不准确。"
               if failed_count else "")
        ),
        "is_complete": bool(declared_total) and len(items) >= declared_total,
        "platform_note": "榜单按日更新，反映前一日数据，不是实时热度。",
    }


@router.get("/book/{book_id}", summary="书籍详情与目录")
async def get_book(
    book_id: str,
    interval: float = Query(default=1.2, ge=0.2, le=10.0),
):
    """取书籍详情与完整章节目录。"""
    _require_enabled()
    bid = _normalize_book_id(book_id)
    client = FanqieClient(interval=interval)

    try:
        book, chapters = await asyncio.to_thread(client.fetch_book, bid)
    except BlockedError as e:
        raise HTTPException(status_code=429, detail=f"被风控拦截：{e}")
    except FanqieError as e:
        raise HTTPException(status_code=404, detail=str(e))

    vols: dict[str, int] = {}
    for c in chapters:
        vols[c.volume or "(无卷名)"] = vols.get(c.volume or "(无卷名)", 0) + 1

    locked_ref = sum(1 for c in chapters if c.is_locked)
    return {
        "book": book.to_dict(),
        "chapters": [c.to_dict() for c in chapters],
        "volumes": [{"name": k, "count": v} for k, v in vols.items()],
        # 说明锁定字段的含义，避免前端误用
        "lock_field_note": (
            f"目录里 {locked_ref} 章带 isChapterLock 标记。"
            "实测该标记表示「不在免费试读段」，**不代表不可读**。"
            "真实可读性以实际抓取结果为准。"
        ),
    }


@router.post("/tasks", response_model=TaskSummary, summary="创建取源任务")
async def create_source_task(
    payload: CreateSourceTaskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """创建并启动取源任务。

    任务**持久化**在数据库里，重启后可以继续。
    已有的章节结果不会被重复抓取（断点续跑）。
    """
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"

    bid = _normalize_book_id(payload.book_id)

    # 先取目录
    client = FanqieClient(interval=payload.interval)
    try:
        book, chapters = await asyncio.to_thread(client.fetch_book, bid)
    except BlockedError as e:
        raise HTTPException(status_code=429, detail=f"被风控拦截：{e}")
    except FanqieError as e:
        raise HTTPException(status_code=404, detail=str(e))

    picked = _select_chapters(chapters, payload.selection)

    task = SourceTask(
        user_id=user_id,
        book_id=bid,
        status="pending",
        message=f"已选 {len(picked)} 章",
        book_title=book.title,
        book_author=book.author,
        total_chapters=len(picked),
        requested_ranges=json.dumps(payload.selection.model_dump(), ensure_ascii=False),
        book_json=json.dumps(book.to_dict(), ensure_ascii=False),
        catalog_json=json.dumps([c.to_dict() for c in chapters], ensure_ascii=False),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 后台启动抓取
    asyncio.create_task(_run_task(
        task.id,
        selected_ids=[c.item_id for c in picked],
        interval=payload.interval,
        max_chapters=payload.max_chapters,
    ))

    return _task_to_summary(task)


async def _run_task(
    task_id: str,
    selected_ids: list[str],
    interval: float,
    max_chapters: int | None,
) -> None:
    """后台执行取源。

    单独开一个会话，避免与请求生命周期耦合。
    """
    from app.database import get_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as AS

    engine = await get_engine("system")
    maker = async_sessionmaker(engine, class_=AS, expire_on_commit=False)

    try:
        async with maker() as db:
            task = await db.get(SourceTask, task_id)
            if task is None:
                return

            catalog = _load_catalog(task)
            want = set(selected_ids)
            targets = [c for c in catalog if c.item_id in want]

            already = _load_chapters(task)

            task.status = "running"
            task.message = f"开始抓取 {len(targets)} 章"
            await db.commit()

            client = FanqieClient(interval=interval)
            fetcher = BatchFetcher(client)

            # 逐章回调写库，保证中途重启也有进度
            last_flush = [0]

            def on_progress(prog, content):
                last_flush[0] += 1
                # 每 5 章落一次库，避免频繁写
                if last_flush[0] % 5 == 0 or prog.done == prog.total:
                    pending.append((content, dict(prog.to_dict())))
                else:
                    pending.append((content, None))

            pending: list[tuple[ChapterContent, dict | None]] = []

            # 抓取放在线程里跑（同步客户端）
            def work():
                return fetcher.fetch_range(
                    targets,
                    already=already,
                    on_progress=on_progress,
                    max_chapters=max_chapters,
                )

            fresh, prog = await asyncio.to_thread(work)

            merged = merge_results(already, fresh)
            _save_chapters(task, merged)

            ok = sum(1 for c in merged.values()
                     if c.status == "ok" and c.item_id in want)
            preview = sum(1 for c in merged.values()
                          if c.status == "preview" and c.item_id in want)
            task.done_count = sum(1 for c in merged.values() if c.item_id in want)
            task.ok_count = ok
            task.failed_count = len(targets) - ok

            if prog.stopped_reason:
                task.status = "paused" if "风控" in prog.stopped_reason else "cancelled"
                task.message = prog.stopped_reason
            else:
                task.status = "completed"
                # 预览章单独报出来。只说「完成 N/N 章」会让人以为
                # 整本到手，而实际可能只覆盖了个位数百分比。
                if preview:
                    task.message = (
                        f"完成：完整 {ok}/{len(targets)} 章，"
                        f"另 {preview} 章只取得试读片段（未登录，正文被截断）"
                    )
                else:
                    task.message = f"完成：{ok}/{len(targets)} 章"

            await db.commit()
            logger.info("取源任务 %s 结束：%s", task_id, task.message)

    except Exception as e:
        logger.error("取源任务 %s 异常: %s", task_id, e)
        try:
            async with maker() as db:
                t = await db.get(SourceTask, task_id)
                if t:
                    t.status = "failed"
                    t.error = f"{type(e).__name__}: {e}"
                    await db.commit()
        except Exception:
            pass


@router.get("/tasks", response_model=list[TaskSummary], summary="取源任务列表")
async def list_source_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=200),
):
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"
    rows = (await db.execute(
        select(SourceTask)
        .where(SourceTask.user_id == user_id)
        .order_by(SourceTask.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [_task_to_summary(t) for t in rows]


@router.get("/tasks/{task_id}", response_model=TaskSummary, summary="取源任务状态")
async def get_source_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _task_to_summary(t)


def _resolve_targets(t: SourceTask) -> list[ChapterRef]:
    """按任务记录的选章范围，从目录里解析出「本任务真正要抓的章」。

    这一步很重要：只有拿准范围，才能正确区分
    「没抓到的缺失」与「本来就没选的」——
    否则会出现「只选 12 章却报 325 章缺失」这种误导。
    """
    catalog = _load_catalog(t)
    try:
        sel = json.loads(t.requested_ranges or "{}")
    except json.JSONDecodeError:
        sel = {}

    item_ids = sel.get("item_ids") or []
    if item_ids:
        want = set(item_ids)
        return [c for c in catalog if c.item_id in want]

    preset = sel.get("preset") or "first"
    if preset == "all":
        return list(catalog)
    if preset == "volume":
        vol = sel.get("volume") or ""
        return [c for c in catalog if c.volume == vol]

    # preset == first：取开篇若干章
    try:
        count = int(sel.get("count") or 10)
    except (TypeError, ValueError):
        count = 10
    return catalog[:count]


@router.get("/tasks/{task_id}/chapters", summary="任务的分章结果与缺章清单")
async def get_source_task_chapters(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    include_text: bool = Query(default=False, description="是否包含正文（可能很大）"),
):
    """分章状态与缺章清单。

    缺章清单是给用户看的「哪些没拿到、为什么」——
    没有它，用户无法判断后续分析覆盖了多少内容。

    **统计范围只含本任务选定的章**，未选中的不算缺失。
    """
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    targets = _resolve_targets(t)
    results = _load_chapters(t)
    report = build_missing_report(targets, results)

    chapters = []
    for ref in targets:
        c = results.get(ref.item_id)
        if c is None:
            chapters.append({
                "order": ref.order,
                "item_id": ref.item_id,
                "title": ref.title,
                "volume": ref.volume,
                "status": "not_fetched",
                "error": "尚未抓取",
                "chars": 0,
                "pua_before": 0,
                "pua_after": 0,
            })
            continue
        entry = {
            "order": ref.order,
            "item_id": ref.item_id,
            "title": c.title or ref.title,
            "volume": c.volume or ref.volume,
            "status": c.status,
            "error": c.error,
            "chars": c.chars,
            "pua_before": c.pua_before,
            "pua_after": c.pua_after,
            # 截断判定依据：前端据此显示「取得 N 字 / 申报 M 字」
            "is_locked": c.is_locked,
            "claimed_words": c.claimed_words,
        }
        if include_text:
            entry["text"] = c.text
        chapters.append(entry)

    return {
        "task": _task_to_summary(t).model_dump(),
        "chapters": chapters,
        "report": report,
        "scope_note": (
            f"统计范围为本任务选定的 {len(targets)} 章；"
            f"该书目录共 {len(_load_catalog(t))} 章，其余章节未纳入本次任务。"
        ),
    }


@router.get("/tasks/{task_id}/txt", summary="导出为 TXT（可直接喂给拆书导入）")
async def export_source_txt(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """把抓取结果拼成 TXT 文本。

    保留章节顺序与段落结构；缺失章节在末尾附说明，
    让后续的拆书分析知道覆盖范围。
    """
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    catalog = _load_catalog(t)
    results = _load_chapters(t)
    targets = _resolve_targets(t)

    txt = results_to_txt(targets, results)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=txt,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="source_{t.book_id}.txt"'
            )
        },
    )


@router.post("/tasks/{task_id}/cancel", summary="取消取源任务")
async def cancel_source_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status in ("completed", "cancelled", "failed"):
        return _task_to_summary(t)
    t.status = "cancelled"
    t.message = "用户取消"
    await db.commit()
    await db.refresh(t)
    return _task_to_summary(t)


# ============================================================
#  与拆书导入衔接
# ============================================================
@router.post("/tasks/{task_id}/to-book-import", summary="把取源结果交给拆书导入")
async def handoff_to_book_import(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    extract_mode: str = Query(default="full", description="解析范围，在线取源已选定内容，固定用 full"),
):
    """把抓取到的原文直接交给现有拆书导入管线。

    这是书源与拆书之间的衔接点：书源负责拿到干净 TXT，
    拆书负责把它变成项目（生成设定、人物、大纲）。
    """
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    ok_count = t.ok_count or 0
    if ok_count == 0:
        raise HTTPException(
            status_code=400,
            detail="该任务没有可用正文，无法导入。请先确认抓取结果。",
        )

    catalog = _load_catalog(t)
    results = _load_chapters(t)
    targets = _resolve_targets(t)

    txt = results_to_txt(targets, results)
    if not txt.strip():
        raise HTTPException(status_code=400, detail="拼接出的文本为空")

    user_id = getattr(request.state, "user_id", None) or "local"

    from app.services.book_import_service import book_import_service
    task = await book_import_service.create_task(
        user_id=user_id,
        filename=f"{t.book_title or t.book_id}.txt",
        file_content=txt.encode("utf-8"),
        project_id=None,
        create_new_project=True,
        import_mode="append",
        # 在线取源已经选好范围，这里按整本处理
        extract_mode="full",
        tail_chapter_count=10,
    )

    return {
        "ok": True,
        "book_title": t.book_title,
        "handed_chapters": len([c for c in targets if c.item_id in results
                                and results[c.item_id].status == "ok"]),
        # 只拿到试读片段的章数。拆书导入拿到的是残缺正文时，
        # 需要知道有多少内容是截断的。
        "preview_chapters": len([c for c in targets if c.item_id in results
                                 and results[c.item_id].status == "preview"]),
        "txt_chars": len(txt),
        "book_import_task": task.model_dump() if hasattr(task, "model_dump") else task,
        "note": "已交给拆书导入。后续在该任务的预览里确认并导入。",
    }


# ============================================================
#  浏览器交接
# ============================================================
class HandoverRequest(BaseModel):
    """浏览器脚本交过来的书。

    字段可能不全 —— 用户脚本从页面里能认多少就给多少，
    服务端以 book_id 或 item_id 为准，其余信息能补则补。
    """

    book_id: str = Field(default="", description="书籍 ID")
    item_id: str = Field(default="", description="章节 ID（阅读页交接时用）")
    title: str = Field(default="", description="书名（来自页面，仅作参考）")
    author: str = Field(default="", description="作者（来自页面，仅作参考）")
    chapter_total: int = Field(default=0, description="章节总数（来自页面）")
    source_url: str = Field(default="", description="来源页面地址")
    # 是否立即建取源任务。默认只登记，让用户在文枢里确认范围
    create_task: bool = Field(default=False, description="是否立即抓取")
    chapter_count: int = Field(default=10, ge=1, le=3000, description="create_task 时抓多少章")


@router.post("/handover", summary="接收浏览器交来的书（用户脚本调用）")
async def handover_from_browser(
    payload: HandoverRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """接收用户在番茄页面选中的书。

    这是「用户在网页选书」的落点：浏览器脚本把 book_id 与登录态发过来，
    服务端负责后续抓取。**浏览器不参与内容搬运**，只递一个标识 ——
    这样既不受混合内容限制，也不会拖慢用户的标签页。

    取值优先级：book_id > item_id。
    item_id 是章节标识，只有阅读页才有；此时需要反查所属书。
    """
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"

    bid = (payload.book_id or "").strip()
    iid = (payload.item_id or "").strip()

    if not bid and not iid:
        raise HTTPException(status_code=400, detail="未提供 book_id 或 item_id")

    client = FanqieClient(interval=1.2)

    # 只有 item_id 时，从阅读页反查所属书
    if not bid and iid:
        ref = client.resolve_book_by_item(iid)
        if not ref:
            raise HTTPException(
                status_code=404,
                detail=f"无法从章节 {iid} 反查所属书籍",
            )
        bid = ref

    # 取详情：既确认书存在，也拿到服务端认定的书名与章数
    try:
        book, chapters = await asyncio.to_thread(client.fetch_book, bid)
    except BlockedError as e:
        raise HTTPException(status_code=429, detail=f"被风控拦截：{e}")
    except FanqieError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Cookie 情况：决定锁定章能否取到全文
    ck = cookie_status()
    can_full_text = bool(ck.get("has_sessionid"))

    # 书名以服务端为准。页面上的可能带字体混淆，或与平台登记不一致。
    # 若差异明显，把页面的值一并回传，便于用户判断是不是选错了书。
    title_mismatch = bool(
        payload.title
        and book.title
        and payload.title.strip() != book.title.strip()
    )

    result: dict[str, Any] = {
        "ok": True,
        "book_id": book.book_id,
        "title": book.title,
        "author": book.author,
        "chapter_total": len(chapters),
        "status": book.status,
        "category": book.category,
        "word_count": book.word_count,
        "read_count": book.read_count,
        "from_page_title": payload.title,
        "title_mismatch": title_mismatch,
        "login_ready": can_full_text,
        "login_note": ck.get("note", ""),
        "task_id": None,
        "message": "",
    }

    if title_mismatch:
        result["message"] = (
            f"注意：页面显示「{payload.title}」，平台登记为「{book.title}」。"
            "以平台登记为准。"
        )

    # 是否立即建任务
    if payload.create_task:
        picked = chapters[: payload.chapter_count]
        task = SourceTask(
            user_id=user_id,
            book_id=bid,
            status="pending",
            message=f"已选 {len(picked)} 章（来自浏览器交接）",
            book_title=book.title,
            book_author=book.author,
            total_chapters=len(picked),
            requested_ranges=json.dumps(
                {"preset": "first", "count": payload.chapter_count,
                 "volume": "", "item_ids": []},
                ensure_ascii=False,
            ),
            book_json=json.dumps(book.to_dict(), ensure_ascii=False),
            catalog_json=json.dumps([c.to_dict() for c in chapters], ensure_ascii=False),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        asyncio.create_task(_run_task(
            task.id,
            selected_ids=[c.item_id for c in picked],
            interval=1.2,
            max_chapters=None,
        ))
        result["task_id"] = task.id
        result["message"] = (
            (result["message"] + " " if result["message"] else "")
            + f"已创建取源任务，将抓取前 {len(picked)} 章。"
        )
        # 未登录时同样要提醒 —— 这条路径才是真去抓的，比只登记更需要说清
        if not can_full_text:
            preview_est = max(0, len(picked) - FREE_CHAPTER_COUNT)
            if preview_est > 0:
                result["message"] += (
                    f" 当前未取得登录态，其中约 {preview_est} 章"
                    f"（第 {FREE_CHAPTER_COUNT + 1} 章起）只能取到试读片段。"
                    " 如需全文，请在番茄页面登录后再交接一次。"
                )
            else:
                result["message"] += (
                    f" 当前未取得登录态，但所选 {len(picked)} 章都在"
                    f"前 {FREE_CHAPTER_COUNT} 章的免费范围内，可以取到全文。"
                )
    else:
        # 只登记，让用户回到文枢再确认范围
        result["message"] = (
            (result["message"] + " " if result["message"] else "")
            + "已在文枢登记。回到书源页面即可选择抓取范围。"
        )
        if not can_full_text:
            result["message"] += (
                f" 当前未取得登录态，第 {FREE_CHAPTER_COUNT + 1} 章起"
                "只能取到试读片段 —— 请在番茄页面登录后再交接一次。"
            )

    return result


@router.get("/handover/summary", summary="最近接收的浏览器交接")
async def recent_handovers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=50),
):
    """列出最近的取源任务，供前端显示「刚从浏览器交来的书」。"""
    _require_enabled()
    user_id = getattr(request.state, "user_id", None) or "local"
    rows = (await db.execute(
        select(SourceTask)
        .where(SourceTask.user_id == user_id)
        .order_by(SourceTask.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [_task_to_summary(t) for t in rows]


@router.get("/userscript.user.js", summary="浏览器联动脚本（安装用）")
async def get_userscript():
    """提供用户脚本本体。

    从本机服务提供而不是让用户去第三方站点装，有两个好处：
      - 脚本永远与当前后端版本匹配，不会出现接口对不上
      - 来源是本机，用户能自己读一遍代码再装

    脚本里的默认地址会自动替换成当前服务地址，用户装完即用。
    """
    script_path = Path(__file__).resolve().parents[3] / "tools" / "fanqie-handover.user.js"
    if not script_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"找不到脚本文件：{script_path}",
        )

    text = script_path.read_text(encoding="utf-8")

    # 返回纯文本，让浏览器/油猴识别为可安装脚本
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=text,
        media_type="text/javascript; charset=utf-8",
        headers={
            # 便于用户直接查看源码，而不是被下载成文件
            "Content-Disposition": 'inline; filename="fanqie-handover.user.js"',
        },
    )


@router.get("/handover/guide", summary="浏览器联动的使用说明")
async def handover_guide():
    """给前端的安装指引，含当前服务地址。"""
    _require_enabled()
    ck = cookie_status()
    return {
        "userscript_url": "/api/source/userscript.user.js",
        "steps": [
            "安装浏览器扩展 Tampermonkey（油猴）",
            "点击下方按钮安装「文枢 · 番茄联动」脚本",
            "在番茄小说登录你的账号",
            "打开任意书籍页，点右下角「交给文枢」",
        ],
        "notes": [
            "脚本只把书籍标识和 Cookie 发到本机 127.0.0.1，不经过任何第三方。",
            "Cookie 只保存在本机数据目录，不入库、不进 git。",
            "登录后第 11 章起才能取到全文；未登录只能取到约 200 字的试读片段。",
        ],
        "login_ready": bool(ck.get("has_sessionid")),
        "login_note": ck.get("note", ""),
    }
