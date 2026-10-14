# -*- coding: utf-8 -*-
"""确定 /api/rank/recommend/list 的参数名与翻页能力。"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def call(path: str, params: dict | None = None, timeout: int = 25):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE}/rank",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)[:120]


def parse(body: str):
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return None
    data = d.get("data")
    if isinstance(data, dict):
        lst = data.get("list") or data.get("data") or []
        total = data.get("total")
        return lst, total
    if isinstance(data, list):
        return data, None
    return None, None


print("=" * 78)
print("[1] 完整看一条数据（字段全貌）")
print("=" * 78)
st, body = call("/api/rank/recommend/list")
d = json.loads(body)
lst, total = parse(body)
print(f"  total = {total}")
print(f"  list  = {len(lst)} 条")
if lst:
    print(f"\n  首条全部字段:")
    print("  " + json.dumps(lst[0], ensure_ascii=False, indent=2)[:1200])

print()
print("=" * 78)
print("[2] 试各种分页参数名")
print("=" * 78)
candidates = [
    {},
    {"page": 1}, {"page": 2}, {"page": 3},
    {"page_index": 1}, {"page_index": 2},
    {"page_num": 2},
    {"offset": 0}, {"offset": 10}, {"offset": 20},
    {"limit": 20}, {"limit": 50}, {"count": 20},
    {"per_page": 20},
    {"page": 2, "page_size": 10},
    {"cursor": ""},
]
base_first = None
for p in candidates:
    st, body = call("/api/rank/recommend/list", p)
    lst, total = parse(body)
    if lst is None:
        print(f"  {str(p):<34} -> 解析失败")
        time.sleep(0.9)
        continue
    first = str(lst[0].get("book_name") or lst[0].get("bookName") or "")[:16] if lst else ""
    bid = str(lst[0].get("book_id") or lst[0].get("bookId") or "") if lst else ""
    if base_first is None:
        base_first = bid
    tag = "（与默认相同）" if bid == base_first else "  ★变了"
    print(f"  {str(p):<34} -> {len(lst):>2} 条  total={total}  首本={first}{tag}")
    time.sleep(0.9)

print()
print("=" * 78)
print("[3] 分类参数")
print("=" * 78)
for p in (
    {"category": 1139},
    {"category_id": 1139},
    {"categoryId": 1139},
    {"gender": 0},
    {"gender": 1},
    {"rank_type": 1},
    {"type": 1},
    {"channel": 0},
):
    st, body = call("/api/rank/recommend/list", p)
    lst, total = parse(body)
    if lst is None:
        print(f"  {str(p):<28} -> 解析失败")
        time.sleep(0.9)
        continue
    first = str(lst[0].get("book_name") or "")[:16] if lst else ""
    bid = str(lst[0].get("book_id") or "") if lst else ""
    tag = "" if bid == base_first else "  ★变了"
    print(f"  {str(p):<28} -> {len(lst):>2} 条  total={total}  首本={first}{tag}")
    time.sleep(0.9)

print()
print("=" * 78)
print("[4] 若 total=100 而 list 只给部分，说明可翻页")
print("=" * 78)
st, body = call("/api/rank/recommend/list", {"book_type": -1, "gender": -1,
                                             "category_id": -1, "creation_status": -1,
                                             "word_count": -1, "sort": 0})
lst, total = parse(body)
if lst is not None:
    print(f"  传参考项目用过的参数组: {len(lst)} 条  total={total}")
