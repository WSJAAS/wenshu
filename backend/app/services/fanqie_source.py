"""番茄书源 —— 榜单、详情、目录、正文抓取。

## 定位

这是一个**可关闭的可选模块**。它依赖外部站点，随时可能因对方改版而失效，
因此与核心的本地拆书能力隔离：关掉它，其余功能不受影响。

## 抓取策略

- **串行**，按域名限速（不并发，避免触发风控）
- **退避重试**：失败按指数退避
- **遇验证页暂停**：检测到风控页立即停止批次，而不是硬撞
- 所有请求带浏览器 UA

## 数据来源

| 内容 | 来源 | 是否需解码 |
| :--- | :--- | :--- |
| 榜单列表 | `/rank` 页内嵌 `__INITIAL_STATE__.rank.book_list` | 书名需解码 |
| 书籍详情 | `/page/{bookId}` 内嵌 `__INITIAL_STATE__.page` | 干净 |
| 章节目录 | 同上，`page.chapterListWithVolume` | 干净 |
| 章节正文 | `/reader/{itemId}` 内嵌 `__INITIAL_STATE__.reader.chapterData.content` | **必须解码** |

书籍详情与目录是服务端渲染的干净文本；只有正文带字体混淆。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from app.logger import get_logger
from app.services.fanqie_decoder import count_pua, extract_body

logger = get_logger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"

# ============================================================
#  限速参数
#
#  实测单章 0.2~0.4s，但**不能按实测速度跑**。这里的默认值偏保守：
#  出错重试的代价远高于慢一点。可按需在设置里调。
# ============================================================
DEFAULT_INTERVAL_SECONDS = 1.2      # 请求间隔基数
DEFAULT_INTERVAL_JITTER = 0.5       # 随机抖动上限
DEFAULT_MAX_RETRIES = 3             # 单请求最多重试
DEFAULT_TIMEOUT = 30                # 单请求超时
DEFAULT_BACKOFF_BASE = 2.0          # 退避基数（秒）

# 单章抓取最多等待多久，超时算失败
CHAPTER_FETCH_TIMEOUT = 60


# ============================================================
#  异常
# ============================================================
class FanqieError(Exception):
    """书源操作失败。"""


class BlockedError(FanqieError):
    """遇到风控或验证页 —— 应立即停止批次，继续撞只会更糟。"""


class NotFoundError(FanqieError):
    """资源不存在（书籍/章节被下架或 ID 错误）。"""


# ============================================================
#  数据结构
# ============================================================
@dataclass
class BookInfo:
    """书籍元信息。详情页取，字段干净。"""

    book_id: str
    title: str = ""
    author: str = ""
    abstract: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    read_count: int = 0
    status: str = ""          # 完结 / 连载
    cover_url: str = ""
    source_url: str = ""
    last_chapter_title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "author": self.author,
            "abstract": self.abstract,
            "category": self.category,
            "tags": self.tags,
            "word_count": self.word_count,
            "read_count": self.read_count,
            "status": self.status,
            "cover_url": self.cover_url,
            "source_url": self.source_url,
            "last_chapter_title": self.last_chapter_title,
        }


@dataclass
class ChapterRef:
    """目录里的一条章节。

    **关于 `is_locked` / `need_pay`：**

    实测三本书（550/337/1288 章）发现，`isChapterLock=True` 的数量恰好是
    「总章数 - 10」，即它标记的是「不在免费试读段内」，**不是「不可读」**。

    同时 `needPay` 恒为 0，`isPaidPublication` / `isPaidStory` 恒为 False。

    因此这两个字段**不能用来预判章节能否抓取**，只能作为参考。
    真实可读性必须**实际请求后才知道** —— 见 `FanqieClient.fetch_chapter`。
    """

    item_id: str
    title: str
    volume: str = ""
    order: int = 0
    is_locked: bool = False      # 仅参考：不在免费试读段
    need_pay: bool = False       # 仅参考，实测恒为 False

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "volume": self.volume,
            "order": self.order,
            "is_locked": self.is_locked,
            "need_pay": self.need_pay,
        }


@dataclass
class ChapterContent:
    """抓取并还原后的章节正文。"""

    item_id: str
    title: str = ""
    text: str = ""
    volume: str = ""
    order: int = 0
    # 逐章状态：ok / locked / unavailable / garbled / failed
    status: str = "ok"
    error: str = ""
    pua_before: int = 0
    pua_after: int = 0
    chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "volume": self.volume,
            "order": self.order,
            "status": self.status,
            "error": self.error,
            "pua_before": self.pua_before,
            "pua_after": self.pua_after,
            "chars": self.chars,
            "text": self.text,
        }


# ============================================================
#  HTTP
# ============================================================
#
# 限速时钟的归属：早期实现用模块级全局 `_last_request_at`，
# 所有任务共享同一个时钟。那会让两个任务互相干扰 ——
# 后发起的任务要等前一个任务的间隔期满，任务之间不再独立。
#
# 现在时钟挂在 RateLimiter 实例上，每个抓取任务持有一个独立实例，
# 各自计自己的间隔。这样：
#   - 任务之间互不等待（符合「任务互不干扰」）
#   - 单任务内部仍然严格串行限速
#
# 注意线程安全：同一个 limiter 可能被 asyncio.to_thread 的工作线程调用，
# 因此加锁。不同 limiter 之间无锁竞争。
class RateLimiter:
    """独立限速器。每个抓取任务持有一个。"""

    __slots__ = ("interval", "jitter", "_last_at", "_lock")

    def __init__(self, interval: float, jitter: float) -> None:
        self.interval = max(0.0, float(interval))
        self.jitter = max(0.0, float(jitter))
        self._last_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """阻塞到距上次请求满 interval（含抖动）。"""
        with self._lock:
            now = time.monotonic()
            gap = self.interval + random.uniform(0, self.jitter)
            wait = gap - (now - self._last_at)
            if wait > 0:
                time.sleep(wait)
            self._last_at = time.monotonic()

    def to_dict(self) -> dict[str, float]:
        return {"interval": self.interval, "jitter": self.jitter}


def _looks_blocked(html: str) -> bool:
    """识别风控/验证页。"""
    if not html or len(html) < 500:
        return False
    markers = (
        "验证码", "captcha", "访问异常", "请稍后再试",
        "系统检测到", "verify", "人机验证",
    )
    head = html[:4000]
    return any(m in head for m in markers)


def _fetch_html_sync(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """取一个页面。一次性请求，不含重试（重试在上层）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # 番茄可能不是 utf-8，但实测是
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFoundError(f"资源不存在: {url}") from e
        if e.code in (403, 429):
            raise BlockedError(f"被拒绝（HTTP {e.code}）: {url}") from e
        raise FanqieError(f"HTTP {e.code}: {url}") from e
    except urllib.error.URLError as e:
        raise FanqieError(f"网络错误: {e.reason}") from e


