# -*- coding: utf-8 -*-
"""验证榜单书名的兜底：enrich=false 时不应是空白。"""
from __future__ import annotations

import io
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import FanqieClient  # noqa: E402

c = FanqieClient(interval=1.2)
items, total = c.fetch_rank()

print(f"榜单 {len(items)} 本，平台声明 {total} 本")
print()
print(f"  {'书名':<26}{'长度':>5}  {'可能乱码':<9} 原始")
print("  " + "-" * 78)
blank = 0
for it in items:
    t = it.get("title") or ""
    if not t:
        blank += 1
    raw = (it.get("title_raw") or "")[:26]
    print(f"  {t[:24]:<26}{len(t):>5}  "
          f"{str(it.get('title_may_be_garbled')):<9} {raw}")

print()
print(f"  空书名: {blank} / {len(items)}")
if blank == 0:
    print("  ✓ 全部有书名 —— enrich=false 时不再是空白")
else:
    print("  ✗ 仍有空书名")
