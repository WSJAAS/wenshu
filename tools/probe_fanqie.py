# -*- coding: utf-8 -*-
"""分析番茄 /rank 页面内嵌的 __INITIAL_STATE__ 结构。

判断：能否不依赖签名接口，直接解析榜单数据。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HTML = Path(r"X:\小说工程\wenshu\runtime\fanqie-rank.html")
raw = HTML.read_text(encoding="utf-8", errors="replace")

print("=" * 70)
print("[1] 定位 __INITIAL_STATE__")
print("=" * 70)
m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", raw, re.S)
if not m:
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*)", raw, re.S)
if not m:
    print("  未匹配到，尝试宽松定位")
    idx = raw.find("__INITIAL_STATE__")
    print(f"  出现位置: {idx}")
    print("  上下文:", raw[idx:idx + 300].replace("\n", " ")[:300])
    sys.exit(1)

blob = m.group(1)
print(f"  原始长度: {len(blob)}")

# 尝试解析（可能被转义）
data = None
for attempt, candidate in enumerate([blob, blob.rstrip().rstrip(";")]):
    try:
        data = json.loads(candidate)
        print(f"  解析成功（第 {attempt + 1} 次尝试）")
        break
    except json.JSONDecodeError as e:
        print(f"  第 {attempt + 1} 次失败: {e.msg} @ pos {e.pos}")
        # 逐步截断找最大可解析前缀
        for end in range(len(candidate), 0, -1):
            if candidate[end - 1] != "}":
                continue
            try:
                data = json.loads(candidate[:end])
                print(f"  截断至 {end} 字符后解析成功")
                break
            except json.JSONDecodeError:
                continue
        if data:
            break

if data is None:
    print("  无法解析")
    sys.exit(1)

print()
print("=" * 70)
print("[2] 顶层结构")
print("=" * 70)


def describe(obj, prefix="", depth=0, max_depth=3):
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:14]:
            t = type(v).__name__
            if isinstance(v, (dict, list)):
                n = len(v)
                print(f"  {prefix}{k}: {t}({n})")
                describe(v, prefix + "  ", depth + 1, max_depth)
            else:
                s = str(v)
                print(f"  {prefix}{k}: {t} = {s[:60]}")
    elif isinstance(obj, list):
        print(f"  {prefix}[0..{min(len(obj),3)}] of {len(obj)}")
        if obj:
            describe(obj[0], prefix + "  ", depth + 1, max_depth)


describe(data)

print()
print("=" * 70)
print("[3] 找书籍列表")
print("=" * 70)


def find_books(obj, path="", found=None, depth=0):
    if found is None:
        found = []
    if depth > 6 or len(found) > 5:
        return found
    if isinstance(obj, dict):
        keys = set(obj.keys())
        # 书籍对象的典型特征
        if {"bookId"} & keys or {"bookName"} & keys or {"book_id"} & keys:
            found.append((path, obj))
            return found
        for k, v in obj.items():
            find_books(v, f"{path}.{k}" if path else k, found, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            find_books(v, f"{path}[{i}]", found, depth + 1)
    return found


books = find_books(data)
print(f"  发现 {len(books)} 处书籍对象")
for path, b in books[:3]:
    print(f"\n  路径: {path}")
    print(f"  字段: {', '.join(list(b.keys())[:16])}")
    for k in ("bookId", "book_id", "bookName", "book_name", "author", "authorName",
              "score", "rank", "category", "tags", "description"):
        if k in b:
            v = b[k]
            print(f"    {k} = {str(v)[:70]}")

print()
print("=" * 70)
print("结论")
print("=" * 70)
if books:
    print("  ✓ 榜单数据可直接从 HTML 解析，无需签名接口")
else:
    print("  ✗ 未找到书籍对象，可能需进一步分析或走接口")
