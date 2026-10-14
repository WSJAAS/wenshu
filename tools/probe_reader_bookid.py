# -*- coding: utf-8 -*-
"""确认阅读页能否反查所属书籍。

用户脚本在阅读页交接时只有 itemId，需要得到 bookId。
检查阅读页状态里有没有书籍标识。
"""
from __future__ import annotations

import io
import json
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import FanqieClient, _extract_state  # noqa: E402

client = FanqieClient(interval=1.2)

# 《惹金枝》第 1 章
ITEM = "7256994009498223144"
html = client._get(f"https://fanqienovel.com/reader/{ITEM}")
state = _extract_state(html)

print("=" * 72)
print("顶层键")
print("=" * 72)
print(f"  {list(state.keys())}")

reader = state.get("reader") or {}
print()
print("=" * 72)
print("reader 下的键")
print("=" * 72)
for k, v in reader.items():
    if isinstance(v, dict):
        print(f"  {k}: dict  键={list(v.keys())[:12]}")
    elif isinstance(v, list):
        print(f"  {k}: list({len(v)})")
    else:
        print(f"  {k}: {str(v)[:80]}")

cd = reader.get("chapterData") or {}
print()
print("=" * 72)
print("chapterData 全部字段")
print("=" * 72)
for k, v in cd.items():
    if isinstance(v, (dict, list)):
        print(f"  {k}: {type(v).__name__}({len(v)})")
    else:
        print(f"  {k}: {str(v)[:90]!r}")

# 找可能的 bookId
print()
print("=" * 72)
print("找 bookId 线索")
print("=" * 72)
BOOKISH = ("bookid", "book_id", "novelid", "novel_id")
found = {}


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k.lower() in BOOKISH and isinstance(v, (str, int)):
                found[f"{path}/{k}"] = v
            walk(v, f"{path}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o[:3]):
            walk(v, f"{path}[{i}]")


walk(state)
for k, v in list(found.items())[:20]:
    print(f"  {k} = {v}")

# 页面 HTML 里直接找
import re
ids = re.findall(r'"bookId"\s*:\s*"?(\d+)"?', html)
print()
print(f"  HTML 里 bookId 出现: {len(ids)} 次  去重: {sorted(set(ids))[:6]}")

print()
print("=" * 72)
print("判定")
print("=" * 72)
if found or ids:
    print("  ✓ 阅读页含书籍标识，可反查 bookId")
    if ids:
        print(f"    最直接的方式：从 HTML 里正则取 bookId → {ids[0]}")
else:
    print("  ✗ 阅读页未含书籍标识，需要额外请求")
