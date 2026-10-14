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
import os
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

# 未登录时平台免费开放前若干章的全文，在此之后只给试读片段。
#
# 实测《惹金枝》：第 1~10 章为全文（约 2100 字），第 11 章起 content
# 恒为约 200 字符，且 isChapterLock 恰好覆盖「总章数 − 10」章。
# 三本书（550 / 337 / 1288 章）的差值都是 10，故取 10。
#
# 这个值只用于**预估**还剩多少章拿不到全文，以及给用户的提示文案；
# 真正的可读性判定仍以每章的 is_locked / 长度比为准，不依赖这个常量。
FREE_CHAPTER_COUNT = 10

# 截断判定阈值：实际字数低于「平台申报字数 × 该比例」即视为只拿到预览。
#
# 取 0.6 而不是更贴近 1.0 的值，是因为正文长度本身有浮动
# （实测同一章解码后与申报值差几个字很常见），但截断的差距
# 是数量级的 —— 未登录时 locked 章的 content 恒为 200 个字符，
# 而申报值在 2000 上下，比例约 0.08。0.6 两者都能清楚分开，
# 又不会被正常浮动误伤。
TRUNCATION_RATIO = 0.6

# 单章抓取最多等待多久，超时算失败
CHAPTER_FETCH_TIMEOUT = 60


# ============================================================
#  登录态
#
#  未登录时，`isChapterLock=true` 的章只能拿到约 200 字符的预览
#  （页面提示「会员登录后，可在网页畅读全文」）。要取全文就得带上
#  登录后的 Cookie。
#
#  Cookie 从哪来：用户在浏览器登录番茄后，从开发者工具里复制。
#  这里只做「原样转发」，不解密、不代管账号；存哪由调用方决定
#  （本项目存在工作区配置目录，不入库、不进 git）。
# ============================================================
def normalize_cookie(raw: str) -> str:
    """清理 Cookie 串里的杂字符。

    用户从浏览器或开发者工具复制 Cookie 时，容易带进两类东西：

      1. 不可见格式字符（零宽空格 U+200B、BOM U+FEFF 等）。
         它们会让 Cookie 名变成 `"\\u200bcsrf_session_id"`，
         服务端认不出，表现为「配了登录态却抓不到」。

      2. 整行复制时带上的 `Cookie:` 前缀，或整体包裹的引号。

    段间用 `"; "` 分隔时分号后本来就有一个普通空格，那是正常的，
    这里统一成 `"; "` 即可，不需要特殊处理。

    注意：这两个问题都不是「带登录态反而解析失败」的原因 ——
    那个是状态里含 JS 的 `undefined`，见 _js_literal_to_json。
    保留本函数是为了让 Cookie 名干净、便于排查与统计。
    """
    if not raw:
        return ""
    # 不可见格式字符与软连字符去掉
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00ad"):
        raw = raw.replace(ch, "")
    # 全角空格与不换行空格还原成普通空格，便于按 ; 切分
    raw = raw.replace("\u3000", " ").replace("\u00a0", " ")
    s = raw.strip()
    # 去掉可能被一起复制进来的 "Cookie:" 前缀
    if s.lower().startswith("cookie:"):
        s = s[7:].strip()
    # 去掉整体包裹的引号
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    # 各段去掉首尾空白，再统一用 "; " 连接
    parts = [p.strip() for p in s.split(";") if p.strip()]
    return "; ".join(parts)


def load_login_cookie() -> str:
    """读取用户配置的登录 Cookie。

    顺序：环境变量 FANQIE_COOKIE > 配置文件。
    返回空字符串表示未配置（此时只能取到免费章）。

    读出来的值一律经过 normalize_cookie —— 从浏览器复制时
    常带入不可见字符，不清理会导致「看起来配了却抓不到」。
    """
    env = os.getenv("FANQIE_COOKIE", "").strip()
    if env:
        return normalize_cookie(env)

    path = _cookie_file_path()
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return normalize_cookie(f.read())
        except OSError as e:
            logger.warning("读取登录 Cookie 失败: %s", e)
    return ""


