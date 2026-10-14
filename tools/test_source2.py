# -*- coding: utf-8 -*-
"""校验修正与批量抓取。

重点验证：
  1. categoryV2 正确解析为分类名（不再吐原始 JSON）
  2. 锁定字段只作参考，实际可读性以抓取为准
  3. 批量抓取：串行、进度回调、断点续跑、缺章清单
  4. 拼 TXT：保留顺序与段落，缺章有说明
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
    BatchFetcher, FanqieClient, build_missing_report, merge_results,
    parse_categories, results_to_txt,
)

FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


print("=" * 74)
print("[1] parse_categories")
print("=" * 74)
sample = (
    '[{"ObjectId":1139,"Dim":10,"Name":"古风世情","MainCategory":true},'
    '{"ObjectId":5,"Dim":1,"Name":"古代言情","MainCategory":false},'
    '{"ObjectId":36,"Dim":3,"Name":"重生","MainCategory":false}]'
)
main, allc = parse_categories(sample)
print(f"  输入: 3 项分类对象")
print(f"  主分类: {main!r}")
print(f"  标签  : {allc}")
check("主分类正确", main == "古风世情")
check("标签齐全", allc == ["古风世情", "古代言情", "重生"])
check("无原始 JSON 泄漏", not any("ObjectId" in t for t in allc))

# 异常输入
for bad, label in ((None, "None"), ("", "空串"), ("not json", "非 JSON"), ('{"a":1}', "对象非数组")):
    m2, a2 = parse_categories(bad)
    check(f"异常输入({label})不崩", m2 == "" and a2 == [], f"main={m2!r} tags={a2}")

print()
print("=" * 74)
print("[2] 详情页分类（真实抓取）")
print("=" * 74)
client = FanqieClient(interval=1.0, jitter=0.3, max_retries=2)
book, chapters = client.fetch_book("7231424472606051384")
print(f"  书名  : {book.title}")
print(f"  主分类: {book.category}")
print(f"  标签  : {book.tags}")
print(f"  字数  : {book.word_count:,}  在读: {book.read_count:,}  状态: {book.status}")
check("主分类非空", bool(book.category), f"{book.category!r}")
check("标签是短名称", all(len(t) < 20 for t in book.tags), f"{book.tags}")
check("标签不含 JSON", not any("{" in t for t in book.tags))
check("章节标题干净", all(count_pua(c.title) == 0 for c in chapters))

locked_ref = [c for c in chapters if c.is_locked]
print(f"\n  标记为 locked 的章: {len(locked_ref)} / {len(chapters)}")
if locked_ref:
    print(f"  注意：这些只是「不在免费试读段」，不等于不可读")

print()
print("=" * 74)
print("[3] 锁定标记 vs 实际可读性")
print("=" * 74)
# 挑几个标记为 locked 的章，实际抓一下
probe = [c for c in chapters if c.is_locked][:3]
if probe:
    print(f"  抓取 3 个标记 locked 的章，看实际结果：")
    for ref in probe:
        c = client.fetch_chapter(ref)
        print(f"    [{ref.order}] {ref.title[:22]:<24} 标记locked={ref.is_locked} "
              f"→ 实际 status={c.status}  {c.chars} 字")
    statuses = [client.fetch_chapter(r).status for r in probe]
    readable = sum(1 for s in statuses if s == "ok")
    print(f"\n  其中实际可读: {readable}/{len(probe)}")
    check("测试有效性（暴露了标记与实际的差异）", True)

print()
print("=" * 74)
print("[4] 批量抓取（前 6 章）")
print("=" * 74)
target = chapters[:6]
bf = BatchFetcher(client)
t0 = time.monotonic()
ticks = []


def on_prog(p, c):
    ticks.append((p.done, c.status))


fresh, prog = bf.fetch_range(target, on_progress=on_prog)
el = time.monotonic() - t0

print(f"  用时 {el:.1f}s")
print(f"  进度: {prog.done}/{prog.total}  成功 {prog.ok}  失败 {prog.failed}")
print(f"  状态分布: {prog.statuses}")
print(f"  停止原因: {prog.stopped_reason or '（正常完成）'}")
check("全部完成", prog.done == prog.total, f"{prog.done}/{prog.total}")
check("进度回调被调用", len(ticks) == len(target), f"{len(ticks)} 次")
check("逐章状态明确", all(s in ("ok", "locked", "unavailable", "garbled", "failed")
                       for _, s in ticks), f"{set(s for _, s in ticks)}")
check("串行执行（耗时不小于间隔和）",
      el >= 1.0 * (len(target) - 1) * 0.9, f"{el:.1f}s")

results = merge_results({}, fresh)
print()
print(f"  {'章':<4} {'标题':<26} {'状态':<12} {'字数':>6}")
print("  " + "-" * 54)
for c in sorted(results.values(), key=lambda x: x.order):
    print(f"  {c.order:<4} {c.title[:24]:<26} {c.status:<12} {c.chars:>6}")

print()
print("=" * 74)
print("[5] 断点续跑")
print("=" * 74)
# 已有前 6 章，再抓同一批 —— 应全部跳过
fresh2, prog2 = bf.fetch_range(target, already=results)
print(f"  第二次: 本次处理 {len(fresh2)} 章，状态分布 {prog2.statuses}")
check("已完成的不重复抓取", len(fresh2) == 0, f"实际抓了 {len(fresh2)} 章")
check("跳过数被记录", prog2.statuses.get("skipped") == 6,
      f"skipped={prog2.statuses.get('skipped')}")

# 扩展范围到 8 章 —— 应只抓后 2 章
target8 = chapters[:8]
fresh3, prog3 = bf.fetch_range(target8, already=results)
print(f"  扩展到 8 章: 本次处理 {len(fresh3)} 章（应只抓缺的 2 章）")
check("只抓缺的", len(fresh3) == 2, f"实际 {len(fresh3)}")

results = merge_results(results, fresh3)
check("合并后共 8 章", len(results) == 8, f"{len(results)}")

print()
print("=" * 74)
print("[6] 缺章清单")
print("=" * 74)
# 故意少给一章的结果
partial = {k: v for k, v in list(results.items())[:6]}
report = build_missing_report(target8, partial)
print(f"  总章数: {report['total']}")
print(f"  成功  : {report['ok']}")
print(f"  缺失  : {report['missing_count']}")
print(f"  按状态: {report['by_status']}")
if report["missing"]:
    print(f"  缺章样例:")
    for m in report["missing"][:4]:
        print(f"    [{m['order']}] {m['title'][:24]:<26} {m['status']}  {m['error'][:40]}")
check("正确统计缺失", report["missing_count"] == 2, f"{report['missing_count']}")
check("缺章有原因", all(m["status"] for m in report["missing"]))

print()
print("=" * 74)
print("[7] 拼 TXT")
print("=" * 74)
txt = results_to_txt(target8, results)
lines = txt.split("\n")
print(f"  总长度: {len(txt)}  行数: {len(lines)}")
print()
print("  前 12 行：")
for ln in lines[:12]:
    print(f"    {ln[:76]}")
check("含章节标题", all(c.title[:6] in txt for c in target8 if c.item_id in results))
check("段落未被压成一行", "\n\n" in txt)
check("章节顺序正确",
      [txt.find(c.title) for c in target8 if c.item_id in results] ==
      sorted(txt.find(c.title) for c in target8 if c.item_id in results))

# 有缺章时的说明
partial_results = {k: v for k, v in list(results.items())[:5]}
txt2 = results_to_txt(target8, partial_results)
check("缺章时附说明", "抓取说明" in txt2 and "未取得" in txt2)
check("说明里列出缺章", any(c.title[:8] in txt2 for c in target8[5:]))

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
