# -*- coding: utf-8 -*-
"""验证 read_count 修正与乱码标记。"""
from __future__ import annotations

import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with urllib.request.urlopen(
    "http://127.0.0.1:8010/api/source/rank?enrich=true", timeout=300
) as r:
    d = json.loads(r.read().decode("utf-8"))

print(f"取得 {d['fetched']} 本，详情补取成功 {d['enriched']}，"
      f"失败 {d['enrich_failed']}，书名可能乱码 {d['garbled_titles']}")
print()
print(f"{'书名':<26}{'在读':>10}{'字数':>11}  分类")
print("-" * 70)
for it in d["items"]:
    flag = "  ⚠书名可能乱码" if it.get("title_may_be_garbled") else ""
    print(f"{it.get('title','')[:24]:<26}"
          f"{it.get('read_count',0):>10,}"
          f"{it.get('word_count',0):>11,}  "
          f"{it.get('category','')}{flag}")

print()
print("覆盖说明:", d["coverage_note"])
print("时间口径:", d["platform_note"])

# 检查是否还有 read_count=0 而详情页有值的情况
zero = [it for it in d["items"] if it.get("read_count", 0) == 0]
print()
if zero:
    print(f"⚠ 仍有 {len(zero)} 本读数为 0:")
    for it in zero:
        print(f"   {it.get('title','')[:24]}  "
              f"榜单原始 readCount={it.get('read_count_raw')} "
              f"read_count={it.get('read_count_alt')}")
else:
    print("✓ 所有上榜书籍都有非零读数")