def _cookie_file_path() -> str:
    """Cookie 存放位置。刻意放在代码目录之外，避免误提交。"""
    data_dir = os.getenv("MUMU_DATA_DIR", "").strip()
    if data_dir:
        return os.path.join(data_dir, "fanqie_cookie.txt")
    return ""


def save_login_cookie(cookie: str) -> str:
    """保存登录 Cookie，返回落盘路径。空串表示清除。"""
    path = _cookie_file_path()
    if not path:
        raise FanqieError(
            "未配置 MUMU_DATA_DIR，无法确定 Cookie 存放位置"
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write((cookie or "").strip())
    return path


def cookie_status() -> dict[str, Any]:
    """登录态概况 —— 只报有没有、长什么样，不回显完整值。"""
    ck = load_login_cookie()
    if not ck:
        return {
            "configured": False,
            "has_sessionid": False,
            "names": [],
            "note": "未配置登录态，锁定章只能取到约 200 字符的试读片段。",
        }
    names = []
    for part in ck.split(";"):
        if "=" in part:
            n = part.split("=", 1)[0].strip()
            if n:
                names.append(n)
    return {
        "configured": True,
        "has_sessionid": any(n in ("sessionid", "sessionid_ss") for n in names),
        "names": names[:30],
        "note": "已配置登录态。",
    }


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
    # 逐章状态：ok / preview / locked / unavailable / garbled / failed
    #
    # preview 是必须单独存在的一档。早先的实现没有它，把
    # 「正文被服务端截断成 200 字符预览」也记成 ok ——
    # 于是 550 章的书导出后只有 10 万字却「零失败」，
    # 错误被推迟到用户读完才发现。状态必须如实反映拿到的是什么。
    status: str = "ok"
    error: str = ""
    pua_before: int = 0
    pua_after: int = 0
    chars: int = 0
    # 截断判定所需的两个事实：服务端标记 + 平台申报字数
    is_locked: bool = False
    claimed_words: int = 0

    @property
    def is_truncated(self) -> bool:
        """是否只拿到预览（内容明显短于平台申报）。"""
        return self.status == "preview"

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
            "is_locked": self.is_locked,
            "claimed_words": self.claimed_words,
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


def _fetch_html_sync(url: str, timeout: int = DEFAULT_TIMEOUT,
                     cookie: str = "") -> str:
    """取一个页面。一次性请求，不含重试（重试在上层）。

    cookie 非空时按登录态请求。未登录时锁定章只会返回试读片段，
    页面上写着「会员登录后，可在网页畅读全文」。
    """
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{BASE}/",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
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


def _js_literal_to_json(text: str) -> str:
    """把 JS 对象字面量里的非 JSON 值换成合法 JSON。

    平台内嵌的状态是 `window.__INITIAL_STATE__ = {...}`，语法上是
    **JavaScript 而不是 JSON**。实测带登录态访问阅读页时会出现：

        {"libra":{"paywall_reverse_config":{"libra":undefined,"ready":true}}}

    这个 `undefined` 是合法 JS、非法 JSON，`json.loads` 会直接报错，
    导致整页状态取不出来 —— 症状是「带了登录态反而什么都解析不出」，
    很容易被误判成风控。

    只做三类替换，都是 JS 专有而 JSON 没有的值：
      undefined → null
      NaN / Infinity / -Infinity → null

    替换按字符串感知处理：跳过被引号包裹的部分，
    避免正文里恰好出现 "undefined" 这个词时被误改。
    """
    if not text or ("undefined" not in text and "NaN" not in text
                    and "Infinity" not in text):
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    quote = ""       # 当前所处的引号
    while i < n:
        ch = text[i]

        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            continue

        matched = False
        for token, repl in (
            ("-Infinity", "null"),
            ("Infinity", "null"),
            ("undefined", "null"),
            ("NaN", "null"),
        ):
            if text.startswith(token, i):
                # 确认是独立词：前一个字符不能是标识符的一部分
                prev = out[-1] if out else ""
                if prev and (prev.isalnum() or prev in "_$"):
                    continue
                # 后一个字符同理
                nxt = text[i + len(token)] if i + len(token) < n else ""
                if nxt and (nxt.isalnum() or nxt in "_$"):
                    continue
                out.append(repl)
                i += len(token)
                matched = True
                break
        if matched:
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _extract_state(html: str) -> dict[str, Any] | None:
    """从页面里取出 __INITIAL_STATE__。

    结束位置不固定（后面可能还有别的脚本），用「从右往左找最大可解析前缀」定位。

    解析前先做 JS→JSON 的值替换，否则带登录态时会因为
    状态里出现 `undefined` 而整页解析失败。
    """
    key = "window.__INITIAL_STATE__"
    start = html.find(key)
    if start < 0:
        return None
    brace = html.find("{", start)
    if brace < 0:
        return None
    blob = _js_literal_to_json(html[brace:])
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
            "title_decoded": decoded.text,           # 还原结果
            # 默认用还原结果，而不是留空。
            #
            # 详情页的书名是干净的，所以调用方若做详情补取会用干净值覆盖这里。
            # 但补取是可选的（enrich=false）—— 留空会让调用方拿到一批
            # 无名书目，界面只能显示空白，比显示一个可能有个别错字的书名更糟。
            # 需要区分时看 title_may_be_garbled。
            "title": decoded.text or raw_title,
            # 还原后仍含私用区字符，说明这个书名没能完全还原
            "title_may_be_garbled": (
                decoded.unresolved > 0
                or any(0xE000 <= ord(c) <= 0xF8FF for c in (decoded.text or ""))
            ),
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


def parse_reader_page(html: str) -> tuple[str, str, dict[str, Any]]:
    """解析阅读页，返回 (标题, 正文HTML, chapterData 原始字段)。

    正文 HTML 仍需解码 —— 见 fanqie_decoder。

    第三个返回值给出 `isChapterLock` / `chapterWordNumber` 等元数据，
    调用方据此判定「拿到的是全文还是预览」。只看 content 长度会误判：
    未登录时 locked 章的 content 恒为 200 字符，看起来像是拿到了内容。
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

    return title, content, cd


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
        cookie: str | None = None,
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
        # 登录态：显式传入优先，否则读配置。带上它才能取到
        # 锁定章的全文，否则只有约 200 字符的试读片段。
        self.cookie = load_login_cookie() if cookie is None else cookie

    def _get(self, url: str) -> str:
        """带限速与退避的取页。"""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.request_count += 1
            try:
                html = _fetch_html_sync(url, self.timeout, self.cookie)
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

    # ---------- 反查 ----------
    def resolve_book_by_item(self, item_id: str) -> str:
        """从章节 ID 反查所属书籍 ID。

        阅读页交接时浏览器只给得出 itemId（URL 里的那串数字），
        需要换算成 bookId 才能取目录。

        实测阅读页的 `reader.chapterData.bookId` 直接给出所属书，
        不需要额外请求目录接口。取不到时返回空串。
        """
        if not item_id:
            return ""
        try:
            html = self._get(f"{BASE}/reader/{item_id}")
            state = _extract_state(html)
        except FanqieError:
            return ""

        if not state:
            return ""
        reader = state.get("reader")
        if not isinstance(reader, dict):
            return ""
        cd = reader.get("chapterData")
        if not isinstance(cd, dict):
            return ""

        bid = cd.get("bookId")
        return str(bid).strip() if bid else ""

    # ---------- 正文 ----------
    def fetch_chapter(self, ref: ChapterRef) -> ChapterContent:
        """取单章正文并解码。

        网络层只负责把 HTML 拿回来，解析与判定统一走
        `build_chapter_content` —— 浏览器代抓通道要用同一套判定，
        两条路径的状态定义必须一致。
        """
        result = ChapterContent(
            item_id=ref.item_id,
            title=ref.title,
            volume=ref.volume,
            order=ref.order,
        )

        try:
            html = self._get(f"{BASE}/reader/{ref.item_id}")
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

        return build_chapter_content(ref, html, fallback=result)


def build_chapter_content(
    ref: ChapterRef,
    html: str,
    fallback: ChapterContent | None = None,
) -> ChapterContent:
    """从阅读页 HTML 构造章节结果（解析 + 解码 + 截断判定）。

    这是**唯一**的正文判定实现。两条取文路径都走它：

      - `FanqieClient.fetch_chapter`：本机直连抓取（未登录只能拿试读）
      - `POST /api/source/browser-chapters`：浏览器代抓回传
        （浏览器自带 HttpOnly 登录态，可拿到全文）

    抽出来是为了避免两份判定逻辑各自演化。上一版的教训是：
    「服务端截断成 200 字符预览」曾被记成 ok，导致 550 章的书
    导出后正文少了 91% 却报「零失败」。

    ## 逐章状态
      - ok           拿到全文（长度与平台申报相符，且无残留私用区）
      - preview      只拿到试读预览（服务端截断）
      - locked       章节被锁定或需付费，正文为空
      - unavailable  正文为空或页面结构异常
      - garbled      解码后仍有私用区字符（码表可能过期）

    ## 为什么必须单独有 preview 这一档

    未登录抓取时，`isChapterLock=true` 的章返回的 content
    **恒为 200 个字符**（HTML 长度正好 200），是截断预览；
    页面里同时写着「会员登录后，可在网页畅读全文」。

    判据取「服务端标记」与「长度显著短于申报」的并集：
      - isChapterLock 是服务端自己的说法，最可靠；
      - 长度判据兜住 isChapterLock 未置位但内容仍被裁剪的情况。
    阈值取 0.6：正常章偶有几百字浮动，但不会只剩十分之一。
    """
    result = fallback or ChapterContent(
        item_id=ref.item_id,
        title=ref.title,
        volume=ref.volume,
        order=ref.order,
    )

    try:
        title, content_html, meta = parse_reader_page(html)
    except FanqieError as e:
        msg = str(e)
        result.status = "locked" if "锁定" in msg else "unavailable"
        result.error = msg
        return result
    except Exception as e:
        result.status = "unavailable"
        result.error = f"{type(e).__name__}: {e}"
        return result

    result.title = title or ref.title
    result.pua_before = count_pua(content_html)

    decoded = extract_body(content_html)
    result.text = decoded.text
    result.pua_after = decoded.unresolved
    result.chars = len(decoded.text)

    # 服务端元数据：判定截断的依据
    result.is_locked = bool(meta.get("isChapterLock"))
    result.claimed_words = _to_int(meta.get("chapterWordNumber"))

    if decoded.unresolved > 0:
        # 有残留说明表可能过期 —— 标为乱码，不进入后续分析
        result.status = "garbled"
        result.error = (
            f"解码后仍有 {decoded.unresolved} 个私用区字符，"
            "码表可能已过期，请核对字体是否更换"
        )
        return result

    if not decoded.text.strip():
        result.status = "unavailable"
        result.error = "解码后正文为空"
        return result

    short = (
        result.claimed_words > 0
        and result.chars < result.claimed_words * TRUNCATION_RATIO
    )
    if result.is_locked or short:
        result.status = "preview"
        result.error = (
            f"服务端只返回试读预览（取得 {result.chars} 字，"
            f"平台申报 {result.claimed_words} 字）。"
            "未登录时锁定章恒定截断为 200 字符，"
            "需登录态才能取得该章全文。"
        )
        return result

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
    # 逐章状态：ok / preview / locked / unavailable / garbled / failed / skipped
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

    **preview 计入缺章。** 只拿到试读预览的章不算成功：
    它看起来有正文，实际是被截断的片段，混进正文会让
    「覆盖率」失真（实测 550 章的书会显示 100% 成功，实则只覆盖 1.8%）。
    """
    by_status: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []
    ok_count = 0
    # 只拿到预览的字数，用来如实汇报「实际可用内容有多少」
    preview_count = 0
    preview_chars = 0

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
                "claimed_words": c.claimed_words,
            }
            if c.status == "ok":
                ok_count += 1
            elif c.status == "preview":
                preview_count += 1
                preview_chars += c.chars

        # 相对本章申报字数还差多少，让「缺了多少」可量化
        if entry.get("claimed_words") and entry.get("chars"):
            entry["missing_words"] = max(
                0, entry["claimed_words"] - entry["chars"]
            )

        by_status.setdefault(entry["status"], []).append(entry)

        # preview 与其它非 ok 状态一样计入缺章清单 ——
        # 它确实没拿到该章全文，只是拿到了开头。
        if entry["status"] != "ok":
            missing.append(entry)

    return {
        "total": len(chapters),
        "ok": ok_count,
        "missing_count": len(missing),
        "by_status": {k: len(v) for k, v in by_status.items()},
        # 预览章单独说明，避免「有正文所以算成功」的误读
        "preview_count": preview_count,
        "preview_chars": preview_chars,
        # 缺章清单可能很长，界面上分页展示；这里全量返回供落盘
        "missing": missing,
    }


