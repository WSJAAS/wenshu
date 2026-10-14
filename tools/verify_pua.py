# -*- coding: utf-8 -*-
"""核对：阅读页正文里到底有没有 PUA 字符。

我此前的结论来自只检查了 content[:3000] 且误判为干净。
这次逐字符精确统计，并区分「PUA」与「其它异常码位」。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
ITEM_ID = "7256994009498223144"


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


raw = fetch(f"https://fanqienovel.com/reader/{ITEM_ID}")
data = parse_state(raw)
content = data["reader"]["chapterData"]["content"]

print("=" * 74)
print("正文精确统计")
print("=" * 74)
print(f"  总长度: {len(content)}")

pua = [c for c in content if 0xE000 <= ord(c) <= 0xF8FF]
print(f"  PUA 字符数: {len(pua)}")
print(f"  PUA 占比  : {len(pua) / len(content) * 100:.1f}%")

other = [c for c in content
         if ord(c) > 0x2FFF and not (0xE000 <= ord(c) <= 0xF8FF)
         and not (0x4E00 <= ord(c) <= 0x9FFF)]
print(f"  其它非常规码位: {len(other)}")

print()
print("=" * 74)
print("PUA 码位分布（前 20 个高频）")
print("=" * 74)
cnt = Counter(pua)
for cp, n in cnt.most_common(20):
    print(f"  U+{ord(cp):04X}  x{n}")

print()
print("=" * 74)
print("去掉 HTML 标签后的实际文本片段")
print("=" * 74)
import re
text = re.sub(r"<[^>]+>", "", content)
print(f"  纯文本长度: {len(text)}")
pua2 = [c for c in text if 0xE000 <= ord(c) <= 0xF8FF]
print(f"  其中 PUA  : {len(pua2)}")
print()
print()
# 把 PUA 替换成 □ 再展示，便于肉眼确认乱码比例
visible = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c for c in text[:200])
print("  展示前 200 字符（PUA 以 □ 代替）：")
print("  " + visible.replace("\n", " "))

print()
print("=" * 74)
print("对比：同一本书的详情页与目录")
print("=" * 74)
d_raw = fetch("https://fanqienovel.com/page/7231424472606051384")
d = parse_state(d_raw)
page = d["page"]
bn = page.get("bookName", "")
print(f"  详情页书名: {bn!r}  PUA={sum(1 for c in bn if 0xE000 <= ord(c) <= 0xF8FF)}")

vols = page.get("chapterListWithVolume") or []
titles = [ch.get("title", "") for v in vols if isinstance(v, list) for ch in v
          if isinstance(ch, dict)]
tpua = sum(sum(1 for c in t if 0xE000 <= ord(c) <= 0xF8FF) for t in titles)
print(f"  目录标题数: {len(titles)}  标题 PUA 合计: {tpua}")
