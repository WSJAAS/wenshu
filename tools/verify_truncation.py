# -*- coding: utf-8 -*-
"""验证截断判定：preview 状态是否真的生效。

背景：550 章整本抓取时，540 章只有约 150 字却标记 ok。
根因是平台未登录时对第 11 章起只返回试读预览（约 200 字符），
而旧实现把它当成功。

判据：实测同一本书的第 8 / 11 / 20 章，看状态是否分别为
ok / preview / preview。
"""
from __future__ import annotations

import io
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import (  # noqa: E402
    FanqieClient, TRUNCATION_RATIO, build_missing_report,
)

print("=" * 76)
print("[1] 截断判定实测（《惹金枝》550 章）")
print("=" * 76)
print(f"  截断阈值 TRUNCATION_RATIO = {TRUNCATION_RATIO}")

client = FanqieClient(interval=1.2)
book, chapters = client.fetch_book("7231424472606051384")
print(f"  《{book.title}》 共 {len(chapters)} 章")
print()

by_order = {c.order: c for c in chapters}
targets = [8, 10, 11, 12, 20, 100]

print(f"  {'章':<5} {'标题':<20} {'状态':<10} {'取得':>7} {'申报':>7} {'比例':>7}  锁定")
print("  " + "-" * 76)

results = {}
for order in targets:
    ref = by_order.get(order)
    if not ref:
        continue
    c = client.fetch_chapter(ref)
    results[order] = c
    ratio = f"{c.chars / c.claimed_words * 100:.0f}%" if c.claimed_words else "—"
    print(f"  {order:<5} {str(c.title)[:18]:<20} {c.status:<10} "
          f"{c.chars:>7,} {c.claimed_words:>7,} {ratio:>7}  "
          f"{'是' if c.is_locked else '否'}")

print()
print("=" * 76)
print("[2] 判定结果")
print("=" * 76)
ok = [o for o, c in results.items() if c.status == "ok"]
pv = [o for o, c in results.items() if c.status == "preview"]
other = [o for o, c in results.items() if c.status not in ("ok", "preview")]

print(f"  ok      : {sorted(ok)}")
print(f"  preview : {sorted(pv)}")
print(f"  其它    : {sorted(other)}")

fails = []
if 8 in results:
    if results[8].status != "ok":
        fails.append(f"第 8 章应为 ok，实际 {results[8].status}")
if 11 in results:
    if results[11].status != "preview":
        fails.append(f"第 11 章应为 preview，实际 {results[11].status}")
if 20 in results:
    if results[20].status != "preview":
        fails.append(f"第 20 章应为 preview，实际 {results[20].status}")

print()
if fails:
    for f in fails:
        print(f"  [FAIL] {f}")
else:
    print("  [PASS] 前 10 章 ok、第 11 章起 preview —— 判定正确")

print()
print("=" * 76)
print("[3] preview 是否计入缺章")
print("=" * 76)
# 造一个「10 全文 + 2 预览」的场景
mixed = dict(results)
report = build_missing_report(
    [by_order[o] for o in sorted(results)],
    {by_order[o].item_id: mixed[o] for o in results},
)
print(f"  total         = {report['total']}")
print(f"  ok            = {report['ok']}")
print(f"  missing_count = {report['missing_count']}")
print(f"  by_status     = {report['by_status']}")
print()
if report["missing_count"] >= len(pv):
    print(f"  [PASS] {len(pv)} 个 preview 都计入了缺章，覆盖率不会被虚报")
else:
    print(f"  [FAIL] preview 未计入缺章")
    fails.append("preview 未计入缺章")

print()
print("=" * 76)
print("[4] 覆盖率计算对照")
print("=" * 76)
cov = report["ok"] / report["total"] * 100 if report["total"] else 0
print(f"  真实覆盖率: {report['ok']}/{report['total']} = {cov:.0f}%")
print(f"  若把 preview 当 ok: {report['ok'] + len(pv)}/{report['total']} = "
      f"{(report['ok'] + len(pv)) / report['total'] * 100:.0f}%")
print()
print("  这正是 550 章任务的问题：旧实现显示 100%，实际只覆盖 1.8%")

print()
print("=" * 76)
print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
print("=" * 76)
for f in fails:
    print("  - " + f)
sys.exit(0 if not fails else 1)
