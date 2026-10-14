# -*- coding: utf-8 -*-
"""分析番茄自定义字体的字形映射。

目标：判断乱码书名能否还原成真实汉字。

方法：解出 woff2 的 cmap（Unicode → 字形）与字形轮廓，
如果字形是「把真实汉字平移/变形到私有区码位」，就能通过
逐字归一化后与标准字比对来还原。
"""
from __future__ import annotations

import sys
from pathlib import Path

FONT = Path(r"X:\小说工程\wenshu\runtime\probe\font.woff2")
OUT = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")

print("=" * 70)
print("[1] 解压 woff2")
print("=" * 70)
from fontTools.ttLib import TTFont

try:
    f = TTFont(str(FONT))
    f.flavor = None
    f.save(str(OUT))
    print(f"  已转为 TTF: {OUT.name}  ({OUT.stat().st_size} 字节)")
except Exception as e:
    print(f"  失败: {e}")
    sys.exit(1)

print()
print("=" * 70)
print("[2] 字形表（cmap）")
print("=" * 70)
tt = TTFont(str(OUT))
cmaps = tt["cmap"].tables
print(f"  cmap 子表数: {len(cmaps)}")
best = None
for t in cmaps:
    n = len(t.cmap)
    print(f"    platformID={t.platformID} platEncID={t.platEncID} 映射数={n}")
    if best is None or n > len(best.cmap):
        best = t

if best is None:
    print("  无可用 cmap")
    sys.exit(1)

codes = sorted(best.cmap.keys())
print()
print(f"  使用子表: platformID={best.platformID}")
print(f"  映射总数: {len(codes)}")
print(f"  码位范围: U+{codes[0]:04X} .. U+{codes[-1]:04X}")

# 判断落在哪个区
pua = [c for c in codes if 0xE000 <= c <= 0xF8FF]
bmp = [c for c in codes if not (0xE000 <= c <= 0xF8FF)]
print(f"  私用区(PUA)码位: {len(pua)}")
print(f"  常规区码位    : {len(bmp)}")
print()
print("  前 20 个码位:")
for c in codes[:20]:
    print(f"    U+{c:04X}  {chr(c) if not (0xE000 <= c <= 0xF8FF) else '(PUA)'}  -> {best.cmap[c]}")

print()
print("=" * 70)
print("[3] 字形数与字号")
print("=" * 70)
glyph_order = tt.getGlyphOrder()
print(f"  字形总数: {len(glyph_order)}")
print(f"  前 12 个字形名: {', '.join(glyph_order[:12])}")

# head 表里的 unitsPerEm 与是否平移
head = tt["head"]
print(f"  unitsPerEm: {head.unitsPerEm}")
print(f"  xMin={head.xMin} yMin={head.yMin} xMax={head.xMax} yMax={head.yMax}")

print()
print("=" * 70)
print("[4] 抽取几个字形轮廓，看是否为平移过的真实汉字")
print("=" * 70)
glyf = tt["glyf"]
hmtx = tt["hmtx"]

for code in codes[:6]:
    gname = best.cmap[code]
    g = glyf[gname]
    adv = hmtx[gname][0]
    if g.numberOfContours <= 0:
        print(f"  U+{code:04X} {gname}: 空字形")
        continue
    coords, endPts, flags = g.getCoordinates(glyf)
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    print(f"  U+{code:04X} {gname}: 轮廓={g.numberOfContours} 点数={len(coords)} "
          f"x=[{min(xs)},{max(xs)}] y=[{min(ys)},{max(ys)}] 宽度={adv}")

print()
print("=" * 70)
print("结论")
print("=" * 70)
print("  字形若为真实汉字的轮廓（点数几十~上百、包围盒接近满格），")
print("  则可通过「归一化 + 与标准字库比对」还原字符。")
print("  若字形是简单几何（矩形/线条），则是替换映射，需另一套解法。")
