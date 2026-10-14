# -*- coding: utf-8 -*-
"""探测番茄榜单的分页能力。

星月用自建后端做了分页，说明「取全榜单」是可行的。
但我要找到**番茄原站**的分页方式，不依赖第三方服务。

线索：
  1. 榜单页常带 category_id / gender / rank_type 之类的筛选参数
  2. reader 状态里的 rank 结构可能含 total / has_more
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)


def state(html: str):
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


print("=" * 76)
print("[1] 榜单页的 rank 结构全貌")
print("=" * 76)
st, html = get(f"{BASE}/rank")
d = state(html)
if not d:
    print("  解析失败")
    sys.exit(1)

rank = d.get("rank", {})
print(f"  rank 顶层键: {', '.join(rank.keys())}")
print()
for k, v in rank.items():
    if isinstance(v, list):
        print(f"  {k}: list({len(v)})")
        if v and isinstance(v[0], dict):
            print(f"      首项字段: {', '.join(list(v[0].keys())[:18])}")
    elif isinstance(v, dict):
        print(f"  {k}: dict  键={', '.join(list(v.keys())[:14])}")
    else:
        print(f"  {k}: {str(v)[:100]}")

print()
print("=" * 76)
print("[2] 找分页/筛选参数线索")
print("=" * 76)
# URL 参数
urls = re.findall(r"https?://[^\s\"'<>]{0,200}rank[^\s\"'<>]{0,200}", html)
seen = set()
for u in urls[:10]:
    u2 = u.replace("\\u002F", "/")
    if u2 not in seen:
        seen.add(u2)
        print(f"  {u2[:150]}")

# 页面里的可点击筛选（分类 tab）
cats = re.findall(r'"category_id"\s*:\s*"?(\d+)"?', html)
print(f"\n  category_id 出现: {len(cats)} 次  样例={cats[:10]}")

genders = re.findall(r'"gender"\s*:\s*(\d+)', html)
print(f"  gender 出现: {len(genders)} 次  样例={genders[:10]}")

# 是否有 total / has_more
for k in ("total", "has_more", "hasMore", "next_page", "nextPage", "page", "offset"):
    n = len(re.findall(rf'"{k}"\s*:', html))
    if n:
        print(f"  '{k}' 出现: {n} 次")

print()
print("=" * 76)
print("[3] 试各类 URL 变体")
print("=" * 76)
variants = [
    "/rank",
    "/rank?category_id=0",
    "/rank?gender=0",
    "/rank?gender=1",
    "/rank/1",
    "/rank?page=2",
    "/rank?page_index=1",
    "/rank?type=new",
    "/rank?rank_type=new",
    "/library",
    "/page/rank",
]
for v in variants:
    st, body = get(f"{BASE}{v}")
    size = len(body) if body else 0
    marker = ""
    if st == 200 and size > 1000:
        d2 = state(body)
        if d2:
            r2 = d2.get("rank", {})
            lst = r2.get("book_list") if isinstance(r2, dict) else None
            if isinstance(lst, list):
                marker = f"  book_list={len(lst)}"
                # 首本 ID，用于判断翻页是否真的换了内容
                if lst and isinstance(lst[0], dict):
                    marker += f"  首本={str(lst[0].get('bookName',''))[:14]}"
    print(f"  {v:<28} -> {st}  {size:>7} 字节{marker}")
