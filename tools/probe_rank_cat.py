# -*- coding: utf-8 -*-
"""确认分类榜单 /rank/x_y_z 的翻页能力。

已确认：
  - rank.total_num = 100（平台声明）
  - rank.defaultPage = 1
  - /rank/0_1_1139 能取到该分类的榜单
  - /rank/x_y_z_N 的第4段不是页码

现在查：分类路由本身是否有页码形态，以及 total_num 与实取数的关系。
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def rank_of(html):
    i = html.find("window.__INITIAL_STATE__")
    if i < 0:
        return None
    b = html.find("{", i)
    blob = html[b:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            d = json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
        r = d.get("rank")
        return r if isinstance(r, dict) else None
    return None


def brief(r):
    if not r:
        return "无 rank"
    lst = r.get("book_list") or []
    return f"list={len(lst):>2}  total_num={r.get('total_num')}  defaultPage={r.get('defaultPage')}"


print("=" * 78)
print("[1] 同一分类的不同 URL 形态")
print("=" * 78)
forms = [
    "/rank/0_1_1139",
    "/rank/0_1_1139_1",
    "/rank/0_1_1139_2",
    "/rank/0_1_1139_3",
    "/rank/0_1_1139/2",
    "/rank/0_1_1139?p=2",
    "/rank/0_1_1139?page=2",
]
ids_by_form = {}
for u in forms:
    st, body = get(f"{BASE}{u}")
    r = rank_of(body)
    lst = (r or {}).get("book_list") or []
    ids = tuple(str(b.get("bookId")) for b in lst if isinstance(b, dict))
    ids_by_form[u] = ids
    print(f"  {u:<28} -> {st}  {brief(r)}")
    time.sleep(1.1)

print()
baseline = ids_by_form.get("/rank/0_1_1139", ())
print(f"  与基准（/rank/0_1_1139）相比:")
for u, ids in ids_by_form.items():
    if not ids:
        continue
    same = "相同" if ids == baseline else f"不同（首本={ids[0][:12]}）"
    print(f"    {u:<28} {same}")

print()
print("=" * 78)
print("[2] 同分类不同 type 段")
print("=" * 78)
seen = set()
for t in range(1, 5):
    u = f"/rank/0_{t}_1139"
    st, body = get(f"{BASE}{u}")
    r = rank_of(body)
    lst = (r or {}).get("book_list") or []
    ids = [str(b.get("bookId")) for b in lst if isinstance(b, dict)]
    fresh = [i for i in ids if i not in seen]
    seen.update(ids)
    print(f"  {u:<24} -> list={len(lst):>2}  新 {len(fresh):>2}  "
          f"累计去重 {len(seen)}  total_num={(r or {}).get('total_num')}")
    time.sleep(1.1)

print()
print("=" * 78)
print("[3] 跨分类累计（这是「完整榜单」的现实路径）")
print("=" * 78)
cats = ["1139", "23", "248", "1015", "8", "746"]
all_ids = {}
for c in cats:
    u = f"/rank/0_1_{c}"
    st, body = get(f"{BASE}{u}")
    r = rank_of(body)
    lst = (r or {}).get("book_list") or []
    for b in lst:
        if isinstance(b, dict):
            bid = str(b.get("bookId"))
            all_ids.setdefault(bid, c)
    print(f"  分类 {c:<6} -> {len(lst):>2} 本  累计去重 {len(all_ids)}")
    time.sleep(1.1)

print()
print(f"  6 个分类合计去重: {len(all_ids)} 本")

print()
print("=" * 78)
print("[4] 页面是否有「加载更多」的接口线索")
print("=" * 78)
st, html = get(f"{BASE}/rank/0_1_1139")
for kw in ("加载更多", "loadmore", "load_more", "has_more", "next_page",
           "分页", "pageSize", "page_size", "offset", "getRankList", "rankList"):
    n = len(re.findall(re.escape(kw), html, re.I))
    if n:
        print(f"  含 {kw!r}: {n} 次")

# 榜单页有没有内嵌 rankList 数据（不止 book_list）
i = html.find("window.__INITIAL_STATE__")
if i > 0:
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
    print(f"\n  rank 全部键: {', '.join(r.keys())}")
    for k, v in r.items():
        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            print(f"    {k}: list({len(v)})  首项键={', '.join(list(v[0].keys())[:12])}")
