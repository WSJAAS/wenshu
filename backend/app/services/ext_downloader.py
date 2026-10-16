"""外部整本下载器 —— 调用第三方 Rust 工具整本抓取，产出 EPUB 再转 TXT。

## 定位

**可关闭的可选模块**，与书源（`fanqie_source.py`）同属「依赖外部站点」的门类，
因此复用同一个开关 `FANQIE_ENABLED`：关掉它，本模块与书源一起消失。

## 为什么要有这个模块

书源那条路是**后端逐章直连**：串行、限速、遇风控就停，未登录时锁定章只有
试读片段（实测《惹金枝》第 11 章只有 171 字，全文应为 2106 字）。
它的强项是「按需取几章」，弱项是「整本 550 章」——耗时长、易被拦、正文还残缺。

整本下载的需求交给专门做这件事的外部工具更合适：它自己解析目录、自己抓全文、
自己打包成 EPUB。实测同一本书同一章可拿到 2106 字全文，550/550 章齐全。
本模块只做三件事：**把工具拉起来、把书 ID 交给它、把产出的 EPUB 转成 TXT**。

## 外部工具

zhongbai2333/Tomato-Novel-Downloader（Rust）。

- 启动：`<exe> --server --data-dir <dir>`，HTTP 服务默认 `127.0.0.1:18423`
  （环境变量 `TOMATO_WEB_ADDR` 可改）。进程常驻。
- 接口（无需登录态）：
  - `POST /api/jobs`  `{"book_id": "...", "range_start": 1, "range_end": 550}`
  - `GET  /api/jobs`  任务列表（含 state / progress / title / author）
  - `POST /api/jobs/<id>/cancel`
  - `GET  /api/config`

## 输出位置：为什么把子进程 CWD 设成输出目录

工具的 `save_path` 配置**为空时**，产出文件落在**进程的工作目录**下
（实测：那个残留实例的 CWD 是 `X:/小说工程`，于是 EPUB 写到了
`X:/小说工程/惹金枝.epub`，而不是任何数据目录里）。

两条可选做法：

1. 改 `<data-dir>/config.yml` 的 `save_path` —— **不可行**：实测配置只在进程
   启动时读一次，运行中改文件不生效（改完 `GET /api/config` 仍是旧值），
   而且改配置需要重启工具，会打断正在跑的任务。
2. **把子进程的 CWD 设成我们的输出目录** —— 采用这条。不动工具的任何配置，
   产出自然落进 `runtime/data/ext_downloader/`，行为可预测、可清理。

代价是：**复用别人拉起的实例时，CWD 不受我们控制**。所以
`find_output_epub()` 除了看我们的输出目录，还会兜底看几个常见落点。
"""
from __future__ import annotations

import html as html_lib
import os
import re
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.config import DATA_DIR, PROJECT_ROOT, settings
from app.logger import get_logger

logger = get_logger(__name__)


# ============================================================
#  默认值
# ============================================================
DEFAULT_ADDR = "http://127.0.0.1:18423"
DEFAULT_EXE_REL = Path("runtime") / "tools" / "tnd" / "TomatoNovelDownloader.exe"
DEFAULT_DATA_REL = Path("runtime") / "data" / "ext_downloader"

# config.py 的默认值表达式的结果（PROJECT_ROOT 是 backend/，再上一层才是仓库根）。
# 用来判断「用户到底有没有显式配置」：值等于默认值 → 默认路径不可用时可以
# 兜底换一处；值不等于默认值 → 用户明确指过路，严格照用，
# 找不到就如实报错，绝不悄悄换地方。
_DEFAULT_EXE = str(PROJECT_ROOT.parent / DEFAULT_EXE_REL)
_DEFAULT_DATA = str(PROJECT_ROOT.parent / DEFAULT_DATA_REL)

