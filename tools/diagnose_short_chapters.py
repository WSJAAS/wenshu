# -*- coding: utf-8 -*-
"""追查 550 章任务的正文长度异常。

现象：平均 185 字/章，最少 35 字。而该书前 4 章实测是 2000+ 字。
说明大量章节只抓到了开头，但状态却标记为 ok。

这是「伪成功」——正是要防的那类问题。
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = r"X:\小说工程\wenshu\runtime\data\wenshu.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
cur.execute("SELECT chapters_json, catalog_json FROM source_tasks WHERE id LIKE 'da35b6b5%'")
row = cur.fetchone()
con.close()

ch = json.loads(row[0])
catalog = json.loads(row[1])

print("=" * 76)
print("[1] 正文长度分布")
print("=" * 76)
lens = sorted((v.get("chars") or 0) for v in ch.values())
n = len(lens)
print(f"  章数: {n}")
print(f"  最短: {lens[0]}    最长: {lens[-1]}    中位: {lens[n//2]}")
print(f"  总计: {sum(lens):,} 字")

# 分桶
buckets = Counter()
for L in lens:
    if L < 100:
        buckets["<100"] += 1
    elif L < 500:
        buckets["100-500"] += 1
    elif L < 1000:
        buckets["500-1000"] += 1
    elif L < 1500:
        buckets["1000-1500"] += 1
    elif L < 2000:
        buckets["1500-2000"] += 1
    else:
        buckets[">=2000"] += 1
print()
print("  长度分桶:")
for k in ("<100", "100-500", "500-1000", "1000-1500", "1500-2000", ">=2000"):
    if buckets[k]:
        bar = "█" * min(40, buckets[k])
        print(f"    {k:<12} {buckets[k]:>4} 章  {bar}")

print()
print("=" * 76)
print("[2] 对比：前 4 章 vs 整本平均")
print("=" * 76)
items = sorted(ch.values(), key=lambda v: v.get("order") or 0)
for v in items[:6]:
    print(f"  [{v.get('order'):>3}] {str(v.get('title'))[:22]:<24} {v.get('chars'):>6} 字")

print("  …")

for v in items[-4:]:
    print(f"  [{v.get('order'):>3}] {str(v.get('title'))[:22]:<24} {v.get('chars'):>6} 字")

print()
print("=" * 76)
print("[3] 检查 pua_before：是否真的解码过")
print("=" * 76)
pua_before = [v.get("pua_before") or 0 for v in ch.values()]
pua_after = [v.get("pua_after") or 0 for v in ch.values()]
print(f"  pua_before: 总计 {sum(pua_before):,}  有值章数 {sum(1 for p in pua_before if p)}")
print(f"  pua_after : 总计 {sum(pua_after):,}")
print()
print(f"  有乱码的章: {sum(1 for p in pua_before if p > 0)} / {n}")

print()
print("=" * 76)
print("[4] 短章抽样：看抓到的内容是什么")
print("=" * 76)
short = [v for v in items if (v.get("chars") or 0) < 200][:5]
print(f"  少于 200 字的章共 {sum(1 for v in items if (v.get('chars') or 0) < 200)} 章")
print()
for v in short:
    print(f"  [{v.get('order')}] {str(v.get('title'))[:26]}  ({v.get('chars')} 字)")
    txt = (v.get("text") or "")
    print(f"      正文: {txt[:150]!r}")
    print(f"      长度: {len(txt)}   pua_before={v.get('pua_before')}  status={v.get('status')}")
    print()

print("=" * 76)
print("[5] 对比：长章抽样")
print("=" * 76)
long_ = sorted(items, key=lambda v: -(v.get("chars") or 0))[:3]
for v in long_:
    print(f"  [{v.get('order')}] {str(v.get('title'))[:26]}  ({v.get('chars')} 字)")
    print(f"      开头: {(v.get('text') or '')[:100]!r}")
    print()

print("=" * 76)
print("[6] 判断")
print("=" * 76)
short_count = sum(1 for v in items if (v.get("chars") or 0) < 200)
if short_count > n * 0.3:
    print(f"  ⚠ {short_count}/{n} 章不足 200 字，但状态都是 ok")
    print("    这不是「抓取失败」被漏报，而是「抓到了不完整的内容却标成成功」")
    print("    属于伪成功，需要在抓取层加正文长度校验")
else:
    print(f"  短章 {short_count}/{n}，比例正常")
