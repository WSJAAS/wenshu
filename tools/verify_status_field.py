# -*- coding: utf-8 -*-
"""确定 page.status 的语义（连载 vs 完结）。

已知：
  - creationStatus 恒为 '0'，无用
  - page.status 是另一字段，且 DOM 里有「已完结」文案可交叉验证

方法：多取几本书，用 DOM 文案做基准，反推 status 的映射。
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


def dom_status(html: str) -> str:
    """从 DOM 文案里取状态。这是用户实际看到的东西，作为基准。"""
    m = re.search(r'>\s*(已完结|连载中)\s*<', html)
    return m.group(1) if m else ""


# 从榜单取一批书，覆盖面广一些
rank_html = fetch(f"{BASE}/rank")
rd = state(rank_html)
books = []
for b in (rd.get("rank", {}).get("book_list") or []):
    if isinstance(b, dict) and b.get("bookId"):
        books.append((str(b["bookId"]), ""))

# 再补几个分类榜，增加样本多样性
for cat in ("1139", "23", "248", "8", "746", "1140", "124", "27"):
    try:
        h = fetch(f"{BASE}/rank/1_1_{cat}")
        d2 = state(h)
        for b in (d2.get("rank", {}).get("book_list") or []):
            if isinstance(b, dict) and b.get("bookId"):
                bid = str(b["bookId"])
                if not any(bid == x[0] for x in books):
                    books.append((bid, cat))
    except Exception:
        pass
    time.sleep(1.0)
    if len(books) >= 18:
        break

print("=" * 78)
print(f"抽样 {len(books)} 本，用 DOM 文案反推 page.status 语义")
print("=" * 78)
print(f"  {'bookId':<21} {'page.status':<12} {'creationStatus':<15} {'DOM 文案':<10} 书名")
print("  " + "-" * 82)

pairs = []
for bid, _cat in books[:18]:
    try:
        html = fetch(f"{BASE}/page/{bid}")
        d = state(html)
        page = d.get("page", {})
        st = page.get("status")
        cs = page.get("creationStatus")
        dom = dom_status(html)
        name = str(page.get("bookName") or "")[:20]
        print(f"  {bid:<21} {str(st):<12} {str(cs):<15} {dom:<10} {name}")
        if dom:
            pairs.append((str(st), dom))
    except Exception as e:
        print(f"  {bid:<21} 抓取失败: {str(e)[:30]}")
    time.sleep(1.1)

print()
print("=" * 78)
print("映射推断")
print("=" * 78)
from collections import Counter
mapping = {}
for st, dom in pairs:
    mapping.setdefault(st, Counter())[dom] += 1
for st, c in sorted(mapping.items()):
    print(f"  page.status={st!r}  ->  {dict(c)}")

print()
if mapping:
    print("  结论：")
    for st, c in sorted(mapping.items()):
        dominant = c.most_common(1)[0][0]
        print(f"    status={st} → {dominant}")
