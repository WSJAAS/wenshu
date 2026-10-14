# -*- coding: utf-8 -*-
"""多站点小说下载器。

支持两种常见页面布局：
  A. 现代 div 布局（努努新书）：正文在 <div class="neirong" id="neirong">
  B. 旧式表格布局（努努老书）：正文在 <p> 或裸 <br> 段落里

流程：目录页 → 逐章抓取 → 清洗 → 落盘 TXT + 元数据。

用法：
    python grab_novel.py --book-url <目录页URL> --out <输出目录> [--title 书名]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 站内导航/版权噪音，逐行剔除
NOISE = re.compile(
    r"(努努书坊|kanunu8?\.com|ptwxz|pt文学|www\.[a-z0-9\-]+\.(com|net|org|cc)|"
    r"上一章|下一章|上一页|下一页|返回目录|返回书页|加入书签|投推荐票|手机版|"
    r"本站.*?整理|请记住本站|最快更新|全文阅读|内容简介|作者：|小说在线阅读$|"
    r"章节错误|点此举报|举报原因|"
    # 页脚与声明
    r"本站内容来源于网络|版权归属作家|如侵犯了您的利益|请联系删除|"
    r"版权所有|Copyright|All Rights Reserved|意见反馈|回顶部|全部分类|"
    r"&copy;|©)"
)


# ============================================================
#  HTTP
# ============================================================
def fetch(url: str, referer: str = "", timeout: int = 25,
          retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            **({"Referer": referer} if referer else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:
            last = e
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def decode(raw: bytes) -> str:
    head = raw[:4000].lower()
    for pat, enc in ((b"gb2312", "gb18030"), (b"gbk", "gb18030"),
                     (b"gb18030", "gb18030"), (b"big5", "big5"),
                     (b"utf-8", "utf-8"), (b"utf8", "utf-8")):
        if pat in head:
            try:
                return raw.decode(enc, "replace")
            except LookupError:
                break
    return raw.decode("utf-8", "replace")


def unescape(h: str) -> str:
    return (h.replace("&nbsp;", " ").replace("&emsp;", "　")
            .replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&apos;", "'")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&ldquo;", "“").replace("&rdquo;", "”")
            .replace("&hellip;", "…").replace("&mdash;", "—"))


# ============================================================
#  正文抽取
# ============================================================
@dataclass
class ExtractResult:
    text: str
    strategy: str
    raw_len: int
    hanzi: int = 0


def _lines_from(seg: str) -> list[str]:
    seg = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", seg)
    seg = re.sub(r"(?i)<br\s*/?>", "\n", seg)
    seg = re.sub(r"(?i)</p\s*>", "\n", seg)
    seg = re.sub(r"(?i)</div\s*>", "\n", seg)
    seg = re.sub(r"<[^>]+>", "", seg)
    seg = unescape(seg)
    out = []
    for ln in seg.split("\n"):
        s = ln.strip().strip("\u3000")
        if not s:
            continue
        # 站点排版残留：行尾的「(《》)」「(《》)」「()」
        s = re.sub(r"\s*[（(]\s*《?\s*》?\s*[)）]\s*$", "", s).strip()
        # 行首的 class="..." 之类属性泄漏（缥天页面首行）
        s = re.sub(r'^\s*class="[^"]*">?\s*', "", s)
        if not s:
            continue
        out.append(s)
    return out


def extract_body(html: str) -> ExtractResult:
    """按布局优先级抽正文，返回最干净的一份。

    优先级（先精确后兜底）：
      1. 飘天文学系：toplink / bottomlink 之间的裸文本
      2. <p> 段落 —— 努努新旧两版正文都在 <p> 里，边界最干净
      3. neirong / book-content 等现代容器
      4. 全页兜底（最后手段，会掺导航，需靠过滤补救）

    早期实现把「whole-page」也当候选并只按汉字数取最多的一份，
    结果永远选中全页版 —— 因为它必然包含正文，字数最多。
    于是章首混进 <h1> 标题、章尾混进版权页脚。现在按优先级取第一个可用的。
    """
    candidates: list[tuple[int, str, str]] = []   # (优先级, 名称, 片段)

    # 0. 飘天文学：正文在 toplink 与 bottomlink 之间的裸文本里，没有包裹标签
    i = html.find('class="toplink"')
    j = html.find('class="bottomlink"')
    if i > 0 and j > i:
        candidates.append((0, "piaotian-raw", html[i:j]))

    # 1. <p> 段落（最干净）
    ps = re.findall(r"<p[^>]*>(.*?)</p\s*>", html, re.S | re.I)
    long_ps = [p for p in ps
               if len(re.findall(r"[\u4e00-\u9fff]", p)) > 200]
    if long_ps:
        candidates.append((1, "p-tags", "\n".join(long_ps)))

    # 2. 现代容器
    for pat, name in (
        (r'<div[^>]*class="[^"]*neirong[^"]*"[^>]*>(.*?)</div>\s*<div',
         "neirong"),
        (r'<div[^>]*class="[^"]*(?:book-content|read-content|chapter-content|showtxt|yd_text2)[^"]*"[^>]*>(.*?)</div>',
         "read-content"),
        (r'<div[^>]*id="(?:content|chaptercontent|booktext|htmlContent)"[^>]*>(.*?)</div>',
         "id-content"),
    ):
        m = re.search(pat, html, re.S | re.I)
        if m:
            candidates.append((2, name, m.group(1)))

    # 3. 兜底
    candidates.append((9, "whole-page", html))

    for _prio, name, seg in sorted(candidates, key=lambda x: x[0]):
        lines = _lines_from(seg)
        lines = [x for x in lines if not NOISE.search(x)]
        # 去掉与章节标题重复的一行（页面 <h1>/<title> 常把「书名 正文 第N回…」混进来）
        lines = [x for x in lines if not _is_title_noise(x)]
        body_lines = [x for x in lines
                      if len(re.findall(r"[\u4e00-\u9fff]", x)) >= 12]
        if not body_lines:
            continue
        text = "\n\n".join(body_lines)
        zh = len(re.findall(r"[\u4e00-\u9fff]", text))
        if zh >= 50:
            return ExtractResult(text=text, strategy=name,
                                 raw_len=len(seg), hanzi=zh)

    return ExtractResult(text="", strategy="none", raw_len=0, hanzi=0)


# 页面级标题行：形如「书名 - 正文 第N回 … by 作者 - 站点 - 小说在线阅读」
_TITLE_NOISE = re.compile(
    r"(小说在线阅读|在线阅读$|^\s*《?[\u4e00-\u9fff]{2,12}\s*[-–—]\s*正文\s|"
    r"\bby\s+[\u4e00-\u9fff]{2,10}\s*[-–—]|^\s*所属书籍|^\s*正文\s+第)"
)


def _is_title_noise(line: str) -> bool:
    s = line.strip()
    if _TITLE_NOISE.search(s):
        return True
    # 长行里如果「书名 + 正文 + 章节名」重复出现，也丢掉
    if len(s) < 120 and s.count("正文") >= 1 and "第" in s and "回" in s:
        return True
    return False


# 飘天章节页正文首行常带章节名副本，形如「第一章 xxx(《》)」，
# 与文件里的章标题行重复，且末尾有排版残留符号。
_CHAP_DUP = re.compile(r"^第\s*[0-9一二三四五六七八九十百千两]+\s*[章回节][^\n]{0,60}"
                       r"(?:\(《》\)|（《》）|\(\)|（）)?\s*$")


def _drop_dup_chapter_title(text: str, title: str) -> str:
    """去掉正文开头的重复章标题行。

    本站把章节名同时放在页面 <title>、导航和正文首行，落盘时会出现
    「第一章 X」两次。这里只删与本章标题重复的首行。
    """
    tnorm = re.sub(r"[\s　]+", "", title)
    lines = text.split("\n\n")
    out = []
    removed = 0
    for ln in lines:
        l = re.sub(r"[\s　]+", "", ln)
        if removed < 2 and (
            l == tnorm
            or (l.startswith(tnorm) and len(l) <= len(tnorm) + 12)
            or _CHAP_DUP.match(ln.strip()) and l[:12] == tnorm[:12]
        ):
            removed += 1
            continue
        out.append(ln)
    return "\n\n".join(out)


# ============================================================
#  目录
# ============================================================
@dataclass
class Chapter:
    order: int
    title: str
    url: str


def list_chapters(catalog_url: str, html: str) -> list[Chapter]:
    """从目录页取章节列表。

    关键约束：**只收同目录下的链接**。目录页顶部/底部通常有一大堆
    分类导航（`/files/7.html` 之类），它们与本书同域名、格式也一样，
    靠标题关键词过滤不干净 —— 实测一本书 30 条链接里只有 12 条是正文。
    用「URL 前缀 == 目录页所在目录」这条硬规则可直接切干净。
    """
    cat_dir = catalog_url if catalog_url.endswith("/") else catalog_url.rsplit("/", 1)[0] + "/"
    m = re.match(r"(https?://[^/]+)", catalog_url)
    host = m.group(1) if m else ""

    seen: dict[str, Chapter] = {}

    for mm in re.finditer(r'href="([^"]+?\.html)"[^>]*>([^<]{1,80})</a>',
                          html, re.I):
        href, title = mm.group(1), mm.group(2)
        title = re.sub(r"\s+", " ", unescape(title)).strip()
        if not title or NOISE.search(title):
            continue
        if re.search(r"(上一|下一|目录|首页|书签|推荐|顶部|底部|作品集|分类)", title):
            continue

        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = host + href
        else:
            url = cat_dir + href

        # 只保留本书目录下的链接
        if not url.startswith(cat_dir):
            continue
        if url.rstrip("/") == cat_dir.rstrip("/"):
            continue

        if url not in seen:
            seen[url] = Chapter(order=0, title=title, url=url)

    # 章节数过少时，尝试匹配纯数字相对链接（老站形态）
    if len(seen) < 3:
        for mm in re.finditer(r'href="([^"]{1,120})"[^>]*>([^<]{1,80})</a>',
                              html, re.I):
            href, title = mm.group(1), mm.group(2)
            if not re.search(r"第\s*[0-9一二三四五六七八九十百千]+\s*[章回节]", title):
                continue
            url = (host + href) if href.startswith("/") else (
                href if href.startswith("http") else cat_dir + href)
            if url not in seen:
                seen[url] = Chapter(order=0, title=title.strip(), url=url)

    # 飘天文学形态：目录页在 /html/{a}/{b}/，章节是同目录下的纯数字 .html
    # 但目录页本身可能只列分卷，需进一步跟进。这里补一条宽容规则：
    # 只要标题像章节名（含「第X章/回/节」），且链接指向本站，就收。
    if len(seen) < 3:
        for mm in re.finditer(r'href="([^"]+\.html)"[^>]*>([^<]{1,80})</a>',
                              html, re.I):
            href, title = mm.group(1), mm.group(2)
            title = re.sub(r"\s+", " ", unescape(title)).strip()
            if not re.search(r"第\s*[0-9一二三四五六七八九十百千两]+\s*[章回节]", title):
                continue
            url = (host + href) if href.startswith("/") else (
                href if href.startswith("http") else cat_dir + href)
            if url not in seen:
                seen[url] = Chapter(order=0, title=title, url=url)

    chs = list(seen.values())
    # 按标题里的序号排序，保证顺序正确
    def key(c: Chapter):
        m = re.search(r"第\s*([0-9一二三四五六七八九十百千两]+)\s*[章回节]", c.title)
        if m:
            return _cn2int(m.group(1))
        return 10**9
    chs.sort(key=key)

    # 去重：目录页里同一章常出现两次（顶部导航 + 正文目录），
    # 或同一章有多个镜像链接。按「标题 + 链接尾号」判重。
    uniq: list[Chapter] = []
    seen_key = set()
    for c in chs:
        k = re.sub(r"\s+", "", c.title)
        if k in seen_key:
            continue
        seen_key.add(k)
        uniq.append(c)
    chs = uniq

    # 如果标题带明确序号，按序号去重（同名不同序号的番外要保留）
    if len(chs) > 3:
        by_order: dict[int, Chapter] = {}
        no_order: list[Chapter] = []
        for c in chs:
            m = re.search(r"第\s*([0-9一二三四五六七八九十百千两]+)\s*[章回节]", c.title)
            if m:
                n = _cn2int(m.group(1))
                if n < 10**9:
                    by_order.setdefault(n, c)
                    continue
            no_order.append(c)
        merged = list(by_order.values()) + no_order
        merged.sort(key=key)
        chs = merged

    for i, c in enumerate(chs, 1):
        c.order = i
    return chs


_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2int(s: str) -> int:
    if s.isdigit():
        return int(s)
    total, section = 0, 0
    num = 0
    for ch in s:
        if ch in _CN:
            num = _CN[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            section += (num or 1) * 1000
            num = 0
        else:
            return 10**9
    return total + section + num


# ============================================================
#  主流程
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-url", required=True, help="目录页 URL")
    ap.add_argument("--out", default=r"X:\小说工程\novel-samples\from-sites")
    ap.add_argument("--title", default="", help="书名（默认从页面 title 取）")
    ap.add_argument("--interval", type=float, default=0.35)
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 章（0=全部）")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 92)
    print("[1] 目录页")
    print("=" * 92)
    raw = fetch(args.book_url)
    html = decode(raw)
    print(f"  URL: {args.book_url}")
    print(f"  {len(raw)} 字节 / {len(html)} 字符")

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    page_title = re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else ""

    # 书名：优先取「书名 by 作者」格式里的书名，其次按分隔符切
    title = args.title.strip()
    if not title:
        mb = re.match(r"\s*(.+?)\s+by\s+(.+?)\s*[-–—|_]", page_title)
        if mb:
            title = mb.group(1).strip()
        else:
            title = re.split(r"\s*[-–—_|,，]\s*", page_title)[0].strip()
        # 站点 SEO 后缀：「XXX最新章节」「XXX无弹窗全文阅读」
        title = re.sub(r"(最新章节|全文阅读|无弹窗|在线全文|小说)$", "", title).strip()
        title = re.sub(r"^《|》$", "", title).strip()
    print(f"  页面标题: {page_title[:80]}")
    print(f"  采用书名: {title or '（未识别）'}")

    # 作者：页面里多种写法
    author = ""
    for pat in (r"作\s*者[：:]\s*([^\s<|（(]{1,24})",
                r'<a[^>]*author[^>]*>([^<]{1,24})</a>',
                r"\bby\s+([^\s<|\-–—]{1,24})"):
        ma = re.search(pat, html, re.I)
        if ma:
            author = ma.group(1).strip()
            break
    if not author:
        mb = re.search(r"\s+by\s+(.+?)\s*[-–—|_]", page_title)
        if mb:
            author = mb.group(1).strip()
    print(f"  作者: {author or '（未识别）'}")

    print()
    print("=" * 92)
    print("[2] 章节列表")
    print("=" * 92)
    chs = list_chapters(args.book_url, html)
    print(f"  共 {len(chs)} 章")
    for c in chs[:4]:
        print(f"    #{c.order:<4} {c.title[:40]:<42} {c.url}")
    if len(chs) > 4:
        print(f"    ... 至 #{chs[-1].order} {chs[-1].title[:40]}")
    if not chs:
        print("  ✗ 未识别到章节，停止")
        return 1
    if args.limit:
        chs = chs[:args.limit]
        print(f"  限制为前 {len(chs)} 章")

    print()
    print("=" * 92)
    print("[3] 逐章抓取")
    print("=" * 92)
    results: list[dict] = []
    t0 = time.time()
    for i, c in enumerate(chs, 1):
        try:
            cr = fetch(c.url, referer=args.book_url)
            ch = decode(cr)
            ex = extract_body(ch)
            body_text = _drop_dup_chapter_title(ex.text, c.title)
            ok = len(re.findall(r"[\u4e00-\u9fff]", body_text)) >= 50
            results.append({
                "order": c.order, "title": c.title, "url": c.url,
                "text": body_text, "chars": len(body_text),
                "hanzi": len(re.findall(r"[\u4e00-\u9fff]", body_text)),
                "strategy": ex.strategy, "ok": ok,
            })
            flag = "✓" if ok else "✗"
            if i <= 3 or i % 20 == 0 or not ok:
                print(f"  {flag} [{i:>4}/{len(chs)}] {c.title[:32]:<34} "
                      f"{results[-1]['hanzi']:>6} 汉字  ({ex.strategy})")
        except Exception as e:
            results.append({"order": c.order, "title": c.title, "url": c.url,
                            "text": "", "chars": 0, "hanzi": 0,
                            "strategy": "ERROR", "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
            print(f"  ✗ [{i:>4}/{len(chs)}] {c.title[:32]:<34} {type(e).__name__}")
        time.sleep(args.interval)

    ok_n = sum(1 for r in results if r["ok"])
    tot_hanzi = sum(r["hanzi"] for r in results)
    print()
    print(f"  完成: {ok_n}/{len(results)} 章，合计 {tot_hanzi} 汉字，"
          f"耗时 {time.time()-t0:.0f}s")

    print()
    print("=" * 92)
    print("[4] 落盘")
    print("=" * 92)
    safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or "novel"
    txt_path = out_dir / f"{safe}_{author or '未知'}.txt"
    lines = []
    if author:
        lines.append(f"《{title}》  作者：{author}")
        lines.append("")
        lines.append(f"来源：{args.book_url}")
        lines.append("")
    for r in results:
        if not r["ok"]:
            continue
        lines.append(r["title"])
        lines.append("")
        lines.append(r["text"])
        lines.append("")
    text = "\n".join(lines).strip()
    txt_path.write_text(text, encoding="utf-8")

    meta = {
        "title": title, "author": author, "catalog_url": args.book_url,
        "chapters": len(results), "ok_chapters": ok_n,
        "total_hanzi": tot_hanzi,
        "txt": str(txt_path),
        "digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "failed": [{"order": r["order"], "title": r["title"],
                    "error": r.get("error", "")}
                   for r in results if not r["ok"]],
    }
    (out_dir / f"{safe}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  TXT : {txt_path}")
    print(f"       {len(text)} 字符 / {len(re.findall(r'[一-龥]', text))} 汉字")
    print(f"  元数据: {out_dir / (safe + '.meta.json')}")
    print(f"  指纹: {meta['digest'][:32]}…")
    if meta["failed"]:
        print(f"  失败 {len(meta['failed'])} 章:")
        for f in meta["failed"][:10]:
            print(f"    #{f['order']} {f['title'][:30]} {f['error'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