def _extract_state(html: str) -> dict[str, Any] | None:
    """从页面里取出 __INITIAL_STATE__。

    结束位置不固定（后面可能还有别的脚本），用「从右往左找最大可解析前缀」定位。
    """
    key = "window.__INITIAL_STATE__"
    start = html.find(key)
    if start < 0:
        return None
    brace = html.find("{", start)
    if brace < 0:
        return None
    blob = html[brace:]
    # 限制回溯步数，避免页面异常时长时间空转
    longest = min(len(blob), 3_000_000)
    tried = 0
    for end in range(longest, 0, -1):
        if blob[end - 1] != "}":
            continue
        tried += 1
        if tried > 5000:
            break
        try:
            return json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
    return None


# ============================================================
#  解析
# ============================================================
def _to_int(v: Any, default: int = 0) -> int:
    """稳妥转整数。榜单里 readCount 出现过 "0" 与 "289091" 并存，字段名不可信。"""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s.isdigit():
            return int(s)
    return default


def _pick_read_count(*candidates: Any) -> int:
    """从多个候选字段里挑出可信的「在读人数」。

    实测榜单里 `readCount` 与 `read_count` 会同时出现，
    且**有时 readCount 是 "0" 而 read_count 有真实值**。
    直接用 readCount 会把一本在榜的书显示成 0 热度。

    策略：取所有候选里的**最大值**。理由是字段缺失记 0、
    而真实热度不会是 0（能进榜就有数据），因此最大者是可信的那个。
    """
    best = 0
    for c in candidates:
        v = _to_int(c)
        if v > best:
            best = v
    return best


