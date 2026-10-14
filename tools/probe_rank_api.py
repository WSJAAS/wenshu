# -*- coding: utf-8 -*-
"""找榜单分页的真实接口。

现象：
  - 服务端渲染只给 10 本，total_num=100
  - /rank/x_y_z_N 的第4段不是页码（11 页内容相同）
  - rank.defaultPage = 1 → 页码是前端行为

思路：榜单页的 JS bundle 里一定有分页请求。
从 HTML 里取出 script 地址，下载后搜 rank 相关接口。
"""
from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def get(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)


st, html = get(f"{BASE}/rank")
print(f"榜单页: {st}  {len(html)} 字节")

# 取出所有 script src
scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
print(f"\n脚本数: {len(scripts)}")
for s in scripts[:20]:
    print(f"  {s[:110]}")

# 内联脚本
inline = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S)
print(f"\n内联脚本数: {len(inline)}")
for i, s in enumerate(inline):
    if len(s) > 200:
        print(f"  [{i}] {len(s)} 字符  开头: {s[:150]!r}")

print()
print("=" * 76)
print("在 JS bundle 里搜榜单接口")
print("=" * 76)

KEYWORDS = ["/api/rank", "rank/list", "rank_list", "book_list", "rankPage",
            "rank/page", "getRank", "rankApi", "rankType", "defaultPage"]

checked = 0
for src in scripts[:25]:
    url = src if src.startswith("http") else f"{BASE}{src}"
    st2, js = get(url)
    if st2 != 200 or len(js) < 1000:
        continue
    checked += 1
    hits = []
    for kw in KEYWORDS:
        idx = js.find(kw)
        if idx >= 0:
            ctx = js[max(0, idx - 120): idx + 200].replace("\n", " ")
            hits.append((kw, ctx))
    if hits:
        print(f"\n--- {src.split('/')[-1][:60]}  ({len(js):,} 字符) ---")
        for kw, ctx in hits[:6]:
            print(f"  含 {kw!r}:")
            print(f"    {ctx[:300]}")

print(f"\n已检查 {checked} 个 bundle")

# 兜底：在首页 HTML 里搜
print()
print("=" * 76)
print("在榜单页 HTML 内联数据里搜接口路径")
print("=" * 76)
for pat in (r'"/[a-z0-9_/\-]*rank[a-z0-9_/\-]*"', r"'/[a-z0-9_/\-]*rank[a-z0-9_/\-]*'"):
    found = set(re.findall(pat, html, re.I))
    for f in sorted(found)[:25]:
        print(f"  {f}")
