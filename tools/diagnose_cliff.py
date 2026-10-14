# -*- coding: utf-8 -*-
"""查清「第 10/11 章断崖」的根因。

两个假设，必须分清：
  A. 平台只给试读（前 10 章全文，之后只有开头一段）→ 拿不到，只能如实标注
  B. 平台给了全文，我的 extract_body 提取错了 → 可修

判定方法：直接看阅读页原始 HTML 里有没有全文。
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"

sys.path.insert(0, r"X:\小说工程\wenshu\backend")
from app.services.fanqie_source import FanqieClient  # noqa: E402
from app.services.fanqie_decoder import extract_body, count_pua  # noqa: E402


def raw(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,*/*;q=0.8",
        "Referer": f"{BASE}/",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


client = FanqieClient(interval=1.2)

# 用惹金枝（550 章，已抓过）
BOOK = "7231424472606051384"
book, chapters = client.fetch_book(BOOK)
print(f"《{book.title}》 {len(chapters)} 章")

# 取第 8、10、11、12 章做对比
targets = [c for c in chapters if (c.order or 0) in (8, 10, 11, 12, 20)]
print(f"对比章节: {[c.order for c in targets]}")

print()
print("=" * 78)
print("[1] 原始 HTML 里的正文字符数（未解码前）")
print("=" * 78)
print(f"  {'章':<4} {'标题':<20} {'原始HTML':<10} {'提取后':<9} {'原始PUA':<9} {'占比'}")
print("  " + "-" * 70)

results = {}
for c in targets:
    html = raw(f"{BASE}/reader/{c.item_id}")
    # extract_body 返回 DecodeResult，取 .text
    res = extract_body(html)
    body = res.text if hasattr(res, "text") else str(res)
    raw_pua = count_pua(html)
    results[c.order] = {
        "html_len": len(html),
        "body_len": len(body),
        "raw_pua": raw_pua,
        "replaced": getattr(res, "replaced", None),
        "unresolved": getattr(res, "unresolved", None),
        "body": body,
        "html": html,
    }
    pct = f"{len(body)/max(1,len(html))*100:.1f}%"
    print(f"  {c.order:<4} {str(c.title)[:18]:<20} {len(html):<10,} {len(body):<9,} "
          f"{raw_pua:<9,} {pct}")
    time.sleep(1.3)

print()
print("=" * 78)
print("[2] 关键：HTML 里是否存在「全文」")
print("=" * 78)
for order in sorted(results):
    r = results[order]
    html = r["html"]
    print(f"\n  第 {order} 章  提取到 {r['body_len']} 字")
    # 找正文容器
    m = re.search(r'<div[^>]*class="[^"]*muye-reader-content[^"]*"[^>]*>(.*?)</div>', html, re.S)
    if m:
        inner = m.group(1)
        print(f"    muye-reader-content 内层长度: {len(inner):,}")
    # 所有 <p> 标签的总文本量
    ps = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    total_p = sum(len(re.sub(r"<[^>]+>", "", p)) for p in ps)
    print(f"    <p> 标签数: {len(ps)}   总文本: {total_p:,} 字")
    # 是否有试读/订阅类标记
    for kw in ("试读", "订阅", "购买", "付费", "会员", "解锁", "preview", "trial",
               "bookChapterContent", "chapterContent"):
        n = html.count(kw)
        if n:
            idx = html.find(kw)
            ctx = html[max(0, idx - 70): idx + 90].replace("\n", " ")
            print(f"    含 {kw!r} ×{n}: …{ctx[:150]}…")

print()
print("=" * 78)
print("[3] 提取后正文的结尾（判断是否被截断）")
print("=" * 78)
for order in sorted(results):
    r = results[order]
    b = r["body"]
    print(f"\n  第 {order} 章（{r['body_len']} 字）")
    print(f"    开头: {b[:60]!r}")
    print(f"    结尾: {b[-60:]!r}")

print()
print("=" * 78)
print("[4] 判定")
print("=" * 78)
lens = {o: results[o]["body_len"] for o in sorted(results)}
small = [o for o, L in lens.items() if L < 400]
big = [o for o, L in lens.items() if L >= 400]
print(f"  完整章（>=400字）: {big}")
print(f"  截断章（<400字） : {small}")

if small and big:
    o = small[0]
    r = results[o]
    html = r["html"]
    # 检查 HTML 里是否含有比提取结果更长的连续中文
    han = re.findall(r"[\u4e00-\u9fff]{40,}", html)
    print()
    print(f"  第 {o} 章 HTML 里最长的连续中文片段:")
    for h in sorted(han, key=len, reverse=True)[:3]:
        print(f"    长度 {len(h)}: {h[:70]!r}")
    mx = max((len(h) for h in han), default=0)
    print()
    if mx > r["body_len"] * 1.5:
        print(f"  ★ HTML 里有更长的文本（{mx} 字 > 提取到的 {r['body_len']} 字）")
        print("    → 假设 B：提取器漏了内容，可修")
    else:
        print(f"  ★ HTML 里最长中文片段 {mx} 字，与提取结果 {r['body_len']} 字相当")
        print("    → 假设 A：平台本身只给这些内容（试读限制）")
