# -*- coding: utf-8 -*-
"""最后一环：阅读页 chapterData 是否含完整正文。"""
from __future__ import annotations

import json
import sys
import urllib.request

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


def has_pua(s):
    return any(0xE000 <= ord(c) <= 0xF8FF for c in s)


raw = fetch(f"https://fanqienovel.com/reader/{ITEM_ID}")
data = parse_state(raw)
reader = data.get("reader", {})

print("=" * 72)
print("chapterData 结构")
print("=" * 72)
cd = reader.get("chapterData")
if isinstance(cd, dict):
    print(f"  字段: {', '.join(list(cd.keys()))}")
    print()
    for k, v in cd.items():
        if isinstance(v, str):
            print(f"  {k}: str(len={len(v)})")
            if len(v) > 60:
                print(f"      开头: {v[:100]!r}")
        elif isinstance(v, (int, float, bool)) or v is None:
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {type(v).__name__}(len={len(v) if hasattr(v,'__len__') else '-'})")
else:
    print(f"  chapterData 类型: {type(cd).__name__}")

# 取正文
content = None
if isinstance(cd, dict):
    for cand in ("content", "chapterContent", "originalText", "text", "htmlContent"):
        v = cd.get(cand)
        if isinstance(v, str) and len(v) > 100:
            content = v
            print(f"\n  ★ 正文在字段 '{cand}'，长度 {len(v)}")
            break

print()
print("=" * 72)
print("正文检查")
print("=" * 72)
if content:
    print(f"  含 HTML 标签 : {('<p' in content) or ('<div' in content)}")
    print(f"  含 PUA 乱码  : {has_pua(content)}")
    print()
    print("  前 400 字符：")
    print("  " + content[:400].replace("\n", "\n  "))
else:
    print("  未找到正文，看 catalogData")
    cat = reader.get("catalogData")
    if isinstance(cat, dict):
        print(f"  catalogData 字段: {', '.join(list(cat.keys())[:12])}")
        for k, v in list(cat.items())[:6]:
            if isinstance(v, list):
                print(f"    {k}: list({len(v)})")
                if v and isinstance(v[0], dict):
                    print(f"      首项: {', '.join(list(v[0].keys())[:10])}")

print()
print("=" * 72)
print("结论")
print("=" * 72)
if content:
    clean = not has_pua(content)
    print(f"  正文可取：是")
    print(f"  正文干净：{clean}")
    if clean:
        print("  → 阅读页正文无字体混淆，可直接用于拆书导入")
else:
    print("  正文需要进一步分析")
