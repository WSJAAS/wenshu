# -*- coding: utf-8 -*-
"""从星月前端 bundle 中提取榜单分页调用的参数结构。

目标：搞清 /v1/ai-rankings/page 与 /total 的参数名与语义，
以便在文枢里实现「取全榜单」。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

CHUNKS = Path(r"X:\小说工程\star-moon-research\raw-assets\chunks")
TARGETS = ["index-cd497c51.js", "index-e2551464.js"]

for name in TARGETS:
    p = CHUNKS / name
    if not p.is_file():
        print(f"找不到 {name}")
        continue
    src = p.read_text(encoding="utf-8", errors="replace")
    print("=" * 76)
    print(f"{name}  ({len(src):,} 字符)")
    print("=" * 76)

    # 找 ai-rankings 出现的上下文
    for m in re.finditer(r"ai-rankings/[a-z\-/]+", src):
        start = max(0, m.start() - 260)
        end = min(len(src), m.end() + 260)
        snippet = src[start:end]
        print(f"\n--- {m.group(0)} ---")
        print("  " + snippet.replace("\n", " ")[:500])

    print()
    # 找分页相关参数名
    for kw in ("page_size", "pageSize", "page_index", "pageIndex", "offset",
               "limit", "category_id", "categoryId", "gender", "rank_type",
               "rankType", "creation_status", "word_count", "sort"):
        found = re.findall(rf'["\']({kw})["\']', src)
        if found:
            print(f"  含参数名 {kw!r}: {len(found)} 次")