# 探活超时要短：它只回答「端口后面有没有活人」，
# 用 10s 会让每个请求都卡住，2s 足够。
PROBE_TIMEOUT = 2.0
# 常态请求超时。实测 /api/jobs 与 /api/jobs/<id>/cancel 都是毫秒级返回。
HTTP_TIMEOUT = 10.0
# 冷启动等待上限。实测 ~3s 起来，30s 留足磁盘慢/杀软扫描的余量。
START_TIMEOUT = 30.0
# 冷启动轮询间隔
START_POLL_INTERVAL = 0.5

# 代理绕过。必须显式给子进程：系统 NO_PROXY 里常有 `[::1]` 这种写法，
# httpx 会把它当成端口 `:1]` 抛 InvalidURL。工具自己发的请求也一样受影响。
_NO_PROXY_VALUE = "localhost,127.0.0.1,::1"


def _exe_fallback() -> Path:
    """exe 的兜底位置：跟着项目的数据根走。

    兜底面向的场景：用户改了 `MUMU_DATA_DIR`（数据搬家），或把仓库挪了
    位置，config 里的默认绝对路径失效。此时按「数据放代码目录之外」
    的既有约定，从数据根往上找 tools/ —— 用户换 `MUMU_DATA_DIR` 时
    工具位置也跟着走。
    """
    return DATA_DIR.parent / "tools" / "tnd" / "TomatoNovelDownloader.exe"


def _data_fallback() -> Path:
    """数据目录兜底：直接用项目数据根下的 `ext_downloader/`。

    与 `_exe_fallback` 同理 —— 产物属于数据，本就该跟 `DATA_DIR` 走。
    """
    return DATA_DIR / "ext_downloader"



