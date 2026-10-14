# -*- coding: utf-8 -*-
"""查清两个解析问题：categoryV2 结构、章节锁定字段的真实语义。"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

from app.services.fanqie_source import BASE, FanqieClient, _extract_state  # noqa: E402

c = FanqieClient(interval=0.5, jitter=0.2, max_retries=1)

for book_id, label in (
    ("7231424472606051384", "惹金枝（完结）"),
    ("7638631629665078297", "父皇他两辈子都在装（完结）"),
    ("7423691438321323070", "穿乱世（连载）"),
):
    print("=" * 74)
    print(f"《{label}》  {book_id}")
    print("=" * 74)
    try:
        html = c._get(f"{BASE}/page/{book_id}")
    except Exception as e:
        print(f"  抓取失败: {e}")
        continue
    st = _extract_state(html)
    page = st["page"]

    print(f"  category   = {page.get('category')!r}")
    raw = page.get("categoryV2")
    if isinstance(raw, str) and raw.strip():
        try:
            cats = json.loads(raw)
            names = [x.get("Name") for x in cats if isinstance(x, dict)]
            mains = [x.get("Name") for x in cats
                     if isinstance(x, dict) and x.get("MainCategory")]
            print(f"  categoryV2 = {len(cats)} 项，主分类={mains}")
            print(f"               全部: {names}")
        except json.JSONDecodeError as e:
            print(f"  categoryV2 解析失败: {e}")
            print(f"    原始: {raw[:150]}")
    else:
        print(f"  categoryV2 = {raw!r}")

    vols = page.get("chapterListWithVolume") or []
    chs = [ch for v in vols if isinstance(v, list) for ch in v if isinstance(ch, dict)]
    print(f"  章数: {len(chs)}")
    for f in ("needPay", "isChapterLock", "isPaidPublication", "isPaidStory"):
        cnt = Counter(str(ch.get(f)) for ch in chs)
        print(f"    {f:<20} {dict(cnt)}")

    print(f"  前 3 章: " + " | ".join(
        f"{ch.get('title','')[:12]}[pay={ch.get('needPay')},lock={ch.get('isChapterLock')}]"
        for ch in chs[:3]))
    print(f"  末 3 章: " + " | ".join(
        f"{ch.get('title','')[:12]}[pay={ch.get('needPay')},lock={ch.get('isChapterLock')}]"
        for ch in chs[-3:]))
    print()
