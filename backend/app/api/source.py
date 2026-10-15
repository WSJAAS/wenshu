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
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, select
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
    build_chapter_content,
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

    # ---------- 与拆书模块的衔接 ----------
    #
    # 记录本任务的正文有没有交出去、交成了没。
    # 没有这几个字段的话，从取源侧看不到拆书那一步的结果，
    # 用户点了「交给拆书导入」之后这边还是一句「已完成」，
    # 无法判断后续到底跑没跑。
    import_task_id = Column(String(36), default="")     # 拆书任务 ID
    import_status = Column(String(20), default="")      # 未交接 / 已交接 / 已导入 / 失败
    import_message = Column(String(500), default="")
    imported_at = Column(DateTime, nullable=True)
    # 抓取完成后是否自动交给拆书模块。默认开 —— 用户交接一本书，
    # 本意就是要分析它，不该再点一次。
    auto_import = Column(Boolean, default=True, nullable=False)
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
    # 与拆书模块的衔接状态
    import_task_id: str = ""
    import_status: str = ""
    import_message: str = ""
    imported_at: Optional[datetime] = None
    # 抓完后是否自动交给拆书模块
    auto_import: bool = True
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
        import_task_id=getattr(t, "import_task_id", "") or "",
        import_status=getattr(t, "import_status", "") or "",
        import_message=getattr(t, "import_message", "") or "",
        imported_at=getattr(t, "imported_at", None),
        auto_import=bool(getattr(t, "auto_import", True)),
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
                # 解析层已经填好了 title 与 title_may_be_garbled，这里保持原样。
                # 早期实现在此处重算，反而可能把解析层的判定覆盖掉。
                if not it.get("title"):
                    it["title"] = it.get("title_decoded") or it.get("title_raw") or ""
                continue

            it["enriched"] = True
            # 详情页书名干净，优先用它。
            # 但详情页也可能偶尔缺书名，此时保留解析层的值，不要写成空。
            it["title"] = book.title or it.get("title") or ""
            # 只有在确实用了详情页书名时，才能说它不乱码
            it["title_may_be_garbled"] = not bool(book.title) and bool(
                it.get("title_may_be_garbled")
            )
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
        # 书源页面创建的任务同样默认抓完自动交接。
        # 两条入口行为要一致，否则从网页交接和从榜单创建会得到不同结果。
        auto_import=True,
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

            # ---------- 自动交给拆书模块 ----------
            #
            # 抓完就交，不用用户再点一次。
            # 但要满足两个前提，否则不自动做：
            #   - 有完整正文（只有试读片段时交过去没有意义）
            #   - 用户没关掉自动交接（auto_import 字段）
            #
            # 失败不改变任务本身的终态 —— 抓取是成功的，
            # 只是交接这一步没成，两者要分开记。
            if (
                task.status == "completed"
                and ok > 0
                and bool(getattr(task, "auto_import", True))
            ):
                try:
                    res = await _hand_to_book_import(task, task.user_id)
                    task.import_task_id = res["import_task_id"]
                    task.import_status = "handed"
                    task.import_message = (
                        f"抓取完成后自动交接：完整 {res['handed_chapters']} 章"
                        + (f"，另 {res['preview_chapters']} 章为试读片段"
                           if res["preview_chapters"] else "")
                        + f"，共 {res['txt_chars']} 字"
                    )
                    task.imported_at = datetime.utcnow()
                    logger.info("取源任务 %s 已自动交接拆书：%s",
                                task_id, res["import_task_id"])

                    # 交接成功 ≠ 拆书成功。create_task 只是登记，
                    # 真正的解析在后台异步跑，可能失败（实测崩在
                    # AI 后处理：'NoneType' object has no attribute 'strip'）。
                    # 那时 import_status 停在 handed，用户端就是
                    # 「交了之后没反应」。这里盯到拆书出结果为止，
                    # 把终态如实写回取源任务。
                    bi_status = await _await_book_import_result(
                        res["import_task_id"], task.user_id,
                    )
                    if bi_status is not None:
                        if bi_status.status == "failed":
                            task.import_status = "failed"
                            task.import_message = (
                                f"拆书解析失败：{bi_status.error or bi_status.message or '未知原因'}"
                            )[:500]
                            logger.warning(
                                "取源任务 %s 的拆书任务 %s 解析失败：%s",
                                task_id, res["import_task_id"], bi_status.error,
                            )
                        else:
                            task.import_message = (
                                task.import_message
                                + f" 拆书解析完成（{bi_status.progress}%）"
                            )
                except Exception as e:
                    task.import_status = "failed"
                    task.import_message = f"自动交接失败：{type(e).__name__}: {e}"[:500]
                    logger.warning("取源任务 %s 自动交接失败：%s", task_id, e)
                await db.commit()

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
async def _await_book_import_result(
    bi_task_id: str,
    user_id: str,
    *,
    timeout_seconds: float = 600.0,
    poll_interval: float = 2.0,
):
    """等拆书任务出终态，返回其状态响应；超时返回 None。

    拆书解析在后台异步跑。交接（create_task）成功只说明登记上了，
    之后管线可能失败 —— 比如实测的 AI 后处理崩溃
    （'NoneType' object has no attribute 'strip'）。
    不盯结果的话，取源任务的 import_status 停在 handed，
    用户端看到的就是「交了之后没反应」。

    只读状态不改数据；拿不到任务（服务重启丢内存态）也返回 None，
    不影响交接本身的成功记录。
    """
    from app.services.book_import_service import book_import_service

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            status = await book_import_service.get_task_status(
                task_id=bi_task_id, user_id=user_id,
            )
        except HTTPException:
            # 任务不存在（内存态丢失，如服务重启）—— 不再等
            return None
        except Exception:
            await asyncio.sleep(poll_interval)
            continue

        if status.status in ("completed", "failed", "cancelled"):
            return status
        await asyncio.sleep(poll_interval)

    return None