def results_to_txt(
    chapters: list[ChapterRef],
    results: dict[str, ChapterContent],
    *,
    include_missing_note: bool = True,
    include_preview: bool = True,
) -> str:
    """把抓取结果拼成 TXT，供现有拆书导入使用。

    **保留章节顺序与段落结构**：
      - 按目录顺序输出
      - 每章一个标题行
      - 章内段落用空行分隔（解码时已保留）

    缺失的章**不会静默跳过**：会在末尾附一段清单，
    让拆书分析知道覆盖范围。

    ## 预览章（status=preview）

    未登录时，锁定章只能拿到被截断的开头。这些内容有两种处理：

      - `include_preview=True`（默认）：收入正文，但**逐章标注**
        「本节仅为试读片段」，页首另附总说明与占比。
        这样文本仍可用于文风研究，又不会被当成完整原文引用。
      - `include_preview=False`：完全排除，只在末尾清单里列出。

    默认选前者，因为纯文风分析需要样本；但标注必须显式，
    否则下游会误判「这本已完整取得」。
    """
    parts: list[str] = []
    missing: list[str] = []
    preview_orders: list[int] = []
    preview_chars = 0
    ok_chars = 0

    for ref in chapters:
        c = results.get(ref.item_id)
        if c is None or not c.text.strip() or c.status in (
            "unavailable", "garbled", "failed", "locked", "not_fetched"
        ):
            reason = c.status if c else "not_fetched"
            missing.append(f"第{ref.order}章 {ref.title}（{reason}）")
            continue

        if c.status == "preview":
            preview_orders.append(ref.order)
            preview_chars += c.chars
            if not include_preview:
                missing.append(f"第{ref.order}章 {ref.title}（preview：仅试读片段）")
                continue

        header = c.title or ref.title
        parts.append(header)
        parts.append("")
        if c.status == "preview":
            # 逐章标注，让片段不可能被误认成完整章
            parts.append(
                f"〖本节仅为试读片段〗取得 {c.chars} 字 / "
                f"平台申报 {c.claimed_words} 字，正文被服务端截断。"
            )
            parts.append("")
        parts.append(c.text)
        parts.append("")

        if c.status == "ok":
            ok_chars += c.chars

    body = "\n".join(parts).strip()

    if include_missing_note:
        note: list[str] = ["", "=" * 40, "【抓取说明】"]

        if preview_orders:
            total_chars = ok_chars + preview_chars
            pct = (preview_chars / total_chars * 100) if total_chars else 0.0
            note.extend([
                f"本书有 {len(preview_orders)} 章只取得试读片段"
                f"（占已取正文的 {pct:.1f}%），共 {preview_chars} 字。",
                "这些章在正文中以〖本节仅为试读片段〗标注，"
                "不可作为完整原文引用。",
                f"完整可取正文约 {ok_chars} 字。",
                "",
                "未登录抓取时，平台对锁定章只返回约 200 字符的预览，"
                "并提示「会员登录后，可在网页畅读全文」。"
                "如需全文，需要提供已登录的登录态。",
                "",
            ])

        note.append(
            f"共 {len(chapters)} 章，完整取得 {len(chapters) - len(missing) - len(preview_orders)} 章，"
            f"仅试读片段 {len(preview_orders)} 章，未取得 {len(missing)} 章。"
        )
        if missing:
            note.append("以下内容未包含在正文中：")
            note.append("")
            note.extend(missing[:200])
            if len(missing) > 200:
                note.append(f"... 另有 {len(missing) - 200} 章未列出")

        body = body + "\n" + "\n".join(note)

    return body


__all__ = [    "BASE",
    "FREE_CHAPTER_COUNT",
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
    "TRUNCATION_RATIO",
    "load_login_cookie",
    "save_login_cookie",
    "cookie_status",
    "normalize_cookie",
    "build_chapter_content",
    "parse_reader_page",
]