def parse_categories(raw: Any) -> tuple[str, list[str]]:
    """解析 categoryV2，返回 (主分类, 全部标签)。

    `categoryV2` 是一段 **JSON 字符串**，内容是分类对象数组，
    每项含 `Name` 与 `MainCategory` 标记。顶层 `category` 字段实测为空串，
    所以分类信息只能从这里取。

    结构示例（截断）：
        [{"ObjectId":1139,"Dim":10,"Name":"古风世情","MainCategory":true,...},
         {"ObjectId":5,"Name":"古代言情","MainCategory":false,...}, ...]
    """
    if not isinstance(raw, str) or not raw.strip():
        return "", []

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        # 解析不了就别硬塞原始 JSON 进标签
        logger.warning("categoryV2 不是合法 JSON，已忽略")
        return "", []

    if not isinstance(items, list):
        return "", []

    names: list[str] = []
    main = ""
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("Name") or "").strip()
        if not name:
            continue
        if name not in names:
            names.append(name)
        if it.get("MainCategory") and not main:
            main = name

    return main, names


def _format_count(n: int) -> str:
    """把大数字格式化成便于阅读的形式，供界面展示。"""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def parse_book_page(html: str, book_id: str) -> tuple[BookInfo, list[ChapterRef]]:
    """解析详情页，返回书籍信息与章节目录。"""
    state = _extract_state(html)
    if not state:
        raise FanqieError("详情页结构无法解析（可能已改版）")

    page = state.get("page")
    if not isinstance(page, dict):
        raise FanqieError("详情页缺少 page 数据")

    main_cat, all_cats = parse_categories(page.get("categoryV2"))

    book = BookInfo(
        book_id=str(page.get("bookId") or book_id),
        title=str(page.get("bookName") or ""),
        author=str(page.get("author") or ""),
        abstract=str(page.get("abstract") or ""),
        category=main_cat or str(page.get("category") or ""),
        tags=all_cats,
        word_count=_to_int(page.get("wordNumber")),
        read_count=_to_int(page.get("readCount")),
        cover_url=str(page.get("thumbUri") or ""),
        source_url=f"{BASE}/page/{book_id}",
        last_chapter_title=str(page.get("lastChapterTitle") or ""),
    )

    # 状态来自 creationStatus，不是 page.status。
    #
    # 实测结论（20 本抽样，用页面 DOM 文案做基准交叉验证）：
    #   creationStatus = "1" → 连载中
    #   creationStatus = "0" → 已完结
    #
    # 注意 page.status 是**另一个字段，实测恒为 "1"**，不能用于判断状态；
    # 它和 creationStatus 不是一回事，别混用。
    _CS_MAP = {"1": "连载", "0": "完结"}
    book.status = _CS_MAP.get(str(page.get("creationStatus")), "")

    chapters: list[ChapterRef] = []
    volumes = page.get("chapterListWithVolume") or []
    order = 0
    for vol in volumes:
        if not isinstance(vol, list):
            continue
        for ch in vol:
            if not isinstance(ch, dict):
                continue
            item_id = str(ch.get("itemId") or "")
            if not item_id:
                continue
            order += 1
            chapters.append(ChapterRef(
                item_id=item_id,
                title=str(ch.get("title") or "").strip(),
                volume=str(ch.get("volume_name") or "").strip(),
                order=order,
                is_locked=bool(ch.get("isChapterLock")),
                need_pay=bool(ch.get("needPay") or ch.get("isPaidStory")),
            ))

    return book, chapters


