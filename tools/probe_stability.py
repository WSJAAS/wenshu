# -*- coding: utf-8 -*-
"""决定性测试：乱码码位对同一本书是否稳定。

若稳定 → 建一次映射表即可长期使用。
若每次请求都变 → 必须每次实时比对字形，成本高很多。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
URL = "https://fanqienovel.com/rank"


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def extract(raw: str):
    """取出榜单里的书名与其码位。

    页面里 __INITIAL_STATE__ 的结束位置不固定，用「从右往左找最大可解析前缀」
    的方式定位，比写死正则稳。
    """
    key = "window.__INITIAL_STATE__"
    start = raw.find(key)
    if start < 0:
        return None, None
    brace = raw.find("{", start)
    if brace < 0:
        return None, None

    blob = raw[brace:]
    data = None
    # 从最长的可能结尾往前试，找到能解析的最大前缀
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            data = json.loads(blob[:end])
            break
        except json.JSONDecodeError:
            continue
        if end < len(blob) - 400000:  # 避免无限退
            break
    if data is None:
        return None, None

    books = (data.get("rank") or {}).get("book_list") or []
    fam = re.search(r"font-family:([A-Za-z0-9_\-]+);font-display", raw)
    return books, (fam.group(1) if fam else None)


print("=" * 72)
print("连续三次抓取，对比同一本书的书名码位")
print("=" * 72)

runs = []
for i in range(3):
    raw = fetch()
    books, fam = extract(raw)
    if not books:
        print(f"  第 {i+1} 次: 解析失败")
        continue
    runs.append((books, fam))
    print(f"  第 {i+1} 次: 字体名={fam}  书籍数={len(books)}")
    time.sleep(2)

if len(runs) < 2:
    print("抓取不足，无法对比")
    raise SystemExit(1)

print()
print("=" * 72)
print("逐本对比前 6 本")
print("=" * 72)

first_books = runs[0][0]
stable = 0
unstable = 0

for idx in range(min(6, len(first_books))):
    ids = [r[0][idx].get("bookId") for r in runs if idx < len(r[0])]
    names = [r[0][idx].get("bookName", "") for r in runs if idx < len(r[0])]
    same_id = len(set(ids)) == 1
    same_name = len(set(names)) == 1

    print(f"\n  [{idx}] bookId={ids[0] if ids else '?'}  同一本书={same_id}")
    for i, n in enumerate(names):
        cps = " ".join(f"{ord(c):04X}" for c in n)
        print(f"       第{i+1}次: '{n}'   {cps}")

    if same_name:
        stable += 1
    else:
        unstable += 1

print()
print("=" * 72)
print("结论")
print("=" * 72)
print(f"  同书书名一致: {stable} 本")
print(f"  同书书名不同: {unstable} 本")
print()

if unstable == 0:
    print("  ✓ 码位稳定 —— 可建一次映射表长期复用")
    print("    做法：人工标注几十个常见字，或与标准字库逐字比对一次后缓存。")
else:
    print("  ✗ 码位会变 —— 每次抓取都要重新比对字形")
    print("    成本显著上升，需评估是否值得做。")

# 额外：对比两次的字体 URL 是否相同
print()
print("  字体 URL 对比：")
for i, (books, fam) in enumerate(runs):
    print(f"    第{i+1}次 family={fam}")
