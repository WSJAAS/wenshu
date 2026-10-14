# -*- coding: utf-8 -*-
"""校验解码器：对真实章节做端到端测试。

判定标准（严格）：
  1. 解码后残留私用区字符必须为 0
  2. 段落结构必须保留（多段而非一整行）
  3. 文本必须通顺（用常用字占比 + 人工抽查）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

from app.services.fanqie_decoder import (  # noqa: E402
    CODE_ED, CODE_ST, TABLE_SIZE, count_pua, extract_body, html_to_text,
    self_check,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


def fetch_reader(item_id: str):
    req = urllib.request.Request(
        f"https://fanqienovel.com/reader/{item_id}",
        headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    s = raw.find("window.__INITIAL_STATE__")
    b = raw.find("{", s)
    blob = raw[b:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            return json.loads(blob[:end])["reader"]["chapterData"]
        except Exception:
            continue
    return None


CHAPTERS = {
    "7256994009498223144": "第001章 初次相见",
    "7257003427505537575": "第002章 时家灾星",
    "7257003641763332647": "第003章 亲人相见",
    "7257444799446581819": "第004章 画上长大",
    "7257456200126726717": "第005章 联手劫囚",
}

print("=" * 74)
print("[1] 码表自检")
print("=" * 74)
info = self_check()
print(f"  表长    : {info['table_size']} / {info['expected_size']}")
print(f"  码位区间: {info['code_range'][0]} .. {info['code_range'][1]}")
print(f"  无字形位: {info['no_glyph_slots']} 个")
check("表长正确", info["table_size"] == info["expected_size"])
check("区间与实测一致", (CODE_ST, CODE_ED) == (0xE3E8, 0xE55B))

print()
print("=" * 74)
print("[2] html_to_text 保留段落")
print("=" * 74)
sample = "<p>第一段。</p><p>第二段，<br>内部换行。</p><p></p><p>第四段。</p>"
txt = html_to_text(sample)
print(f"  输入: {sample}")
print(f"  输出: {txt!r}")
paras = [p for p in txt.split("\n\n") if p.strip()]
check("段落被分开", len(paras) == 3, f"段数={len(paras)}")
check("空段落被丢弃", "" not in paras)
check("段内 br 转换行", "内部换行" in txt and "\n" in txt)
check("未连成一行", "\n\n" in txt)

print()
print("=" * 74)
print("[3] 真实章节端到端")
print("=" * 74)
print(f"  {'章节':<22} {'原字符':>7} {'乱码':>7} {'解码后残留':>10} {'段数':>5} {'用时':>6}")
print("  " + "-" * 64)

import time

all_clean = True
results = []
for item_id, name in CHAPTERS.items():
    t0 = time.monotonic()
    cd = fetch_reader(item_id)
    if not cd:
        print(f"  {name:<22} 获取失败")
        all_clean = False
        continue
    html = cd.get("content", "")
    pua_before = count_pua(html)

    res = extract_body(html)
    elapsed = time.monotonic() - t0

    paras = [p for p in res.text.split("\n\n") if p.strip()]
    flag = ""
    if res.unresolved:
        flag = "  ← 有残留"
        all_clean = False

    print(f"  {name:<22} {len(html):>7} {pua_before:>7} {res.unresolved:>10} "
          f"{len(paras):>5} {elapsed:>5.1f}s{flag}")
    results.append((name, res, html))

print()
check("全部章节解码后无残留", all_clean)
check("所有章节都取到了", len(results) == len(CHAPTERS))

if results:
    name, res, html = results[0]
    print()
    print("=" * 74)
    print(f"[4] 解码样例（{name}）")
    print("=" * 74)
    print(f"  段落数: {len(res.text.split(chr(10)+chr(10)))}")
    print(f"  被还原字符: {res.replaced}")
    print()
    print("  前 5 段：")
    for i, p in enumerate([p for p in res.text.split("\n\n") if p.strip()][:5], 1):
        print(f"    [{i}] {p[:88]}")

    # 常用字占比：解码正确的话会是一个正常中文文本的占比
    COMMON = set("的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和要"
                 "她出也得里后自以会家可下而过天去能对小多然于心学么之都好看起发"
                 "当没成只如事把还用第样道想作种开美总从无情己面最女但现前些所同")
    han = [c for c in res.text if 0x4E00 <= ord(c) <= 0x9FFF]
    ratio = sum(1 for c in han if c in COMMON) / len(han) * 100 if han else 0
    print()
    check("常用字占比正常（>40%）", ratio > 40, f"{ratio:.1f}%")
    check("无残留私用区", res.unresolved == 0)
    check("段落结构保留", len([p for p in res.text.split("\n\n") if p.strip()]) > 3)

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