def parse_rank_page(html: str) -> tuple[list[dict[str, Any]], int]:
    """解析榜单页，返回 (条目列表, 平台声明总数)。

    榜单页的书名**带字体混淆**，这里就地用码表还原。
    不过码表是针对正文区的，榜单书名可能用另一套映射（实测两者字体名相同
    但映射不同），所以还原结果标记为 `title_decoded`；调用方应以
    **详情页的书名为准**，榜单书名仅作兜底。

    覆盖范围：首屏通常只有 10 本，而平台会声明一个总数（如 100）。
    返回值把两者分开，调用方必须如实展示，不能当作完整榜单。
    """
    from app.services.fanqie_decoder import decode

    state = _extract_state(html)
    if not state:
        raise FanqieError("榜单页结构无法解析（可能已改版）")

    rank = state.get("rank")
    if not isinstance(rank, dict):
        raise FanqieError("榜单页缺少 rank 数据")

    books = rank.get("book_list") or []
    total = _to_int(rank.get("total")) if "total" in rank else 0

    items: list[dict[str, Any]] = []
    for b in books:
        if not isinstance(b, dict):
            continue

        raw_title = str(b.get("bookName") or "")
        # 榜单书名的混淆用的是另一套映射，解出来不一定对。
        # 保留原始值，另存一个尝试还原的结果供参考。
        decoded = decode(raw_title)

        items.append({
            "book_id": str(b.get("bookId") or ""),
            "title_raw": raw_title,                  # 原始（可能带混淆）
            "title_decoded": decoded.text,           # 尝试还原（可能不准）
            "title": "",                             # 以详情页为准，此处留空
            "author": str(b.get("author") or ""),
            "cover_url": str(b.get("thumbUri") or ""),
            # 榜单里 readCount 与 read_count 会并存且不一定一致，
            # 取其中可信的那个（详见 _pick_read_count）
            "read_count": _pick_read_count(b.get("readCount"), b.get("read_count")),
            "read_count_raw": _to_int(b.get("readCount")),
            "read_count_alt": _to_int(b.get("read_count")),
            "word_count": _pick_read_count(b.get("wordNumber"), b.get("word_count")),
            "abstract": str(b.get("abstract") or ""),
            "rank_diff": b.get("rankPosDiff"),
        })

    return items, total


def parse_reader_page(html: str) -> tuple[str, str]:
    """解析阅读页，返回 (标题, 正文HTML)。

    正文 HTML 仍需解码 —— 见 fanqie_decoder。
    """
    state = _extract_state(html)
    if not state:
        raise FanqieError("阅读页结构无法解析（可能已改版）")

    reader = state.get("reader")
    if not isinstance(reader, dict):
        raise FanqieError("阅读页缺少 reader 数据")

    cd = reader.get("chapterData")
    if not isinstance(cd, dict):
        raise FanqieError("阅读页缺少 chapterData")

    title = str(cd.get("title") or "").strip()
    content = cd.get("content")
    if not isinstance(content, str) or not content.strip():
        # 锁定或付费章
        if cd.get("isChapterLock") or cd.get("needPay"):
            raise FanqieError("章节被锁定")
        raise FanqieError("章节正文为空")

    return title, content


