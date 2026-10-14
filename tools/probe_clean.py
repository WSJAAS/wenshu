# -*- coding: utf-8 -*-
"""关键验证：详情页文本是否干净，以及章节 itemId 能否正确提取。"""
from __future__ import annotations

import json
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
BOOK_ID = "7231424472606051384"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_state(raw: str):
    start = raw.find("window.__INITIAL_STATE__")
    if start < 0:
        return None
    brace = raw.find("{", start)
    blob = raw[brace:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            return json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
    return None


def has_pua(s: str) -> bool:
    return any(0xE000 <= ord(c) <= 0xF8FF for c in s)


print("=" * 72)
print("[1] 详情页文本是否含 PUA 乱码")
print("=" * 72)
raw = fetch(f"https://fanqienovel.com/page/{BOOK_ID}")
data = parse_state(raw)
page = data.get("page", {})

for field in ("bookName", "author", "abstract", "lastChapterTitle"):
    v = page.get(field)
    if isinstance(v, str):
        cps = " ".join(f"{ord(c):04X}" for c in v[:12])
        print(f"  {field}:")
        print(f"    值    : {v[:70]}")
        print(f"    含PUA : {has_pua(v)}")
        print(f"    码位  : {cps}")

print()
print("=" * 72)
print("[2] 章节列表结构（chapterListWithVolume）")
print("=" * 72)
vols = page.get("chapterListWithVolume") or []
print(f"  分卷数: {len(vols)}")
total = 0
flat: list[dict] = []
for vi, vol in enumerate(vols):
    if not isinstance(vol, list):
        continue
    total += len(vol)
    for ch in vol:
        if isinstance(ch, dict):
            flat.append(ch)
print(f"  章节总数: {total}")
if flat:
    print(f"\n  首章字段: {', '.join(list(flat[0].keys()))}")
    print()
    for ch in flat[:5]:
        print(f"    itemId={ch.get('itemId')}  title={str(ch.get('title'))[:36]!r}  "
              f"卷={ch.get('volume_name')}")
    print("    ...")
    for ch in flat[-2:]:
        print(f"    itemId={ch.get('itemId')}  title={str(ch.get('title'))[:36]!r}")

    titles_with_pua = [ch for ch in flat if has_pua(str(ch.get("title", "")))]
    print(f"\n  章节标题含 PUA 的: {len(titles_with_pua)} / {len(flat)}")
    print(f"  bookName 含 PUA  : {has_pua(str(page.get('bookName','')))}")

print()
print("=" * 72)
print("[3] 榜单页 vs 详情页：同一本书")
print("=" * 72)
rank_raw = fetch("https://fanqienovel.com/rank")
rank_data = parse_state(rank_raw)
rb = None
for b in (rank_data.get("rank", {}).get("book_list") or []):
    if b.get("bookId") == BOOK_ID:
        rb = b
        break
if rb:
    print(f"  榜单页书名: {rb.get('bookName')!r}  含PUA={has_pua(str(rb.get('bookName')))}")
    print(f"  详情页书名: {page.get('bookName')!r}  含PUA={has_pua(str(page.get('bookName')))}")
    print()
    if has_pua(str(rb.get("bookName"))) and not has_pua(str(page.get("bookName"))):
        print("  ★ 结论：乱码只在榜单页，详情页是干净文本")
        print("     → 用 bookId 跳详情页即可拿到正确书名，无需破解字体")

print()
print("=" * 72)
print("[4] 阅读页能否取正文")
print("=" * 72)
if flat:
    item_id = flat[0].get("itemId")
    print(f"  测试首章 itemId: {item_id}")
    for url in (f"https://fanqienovel.com/reader/{item_id}",):
        try:
            rbody = fetch(url)
            rd = parse_state(rbody)
            print(f"  {url}")
            print(f"    HTTP OK  len={len(rbody)}  STATE={'__INITIAL_STATE__' in rbody}")
            if rd:
                rd_keys = list(rd.keys())
                print(f"    顶层键: {', '.join(rd_keys[:15])}")
                rd_reader = rd.get("reader") or {}
                print(f"    reader 键: {', '.join(list(rd_reader.keys())[:15])}")
                # 找正文
                found = None
                def dig(o, d=0):
                    if d > 6:
                        return None
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if isinstance(v, str) and len(v) > 300 and not v.startswith("http"):
                                return (k, v)
                            r2 = dig(v, d + 1)
                            if r2:
                                return r2
                    elif isinstance(o, list):
                        for v in o[:4]:
                            r2 = dig(v, d + 1)
                            if r2:
                                return r2
                    return None
                found = dig(rd)
                if found:
                    k, v = found
                    print(f"    ★ 字段 '{k}' 长度 {len(v)}")
                    print(f"      开头: {v[:150]}")
                    print(f"      含PUA: {has_pua(v[:3000])}")
                else:
                    print("    未找到长文本")
        except Exception as e:
            print(f"    -> {str(e).splitlines()[0]}")