async def _hand_to_book_import(
    t: SourceTask,
    user_id: str,
) -> dict[str, Any]:
    """把取源结果交给拆书导入。返回交接结果。

    抽出来是因为有两条入口都要用它：用户手动点按钮，
    以及抓取完成后自动交接。两处必须走同一套逻辑，
    否则「自动」与「手动」的行为会分叉。

    调用方负责写 import_* 字段与提交。
    """
    ok_count = t.ok_count or 0
    if ok_count == 0:
        raise HTTPException(
            status_code=400,
            detail="该任务没有可用正文，无法导入。请先确认抓取结果。",
        )

    results = _load_chapters(t)
    targets = _resolve_targets(t)
    txt = results_to_txt(targets, results)
    if not txt.strip():
        raise HTTPException(status_code=400, detail="拼接出的文本为空")

    from app.services.book_import_service import book_import_service
    bi_task = await book_import_service.create_task(
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

    bi_id = getattr(bi_task, "task_id", "") or ""
    full = len([c for c in targets if c.item_id in results
                and results[c.item_id].status == "ok"])
    preview = len([c for c in targets if c.item_id in results
                   and results[c.item_id].status == "preview"])

    return {
        "ok": True,
        "book_title": t.book_title,
        "handed_chapters": full,
        # 只拿到试读片段的章数。拆书导入拿到的是残缺正文时，
        # 需要知道有多少内容是截断的。
        "preview_chapters": preview,
        "txt_chars": len(txt),
        "book_import_task": bi_task.model_dump() if hasattr(bi_task, "model_dump") else bi_task,
        "import_task_id": bi_id,
        "note": "已交给拆书导入。后续在该任务的预览里确认并导入。",
    }


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

    重复调用是安全的：会把旧的拆书任务替换成新的一次。
    """
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    user_id = getattr(request.state, "user_id", None) or "local"

    try:
        result = await _hand_to_book_import(t, user_id)
    except HTTPException as e:
        # 交接失败也要留痕，否则从取源侧看不到「试过但没成」
        t.import_status = "failed"
        t.import_message = str(e.detail)[:500]
        await db.commit()
        raise

    t.import_task_id = result["import_task_id"]
    t.import_status = "handed"
    t.import_message = (
        f"已交接：完整 {result['handed_chapters']} 章"
        + (f"，另 {result['preview_chapters']} 章为试读片段"
           if result["preview_chapters"] else "")
        + f"，共 {result['txt_chars']} 字"
    )
    t.imported_at = datetime.utcnow()
    await db.commit()

    return result


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
    # 默认直接抓取并自动交给拆书。
    # 用户在网页上「交给文枢」的本意就是要分析这本书，
    # 让它只登记、还要回到文枢再点两次，是把流程切碎了。
    create_task: bool = Field(default=True, description="是否立即抓取")
    chapter_count: int = Field(default=30, ge=1, le=3000, description="create_task 时抓多少章")
    auto_import: bool = Field(default=True, description="抓完后是否自动交给拆书模块")
    # 取文方式：
    #   server  —— 后端直连抓（未登录只能拿到前 10 章全文）
    #   browser —— 只建任务，正文由浏览器站内取回后回传
    #              （浏览器自带 HttpOnly 登录态，可拿全文）
    # 默认 server，保持既有行为不变。
    fetch_mode: str = Field(default="server", description="server=后端直连 / browser=浏览器代抓")


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
            auto_import=payload.auto_import,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        result["task_id"] = task.id
        result["auto_import"] = payload.auto_import

        if payload.fetch_mode == "browser":
            # 浏览器代抓：不起后端直连抓取，正文由脚本回传。
            # 同时起的坏处是同一批章被抓两遍（白耗配额、更易触发风控），
            # 且直连拿到的试读结果会先落库、再被浏览器结果覆盖。
            task.status = "running"
            task.message = f"等待浏览器代抓 {len(picked)} 章"
            await db.commit()
            result["fetch_mode"] = "browser"
            result["message"] = (
                (result["message"] + " " if result["message"] else "")
                + f"任务已建立，等待浏览器取回 {len(picked)} 章正文。"
                + " 浏览器会带上你的登录态，因此可以取到全文。"
            )
            return result

        asyncio.create_task(_run_task(
            task.id,
            selected_ids=[c.item_id for c in picked],
            interval=1.2,
            max_chapters=None,
        ))
        result["fetch_mode"] = "server"
        result["message"] = (
            (result["message"] + " " if result["message"] else "")
            + f"已开始抓取前 {len(picked)} 章。"
            + ("抓完会自动交给拆书模块。" if payload.auto_import else "")
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


# ============================================================
#  浏览器代抓通道
#
#  背景：番茄的登录凭据 `sessionid` 是 HttpOnly Cookie，
#  页内脚本读不到（document.cookie 拿不到），后端也拿不到。
#  而**浏览器自己**在 fanqienovel.com 源内发请求时，会由浏览器
#  自动带上这些 HttpOnly Cookie —— 所以不必把凭据交出来。
#
#  做法：
#    1. 后端先把要抓的章（item_id 列表）下发
#    2. 用户在番茄页面运行的脚本，对每个 item_id 发同源请求
#       fetch('/reader/' + itemId)，浏览器自动附登录态
#    3. 脚本把原始 HTML 回传，后端用与直连抓取**同一套**解码
#       与截断判定逻辑落库
#
#  这样绕开了两个死结：HttpOnly 读不到、风控签名算不出。
#  浏览器发的是正常页面请求，不涉及逆向签名。
# ============================================================
class BrowserChapterItem(BaseModel):
    item_id: str
    html: str


class BrowserChaptersRequest(BaseModel):
    task_id: str
    chapters: list[BrowserChapterItem] = Field(default_factory=list)


@router.post("/browser-chapters", summary="接收浏览器代抓的正文（用户脚本调用）")
async def submit_browser_chapters(
    payload: BrowserChaptersRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """把浏览器抓到的原始 HTML 解码后并入任务结果。

    为什么由浏览器抓：登录凭据是 HttpOnly Cookie，脚本读不到、
    后端也拿不到；但浏览器在同源页面里发请求时会自动携带，
    因此让浏览器去抓是最省事且不碰凭据的做法。

    解码与截断判定复用 `build_chapter_content`，
    与直连抓取走同一套逻辑，两条路径的状态定义保持一致。

    统计范围以**本任务选定的章**为准（`_resolve_targets`），
    而不是整本目录 —— 否则「只选 12 章」的任务会被
    未选中的章节拉低分母，与其它接口的口径不一致。
    """
    _require_enabled()
    t = await db.get(SourceTask, payload.task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 只接受本任务范围内的章：避免任务之间串内容，也保证统计口径一致
    targets = _resolve_targets(t)
    ref_by_id = {c.item_id: c for c in targets}

    already = _load_chapters(t)
    fresh: dict[str, ChapterContent] = {}
    skipped: list[str] = []

    for item in payload.chapters:
        ref = ref_by_id.get(item.item_id)
        if ref is None:
            skipped.append(item.item_id)
            continue
        fresh[item.item_id] = build_chapter_content(ref, item.html)

    if not fresh:
        raise HTTPException(
            status_code=400,
            detail="没有可用的章节内容（item_id 是否属于该任务选定的范围？）",
        )

    merged = merge_results(already, list(fresh.values()))
    _save_chapters(t, merged)

    # 重算统计（口径：本任务选定的章）
    want = set(ref_by_id.keys())
    ok = sum(1 for c in merged.values()
             if c.status == "ok" and c.item_id in want)
    preview = sum(1 for c in merged.values()
                  if c.status == "preview" and c.item_id in want)
    t.done_count = sum(1 for c in merged.values() if c.item_id in want)
    t.ok_count = ok
    t.failed_count = max(0, len(want) - ok)

    total = len(want)
    if t.status not in ("cancelled",):
        # 还有没抓完的就保持 running，抓完标 completed
        if t.done_count >= total:
            t.status = "completed"
            t.message = (
                f"完成：完整 {ok}/{total} 章"
                + (f"，另 {preview} 章为试读片段" if preview else "")
                + "（浏览器代抓）"
            )
        else:
            t.status = "running"
            t.message = f"浏览器代抓进行中：已收 {t.done_count}/{total} 章"

    await db.commit()

    # 抓完就交给拆书 —— 与后端直连路径行为一致。
    # 两条入口的最终动作必须相同，否则「同样点一下」会得到不同结果。
    if (
        t.status == "completed"
        and ok > 0
        and bool(getattr(t, "auto_import", True))
        and t.import_status != "handed"
    ):
        try:
            res = await _hand_to_book_import(t, t.user_id)
            t.import_task_id = res["import_task_id"]
            t.import_status = "handed"
            t.import_message = (
                f"浏览器代抓完成后自动交接：完整 {res['handed_chapters']} 章"
                + (f"，另 {res['preview_chapters']} 章为试读片段"
                   if res["preview_chapters"] else "")
                + f"，共 {res['txt_chars']} 字"
            )
            t.imported_at = datetime.utcnow()
            logger.info("浏览器代抓任务 %s 已自动交接拆书：%s",
                        t.id, res["import_task_id"])
        except Exception as e:
            t.import_status = "failed"
            t.import_message = f"自动交接失败：{type(e).__name__}: {e}"[:500]
            logger.warning("浏览器代抓任务 %s 自动交接失败：%s", t.id, e)
        await db.commit()

    return {
        "ok": True,
        "task_id": t.id,
        "accepted": len(fresh),
        "accepted_ok": sum(1 for c in fresh.values() if c.status == "ok"),
        "accepted_preview": sum(1 for c in fresh.values() if c.status == "preview"),
        "skipped": skipped[:20],
        "done": t.done_count,
        "total": t.total_chapters,
        "ok_count": t.ok_count,
        "status": t.status,
        "message": t.message,
    }


@router.get("/tasks/{task_id}/browser-plan", summary="浏览器代抓的取文清单")
async def browser_fetch_plan(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    only_missing: bool = Query(default=True, description="只返回尚未拿到全文的章"),
):
    """给浏览器脚本的取文清单。

    只给 item_id 与章节标题，**不含任何凭据**。
    脚本据此在番茄页面内逐章发同源请求。
    """
    _require_enabled()
    t = await db.get(SourceTask, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    targets = _resolve_targets(t)
    results = _load_chapters(t)

    items = []
    for ref in targets:
        c = results.get(ref.item_id)
        if only_missing and c is not None and c.status == "ok":
            continue
        items.append({
            "order": ref.order,
            "item_id": ref.item_id,
            "title": ref.title,
            "volume": ref.volume,
            "current_status": c.status if c else "not_fetched",
        })

    return {
        "task_id": t.id,
        "book_title": t.book_title,
        "book_id": t.book_id,
        "total": t.total_chapters,
        "done": t.done_count,
        "pending": len(items),
        "items": items,
    }


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
            "在番茄小说登录你的账号（不登录只能取到前 10 章）",
            "打开任意书籍页，点右下角「交给文枢」，等取文跑完",
        ],
        "notes": [
            "正文由浏览器在番茄站内取回，登录态始终留在浏览器里，不需要交出 Cookie。",
            "之所以这样设计：番茄的登录凭据 sessionid 是 HttpOnly Cookie，"
            "页内脚本和本机服务都读不到，但浏览器发站内请求时会自动带上。",
            "取文间隔默认 1.2 秒，可在脚本菜单里调整；调太快容易触发风控。",
            "脚本只把书籍标识和正文发到本机 127.0.0.1，不经过任何第三方。",
        ],
        "login_ready": bool(ck.get("has_sessionid")),
        "login_note": ck.get("note", ""),
        # 说明两条取文路径的差别，供界面提示
        "fetch_modes": {
            "browser": "浏览器站内取文：自带登录态，可拿全文（推荐）",
            "server": "本机直连抓取：未登录时仅前 10 章可用，其余为试读片段",
        },
    }