# ============================================================
#  抓取器
# ============================================================
class FanqieClient:
    """同步实现的抓取器。

    内部用同步 urllib，由调用方用 `asyncio.to_thread` 包起来即可异步使用。
    这样避免引入 aiohttp 依赖，也便于单独调试。
    """

    def __init__(
        self,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        jitter: float = DEFAULT_INTERVAL_JITTER,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = DEFAULT_TIMEOUT,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> None:
        self.interval = interval
        self.jitter = jitter
        self.max_retries = max_retries
        self.timeout = timeout
        self.backoff_base = backoff_base
        self.request_count = 0
        # 每个客户端（即每个抓取任务）持有独立限速器，
        # 任务之间不共享时钟，互不等待。
        self.limiter = RateLimiter(interval, jitter)

    def _get(self, url: str) -> str:
        """带限速与退避的取页。"""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.request_count += 1
            try:
                html = _fetch_html_sync(url, self.timeout)
            except BlockedError:
                # 风控不重试：继续撞只会更糟
                raise
            except NotFoundError:
                # 404 不重试
                raise
            except FanqieError as e:
                last_error = e
            else:
                if _looks_blocked(html):
                    raise BlockedError("检测到验证页，已暂停")
                return html

            if attempt < self.max_retries:
                wait = self.backoff_base ** (attempt + 1) + random.uniform(0, 1)
                logger.warning(
                    "请求失败，第 %d 次重试，等待 %.1fs: %s",
                    attempt + 1, wait, url,
                )
                time.sleep(wait)

        raise FanqieError(f"重试 {self.max_retries} 次后仍失败: {last_error}")

    # ---------- 榜单 ----------
    def fetch_rank(self) -> tuple[list[dict[str, Any]], int]:
        """取榜单首屏。返回 (条目, 平台声明总数)。

        注意：首屏通常只有 10 本，而平台声明 100 本。
        调用方必须把「已取得数 / 声明总数」如实展示，
        不能当作完整榜单，也不要在分析里声称覆盖全榜。
        """
        html = self._get(f"{BASE}/rank")
        return parse_rank_page(html)

    # ---------- 详情 ----------
    def fetch_book(self, book_id: str) -> tuple[BookInfo, list[ChapterRef]]:
        """取书籍详情与完整目录。"""
        html = self._get(f"{BASE}/page/{book_id}")
        return parse_book_page(html, book_id)

    # ---------- 正文 ----------
    def fetch_chapter(self, ref: ChapterRef) -> ChapterContent:
        """取单章正文并解码。

        逐章状态判定：
          - ok           解码后无残留
          - locked       章节被锁定或需付费
          - unavailable  正文为空或页面结构异常
          - garbled      解码后仍有私用区字符（码表可能过期）
          - failed       网络或其它错误
        """
        result = ChapterContent(
            item_id=ref.item_id,
            title=ref.title,
            volume=ref.volume,
            order=ref.order,
        )

        try:
            html = self._get(f"{BASE}/reader/{ref.item_id}")
            title, content_html = parse_reader_page(html)
        except BlockedError:
            # 风控要向上抛，让批次停止
            raise
        except NotFoundError as e:
            result.status = "unavailable"
            result.error = str(e)
            return result
        except FanqieError as e:
            msg = str(e)
            if "锁定" in msg:
                result.status = "locked"
            else:
                result.status = "unavailable"
            result.error = msg
            return result
        except Exception as e:  # 兜底，不让单章失败弄挂整批
            result.status = "failed"
            result.error = f"{type(e).__name__}: {e}"
            return result

        result.title = title or ref.title
        result.pua_before = count_pua(content_html)

        decoded = extract_body(content_html)
        result.text = decoded.text
        result.pua_after = decoded.unresolved
        result.chars = len(decoded.text)

        if decoded.unresolved > 0:
            # 有残留说明表可能过期 —— 标为乱码，不进入后续分析
            result.status = "garbled"
            result.error = (
                f"解码后仍有 {decoded.unresolved} 个私用区字符，"
                "码表可能已过期，请核对字体是否更换"
            )
        elif not decoded.text.strip():
            result.status = "unavailable"
            result.error = "解码后正文为空"
        else:
            result.status = "ok"

        return result


# ============================================================
#  异步包装
# ============================================================
async def fetch_rank_async(client: FanqieClient) -> tuple[list[dict[str, Any]], int]:
    return await asyncio.to_thread(client.fetch_rank)


async def fetch_book_async(client: FanqieClient, book_id: str):
    return await asyncio.to_thread(client.fetch_book, book_id)


async def fetch_chapter_async(client: FanqieClient, ref: ChapterRef) -> ChapterContent:
    return await asyncio.to_thread(client.fetch_chapter, ref)


# ============================================================
#  批量抓取（串行 + 可恢复）
# ============================================================
@dataclass
class BatchProgress:
    """一批抓取的进度快照。"""

    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    # 逐章状态：ok / locked / unavailable / garbled / failed / skipped
    statuses: dict[str, int] = field(default_factory=dict)
    current_title: str = ""
    stopped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "done": self.done,
            "ok": self.ok,
            "failed": self.failed,
            "statuses": self.statuses,
            "current_title": self.current_title,
            "stopped_reason": self.stopped_reason,
            "percent": round(self.done / self.total * 100, 1) if self.total else 0.0,
        }


