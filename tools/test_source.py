# -*- coding: utf-8 -*-
"""校验抓取器：榜单、详情、目录、正文全链路。

严格判定：
  - 榜单条目的 bookId 可用
  - 详情页字段干净（书名/作者/简介 无残留私用区）
  - 目录条数与分卷结构正确
  - 正文逐章状态判定准确
  - 限速参数实际生效（不是空谈）
"""
from __future__ import annotations

import os
import sys
import time

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

from app.services.fanqie_decoder import count_pua  # noqa: E402
from app.services.fanqie_source import (  # noqa: E402
    BASE, BlockedError, ChapterRef, FanqieClient, FanqieError,
)

FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


# 保守设置：测试时也不快跑
client = FanqieClient(interval=1.0, jitter=0.3, max_retries=2)

print("=" * 74)
print("[1] 榜单")
print("=" * 74)
try:
    t0 = time.monotonic()
    items, total = client.fetch_rank()
    el = time.monotonic() - t0
    print(f"  取得 {len(items)} 条，平台声明总数 {total}，用时 {el:.1f}s")
    check("取到条目", len(items) > 0, f"{len(items)} 条")
    check("bookId 有效", all(i["book_id"] for i in items))
    if items:
        print()
        print(f"  {'bookId':<20} {'在读':>8} {'字数':>8}  书名（榜单页，可能带混淆）")
        for it in items[:8]:
            title = it["title_raw"]
            masked = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c for c in title)
            print(f"  {it['book_id']:<20} {it['read_count']:>8} "
                  f"{it['word_count']:>8}  {masked}")
    check("榜单数与声明数不等（说明首屏只是部分）",
          len(items) != total or total == 0,
          f"{len(items)} vs {total}")
except Exception as e:
    check("榜单抓取", False, str(e)[:120])

print()
print("=" * 74)
print("[2] 详情 + 目录")
print("=" * 74)
book = None
chapters = []
if items:
    target_id = items[0]["book_id"]
    try:
        t0 = time.monotonic()
        book, chapters = client.fetch_book(target_id)
        el = time.monotonic() - t0
        print(f"  用时 {el:.1f}s")
        print()
        print(f"  bookId   : {book.book_id}")
        print(f"  书名     : {book.title}")
        print(f"  作者     : {book.author}")
        print(f"  分类     : {book.category}  / {book.tags}")
        print(f"  字数     : {book.word_count:,}")
        print(f"  在读     : {book.read_count:,}")
        print(f"  状态     : {book.status}")
        print(f"  最新章节 : {book.last_chapter_title}")
        print(f"  封面     : {book.cover_url[:70]}")
        print(f"  简介     : {book.abstract[:70]}")
        print()
        print(f"  目录     : {len(chapters)} 章")
        vols = {}
        for c in chapters:
            vols[c.volume] = vols.get(c.volume, 0) + 1
        for v, n in list(vols.items())[:6]:
            print(f"    {v or '(无卷名)'}  {n} 章")
        print()
        print(f"  首章     : [{chapters[0].order}] {chapters[0].title}  itemId={chapters[0].item_id}")
        print(f"  末章     : [{chapters[-1].order}] {chapters[-1].title}")

        check("书名干净（无混淆）", count_pua(book.title) == 0, f"title={book.title!r}")
        check("作者干净", count_pua(book.author) == 0, f"author={book.author!r}")
        check("简介干净", count_pua(book.abstract) == 0)
        check("目录非空", len(chapters) > 0, f"{len(chapters)} 章")
        check("章节 itemId 齐全", all(c.item_id for c in chapters))
        check("章节标题干净",
              all(count_pua(c.title) == 0 for c in chapters),
              f"带混淆的: {sum(1 for c in chapters if count_pua(c.title))} 个")
        check("章号连续", [c.order for c in chapters] == list(range(1, len(chapters) + 1)))
        locked = sum(1 for c in chapters if c.is_locked or c.need_pay)
        print(f"       锁定/付费章: {locked} 个")
    except Exception as e:
        check("详情抓取", False, str(e)[:160])

print()
print("=" * 74)
print("[3] 正文逐章")
print("=" * 74)
if chapters:
    sample = chapters[:3]
    print(f"  {'章节':<22} {'状态':<12} {'原长度':>7} {'解码后':>7} {'残留':>5}")
    print("  " + "-" * 62)
    t0 = time.monotonic()
    contents = []
    for ref in sample:
        try:
            c = client.fetch_chapter(ref)
            contents.append(c)
            flag = "" if c.status == "ok" else "  ←"
            print(f"  {c.title[:20]:<22} {c.status:<12} "
                  f"{len(c.text):>7} {c.chars:>7} {c.pua_after:>5}{flag}")
        except BlockedError as e:
            print(f"  {ref.title[:20]:<22} 被风控暂停: {e}")
            break
    el = time.monotonic() - t0
    print(f"  用时 {el:.1f}s（{len(contents)} 章）")

    ok = [c for c in contents if c.status == "ok"]
    check("全部章节成功", len(ok) == len(sample), f"{len(ok)}/{len(sample)}")
    check("解码后无残留", all(c.pua_after == 0 for c in ok))
    check("原文非空", all(c.text.strip() for c in ok))

    if ok:
        print()
        print("  第1章前 3 段：")
        for i, p in enumerate([x for x in ok[0].text.split("\n\n") if x.strip()][:3], 1):
            print(f"    [{i}] {p[:86]}")

print()
print("=" * 74)
print("[4] 限速实际生效")
print("=" * 74)
if len(chapters) >= 3:
    c2 = FanqieClient(interval=1.5, jitter=0.0, max_retries=0)
    t0 = time.monotonic()
    n = 0
    for ref in chapters[:3]:
        try:
            c2.fetch_chapter(ref)
            n += 1
        except Exception:
            break
    span = time.monotonic() - t0
    expected_min = 1.5 * (n - 1)
    print(f"  {n} 次请求，间隔 1.5s，实际耗时 {span:.2f}s（理论下限 {expected_min:.1f}s）")
    check("限速生效", span >= expected_min * 0.9,
          f"{span:.2f}s >= {expected_min*0.9:.2f}s")

print()
print("=" * 74)
print("[5] 错误处理")
print("=" * 74)
c3 = FanqieClient(interval=0.5, jitter=0.2, max_retries=1)
try:
    b, ch = c3.fetch_book("1")
    check("错误 ID 应报错", False, f"却返回了 book={b.title!r} chapters={len(ch)}")
except FanqieError as e:
    check("错误 ID 报 FanqieError", True, str(e)[:80])
except Exception as e:
    check("错误 ID 报 FanqieError", False, f"{type(e).__name__}: {e}")

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
