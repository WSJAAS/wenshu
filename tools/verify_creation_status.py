# -*- coding: utf-8 -*-
"""核实 creationStatus 的真实语义。

背景：工作区里有一处改动，把原来的 0=连载/1=完结 改成了 0=完结/1=连载。
两者正好相反，必须用真实数据判定，不能靠猜。

判定方法：找一本**确定已完结**和一本**确定在连载**的书，
比对它们的 creationStatus，以及页面上明示的状态文案。
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
    if i < 0:
        return None
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


# 已知信息：
#  惹金枝（7231424472606051384）——详情页写「最新章：第550章 终（完结）」
#  穿乱世（7423691438321323070）——1288 章，详情页 lastChapterTitle 无「完结」字样
CASES = [
    ("7231424472606051384", "惹金枝", "预期已完结"),
    ("7423691438321323070", "穿乱世", "预期连载"),
]

print("=" * 76)
print("creationStatus 真实语义核实")
print("=" * 76)

for book_id, name, expect in CASES:
    html = fetch(f"{BASE}/page/{book_id}")
    d = state(html)
    page = d.get("page", {})

    cs = page.get("creationStatus")
    last = str(page.get("lastChapterTitle") or "")
    total = page.get("chapterTotal")
    word = page.get("wordNumber")

    print(f"\n《{name}》 {expect}")
    print(f"  creationStatus = {cs!r}  (type={type(cs).__name__})")
    print(f"  lastChapterTitle = {last[:50]!r}")
    print(f"  chapterTotal = {total}   wordNumber = {word}")

    # 页面 HTML 里是否有明示的状态文案
    # 找「连载中」「已完结」这类字样出现的上下文
    for kw in ("已完结", "连载中", "完结", "连载"):
        for m in re.finditer(re.escape(kw), html):
            ctx = html[max(0, m.start() - 80): m.start() + 60].replace("\n", " ")
            if "creationStatus" in ctx or "status" in ctx.lower() or "章" in ctx:
                print(f"  含 {kw!r} 的上下文: …{ctx[-140:]}…")
                break

print()
print("=" * 76)
print("判定")
print("=" * 76)
print("""
判据：
  - 惹金枝的 lastChapterTitle 含「终（完结）」→ 该书已完结
  - 穿乱世的 lastChapterTitle 未见完结字样 → 该书在连载

对照上表 creationStatus 的取值即可确定映射方向。
""")
