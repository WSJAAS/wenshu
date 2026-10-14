# -*- coding: utf-8 -*-
"""在番茄主 bundle 里找榜单分页接口。"""
from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

BUNDLES = [
    "https://lf-fe.fqnovelstatic.com/obj/novel-fanqie-fe/toutiao/muye/Home.f2187393.js",
    "https://lf-fe.fqnovelstatic.com/obj/novel-fanqie-fe/toutiao/muye/js/muye_5f7ec5dd.js",
]


def get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)


for url in BUNDLES:
    st, js = get(url)
    name = url.split("/")[-1]
    print("=" * 78)
    print(f"{name}   {st}   {len(js):,} 字符")
    print("=" * 78)
    if st != 200:
        continue

    # 找所有像接口路径的字符串
    paths = set()
    for m in re.finditer(r'["\'`](/[a-z0-9_/\-]{4,80})["\'`]', js, re.I):
        p = m.group(1)
        if any(x in p.lower() for x in ("rank", "book", "list", "page", "novel", "api")):
            paths.add(p)

    print(f"\n候选路径 {len(paths)} 个:")
    for p in sorted(paths)[:60]:
        print(f"  {p}")

    # 搜 rank 相关函数名与调用
    print(f"\n含 'rank' 的代码片段:")
    shown = 0
    for m in re.finditer(r"rank", js, re.I):
        start = max(0, m.start() - 100)
        end = min(len(js), m.end() + 160)
        ctx = js[start:end].replace("\n", " ")
        # 只挑像接口调用的
        if any(k in ctx for k in ("url:", "fetch(", "request", "get(", "post(")):
            print(f"  …{ctx[:260]}…")
            shown += 1
            if shown >= 12:
                break

    if not shown:
        print("  （无可疑片段）")
    print()
