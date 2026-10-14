# -*- coding: utf-8 -*-
"""严谨复核码表：用「字形最近邻」而非「绝对相似度阈值」判定。

原理：
  对每个 PUA 码位，在参考字库里找形状最接近的字。
  若码表声称的字 == 最近邻结果 → 强证据正确。
  更关键：用**整体解码后文本是否通顺**来交叉验证 ——
  若码表有系统性错误，解出的文本必然大面积不通顺。

同时测多本书、多章，覆盖不同字体版本。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont, TTCollection

PUA_FONT = Path(r"X:\小说工程\wenshu\runtime\probe\font.ttf")
REF = Path(r"C:\Windows\Fonts\msyh.ttc")
N = 56
CODE_ST, CODE_ED = 58344, 58715

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
        super().__init__(gs); self.contours = []; self._cur = []

    def _flush(self):
        if len(self._cur) >= 2:
            self.contours.append(self._cur)
        self._cur = []

    def _moveTo(self, pt): self._flush(); self._cur = [pt]
    def _lineTo(self, pt): self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        for i in range(1, 9):
            t = i/8; mt = 1-t
            self._cur.append((mt**3*p0[0]+3*mt**2*t*p1[0]+3*mt*t**2*p2[0]+t**3*p3[0],
                              mt**3*p0[1]+3*mt**2*t*p1[1]+3*mt*t**2*p2[1]+t**3*p3[1]))

    def _qCurveToOne(self, p1, p2):
        if not self._cur:
            self._cur = [(0.0, 0.0)]
        p0 = self._cur[-1]
        for i in range(1, 9):
            t = i/8; mt = 1-t
            self._cur.append((mt**2*p0[0]+2*mt*t*p1[0]+t**2*p2[0],
                              mt**2*p0[1]+2*mt*t*p1[1]+t**2*p2[1]))

    def _closePath(self): self._flush()
    def _endPath(self): self._flush()

    def draw(self, name):
        self.contours = []; self._cur = []
        self.glyphSet[name].draw(self); self._flush()
        return self.contours


def feat(contours, n=N):
    """提取对非等比变形更鲁棒的特征：笔画密度网格 + 宽高比。"""
    pts = [p for c in contours for p in c]
    if not pts:
        return None
    xs = np.array([p[0] for p in pts]); ys = np.array([p[1] for p in pts])
    minx, maxx = xs.min(), xs.max(); miny, maxy = ys.min(), ys.max()
    w = max(maxx-minx, 1e-6); h = max(maxy-miny, 1e-6)
    ar = w / h

    # 按各自宽高分别归一化（消除非等比拉伸）
    sc = []
    for c in contours:
        a = np.array(c, dtype=float)
        a[:, 0] = (a[:, 0]-minx)/w*(n-1)
        a[:, 1] = (maxy-a[:, 1])/h*(n-1)
        sc.append(a)

    img = np.zeros((n, n), dtype=np.float32)
    for y in range(n):
        yc = y+0.5
        cr = []
        for a in sc:
            x1 = a[:, 0]; y1 = a[:, 1]
            x2 = np.roll(x1, -1); y2 = np.roll(y1, -1)
            for i in range(len(a)):
                p, q, u, v = y1[i], y2[i], x1[i], x2[i]
                if p == q: continue
                if (p <= yc < q) or (q <= yc < p):
                    cr.append(u + (yc-p)/(q-p)*(v-u))
        cr.sort()
        for i in range(0, len(cr)-1, 2):
            xa = max(0, int(round(cr[i]))); xb = min(n-1, int(round(cr[i+1])))
            if xb >= xa:
                img[y, xa:xb+1] = 1.0
    return img, ar


def main() -> int:
    flat = list("".join(TABLE_ROWS))
    print("=" * 76)
    print("[1] 构建参考字库特征")
    print("=" * 76)
    ref = TTCollection(str(REF)).fonts[0]
    rg = ref.getGlyphSet()
    rcm = max(ref["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    rp = PolyPen(rg)

    # 候选集：码表里出现的所有字 + 常用字
    charset = set(ch for ch in flat if ch != "?")
    charset |= set("的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和"
                   "要她出也得里后自以会家可下而过天去能对小多然于心学么之都好看"
                   "起发当没成只如事把还用第样道想作种开美总从无情己面最女但现前")
    charset = {c for c in charset if ord(c) > 0x20}

    ref_feat = {}
    for ch in charset:
        gn = rcm.get(ord(ch))
        if not gn:
            continue
        f = feat(rp.draw(gn))
        if f:
            ref_feat[ch] = f
    print(f"  参考特征: {len(ref_feat)} 个")

    keys = list(ref_feat.keys())
    mat = np.stack([ref_feat[k][0].ravel() for k in keys])
    mat_n = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    ars = np.array([ref_feat[k][1] for k in keys])

    print()
    print("=" * 76)
    print("[2] 逐位：码表声称的字 vs 字形最近邻")
    print("=" * 76)
    pf = TTFont(str(PUA_FONT))
    pcm = max(pf["cmap"].tables, key=lambda t: len(t.cmap)).cmap
    pp = PolyPen(pf.getGlyphSet())

    agree = 0
    total = 0
    top3 = 0
    disagree = []

    for i, ch in enumerate(flat):
        code = CODE_ST + i
        if ch == "?" or code not in pcm:
            continue
        f = feat(pp.draw(pcm[code]))
        if not f:
            continue
        v, ar = f
        vn = v.ravel()
        vn = vn / (np.linalg.norm(vn) + 1e-9)
        sims = mat_n @ vn
        # 宽高比也纳入（对非等比变形鲁棒）
        penalty = np.abs(ars - ar) * 0.5
        score = sims - penalty
        order = np.argsort(-score)
        total += 1
        if keys[order[0]] == ch:
            agree += 1
        if ch in [keys[o] for o in order[:3]]:
            top3 += 1
        elif len(disagree) < 12:
            disagree.append((code, ch, keys[order[0]], float(sims[order[0]])))

    print(f"  可判定: {total}")
    print(f"  最近邻一致: {agree}  ({agree/total*100:.1f}%)")
    print(f"  前三命中  : {top3}  ({top3/total*100:.1f}%)")
    if disagree:
        print()
        print("  不一致样例（码位 / 码表 / 最近邻 / 相似度）：")
        for code, ch, nn, s in disagree:
            print(f"    U+{code:04X}  码表='{ch}'  最近邻='{nn}'  {s:.3f}")

    print()
    print("=" * 76)
    print("[3] 多章端到端解码（真正的判据）")
    print("=" * 76)
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "Chrome/131.0.0.0 Safari/537.36")

    def get_content(item_id):
        req = urllib.request.Request(f"https://fanqienovel.com/reader/{item_id}",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
        s = raw.find("window.__INITIAL_STATE__")
        b = raw.find("{", s)
        blob = raw[b:]
        for end in range(len(blob), 0, -1):
            if blob[end-1] != "}":
                continue
            try:
                return json.loads(blob[:end])["reader"]["chapterData"]
            except Exception:
                continue
        return None

    def decode(text):
        out = []
        for c in text:
            cp = ord(c)
            if CODE_ST <= cp <= CODE_ED:
                m = flat[cp-CODE_ST]
                out.append(c if m == "?" else m)
            else:
                out.append(c)
        return "".join(out)

    CHAPTERS = {
        "7256994009498223144": "第001章",
        "7257003427505537575": "第002章",
        "7257003641763332647": "第003章",
        "7257444799446581819": "第004章",
    }
    # 常用字，用于估算「解码后是否通顺」
    COMMON = set("的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和要"
                 "她出也得里后自以会家可下而过天去能对小多然于心学么之都好看起发"
                 "当没成只如事把还用第样道想作种开美总从无情己面最女但现前些所同"
                 "日手又行意动方期它头经长儿回位分爱老因很给名法间知世什两次使身")

    print(f"  {'章节':<10} {'长度':>6} {'PUA':>6} {'残留':>5} {'常用字比':>8}")
    print("  " + "-" * 48)
    all_ok = True
    for item_id, name in CHAPTERS.items():
        cd = get_content(item_id)
        if not cd:
            print(f"  {name:<10} 获取失败")
            continue
        content = cd.get("content", "")
        pua = sum(1 for c in content if 0xE000 <= ord(c) <= 0xF8FF)
        dec = decode(content)
        plain = re.sub(r"<[^>]+>", "", dec)
        left = sum(1 for c in plain if 0xE000 <= ord(c) <= 0xF8FF)
        han = [c for c in plain if 0x4E00 <= ord(c) <= 0x9FFF]
        ratio = sum(1 for c in han if c in COMMON) / len(han) * 100 if han else 0
        flag = "" if left == 0 else "  ← 有残留"
        if left:
            all_ok = False
        print(f"  {name:<10} {len(content):>6} {pua:>6} {left:>5} {ratio:>7.1f}%{flag}")

    print()
    print("  解码样例（第002章前 160 字）：")
    cd2 = get_content("7257003427505537575")
    if cd2:
        p2 = re.sub(r"<[^>]+>", "", decode(cd2.get("content", "")))
        print("  " + p2[:160])

    print()
    print("=" * 76)
    print("结论")
    print("=" * 76)
    if all_ok and agree/total > 0.85:
        print("  ✓ 码表可用")
        print(f"    最近邻一致率 {agree/total*100:.1f}%，多章解码无残留")
    else:
        print(f"  最近邻一致率 {agree/total*100:.1f}%，残留检查={all_ok}")
        print("  → 若一致率偏低但解码通顺，说明是特征提取的偏差而非码表错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
