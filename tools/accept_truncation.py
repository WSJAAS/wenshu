# -*- coding: utf-8 -*-
"""验收：截断判定修复。

对应缺陷：未登录时 locked 章被服务端硬截断成 200 字符预览，
却被标记为 ok —— 550 章的书「零失败」，正文实际只有 1.8%。

验证点：
  1. 前 10 章（isChapterLock=false）判定为 ok，字数与申报相符
  2. 第 11 章起（isChapterLock=true）判定为 preview，不再冒充 ok
  3. 报表把 preview 计入缺章，并单独给出预览字数
  4. 导出的 TXT 逐章标注「仅为试读片段」，且附总说明
  5. 真实跑一遍 12 章，确认状态分布符合预期
"""
from __future__ import annotations

import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"X:\小说工程\wenshu\backend")

from app.services.fanqie_source import (  # noqa: E402
    FanqieClient, BatchFetcher, build_missing_report, results_to_txt,
    TRUNCATION_RATIO,
)

FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


BID = "7231424472606051384"   # 惹金枝

print("=" * 74)
print("[1] 抓取 12 章（跨过第 10 章的截断边界）")
print("=" * 74)
c = FanqieClient(interval=0.4)
book, chs = c.fetch_book(BID)
print(f"  《{book.title}》共 {len(chs)} 章")

targets = chs[:12]
fetcher = BatchFetcher(c)
fresh, prog = fetcher.fetch_range(targets)
res = {x.item_id: x for x in fresh}

print()
print("  逐章：#order  status   取得字数  申报字数  判定")
for x in sorted(fresh, key=lambda v: v.order):
    print(f"    #{x.order:<4} {x.status:<8} {x.chars:<9} "
          f"{x.claimed_words:<9} {'完整' if x.status=='ok' else '截断' if x.status=='preview' else x.status}")

print()
print("=" * 74)
print("[2] 判定正确性")
print("=" * 74)
free = [x for x in fresh if x.order <= 10]
locked = [x for x in fresh if x.order > 10]

check("前 10 章全部判定为 ok", all(x.status == "ok" for x in free),
      f"{[x.status for x in free]}")
check("第 11 章起全部判定为 preview", all(x.status == "preview" for x in locked),
      f"{[x.status for x in locked]}")
check("locked 章确实带 isChapterLock 标记",
      all(x.is_locked for x in locked))
check("full 章不带 isChapterLock 标记",
      all(not x.is_locked for x in free))
check("预览章字数远小于申报",
      all(x.chars < x.claimed_words * TRUNCATION_RATIO for x in locked),
      f"阈值 {TRUNCATION_RATIO}")
check("完整章字数接近申报（未被误判）",
      all(x.chars >= x.claimed_words * TRUNCATION_RATIO for x in free))

print()
print("=" * 74)
print("[3] 报表把 preview 计入缺章")
print("=" * 74)
rep = build_missing_report(targets, res)
print("  by_status:", rep["by_status"])
print(f"  ok={rep['ok']}  missing_count={rep['missing_count']}  "
      f"preview_count={rep['preview_count']}  preview_chars={rep['preview_chars']}")
check("ok 数 == 10", rep["ok"] == 10, str(rep["ok"]))
check("preview 计入缺章清单", rep["missing_count"] == 2, str(rep["missing_count"]))
check("preview_count 正确", rep["preview_count"] == 2)
check("缺章清单里带 missing_words",
      all("missing_words" in m for m in rep["missing"]),
      str([m.get("missing_words") for m in rep["missing"]]))

print()
print("=" * 74)
print("[4] 导出 TXT 的标注")
print("=" * 74)
txt = results_to_txt(targets, res)
print(f"  TXT 长度: {len(txt)}")
check("含逐章试读标注", "〖本节仅为试读片段〗" in txt,
      f"出现 {txt.count('〖本节仅为试读片段〗')} 次")
check("含平台登录提示说明", "会员登录后" in txt)
check("含占比说明", "占已取正文" in txt)
check("声明完整可取正文字数", "完整可取正文约" in txt)

print()
print("  说明段落原文：")
tail = txt[txt.rfind("=" * 40):]
for line in tail.splitlines()[:14]:
    print("    " + line)

print()
print("  被标注的章片段：")
m = re.search(r"第011章[^\n]*\n\n〖本节仅为试读片段〗[^\n]*", txt)
if m:
    print("    " + m.group(0).replace("\n", "\n    "))

print()
print("=" * 74)
print(f"RESULT: {'PASS' if not FAILS else 'FAIL (' + str(len(FAILS)) + ')'}")
for f in FAILS:
    print("  -", f)
print("=" * 74)
sys.exit(1 if FAILS else 0)