class BatchFetcher:
    """按章串行抓取，支持断点续跑。

    设计要点：

    - **串行**：不并发。实测单章 0.2~0.4s，但风控代价远高于慢。
    - **可恢复**：已成功的章跳过，只抓缺的。中断后重跑不会重复请求。
    - **遇风控即停**：检测到验证页立即结束批次，把已抓到的保留下来，
      而不是继续撞。`progress.stopped_reason` 会说明原因。
    - **逐章状态**：每章的结果都是明确状态，不用异常表达控制流。
    """

    def __init__(self, client: FanqieClient) -> None:
        self.client = client

    def fetch_range(
        self,
        chapters: list[ChapterRef],
        *,
        already: dict[str, ChapterContent] | None = None,
        on_progress=None,
        should_stop=None,
        max_chapters: int | None = None,
    ) -> tuple[list[ChapterContent], BatchProgress]:
        """抓取指定章节。

        Args:
            chapters: 要抓的章节（已按需要筛选好）
            already: 已有的结果，按 item_id 索引。命中的会跳过，实现断点续跑。
            on_progress: 每完成一章调用一次，收到 BatchProgress
            should_stop: 每章前调用，返回 True 则停止（用于用户取消）
            max_chapters: 本次最多抓多少章

        Returns:
            (本章结果列表, 进度)。结果里**只含本次实际处理的章节**，
            调用方应把 `already` 与本次结果合并后使用。
        """
        already = already or {}
        results: list[ChapterContent] = []
        progress = BatchProgress(total=len(chapters))

        todo = [c for c in chapters if c.item_id not in already]
        skipped_existing = len(chapters) - len(todo)

        if skipped_existing:
            progress.done += skipped_existing
            progress.statuses["skipped"] = skipped_existing

        if max_chapters is not None:
            todo = todo[:max_chapters]

        for ref in todo:
            if should_stop is not None and should_stop():
                progress.stopped_reason = "用户取消"
                break

            progress.current_title = ref.title

            try:
                content = self.client.fetch_chapter(ref)
            except BlockedError as e:
                # 风控：立即停，保留已有结果
                progress.stopped_reason = f"遇到风控，已暂停：{e}"
                logger.warning("批量抓取被风控中断：%s", e)
                break
            except Exception as e:
                content = ChapterContent(
                    item_id=ref.item_id,
                    title=ref.title,
                    volume=ref.volume,
                    order=ref.order,
                    status="failed",
                    error=f"{type(e).__name__}: {e}",
                )

            results.append(content)
            progress.done += 1
            st = content.status
            progress.statuses[st] = progress.statuses.get(st, 0) + 1
            if st == "ok":
                progress.ok += 1
            else:
                progress.failed += 1

            if on_progress is not None:
                try:
                    on_progress(progress, content)
                except Exception as e:
                    logger.warning("进度回调异常（已忽略）: %s", e)

        return results, progress