# ============================================================
#  EPUB -> TXT
# ============================================================
# 章节文件：OEBPS/chapter_00001.xhtml。工具还会产出 aux_*.xhtml
# （封面、版权页之类）与 table-of-contents.html，都不算正文章节。
_CHAPTER_NAME_RE = re.compile(r"OEBPS/chapter_(\d+)\.xhtml$", re.IGNORECASE)
_HEAD_BLOCK_RE = re.compile(r"<head\b.*?</head>", re.IGNORECASE | re.DOTALL)
_HEADER_BLOCK_RE = re.compile(r"<header\b.*?</header>", re.IGNORECASE | re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"<style\b.*?</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_TAG_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_TAG_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
# 文件名里对 Windows 非法的字符，用于书名与文件名的宽松比对
_ILLEGAL_FILENAME_CHARS = r'\/:*?"<>|'


def _norm_filename(s: str) -> str:
    """把书名归一化后比对文件名。

    工具写文件时可能替换掉 Windows 非法字符（`/ : * ? " < > |`），
    直接拿书名拼路径会找不到文件，因此比对时两边都归一化。
    """
    out = []
    for ch in (s or "").strip():
        if ch in _ILLEGAL_FILENAME_CHARS or ch.isspace():
            continue
        out.append(ch)
    return "".join(out).casefold()


def _extract_chapter_title(raw: str) -> str:
    """从 xhtml 里取出本章标题。

    先看 `<title>`，再看 `<h1>`。取它的目的不是显示，而是**精确去重**：
    同一个标题在文件里出现两次，靠猜（比较相邻两行是否相同）无法覆盖
    「两次标题被粘在同一行」的排版，拿到标题原文才能准确砍掉一份。
    """
    for rx in (_TITLE_TAG_RE, _H1_TAG_RE):
        m = rx.search(raw or "")
        if not m:
            continue
        text = html_lib.unescape(_ANY_TAG_RE.sub("", m.group(1))).strip()
        if text:
            return text
    return ""


def _strip_duplicate_title(text: str, title: str) -> str:
    """去掉开头重复出现的章节标题。

    实测这个工具产出的 EPUB 里每章标题打了两遍：`<head><title>` 一份、
    `<body><h1>` 一份。不去重的话，TXT 里每章开头都会多一行，
    拆书切分时会看着像重复章节。

    两种排版都要覆盖：
      - 两份标题各占一行（该工具的默认排版）
      - 两份标题被粘在同一行、后面接着正文（某些生成器的紧凑排版）
    """
    if not text:
        return text
    if not title:
        return text

    doubled = title + title
    if text.startswith(doubled):
        # 同一行内粘了两份：砍掉一份，保留后面接着的正文
        return text[len(title):]
    return text


def _clean_chapter_xhtml(raw: str) -> str:
    """把一个章节 xhtml 转成纯文本（一行一段）。

    处理顺序是有讲究的：

    1. **先整块删 `<head>` / `<header>` / `<style>` / `<script>`**：
       必须在「去标签」之前做，否则块内的 CSS 与 JS 源码，
       以及 `<head><title>` 里的标题文本，都会被当成正文留下来。
       删掉 `<head>` 顺带解决了标题重复的主因 —— `<title>` 那份不存在了。
    2. `</p>` 换成换行：正文段落都在 `<p>` 里，这一步保住段落边界。
    3. 去掉所有剩余标签，再解 HTML 实体（`&amp;` `&nbsp;` 之类）。
    4. **去掉重复出现的章节标题**：`<head>` 被删后正常情况下标题只剩一份，
       但不同版本的生成器排版可能有别（比如没有 `<head>`、或两份标题
       粘在同一行）。用手上已知的标题原文做精确去重，比靠「相邻两行
       是否相同」猜要可靠。
    """
    title = _extract_chapter_title(raw)

    s = _HEAD_BLOCK_RE.sub("", raw)
    s = _HEADER_BLOCK_RE.sub("", s)
    s = _STYLE_BLOCK_RE.sub("", s)
    s = _SCRIPT_BLOCK_RE.sub("", s)

    s = s.replace("</p>", "\n")
    s = _ANY_TAG_RE.sub("", s)
    s = html_lib.unescape(s)

    # 只留非空行：xhtml 的缩进换行会造出一堆空行，留着会把章节撑散
    lines = [ln.strip() for ln in s.splitlines()]
    lines = [ln for ln in lines if ln]

    if not lines:
        return ""

    # 去重一：两份标题被粘在同一行
    lines[0] = _strip_duplicate_title(lines[0], title)

    # 去重二：两份标题各占一行（兜底，`<head>` 未删净时才会命中）
    if len(lines) >= 2 and lines[1] == lines[0]:
        del lines[1]

    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def epub_to_txt(epub_path: Path) -> str:
    """把整本 EPUB 转成 TXT。

    **只取正文章节**（`OEBPS/chapter_\\d+.xhtml`），按文件名里的数字排序 ——
    不能按 `namelist()` 的字典序，否则第 10 章会排到第 2 章前面。

    章与章之间空两行（`"\\n\\n\\n"`）：拆书管线按空行切段，
    空两行能让人工翻看时一眼分清章节边界。
    """
    p = Path(epub_path)
    if not p.is_file():
        raise FileNotFoundError(f"EPUB 不存在：{p}")

    chapters: list[tuple[int, str]] = []
    with zipfile.ZipFile(p) as z:
        for name in z.namelist():
            m = _CHAPTER_NAME_RE.search(name)
            if not m:
                continue
            chapters.append((int(m.group(1)), name))

        chapters.sort(key=lambda x: x[0])

        texts: list[str] = []
        for _idx, name in chapters:
            raw = z.read(name).decode("utf-8", "replace")
            body = _clean_chapter_xhtml(raw)
            if body.strip():
                texts.append(body)

    return "\n\n\n".join(texts)


# ============================================================
#  服务
# ============================================================
class ExtDownloaderService:
    """外部整本下载器的进程与 HTTP 封装（模块级单例见文件末尾）。

    并发安全：`ensure_running()` 可能被多个请求同时触发，
    没有锁会拉起两个进程、抢同一个端口（第二个必然失败）。
    """

    def __init__(
        self,
        exe_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        addr: str | None = None,
    ) -> None:
        self._exe_override = Path(exe_path) if exe_path else None
        self._data_dir_override = Path(data_dir) if data_dir else None
        self._addr_override = addr or None

        self._proc: subprocess.Popen | None = None
        # 子进程日志的写入句柄。常驻进程活着期间一直持有；
        # 进程退出/启动失败时关掉，避免句柄累积。
        self._log_fh = None
        self._lock = threading.Lock()

    # ---------- 配置读取 ----------
    #
    # 每项都「先看构造参数、再看 settings、最后退回默认」：
    # 构造参数用于测试与单点覆盖，settings 是正常入口。
    @property
    def exe_path(self) -> Path:
        """可执行文件位置。

        用户显式配过 `EXT_DOWNLOADER_PATH` 就严格照用（找不到会报错，
        不会悄悄换路径 —— 那样用户改错了配置也发现不了）；
        用的是默认值且该位置不存在时，退回跟随数据根的兜底位置。
        """
        if self._exe_override is not None:
            return self._exe_override

        raw = getattr(settings, "EXT_DOWNLOADER_PATH", "") or _DEFAULT_EXE
        p = Path(raw)
        if p.is_file() or raw != _DEFAULT_EXE:
            return p

        fb = _exe_fallback()
        if fb.is_file():
            logger.debug("EXT_DOWNLOADER_PATH 默认位置不存在，改用 %s", fb)
            return fb
        # 两处都没有：返回原值，让报错信息里显示用户期待的那个路径
        return p

    @property
    def output_dir(self) -> Path:
        """输出目录 == 子进程 CWD == 产出 EPUB 的落点。

        与 `exe_path` 同规则：显式配置优先，默认值不存在时跟随数据根。
        """
        if self._data_dir_override is not None:
            return self._data_dir_override

        raw = getattr(settings, "EXT_DOWNLOADER_DATA_DIR", "") or _DEFAULT_DATA
        p = Path(raw)
        if p.exists() or raw != _DEFAULT_DATA:
            return p

        fb = _data_fallback()
        if fb != p:
            logger.debug("EXT_DOWNLOADER_DATA_DIR 默认位置不存在，改用 %s", fb)
        return fb

    @property
    def addr(self) -> str:
        raw = self._addr_override or getattr(settings, "EXT_DOWNLOADER_ADDR", "") or DEFAULT_ADDR
        return str(raw).rstrip("/")

    @property
    def log_path(self) -> Path:
        """子进程 stdout/stderr 落盘位置。

        留着它是因为「启动失败」是这条链路最常见的问题，
        没有日志就只能报一句「起来了但探不通」，无从下手。
        """
        return self.output_dir / "launcher.log"

    def search_dirs(self) -> list[Path]:
        """找 EPUB 时按顺序尝试的目录。

        第一个是正解：我们自己拉起的进程，CWD 就是 `output_dir`。

        后面几个是兜底 —— 见模块头部「输出位置」一节：复用**别人**拉起的
        实例时，它的 CWD 不受我们控制，产出会落在它的工作目录。实测那个残留
        实例的 CWD 是 `X:/小说工程`（仓库根的上一级），EPUB 就写在那儿。

        因此把「数据根往上」与「代码根往上」两条链都纳入：前者覆盖
        Docker / 自定义 `MUMU_DATA_DIR` 的部署，后者覆盖在代码目录里跑的情形。
        多探几处，比「下载明明成功了却找不到文件」强。
        """
        dirs = [self.output_dir]

        for start in (DATA_DIR, PROJECT_ROOT):
            cur = start
            for _ in range(3):
                if cur not in dirs:
                    dirs.append(cur)
                parent = cur.parent
                if parent == cur:      # 到盘根了
                    break
                cur = parent

        # output_dir 永远排第一并保留：它可能还没建出来（进程没起过），
        # 但它是唯一由我们控制的落点，不能因为「目录暂不存在」就从列表里消失
        # —— 报错信息里要能把它显示给用户。
        head, rest = dirs[0], dirs[1:]
        return [head] + [d for d in rest if d.is_dir()]

    def stop(self) -> bool:
        """停掉**我们自己拉起的**那个进程。

        只动 `self._proc`：复用的实例（别人起的、用户在浏览器里正用着的）
        我们既不知道来龙去脉，也没有权力关掉它。
        返回是否真的停了一个进程。
        """
        proc = self._proc
        if proc is None or proc.poll() is not None:
            self._close_log()
            return False

        logger.info("停止外部下载器（pid=%s）", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("外部下载器未在 10s 内退出，强制结束（pid=%s）", proc.pid)
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._close_log()
        return True

    # ---------- HTTP ----------
    def _client(self) -> httpx.Client:
        """建一个短生命周期的同步客户端。

        `trust_env=False` 是有意为之：这些请求全部指向本机回环地址，
        不需要任何代理。而系统 `NO_PROXY` 里常见的 `[::1]` 写法会让
        httpx 解析代理绕过列表时抛 `InvalidURL: Invalid port`，
        关掉环境变量读取可以从根上避开这一类失败。
        """
        return httpx.Client(timeout=HTTP_TIMEOUT, trust_env=False)

    def _url(self, path: str) -> str:
        return f"{self.addr}{path}"

    def is_alive(self) -> bool:
        """探活：GET /api/jobs 能否在 2s 内正常应答。

        用业务接口而不是纯 TCP 连接：端口被占但服务没起来
        （比如上一个进程刚被 kill、句柄还没释放）时，
        TCP 能连上、HTTP 拿不到数据，只有真正发一次请求才分得清。
        """
        try:
            with self._client() as c:
                r = c.get(self._url("/api/jobs"), timeout=PROBE_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

    # ---------- 进程 ----------
    def ensure_running(self) -> dict[str, Any]:
        """保证下载器在跑。活着就复用，死了才拉起。

        **复用优先**：这个工具本来是给人在浏览器里用的常驻服务，
        本机很可能已经有一个实例在跑（甚至带着用户手头的下载任务）。
        再来一个会抢同一个端口，毫无意义。
        """
        if self.is_alive():
            return {"started": False, "reused": True, "addr": self.addr}

        with self._lock:
            # 双重检查：等锁期间别的请求可能已经拉起来了
            if self.is_alive():
                return {"started": False, "reused": True, "addr": self.addr}

            self._spawn()
            self._wait_until_alive()
            return {"started": True, "reused": False, "addr": self.addr}

    def _spawn(self) -> None:
        exe = self.exe_path
        if not exe.is_file():
            raise RuntimeError(
                f"找不到外部下载器可执行文件：{exe}。"
                "请确认 runtime/tools/tnd/TomatoNovelDownloader.exe 存在，"
                "或用环境变量 EXT_DOWNLOADER_PATH 指向实际路径。"
            )

        out_dir = self.output_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # 目录建不出来（盘不存在、无权限、只读挂载）是配置问题，
            # 必须是可读的 RuntimeError，而不是一个裸的 OSError
            # —— 上层把它当 502 的 detail 直接展示给用户。
            raise RuntimeError(
                f"输出/数据目录不可用：{out_dir} —— {type(e).__name__}: {e}。"
                "请确认该路径所在磁盘可写，或用环境变量 EXT_DOWNLOADER_DATA_DIR 改到可写位置。"
            ) from e

        env = os.environ.copy()
        # 子进程也必须拿到干净的 NO_PROXY：否则它自己发出去的请求
        # 会因为 `[::1]` 解析失败而全挂（表现是任务一直 queued）。
        env["NO_PROXY"] = _NO_PROXY_VALUE
        env["no_proxy"] = _NO_PROXY_VALUE
        # 它是 Rust 二进制，这个变量不起作用；留着无害，
        # 万一将来换成带 Python 组件的版本，编码也不会出错。
        env["PYTHONIOENCODING"] = "utf-8"

        # 输出重定向到文件而不是 PIPE：
        # 常驻进程不读 PIPE 会把它写满，然后子进程卡死在写日志上。
        # 日志句柄由 _close_log() 在进程结束时关闭 —— 不关就是句柄泄漏，
        # 反复拉起会累积一堆打开的 fd。
        log_fh = open(self.log_path, "a", encoding="utf-8", errors="replace")
        self._log_fh = log_fh
        log_fh.write(
            f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 "
            f"{exe} --server --data-dir {out_dir}（CWD={out_dir}）=====\n"
        )
        log_fh.flush()

        creationflags = 0
        if os.name == "nt":
            # 不加这个会弹一个控制台窗口出来，服务端部署时很突兀
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        logger.info("拉起外部下载器：%s --server --data-dir %s（CWD=%s）", exe, out_dir, out_dir)
        try:
            self._proc = subprocess.Popen(
                [str(exe), "--server", "--data-dir", str(out_dir)],
                cwd=str(out_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as e:
            self._close_log()
            self._proc = None
            raise RuntimeError(f"启动外部下载器失败：{exe} —— {type(e).__name__}: {e}") from e

    def _close_log(self) -> None:
        """关掉子进程日志句柄。重复调用安全。"""
        fh = getattr(self, "_log_fh", None)
        if fh is None:
            return
        self._log_fh = None
        try:
            fh.close()
        except OSError:
            pass

    def _wait_until_alive(self) -> None:
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                # 进程已经退出，再等下去毫无意义 —— 直接把退出码和日志尾巴报出来。
                # 先关日志句柄再拼原因：读日志尾巴要的是文件内容，不是句柄。
                self._close_log()
                raise RuntimeError(self._failure_reason(proc.returncode))
            if self.is_alive():
                logger.info("外部下载器已就绪：%s", self.addr)
                return
            time.sleep(START_POLL_INTERVAL)

        # 超时：进程还活着但服务没起来，或端口被别的程序占着。
        # 此时进程仍在跑，日志句柄要继续持有（后面还要写）。
        raise RuntimeError(self._failure_reason(None))

    def _failure_reason(self, returncode: int | None) -> str:
        """拼一条可读的失败原因：exe、退出码、日志尾部。

        只报「启动失败」是没用的 —— 真正的原因几乎总在 stderr 尾巴上
        （端口被占、data-dir 不可写、缺运行库）。
        """
        parts = [f"外部下载器未能就绪（等待上限 {START_TIMEOUT:.0f}s）"]
        parts.append(f"exe={self.exe_path}")
        parts.append(f"data_dir={self.output_dir}")
        parts.append(f"addr={self.addr}")
        if returncode is not None:
            parts.append(f"进程已退出，退出码={returncode}")
        else:
            parts.append("进程仍在运行，但端口无响应（可能被其它程序占用）")

        tail = self._log_tail()
        if tail:
            parts.append(f"日志尾部（{self.log_path}）：\n{tail}")
        else:
            parts.append(f"日志为空或不存在：{self.log_path}")
        return "\n".join(parts)

    def _log_tail(self, max_lines: int = 30, max_chars: int = 4000) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = text.splitlines()[-max_lines:]
        return "\n".join(lines)[-max_chars:]

    # ---------- 搜索 ----------
    def search_books(self, query: str) -> list[dict[str, str]]:
        """复用下载器的书名搜索，只返回书籍标识，不创建下载任务。"""
        keyword = query.strip()
        if not keyword:
            raise ValueError("请输入书名或作者")

        self.ensure_running()
        try:
            with self._client() as client:
                response = client.get(self._url("/api/search"), params={"q": keyword}, timeout=30)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("书名搜索暂时不可用，请稍后重试或使用书籍链接") from exc

        if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("items"), list):
            raise RuntimeError("当前搜索服务不可用，请使用书籍链接查询")

        books: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in data["items"]:
            if not isinstance(item, dict):
                continue
            book_id = str(item.get("book_id") or "").strip()
            if not book_id.isdigit() or book_id in seen:
                continue
            seen.add(book_id)
            books.append({
                "book_id": book_id,
                "title": str(item.get("title") or "未命名作品"),
                "author": str(item.get("author") or ""),
            })
        return books

    # ---------- 任务 ----------
    def download_book(
        self,
        book_id: str,
        range_start: int | None = None,
        range_end: int | None = None,
    ) -> dict[str, Any]:
        """把一本书交给下载器，立即返回任务信息（不等待下载完成）。

        整本几百章要跑十几分钟，HTTP 请求不可能等到它结束 ——
        这里只负责排队，进度由 `get_job()` 轮询。
        """
        bid = str(book_id or "").strip()
        if not bid:
            raise ValueError("book_id 不能为空")

        self.ensure_running()

        body: dict[str, Any] = {"book_id": bid}
        # 范围可选。全量下载时不传，交给工具自己解析目录。
        if range_start is not None:
            body["range_start"] = int(range_start)
        if range_end is not None:
            body["range_end"] = int(range_end)

        try:
            with self._client() as c:
                r = c.post(self._url("/api/jobs"), json=body)
        except Exception as e:
            raise RuntimeError(f"请求下载器失败（{self.addr}/api/jobs）：{type(e).__name__}: {e}") from e

        if r.status_code >= 400:
            raise RuntimeError(
                f"下载器拒绝创建任务：HTTP {r.status_code} —— {r.text[:300]}"
            )

        try:
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"下载器返回的不是 JSON：{r.text[:300]}") from e

        return {
            "job_id": data.get("id"),
            "state": data.get("state", ""),
            "book_id": data.get("book_id", bid),
        }

    def get_job(self, job_id: Any) -> dict[str, Any] | None:
        """按 id 查任务。找不到返回 None。

        没有 `GET /api/jobs/<id>` 这种单任务接口，只能拉列表再筛。
        列表本身很小（只保留近期任务），开销可以接受。
        """
        if job_id is None:
            return None

        self.ensure_running()

        try:
            with self._client() as c:
                r = c.get(self._url("/api/jobs"))
        except Exception as e:
            raise RuntimeError(f"请求下载器失败（{self.addr}/api/jobs）：{type(e).__name__}: {e}") from e

        if r.status_code >= 400:
            raise RuntimeError(f"读取任务列表失败：HTTP {r.status_code} —— {r.text[:300]}")

        try:
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"下载器返回的不是 JSON：{r.text[:300]}") from e

        items = data.get("items") or []
        want = str(job_id)
        for it in items:
            # id 可能是数字也可能被序列化成字符串，统一按字符串比
            if str(it.get("id")) == want:
                return it
        return None

    def cancel_job(self, job_id: Any) -> bool:
        """取消任务。失败不抛错 —— 取消是尽力而为的操作。"""
        try:
            with self._client() as c:
                r = c.post(self._url(f"/api/jobs/{job_id}/cancel"))
            return r.status_code < 400
        except Exception as e:
            logger.warning("取消下载任务 %s 失败：%s", job_id, e)
            return False

    # ---------- 产出 ----------
    def find_output_epub(self, title: str) -> Path | None:
        """按书名找产出的 EPUB。找不到返回 None。

        先按 `<title>.epub` 精确找，再退一步做归一化比对 ——
        工具写文件时可能替换书名里的非法字符。
        """
        name = (title or "").strip()

        if name:
            for d in self.search_dirs():
                p = d / f"{name}.epub"
                if p.is_file():
                    return p

        if not name:
            return None

        want = _norm_filename(name)
        for d in self.search_dirs():
            try:
                candidates = sorted(d.glob("*.epub"))
            except OSError:
                continue
            for p in candidates:
                if _norm_filename(p.stem) == want:
                    return p
        return None


# 模块级单例：进程状态（_proc）必须全局唯一，
# 否则每个请求各拿一个实例，就可能各拉一个进程。
ext_downloader_service = ExtDownloaderService()
