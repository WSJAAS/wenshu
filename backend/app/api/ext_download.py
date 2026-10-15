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

## 任务状态为什么不入库

整本下载的任务状态**由外部工具自己持有**（内存里，带 `done_retention_ms`）。
本模块只是它的一个薄封装，不做第二份真相 —— 两边各记一份进度，
迟早会不一致。这里所有接口都是「现场去问工具」。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.logger import get_logger
from app.services.ext_downloader import epub_to_txt, ext_downloader_service

router = APIRouter(prefix="/ext-download", tags=["整本下载"])
logger = get_logger(__name__)


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
