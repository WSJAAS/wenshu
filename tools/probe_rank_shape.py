# -*- coding: utf-8 -*-
"""搞清 /rank/{gender}_{type}_{category} 的完整分页拼法。

已发现 URL 形态：/rank/0_1_1139
需要确认：分页页码加在哪一段，以及各段含义。
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

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
    first = ""
    if lst and isinstance(lst[0], dict):
        bn = str(lst[0].get("bookName", ""))
        first = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c for c in bn)[:14]
    return (f"list={len(lst):>2} total={r.get('total_num')} "
            f"page={r.get('defaultPage')} 首本={first}")


print("=" * 78)
print("[1] 收集页面上的全部 /rank/ URL（含分页）")
print("=" * 78)
st, html = get(f"{BASE}/rank")
all_links = sorted(set(re.findall(r'href="(/rank[^"]*)"', html)))
print(f"  共 {len(all_links)} 个")

# 按形态分组
shapes = Counter()
for l in all_links:
    m = re.match(r"^/rank/(\d+)_(\d+)_(\d+)(?:_(\d+))?", l)
    if m:
        seg = m.groups()
        shape = f"{seg[0]}_{seg[1]}_{seg[2]}" + (f"_+第4段" if seg[3] else "")
        shapes[shape] += 1
    else:
        shapes[l.split("?")[0]] += 1

print("  形态分布:")
for s, n in shapes.most_common(12):
    print(f"    {s}  ×{n}")

print()
print("  样例（含第4段的）:")
for l in all_links:
    if re.match(r"^/rank/\d+_\d+_\d+_\d+", l):
        print(f"    {l}")
for l in all_links[:6]:
    print(f"    {l}")

print()
print("=" * 78)
print("[2] 分组含义探测")
print("=" * 78)
probes = [
    "/rank/0_1_1139",     # 女频 类型1 古风世情
    "/rank/0_1_1139_1",   # 疑似第 1 页
    "/rank/0_1_1139_2",   # 疑似第 2 页
    "/rank/0_1_1139_10",  # 第 10 页
    "/rank/1_1_1139",     # 换 gender
    "/rank/0_2_1139",     # 换 type
]
for u in probes:
    st, body = get(f"{BASE}{u}")
    r = rank_of(body)
    print(f"  {u:<28} -> {st}  {len(body):>6} 字节  {brief(r)}")
    time.sleep(1.2)

print()
print("=" * 78)
print("[3] 若是第4段分页，连取验证是否换内容")
print("=" * 78)
seen = {}
page_ok = 0
for p in range(1, 12):
    u = f"{BASE}/rank/0_1_1139_{p}"
    st, body = get(u)
    r = rank_of(body)
    if not r:
        print(f"  第{p:>2}页 -> {st} {len(body)} 字节  无数据")
        break
    lst = r.get("book_list") or []
    ids = [str(b.get("bookId")) for b in lst if isinstance(b, dict)]
    fresh = [i for i in ids if i not in seen]
    for i in ids:
        seen.setdefault(i, p)
    if not ids:
        print(f"  第{p:>2}页 -> 空列表，停止")
        break
    page_ok = p
    print(f"  第{p:>2}页 -> {len(ids):>2} 本  新 {len(fresh):>2}  累计 {len(seen)}")
    time.sleep(1.2)

print()
print(f"  成功翻页: {page_ok} 页  累计去重 {len(seen)} 本")
print(f"  平台声明总数: 100")

print()
print("=" * 78)
print("[4] 若第4段不是分页，找其它形式")
print("=" * 78)
alts = [
    "/rank/0_1_1139?page=2",
    "/rank/0_1_1139_2_2",
    "/rank/0_1_1139/p2",
    "/rank/1_1_1139_1",
]
for u in alts:
    st, body = get(f"{BASE}{u}")
    r = rank_of(body)
    print(f"  {u:<32} -> {st}  {brief(r)}")
    time.sleep(1.0)
