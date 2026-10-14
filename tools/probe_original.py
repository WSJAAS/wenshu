# -*- coding: utf-8 -*-
"""找返回干净原文的通道。

思路：不走字形还原，找直接给纯文本的接口/字段。
线索：
  1. reader 状态顶层有 seo / preview 键 —— SEO 内容常是干净文本
  2. /reading/bookapi/ 是 APP 接口（网页站同源挂载）
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
ITEM_ID = "7256994009498223144"
BOOK_ID = "7231424472606051384"


def fetch(url, headers=None, timeout=25):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e).splitlines()[0]


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


def pct(s):
    if not s:
        return "空"
    n = sum(1 for c in s if 0xE000 <= ord(c) <= 0xF8FF)
    return f"{len(s)}字 PUA={n}({n/len(s)*100:.1f}%)"


print("=" * 74)
print("[1] 阅读页里 seo / preview 字段")
print("=" * 74)
st, raw = fetch(f"https://fanqienovel.com/reader/{ITEM_ID}")
d = parse_state(raw)
if not d:
    print("  解析失败")
    sys.exit(1)

for key in ("seo", "preview", "common", "readProgress"):
    v = d.get(key)
    print(f"\n  ── {key} ──  类型={type(v).__name__}")
    if isinstance(v, dict):
        print(f"     字段: {', '.join(list(v.keys())[:20])}")
        for k2, v2 in list(v.items())[:12]:
            if isinstance(v2, str) and len(v2) > 50:
                npua = sum(1 for c in v2 if 0xE000 <= ord(c) <= 0xF8FF)
                flag = "★干净" if npua == 0 else f"乱码{npua}"
                print(f"       {k2}: {flag}  {v2[:90]!r}")
            elif isinstance(v2, (dict, list)):
                print(f"       {k2}: {type(v2).__name__}(len={len(v2)})")

# 深度搜：整个 state 里有没有 PUA=0 的长中文串
print()
print("=" * 74)
print("[2] 全 state 搜「无 PUA 的长文本」（>=300 字）")
print("=" * 74)
hits = []


def dig(o, path="", d_=0):
    if d_ > 8:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            dig(v, f"{path}.{k}" if path else k, d_ + 1)
    elif isinstance(o, list):
        for i, v in enumerate(o[:8]):
            dig(v, f"{path}[{i}]", d_ + 1)
    elif isinstance(o, str) and len(o) >= 300:
        pua = sum(1 for c in o if 0xE000 <= ord(c) <= 0xF8FF)
        han = sum(1 for c in o if 0x4E00 <= ord(c) <= 0x9FFF)
        if han > 50:
            hits.append((path, len(o), pua, han, o[:70]))


dig(d)
hits.sort(key=lambda x: x[2])
for path, ln, pua, han, head in hits[:12]:
    tag = "★无乱码" if pua == 0 else f"乱码{pua}"
    print(f"  [{tag}] {path}")
    print(f"          长度={ln} 汉字={han}")
    print(f"          {head!r}")

print()
print("=" * 74)
print("[3] APP 接口探测")
print("=" * 74)
candidates = [
    ("多书详情", f"https://fanqienovel.com/reading/bookapi/multi-detail/v/?book_id={BOOK_ID}"),
    ("目录", f"https://fanqienovel.com/reading/bookapi/directory/all_items/v/?book_id={BOOK_ID}"),
    ("章节", f"https://fanqienovel.com/reading/bookapi/chapter/v/?item_id={ITEM_ID}"),
    ("正文", f"https://fanqienovel.com/reading/bookapi/content/v/?item_id={ITEM_ID}"),
    ("搜索", "https://fanqienovel.com/reading/bookapi/search/page/v/?query=%E7%8E%84%E5%B9%BB"),
]
for label, url in candidates:
    st, body = fetch(url)
    print(f"\n  {label}  -> {st}")
    print(f"    {url[:96]}")
    if st == 200:
        try:
            j = json.loads(body)
            print(f"    JSON 顶层: {', '.join(list(j.keys())[:10])}")
        except json.JSONDecodeError:
            print(f"    非 JSON，前 200 字: {body[:200]!r}")
    elif body:
        print(f"    {body[:180]!r}")
