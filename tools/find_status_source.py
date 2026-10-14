# -*- coding: utf-8 -*-
"""找书籍「连载/完结」状态的真正来源。

已否定：creationStatus —— 测试的两本书都返回 0，但一本完结一本连载。

候选：
  1. 页面上的文案元素（info- 前缀的 DOM）
  2. page 对象里的其它字段
  3. 页面的 meta / seo 字段
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": f"{BASE}/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def state(html):
    i = html.find("window.__INITIAL_STATE__")
    b = html.find("{", i)
    blob = html[b:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            return json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
    return None


CASES = [
    ("7231424472606051384", "惹金枝", "已完结"),
    ("7423691438321323070", "穿乱世", "已完结?"),
    ("7638631629665078297", "父皇他两辈子都在装", "已完结"),
]

# 再取一本确定连载的：从榜单找一个字数少更新的
import time

for book_id, name, note in CASES:
    html = fetch(f"{BASE}/page/{book_id}")
    d = state(html)
    page = d.get("page", {})

    print("=" * 76)
    print(f"《{name}》  {note}   bookId={book_id}")
    print("=" * 76)

    # 1) page 全部字段
    print(f"  page 字段 ({len(page)} 个):")
    for k in sorted(page.keys()):
        v = page[k]
        if isinstance(v, (list, dict)):
            print(f"    {k:<28} {type(v).__name__}({len(v)})")
        else:
            s = str(v)
            if len(s) < 60:
                print(f"    {k:<28} {s!r}")

    # 2) 页面上的状态类 DOM 文案
    print(f"\n  DOM 里的状态文案:")
    # 番茄常用 class：info-label / info-value 之类
    for pat in (
        r'<span[^>]*class="[^"]*info-label[^"]*"[^>]*>([^<]{1,16})</span>',
        r'<span[^>]*class="[^"]*info-value[^"]*"[^>]*>([^<]{1,16})</span>',
        r'>\s*(已完结|连载中|连载|完结)\s*<',
    ):
        hits = re.findall(pat, html)
        if hits:
            uniq = list(dict.fromkeys(hits))[:8]
            print(f"    {pat[:40]}… -> {uniq}")

    # 3) seo 字段（常含结构化描述）
    seo = d.get("seo") or {}
    if isinstance(seo, dict):
        for k, v in seo.items():
            if isinstance(v, str) and any(x in v for x in ("连载", "完结")):
                print(f"\n  seo.{k}: {v[:180]}")

    # 4) 页面 title 与 meta description
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        print(f"\n  <title>: {m.group(1)[:120]}")
    m2 = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]{0,200})"', html)
    if m2:
        print(f"  meta description: {m2.group(1)[:160]}")

    print()
    time.sleep(1.2)
