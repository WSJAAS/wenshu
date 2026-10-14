# -*- coding: utf-8 -*-
"""查清：带上已保存的 Cookie 后，阅读页反查为何失败。

不带 Cookie 时反查成功，带 Cookie 时失败。
对比两种情况的页面差异。
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

from app.services.fanqie_source import (  # noqa: E402
    FREE_CHAPTER_COUNT, FanqieClient, _extract_state, load_login_cookie,
)

ITEM = "7256994009498223144"

cookie = load_login_cookie()
print(f"已保存 Cookie: {len(cookie)} 字节")
print(f"  片段: {cookie[:80]}…")
print()

for label, ck in (("不带 Cookie", ""), ("带 Cookie", cookie)):
    print("=" * 72)
    print(f"{label}")
    print("=" * 72)
    client = FanqieClient(interval=1.2, cookie=ck)
    try:
        html = client._get(f"https://fanqienovel.com/reader/{ITEM}")
    except Exception as e:
        print(f"  取页异常: {type(e).__name__}: {e}")
        continue

    print(f"  HTML: {len(html):,} 字节")

    state = _extract_state(html)
    print(f"  state: {'有' if state else '无'}")
    if not state:
        # 看看是什么页面
        head = html[:400].replace("\n", " ")
        print(f"  开头: {head}")
        continue

    print(f"  顶层键: {list(state.keys())}")
    reader = state.get("reader")
    print(f"  reader: {type(reader).__name__}")
    if isinstance(reader, dict):
        print(f"    reader 键: {list(reader.keys())}")
        cd = reader.get("chapterData")
        print(f"    chapterData: {type(cd).__name__}")
        if isinstance(cd, dict):
            print(f"      bookId: {cd.get('bookId')!r}")
            print(f"      title : {str(cd.get('title'))[:40]!r}")
        else:
            print(f"      chapterData 值: {cd!r}")

    # seo 段常含登录态线索
    seo = state.get("seo") or {}
    if isinstance(seo, dict):
        for k in ("title", "description"):
            v = seo.get(k)
            if v:
                print(f"    seo.{k}: {str(v)[:80]}")

    bid = client.resolve_book_by_item(ITEM)
    print(f"  → resolve 结果: {bid!r}")
    print()
