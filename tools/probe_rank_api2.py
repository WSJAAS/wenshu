# -*- coding: utf-8 -*-
"""实测 /api/rank/* 三个接口，找出取全榜单的方式。"""
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
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        return None, str(e)[:200]


def show(label: str, path: str, params: dict | None = None):
    st, body = call(path, params)
    print(f"\n  {label}")
    print(f"    {path}  {params or ''}")
    print(f"    -> {st}  {len(body)} 字节")
    if st == 200 and body.strip():
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                print(f"    顶层键: {', '.join(list(d.keys())[:14])}")
                # 找列表
                for k, v in d.items():
                    if isinstance(v, list):
                        print(f"    {k}: list({len(v)})")
                        if v and isinstance(v[0], dict):
                            print(f"      首项字段: {', '.join(list(v[0].keys())[:16])}")
                            # 打印一个样例
                            s = json.dumps(v[0], ensure_ascii=False)
                            print(f"      样例: {s[:400]}")
                    elif isinstance(v, dict):
                        print(f"    {k}: dict  键={', '.join(list(v.keys())[:12])}")
                    else:
                        print(f"    {k}: {str(v)[:80]}")
            elif isinstance(d, list):
                print(f"    列表({len(d)})")
                if d and isinstance(d[0], dict):
                    print(f"      首项字段: {', '.join(list(d[0].keys())[:16])}")
                    print(f"      样例: {json.dumps(d[0], ensure_ascii=False)[:400]}")
        except json.JSONDecodeError:
            print(f"    非 JSON: {body[:250]!r}")
    elif body.strip():
        print(f"    {body[:200]!r}")
    return st, body


print("=" * 78)
print("[1] /api/rank/category/list —— 分类列表")
print("=" * 78)
show("无参数", "/api/rank/category/list")
time.sleep(1.2)

print()
print("=" * 78)
print("[2] /api/rank/recommend/list —— 推荐榜（可能带分页）")
print("=" * 78)
show("默认", "/api/rank/recommend/list")
time.sleep(1.2)
show("带分页", "/api/rank/recommend/list", {"page": 2, "page_size": 20})
time.sleep(1.2)
show("带分类", "/api/rank/recommend/list", {"category_id": 1139, "gender": 0})
time.sleep(1.2)

print()
print("=" * 78)
print("[3] /api/rank/recent/update/list —— 最近更新榜")
print("=" * 78)
show("默认", "/api/rank/recent/update/list")
time.sleep(1.2)

print()
print("=" * 78)
print("[4] 若上面是 404，试带前缀的变体")
print("=" * 78)
for p in (
    "/api/rank/category/list",
    "/reading/bookapi/rank/category/list/v/",
    "/api/rank/list",
    "/api/rank/book/list",
):
    st, _ = call(p)
    print(f"  {p:<48} -> {st}")
    time.sleep(0.8)
