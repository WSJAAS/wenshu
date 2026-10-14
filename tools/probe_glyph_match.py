# -*- coding: utf-8 -*-
"""决定性验证：用字形比对自动还原番茄的 PUA 乱码字。

原理：
  字体里的 PUA 字形就是真实汉字，只是被放到私用区并做了缩放/平移。
  把每个 PUA 字形归一化成位图，与标准字库里每个候选字比对，
  取最相似的即为答案。

用 numpy 加速，候选集 = 常用汉字 + ASCII。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont, TTCollection

PUA_FONT = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")

# 常见 Windows 中文字体（做基准）
CANDIDATE_FONTS = [
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path(r"C:\Windows\Fonts\Deng.ttf"),
]

N = 40  # 归一化位图边长


class PolyPen(BasePen):
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
        for i in range(1, 9):
            t = i / 8
            mt = 1 - t
            self._cur.append((
                mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0],
                mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1],
            ))

    def _qCurveToOne(self, p1, p2):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        for i in range(1, 9):
            t = i / 8
            mt = 1 - t
            self._cur.append((
                mt**2*p0[0] + 2*mt*t*p1[0] + t**2*p2[0],
                mt**2*p0[1] + 2*mt*t*p1[1] + t**2*p2[1],
            ))

    def _closePath(self):
        self._flush()

    def _endPath(self):
        self._flush()

    def draw(self, glyphName):
        self.contours = []
        self._cur = []
        self.glyphSet[glyphName].draw(self)
        self._flush()
        return self.contours


def to_bitmap(contours, n: int = N) -> np.ndarray | None:
    """轮廓 → 归一化 n×n 位图（0/1）。"""
    pts = [p for c in contours for p in c]
    if not pts:
        return None
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    minx, maxx = xs.min(), xs.max()
    miny, maxy = ys.min(), ys.max()
    w = max(maxx - minx, 1e-6)
    h = max(maxy - miny, 1e-6)
    scale = n / max(w, h)          # 等比缩放到 n

    scaled = []
    for c in contours:
        arr = np.array(c, dtype=float)
        arr[:, 0] = (arr[:, 0] - minx) * scale
        arr[:, 1] = (maxy - arr[:, 1]) * scale   # y 翻转
        scaled.append(arr)

    # 扫描线填充
    img = np.zeros((n, n), dtype=np.uint8)
    for y in range(n):
        yc = y + 0.5
        crossings: list[float] = []
        for arr in scaled:
            m = len(arr)
            x1 = arr[:, 0]
            y1 = arr[:, 1]
            x2 = np.roll(x1, -1)
            y2 = np.roll(y1, -1)
            for i in range(m):
                a, b, c2, d = y1[i], y2[i], x1[i], x2[i]
                if a == b:
                    continue
                if (a <= yc < b) or (b <= yc < a):
                    t = (yc - a) / (b - a)
                    crossings.append(c2 + t * (d - c2))
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            xa = max(0, int(round(crossings[i])))
            xb = min(n - 1, int(round(crossings[i + 1])))
            if xb >= xa:
                img[y, xa:xb + 1] = 1
    return img


def load_font(path: Path):
    if path.suffix.lower() == ".ttc":
        coll = TTCollection(str(path))
        return coll.fonts[0]
    return TTFont(str(path))


def build_index(font, chars: str):
    """标准字库：字符 → 位图。"""
    glyphset = font.getGlyphSet()
    cmap = max(font["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    pen = PolyPen(glyphset)
    out = {}
    for ch in chars:
        gname = cmap.get(ord(ch))
        if not gname:
            continue
        bm = to_bitmap(pen.draw(gname))
        if bm is not None:
            out[ch] = bm
    return out


def main() -> int:
    print("=" * 72)
    print("[1] 加载 PUA 字体")
    print("=" * 72)
    pua = TTFont(str(PUA_FONT))
    pua_cmap = max(pua["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    pua_pen = PolyPen(pua.getGlyphSet())
    codes = sorted(pua_cmap.keys())
    print(f"  PUA 字形数: {len(codes)}  范围 U+{codes[0]:04X}..U+{codes[-1]:04X}")

    # 先渲染全部 PUA 字形
    pua_bitmaps: dict[int, np.ndarray] = {}
    for c in codes:
        bm = to_bitmap(pua_pen.draw(pua_cmap[c]))
        if bm is not None:
            pua_bitmaps[c] = bm
    print(f"  可渲染: {len(pua_bitmaps)}")

    print()
    print("=" * 72)
    print("[2] 加载基准字库")
    print("=" * 72)
    base = None
    for f in CANDIDATE_FONTS:
        if f.is_file():
            base = f
            print(f"  使用: {f.name}")
            break
    if base is None:
        print("  未找到可用中文字体")
        return 1

    bf = load_font(base)

    # 候选集：ASCII 可见字符 + 常用汉字
    ascii_chars = "".join(chr(i) for i in range(0x20, 0x7F))
    common = (
        "的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和要她出也得里"
        "后自以会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第"
        "样道想作种开美总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位"
        "分爱老因很给名法间斯知世什两次使身者被高已亲其进此话常与活正感死光白风色"
        "主特军然表场要只别还现岁此象月出战工相男直失平文将真那当会立是十张学气大"
        "爱两命全东性通被乐接而车山公了常以何话先叫轻土着变尔快个说少里安花远难师"
        "放报认面道克地度好机民写把万同水新没书电"
    )
    chars = ascii_chars + "".join(dict.fromkeys(common))

    print(f"  候选字符数: {len(chars)}")
    index = build_index(bf, chars)
    print(f"  建成位图: {len(index)}")
    if not index:
        print("  基准字库构建失败")
        return 1

    # 预堆叠成矩阵加速
    keys = list(index.keys())
    mat = np.stack([index[k].ravel() for k in keys]).astype(np.float32)
    mat_norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)

    print()
    print("=" * 72)
    print("[3] 逐字还原（余弦相似度）")
    print("=" * 72)
    mapping: dict[int, tuple[str, float]] = {}
    for c, bm in pua_bitmaps.items():
        v = bm.ravel().astype(np.float32)
        vn = v / (np.linalg.norm(v) + 1e-9)
        sims = mat_norm @ vn
        best = int(np.argmax(sims))
        mapping[c] = (keys[best], float(sims[best]))

    scores = np.array([s for _, s in mapping.values()])
    print(f"  还原 {len(mapping)} 个字")
    print(f"  相似度: 均值={scores.mean():.3f}  中位={np.median(scores):.3f}  "
          f"最低={scores.min():.3f}")
    high = (scores > 0.90).sum()
    mid = ((scores > 0.75) & (scores <= 0.90)).sum()
    low = (scores <= 0.75).sum()
    print(f"  >0.90: {high} 个   0.75~0.90: {mid} 个   <=0.75: {low} 个")

    print()
    print("  前 40 个还原结果（字符(相似度)）：")
    line = []
    for i, c in enumerate(codes[:40]):
        ch, s = mapping.get(c, ("?", 0))
        line.append(f"{ch}({s:.2f})")
        if (i + 1) % 8 == 0:
            print("    " + "  ".join(line))
            line = []
    if line:
        print("    " + "  ".join(line))

    print()
    print("=" * 72)
    print("[4] 用已知书名反验")
    print("=" * 72)
    cases = [
        ("惹枝", [0x60F9, 0xE49C, 0x679D]),
        ("掌娇娇", [0x638C, 0xE47D, 0x5A07, 0x5A07]),
        ("攀枝", [0x6500, 0xE53E, 0x679D]),
        ("舟渡", [0xE439, 0xE52A, 0xE467, 0x821F, 0x6E21]),
    ]
    for label, cps in cases:
        out = []
        for cp in cps:
            if 0xE000 <= cp <= 0xF8FF:
                ch, s = mapping.get(cp, ("□", 0.0))
                out.append(f"{ch}←U+{cp:04X}({s:.2f})")
            else:
                out.append(chr(cp))
        print(f"  {label:8s} → " + "".join(out))

    print()
    print("=" * 72)
    print("结论")
    print("=" * 72)
    if high >= len(mapping) * 0.5:
        print(f"  ✓ 字形比对可行：{high}/{len(mapping)} 个字形能以 >0.90 相似度还原")
        print("    可用自动流程建表，无需人工标注。")
    else:
        print(f"  △ 部分可行：高置信 {high} 个，需人工校对 {mid + low} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
