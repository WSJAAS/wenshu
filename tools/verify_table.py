# -*- coding: utf-8 -*-
"""验证 naiyQAQ/fanqie-assistant 的静态码表是否准确。

方法（不靠信任，靠实测）：
  对码表里的每一个位置，
    1. 用真实字体渲染该码位的字形
    2. 用标准中文字体渲染码表声称的那个字
    3. 两者比对
  完全一致 → 该位置正确；不一致 → 标出可疑（可能是抄漏/错字）

同时用真实章节正文做端到端验证。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont, TTCollection

PUA_FONT = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")
REF_FONTS = [Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\simsun.ttc")]
N = 48

CODE_ST = 58344
CODE_ED = 58715

# 直接抄自 src/fontDecrypt.ts 的 DNMrHsV173Pd4pgy 表
TABLE_ROWS = [
    'D在主特家军然表场4要只v和?6别还g现儿岁??此象月3出战工相',
    'o男直失世F都平文什VO将真T那当?会立些u是十张学气大爱两命全',
    '后东性通被1它乐接而感车山公了常以何可话先pi叫轻M士w着变尔快',
    'l个说少色里安花远7难师放t报认面道S?克地度I好机U民写把万同',
    '水新没书电吃像斯5为y白几日教看但第加候作上拉住有法r事应位利你',
    '声身国问马女他Y比父xAHNsX边美对所金活回意到z从j知又内因',
    '点Q三定8Rb正或夫向德听更?得告并本q过记L让打f人就者去原满',
    '体做经K走如孩cG给使物?最笑部?员等受k行一条果动光门头见往自',
    '解成处天能于名其发总母的死手入路进心来h时力多开已许d至由很界n',
    '小与Z想代么分生口再妈望次西风种带J?实情才这?E我神格长觉间年',
    '眼无不亲关结0友信下却重己老2音字m呢明之前高PB目太e9起稜她',
    '也W用方子英每理便四数期中C外样a海们任',
]


class PolyPen(BasePen):
    def __init__(self, gs):
        super().__init__(gs)
        self.contours = []
        self._cur = []

    def _flush(self):
        if len(self._cur) >= 2:
            self.contours.append(self._cur)
        self._cur = []

    def _moveTo(self, pt):
        self._flush(); self._cur = [pt]

    def _lineTo(self, pt):
        self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        for i in range(1, 9):
            t = i / 8; mt = 1 - t
            self._cur.append((
                mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0],
                mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1]))

    def _qCurveToOne(self, p1, p2):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        for i in range(1, 9):
            t = i / 8; mt = 1 - t
            self._cur.append((
                mt**2*p0[0] + 2*mt*t*p1[0] + t**2*p2[0],
                mt**2*p0[1] + 2*mt*t*p1[1] + t**2*p2[1]))

    def _closePath(self):
        self._flush()

    def _endPath(self):
        self._flush()

    def draw(self, name):
        self.contours = []; self._cur = []
        self.glyphSet[name].draw(self)
        self._flush()
        return self.contours


def to_bitmap(contours, n=N):
    pts = [p for c in contours for p in c]
    if not pts:
        return None
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    minx, maxx = xs.min(), xs.max(); miny, maxy = ys.min(), ys.max()
    w = max(maxx - minx, 1e-6); h = max(maxy - miny, 1e-6)
    scale = n / max(w, h)
    sc = []
    for c in contours:
        a = np.array(c, dtype=float)
        a[:, 0] = (a[:, 0] - minx) * scale
        a[:, 1] = (maxy - a[:, 1]) * scale
        sc.append(a)
    img = np.zeros((n, n), dtype=np.uint8)
    for y in range(n):
        yc = y + 0.5
        cr = []
        for a in sc:
            x1 = a[:, 0]; y1 = a[:, 1]
            x2 = np.roll(x1, -1); y2 = np.roll(y1, -1)
            for i in range(len(a)):
                p, q, u, v = y1[i], y2[i], x1[i], x2[i]
                if p == q:
                    continue
                if (p <= yc < q) or (q <= yc < p):
                    t = (yc - p) / (q - p)
                    cr.append(u + t * (v - u))
        cr.sort()
        for i in range(0, len(cr) - 1, 2):
            xa = max(0, int(round(cr[i]))); xb = min(n - 1, int(round(cr[i + 1])))
            if xb >= xa:
                img[y, xa:xb + 1] = 1
    return img


def load_ref():
    for f in REF_FONTS:
        if f.is_file():
            return f, TTCollection(str(f)).fonts[0] if f.suffix == ".ttc" else TTFont(str(f))
    return None, None


def main() -> int:
    flat = list("".join(TABLE_ROWS))
    expected = CODE_ED - CODE_ST + 1
    print("=" * 76)
    print("[1] 码表长度")
    print("=" * 76)
    print(f"  应有: {expected}  实际: {len(flat)}")
    if len(flat) != expected:
        print("  ✗ 长度不符，码表抄漏，后续会整体错位")
        return 1
    print("  ✓ 长度一致")

    print()
    print("=" * 76)
    print("[2] 逐位验证：PUA 字形 vs 码表声称的字")
    print("=" * 76)
    pf = TTFont(str(PUA_FONT))
    pcm = max(pf["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    pp = PolyPen(pf.getGlyphSet())

    _, ref = load_ref()
    if ref is None:
        print("  无参考字体")
        return 1
    rg = ref.getGlyphSet()
    rcm = max(ref["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    rp = PolyPen(rg)

    def sim(a, b):
        va = a.ravel().astype(np.float32); vb = b.ravel().astype(np.float32)
        na = np.linalg.norm(va); nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(va @ vb / (na * nb))

    mismatches = []
    no_pua_glyph = []
    checked = 0

    for i, ch in enumerate(flat):
        code = CODE_ST + i
        if ch == "?":
            # 码表说这里无字形 —— 验证字体里确实没有
            if code in pcm:
                no_pua_glyph.append((code, ch))
            continue
        if code not in pcm:
            mismatches.append((code, ch, "PUA字体无此码位", 0.0))
            continue
        bm_pua = to_bitmap(pp.draw(pcm[code]))
        rname = rcm.get(ord(ch))
        if not rname:
            mismatches.append((code, ch, "参考字体无此字", 0.0))
            continue
        bm_ref = to_bitmap(rp.draw(rname))
        if bm_pua is None or bm_ref is None:
            continue
        s = sim(bm_pua, bm_ref)
        checked += 1
        if s < 0.80:
            mismatches.append((code, ch, "字形不符", s))

    print(f"  已比对: {checked} 个")
    print(f"  可疑不符: {len(mismatches)} 个")
    print(f"  码表标 '?' 但字体有字形的: {len(no_pua_glyph)} 个")

    if mismatches:
        print()
        print("  可疑项（码位 / 码表字 / 原因 / 相似度）：")
        for code, ch, why, s in mismatches[:30]:
            print(f"    U+{code:04X}  '{ch}'  {why}  {s:.3f}")

    if no_pua_glyph:
        print()
        print("  '?' 位置但字体存在字形（说明码表该位置可能有字）：")
        for code, ch in no_pua_glyph[:10]:
            print(f"    U+{code:04X}")

    print()
    print("=" * 76)
    print("[3] 端到端：解真实章节正文")
    print("=" * 76)
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "Chrome/131.0.0.0 Safari/537.36")
    req = urllib.request.Request(
        "https://fanqienovel.com/reader/7256994009498223144",
        headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    start = raw.find("window.__INITIAL_STATE__")
    brace = raw.find("{", start)
    blob = raw[brace:]
    data = None
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            data = json.loads(blob[:end])
            break
        except json.JSONDecodeError:
            continue
    content = data["reader"]["chapterData"]["content"]

    def decode(text):
        out = []
        for c in text:
            cp = ord(c)
            if CODE_ST <= cp <= CODE_ED:
                m = flat[cp - CODE_ST]
                out.append(c if m == "?" else m)
            else:
                out.append(c)
        return "".join(out)

    dec = decode(content)
    plain = re.sub(r"<[^>]+>", "", dec)
    left = sum(1 for c in plain if 0xE000 <= ord(c) <= 0xF8FF)
    print(f"  原文长度: {len(content)}")
    print(f"  解码后残留 PUA: {left}")
    print()
    print("  解码结果前 300 字：")
    print("  " + plain[:300])
    print()
    if left == 0:
        print("  ✓ 全部字符已还原，无残留")
    else:
        print(f"  ✗ 仍有 {left} 个未还原")

    print()
    print("=" * 76)
    print("结论")
    print("=" * 76)
    ok_rate = (checked - len(mismatches)) / checked * 100 if checked else 0
    print(f"  码表准确率: {ok_rate:.1f}%  ({checked - len(mismatches)}/{checked})")
    if left == 0 and ok_rate > 97:
        print("  ✓ 码表可用，正文可直接还原为原文")
    else:
        print("  △ 需要人工校对列出的可疑项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
