# -*- coding: utf-8 -*-
"""核实注入方案的可行性前提。

注入的关键问题：
  用户浏览器里看到的分页数据，是「服务端渲染」还是「JS 请求」？
  如果是 JS 请求 → 注入能拿到完整数据（我们拿不到的那些）
  如果是服务端渲染且本来就只有 10 本 → 注入也拿不到更多

方法：检查 /rank 页面的 HTML，看是否有「加载更多」对应的数据源，
以及页面首屏是否已经含全部数据（只是 CSS 隐藏）。
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception:
        return None, ""


st, html = get(f"{BASE}/rank/0_1_1139")
print(f"榜单页: {st}  {len(html)} 字节")

print()
print("=" * 78)
print("[1] HTML 里 book_list 的完整条目数")
print("=" * 78)
# 数 bookId 出现次数
ids = re.findall(r'"bookId":"(\d+)"', html)
print(f"  HTML 里 bookId 出现: {len(ids)} 次（去重 {len(set(ids))} 个）")

# 数 DOM 里的卡片
cards = re.findall(r'class="[^"]*rank[^"]*"', html)
print(f"  class 含 rank 的元素: {len(cards)} 个")
for c in sorted(set(cards))[:12]:
    print(f"    {c}")

print()
print("=" * 78)
print("[2] 是否有「加载更多」相关的 DOM 与数据")
print("=" * 78)
for kw in ("加载更多", "没有更多", "load-more", "loadMore", "scroll",
           "infinite", "pagination", "分页"):
    n = len(re.findall(re.escape(kw), html, re.I))
    if n:
        idx = html.lower().find(kw.lower())
        ctx = html[max(0, idx - 100): idx + 150].replace("\n", " ")
        print(f"  含 {kw!r}: {n} 次")
        print(f"    上下文: …{ctx[:230]}…")

print()
print("=" * 78)
print("[3] 首屏数据里 defaultPage 与 total_num 的关系")
print("=" * 78)
i = html.find("window.__INITIAL_STATE__")
b = html.find("{", i)
blob = html[b:]
for end in range(len(blob), 0, -1):
    if blob[end - 1] != "}":
        continue
    try:
        d = json.loads(blob[:end])
        break
    except json.JSONDecodeError:
        continue

r = d.get("rank", {})
lst = r.get("book_list") or []
print(f"  defaultPage = {r.get('defaultPage')}")
print(f"  total_num   = {r.get('total_num')}")
print(f"  book_list   = {len(lst)} 本")
print(f"  rankVersion = {r.get('rankVersion')}")
print(f"  serverRendered = {r.get('serverRendered')}")

print()
print("  book_list 每本的 currentPos（排名指针）:")
for bb in lst[:10]:
    if isinstance(bb, dict):
        print(f"    pos={bb.get('currentPos')}  "
              f"diff={bb.get('rankPosDiff')}  "
              f"cat={bb.get('curent_category_id')}  "
              f"{(bb.get('bookName') or '')[:18]}")

print()
print("=" * 78)
print("[4] 判断")
print("=" * 78)
if len(set(ids)) <= 12:
    print(f"  服务端只给 {len(set(ids))} 本，其余靠 JS 加载")
    print("  → 注入能拿到 JS 加载的那部分（我们直连拿不到的）")
else:
    print(f"  服务端给了 {len(set(ids))} 本，可能首屏就含全部（CSS 隐藏）")
    print("  → 注入只需读取 DOM，无需抓接口")

# 检查是否有 hidden 的书
hidden = re.findall(r'style="[^"]*display:\s*none[^"]*"[^>]*>[^<]*<', html)
print(f"\n  display:none 元素: {len(hidden)} 个（若很多，说明首屏含隐藏数据）")
