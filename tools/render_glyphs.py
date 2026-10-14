# -*- coding: utf-8 -*-
"""把番茄自定义字体里的 PUA 字形渲染成图片，确认它们是不是可辨认的汉字。

这一步决定可行性：
  - 若是真实汉字轮廓 → 可用字形比对自动还原
  - 若是随机几何图形   → 只能人工标注，成本高
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

TTF = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")
OUT_PNG = Path(r"X:\小说工程\wenshu\runtime\probe\glyphs.png")
OUT_LIST = Path(r"X:\小说工程\wenshu\runtime\probe\glyph_codes.txt")

CELL = 48          # 每格像素
COLS = 16
MARGIN = 4


class PolyPen(BasePen):
    """把字形轮廓取成折线点集。"""

    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.contours: list[list[tuple[float, float]]] = []
        self._cur: list[tuple[float, float]] = []

    def _flush(self):
        if len(self._cur) >= 2:
            self.contours.append(self._cur)
        self._cur = []

    def _moveTo(self, pt):
        self._flush()
        self._cur = [pt]

    def _lineTo(self, pt):
        self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        steps = 10
        for i in range(1, steps + 1):
            t = i / steps
            mt = 1 - t
            x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
            y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
            self._cur.append((x, y))

    def _qCurveToOne(self, p1, p2):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        steps = 10
        for i in range(1, steps + 1):
            t = i / steps
            mt = 1 - t
            x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
            y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
            self._cur.append((x, y))

    def _closePath(self):
        self._flush()

    def _endPath(self):
        self._flush()

    def draw(self, glyphName):
        self.contours = []
        self._cur = []
        g = self.glyphSet[glyphName]
        g.draw(self)
        self._flush()
        return self.contours


def rasterize(contours, size=CELL, margin=MARGIN) -> list[list[int]] | None:
    """扫描线填充，返回灰度像素（0=黑字，255=白底）。"""
    pts = [p for c in contours for p in c]
    if not pts:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    w = max(maxx - minx, 1e-6)
    h = max(maxy - miny, 1e-6)
    inner = size - 2 * margin
    scale = inner / max(w, h)

    def tx(p):
        x = margin + (p[0] - minx) * scale
        # 字体 y 轴向上，图像 y 轴向下
        y = margin + (maxy - p[1]) * scale
        return (x, y)

    scaled = [[tx(p) for p in c] for c in contours]

    img = [[255] * size for _ in range(size)]
    for y in range(size):
        yc = y + 0.5
        xs_cross: list[float] = []
        for contour in scaled:
            n = len(contour)
            for i in range(n):
                x1, y1 = contour[i]
                x2, y2 = contour[(i + 1) % n]
                if y1 == y2:
                    continue
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    t = (yc - y1) / (y2 - y1)
                    xs_cross.append(x1 + t * (x2 - x1))
        xs_cross.sort()
        # 奇偶规则
        for i in range(0, len(xs_cross) - 1, 2):
            xa = max(0, int(round(xs_cross[i])))
            xb = min(size - 1, int(round(xs_cross[i + 1])))
            for x in range(xa, xb + 1):
                img[y][x] = 0
    return img


def write_png(path: Path, pixels: list[list[int]], w: int, h: int) -> None:
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> int:
    tt = TTFont(str(TTF))
    glyphset = tt.getGlyphSet()
    cmap = max(tt["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    codes = sorted(cmap.keys())

    pen = PolyPen(glyphset)

    # 只渲染前 128 个，足够判断字形性质
    subset = codes[:128]
    rows = (len(subset) + COLS - 1) // COLS
    W = COLS * CELL
    H = rows * CELL
    canvas = [[255] * W for _ in range(H)]

    lines = []
    for idx, code in enumerate(subset):
        gname = cmap[code]
        contours = pen.draw(gname)
        cell = rasterize(contours, CELL, MARGIN)
        r, c = divmod(idx, COLS)
        if cell:
            for y in range(CELL):
                for x in range(CELL):
                    canvas[r * CELL + y][c * CELL + x] = cell[y][x]
        lines.append(f"{idx:3d}  U+{code:04X}  {gname}  contours={len(contours)}")
        # 格子分隔线
        for x in range(W):
            canvas[r * CELL][x] = 200
        for y in range(H):
            canvas[y][c * CELL] = 200

    write_png(OUT_PNG, canvas, W, H)
    OUT_LIST.write_text("\n".join(lines), encoding="utf-8")

    print(f"图像: {OUT_PNG}  ({W}x{H})")
    print(f"清单: {OUT_LIST}")
    print(f"字形总数: {len(codes)}，本次渲染前 {len(subset)} 个")
    print()
    print("前 8 个字形：")
    for ln in lines[:8]:
        print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
