# -*- coding: utf-8 -*-
"""查清矛盾：同一 URL 多次请求，PUA 比例是否变化。

若时高时低 → 服务端做了概率性混淆，可能存在「干净变体」可稳定获取。
若稳定 49.8% → 先前读到干净文本另有原因（缓存/其它字段）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
URL = "https://fanqienovel.com/reader/7256994009498223144"


def fetch(url, extra_headers=None, timeout=30):
    h = {"User-Agent": UA}
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_state(raw):
    start = raw.find("window.__INITIAL_STATE__")
    if start < 0:
        return None
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


def pua_ratio(s: str) -> tuple[int, float]:
    n = sum(1 for c in s if 0xE000 <= ord(c) <= 0xF8FF)
    return n, (n / len(s) * 100 if s else 0.0)


print("=" * 74)
print("[1] 同一 URL 连续 6 次请求（默认 UA）")
print("=" * 74)
results = []
for i in range(6):
    try:
        raw = fetch(URL)
        d = parse_state(raw)
        if not d:
            print(f"  第{i+1}次: 解析失败")
            continue
        content = d["reader"]["chapterData"]["content"]
        n, pct = pua_ratio(content)
        head = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c
                       for c in content[:24])
        results.append((len(raw), len(content), n, pct))
        print(f"  第{i+1}次: 页面={len(raw):>7}  正文={len(content):>5}  "
              f"PUA={n:>4} ({pct:5.1f}%)  {head}")
    except Exception as e:
        print(f"  第{i+1}次: 失败 {str(e).splitlines()[0]}")
    time.sleep(1.5)

print()
print("=" * 74)
print("[2] 换 UA / 加 Referer 再试")
print("=" * 74)
variants = [
    ("无 UA", {"User-Agent": ""}),
    ("移动端 UA", {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                 "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}),
    ("带 Referer", {"Referer": "https://fanqienovel.com/"}),
    ("Googlebot", {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; "
                                 "+http://www.google.com/bot.html)"}),
]
for label, hdr in variants:
    try:
        raw = fetch(URL, hdr)
        d = parse_state(raw)
        if not d:
            print(f"  {label:12s}: 解析失败")
            continue
        content = d["reader"]["chapterData"]["content"]
        n, pct = pua_ratio(content)
        print(f"  {label:12s}: 正文={len(content):>5}  PUA={n:>4} ({pct:5.1f}%)")
    except Exception as e:
        print(f"  {label:12s}: 失败 {str(e).splitlines()[0]}")
    time.sleep(1.5)

print()
print("=" * 74)
print("[3] 汇总判断")
print("=" * 74)
if results:
    pcts = [r[3] for r in results]
    print(f"  默认 UA 的 PUA 比例: min={min(pcts):.1f}%  max={max(pcts):.1f}%")
    if max(pcts) - min(pcts) > 20:
        print("  ★ 比例波动大 → 服务端存在变体，值得继续找干净通道")
    else:
        print("  → 比例稳定，服务端始终混淆正文")
        print("     先前读到干净文本，需回头核对那次的取数路径")
