# -*- coding: utf-8 -*-
"""决定性排查：全 state 逐字段扫描 PUA，找出干净原文到底在哪个字段。

前面出现矛盾（同 URL 同长度，一次干净一次乱码），必须查清：
  1. reader.chapterData.content 与 preview.chapterData.content 各自情况
  2. 是否所有字段都做了同样处理
  3. 多次请求是否稳定
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
ITEM_ID = "7256994009498223144"
URL = f"https://fanqienovel.com/reader/{ITEM_ID}"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
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


def pua_n(s):
    return sum(1 for c in s if 0xE000 <= ord(c) <= 0xF8FF)


def scan_strings(obj, path="", out=None, depth=0):
    """递归收集所有字符串及其 PUA 计数。"""
    if out is None:
        out = []
    if depth > 10:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            scan_strings(v, f"{path}.{k}" if path else k, out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            scan_strings(v, f"{path}[{i}]", out, depth + 1)
    elif isinstance(obj, str):
        out.append((path, len(obj), pua_n(obj), obj))
    return out


print("=" * 76)
print("[1] 单次请求：逐字段 PUA 扫描（只看长度 > 100 的字符串）")
print("=" * 76)
raw = fetch(URL)
d = parse_state(raw)
strings = [s for s in scan_strings(d) if s[1] > 100]
strings.sort(key=lambda x: -x[1])

print(f"  长度>100 的字符串字段: {len(strings)} 个")
print()
print(f"  {'字段路径':<52} {'长度':>6} {'PUA':>6}")
print("  " + "-" * 68)
for path, ln, npua, val in strings[:20]:
    flag = "干净" if npua == 0 else f"乱码{npua}"
    print(f"  {path:<52} {ln:>6} {npua:>6}   {flag}")

print()
print("=" * 76)
print("[2] 重点字段对照")
print("=" * 76)
candidates = {
    "reader.chapterData.content": None,
    "preview.chapterData.content": None,
    "reader.preChapterData.content": None,
    "reader.nextChapterData.content": None,
}
for key in candidates:
    parts = key.split(".")
    cur = d
    ok = True
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            ok = False
            break
    if ok and isinstance(cur, str):
        candidates[key] = cur
        print(f"\n  {key}")
        print(f"    长度={len(cur)}  PUA={pua_n(cur)}")
        # 去标签后看开头
        import re
        plain = re.sub(r"<[^>]+>", "", cur)
        vis = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c for c in plain[:70])
        print(f"    开头: {vis}")
    else:
        print(f"\n  {key}")
        print(f"    不存在或非字符串")

print()
print("=" * 76)
print("[3] 连续 3 次请求，看候选字段是否稳定")
print("=" * 76)
for i in range(3):
    r2 = fetch(URL)
    d2 = parse_state(r2)
    row = []
    for key in ("reader.chapterData.content", "preview.chapterData.content"):
        parts = key.split(".")
        cur = d2
        for p in parts:
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, str):
            row.append(f"{key.split('.')[0]}={len(cur)}字/PUA{pua_n(cur)}")
        else:
            row.append(f"{key.split('.')[0]}=无")
    print(f"  第{i+1}次: " + "   ".join(row))
    time.sleep(2)

print()
print("=" * 76)
print("[4] 判断")
print("=" * 76)
rc = candidates.get("reader.chapterData.content")
pc = candidates.get("preview.chapterData.content")
if pc and pua_n(pc) == 0:
    print("  ★ preview.chapterData.content 是干净的")
    print(f"    长度 {len(pc)}，可作原文来源（需确认它覆盖多少内容）")
elif rc and pua_n(rc) == 0:
    print("  ★ reader.chapterData.content 本次干净")
else:
    print("  两个字段都有乱码 —— 需要走字形还原或其它通道")
    if rc:
        print(f"    reader  : {len(rc)} 字，PUA {pua_n(rc)}")
    if pc:
        print(f"    preview : {len(pc)} 字，PUA {pua_n(pc)}")
