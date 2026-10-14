# -*- coding: utf-8 -*-
"""统计：正文用字与字体字形表的覆盖关系。

决定「正文能否还原」的关键数据：
  1. 正文用了多少个不同的 PUA 码位
  2. 字体表里有几个字形
  3. 两者是否一一对应（若字形表是页面级子集，就无法跨页复用）
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")

sys.path.insert(0, r"X:\小说工程\wenshu\backend")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_state(raw):
    start = raw.find("window.__INITIAL_STATE__")
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


# 取三章，看字形表是否随页面变化
CHAPTERS = [
    "7256994009498223144",  # 第1章
    "7257003427505537575",  # 第2章
    "7257003641763332647",  # 第3章
]

print("=" * 74)
print("[1] 逐章提取 PUA 码位，并取该页的字体")
print("=" * 74)

all_fonts = {}
all_pua = set()

for idx, item in enumerate(CHAPTERS, start=1):
    raw = fetch(f"https://fanqienovel.com/reader/{item}")
    d = parse_state(raw)
    if not d:
        print(f"  第{idx}章: 解析失败")
        continue
    cd = d["reader"]["chapterData"]
    content = cd.get("content", "")
    title = cd.get("title", "")

    pua = {ord(c) for c in content if 0xE000 <= ord(c) <= 0xF8FF}
    all_pua |= pua

    fam = re.search(r"font-family:([A-Za-z0-9_\-]+);font-display", raw)
    font_url = re.search(r"src:url\((https://[^)]+?\.woff2)\)", raw)

    print(f"\n  第{idx}章 {title}")
    print(f"    正文长度  : {len(content)}")
    print(f"    PUA 码位数: {len(pua)}")
    print(f"    字体名    : {fam.group(1) if fam else '?'}")
    print(f"    字体URL   : {(font_url.group(1).split('/')[-1] if font_url else '?')}")

    if font_url:
        all_fonts[idx] = (fam.group(1) if fam else "", font_url.group(1))

print()
print("=" * 74)
print("[2] 三章 PUA 码位交集与并集")
print("=" * 74)
print(f"  三章并集: {len(all_pua)} 个不同码位")

# 对比字体表的字形数
from fontTools.ttLib import TTFont
PUA_FONT = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")
if PUA_FONT.is_file():
    tt = TTFont(str(PUA_FONT))
    cmap = max(tt["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    font_codes = set(cmap.keys())
    print(f"  已存字体字形数: {len(font_codes)}")
    print(f"  码位范围: U+{min(font_codes):04X} .. U+{max(font_codes):04X}")
    print()
    print(f"  正文码位 ⊆ 字体码位 : {all_pua <= font_codes}")
    print(f"  正文中字体缺失的码位: {len(all_pua - font_codes)}")

print()
print("=" * 74)
print("[3] 字体 URL 是否三章一致")
print("=" * 74)
urls = {v[1] for v in all_fonts.values()}
fams = {v[0] for v in all_fonts.values()}
print(f"  不同 URL 数: {len(urls)}")
for u in urls:
    print(f"    {u.split('/')[-1]}")
print(f"  不同字体名数: {len(fams)}")
for f in fams:
    print(f"    {f}")

print()
print("=" * 74)
print("[4] 关键判断")
print("=" * 74)
if len(urls) == 1:
    print("  ✓ 字体全站统一 —— 一份映射表可长期复用")
    print("    这是最有利的情况：只需还原一次。")
else:
    print(f"  △ 字体随页面变化（{len(urls)} 种）—— 需要按字体文件分别建表")
    print("    但若能识别出「同一字体文件产生的码位映射恒定」，仍可缓存复用。")
print()
print("  下一步需要确认的是：同一个字形文件里，")
print("  码位 → 汉字 的映射关系是否恒定（与页面无关）。")