def merge_results(
    already: dict[str, ChapterContent],
    fresh: list[ChapterContent],
) -> dict[str, ChapterContent]:
    """合并已有结果与本次抓取结果。本次的覆盖已有的同名项。"""
    merged = dict(already)
    for c in fresh:
        merged[c.item_id] = c
    return merged


def build_missing_report(
    chapters: list[ChapterRef],
    results: dict[str, ChapterContent],
) -> dict[str, Any]:
    """生成缺章清单与状态汇总。

    这是给用户看的「哪些没拿到、为什么」。
    没有这张表，用户无法判断分析报告覆盖了多少内容。
    """
    by_status: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []
    ok_count = 0

    for ref in chapters:
        c = results.get(ref.item_id)
        if c is None:
            entry = {
                "order": ref.order,
                "item_id": ref.item_id,
                "title": ref.title,
                "volume": ref.volume,
                "status": "not_fetched",
                "error": "尚未抓取",
            }
        else:
            entry = {
                "order": ref.order,
                "item_id": ref.item_id,
                "title": c.title or ref.title,
                "volume": c.volume or ref.volume,
                "status": c.status,
                "error": c.error,
                "chars": c.chars,
            }
            if c.status == "ok":
                ok_count += 1

        by_status.setdefault(entry["status"], []).append(entry)
        if entry["status"] != "ok":
            missing.append(entry)

    return {
        "total": len(chapters),
        "ok": ok_count,
        "missing_count": len(missing),
        "by_status": {k: len(v) for k, v in by_status.items()},
        # 缺章清单可能很长，界面上分页展示；这里全量返回供落盘
        "missing": missing,
    }


def results_to_txt(
    chapters: list[ChapterRef],
    results: dict[str, ChapterContent],
    *,
    include_missing_note: bool = True,
) -> str:
    """把抓取结果拼成 TXT，供现有拆书导入使用。

    **保留章节顺序与段落结构**：
      - 按目录顺序输出
      - 每章一个标题行
      - 章内段落用空行分隔（解码时已保留）

    缺失的章**不会静默跳过**：会在末尾附一段清单，
    让拆书分析知道覆盖范围。
    """
    parts: list[str] = []
    missing: list[str] = []

    for ref in chapters:
        c = results.get(ref.item_id)
        if c is None or c.status != "ok" or not c.text.strip():
            reason = c.status if c else "not_fetched"
            missing.append(f"第{ref.order}章 {ref.title}（{reason}）")
            continue

        # 标题行：保留卷名便于追溯
        header = c.title or ref.title
        parts.append(header)
        parts.append("")
        parts.append(c.text)
        parts.append("")

    body = "\n".join(parts).strip()

    if include_missing_note and missing:
        note = [
            "",
            "=" * 40,
            f"【抓取说明】共 {len(chapters)} 章，成功 {len(chapters) - len(missing)} 章，",
            f"未取得 {len(missing)} 章。以下内容未包含在正文中：",
            "",
        ]
        note.extend(missing[:200])
        if len(missing) > 200:
            note.append(f"... 另有 {len(missing) - 200} 章未列出")
        body = body + "\n" + "\n".join(note)

    return body


__all__ = [
    "BASE",
    "FanqieError",
    "BlockedError",
    "NotFoundError",
    "BookInfo",
    "ChapterRef",
    "ChapterContent",
    "BatchProgress",
    "BatchFetcher",
    "FanqieClient",
    "parse_book_page",
    "parse_rank_page",
    "parse_reader_page",
    "parse_categories",
    "merge_results",
    "build_missing_report",
    "results_to_txt",
    "fetch_rank_async",
    "fetch_book_async",
    "fetch_chapter_async",
    "DEFAULT_INTERVAL_SECONDS",
]
