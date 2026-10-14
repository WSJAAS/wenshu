# -*- coding: utf-8 -*-
"""探测：番茄详情页的章节列表结构，以及正文是否可取。"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/131.0.0.0 Safari/537.36")
BOOK_URL = "https://fanqienovel.com/page/7231424472606051384"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_state(raw: str):
    key = "window.__INITIAL_STATE__"
    start = raw.find(key)
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


print("=" * 72)
print("[1] 详情页")
print("=" * 72)
raw = fetch(BOOK_URL)
print(f"  长度: {len(raw)}")
data = parse_state(raw)
if not data:
    print("  解析失败")
    sys.exit(1)

# 顶层键
print(f"  顶层键: {', '.join(list(data.keys())[:20])}")

# 找书籍信息
def walk(obj, path="", depth=0, hits=None):
    if hits is None:
        hits = []
    if depth > 6 or len(hits) > 12:
        return hits
    if isinstance(obj, dict):
        if "bookId" in obj or "bookName" in obj:
            hits.append((path, obj))
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else k, depth + 1, hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):
            walk(v, f"{path}[{i}]", depth + 1, hits)
    return hits


print()
print("=" * 72)
print("[2] 书籍对象与章节")
print("=" * 72)
hits = walk(data)
for path, b in hits[:3]:
    print(f"\n  路径: {path}")
    ks = list(b.keys())
    print(f"  字段({len(ks)}): {', '.join(ks[:24])}")
    # 章节目录
    for cand in ("chapterList", "chapter_list", "chapters", "chapterListWithVolume"):
        if cand in b:
            v = b[cand]
            print(f"  ★ {cand}: {type(v).__name__} 长度={len(v)}")
            if isinstance(v, list) and v:
                first = v[0]
                if isinstance(first, dict):
                    print(f"    首项字段: {', '.join(list(first.keys())[:14])}")
                    for k in ("itemId", "chapterId", "title", "chapterTitle"):
                        if k in first:
                            print(f"      {k} = {str(first[k])[:60]}")
                elif isinstance(first, list):
                    print(f"    首项是列表(分卷?)，长度={len(first)}")
                    if first and isinstance(first[0], dict):
                        print(f"      卷内首项字段: {', '.join(list(first[0].keys())[:14])}")
    # 书籍基本字段
    for k in ("bookId", "bookName", "author", "abstract", "wordNumber",
              "readCount", "category", "creationStatus", "thumbUri", "score"):
        if k in b:
            print(f"    {k} = {str(b[k])[:70]}")

print()
print("=" * 72)
print("[3] 章节正文接口探测")
print("=" * 72)
# 从数据里找第一个 itemId
item_id = None
for path, b in hits:
    for cand in ("chapterList", "chapter_list", "chapters"):
        v = b.get(cand)
        if isinstance(v, list) and v:
            f = v[0]
            if isinstance(f, dict):
                item_id = f.get("itemId") or f.get("chapterId")
            elif isinstance(f, list) and f and isinstance(f[0], dict):
                item_id = f[0].get("itemId") or f[0].get("chapterId")
            if item_id:
                break
    if item_id:
        break

print(f"  首个章节 itemId: {item_id}")
if item_id:
    for tmpl in (
        f"https://fanqienovel.com/reader/{item_id}",
        f"https://fanqienovel.com/page/{item_id}",
    ):
        try:
            req = urllib.request.Request(tmpl, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read().decode("utf-8", "replace")
            has_state = "__INITIAL_STATE__" in body
            print(f"  {tmpl}")
            print(f"    -> {r.status}  len={len(body)}  STATE={has_state}")
            if has_state:
                d2 = parse_state(body)
                if d2:
                    # 找正文
                    txt = None
                    def find_text(o, depth=0):
                        if depth > 7:
                            return None
                        if isinstance(o, dict):
                            for k in ("content", "chapterContent", "originalText", "text"):
                                v = o.get(k)
                                if isinstance(v, str) and len(v) > 200:
                                    return v
                            for v in o.values():
                                r2 = find_text(v, depth + 1)
                                if r2:
                                    return r2
                        elif isinstance(o, list):
                            for v in o[:5]:
                                r2 = find_text(v, depth + 1)
                                if r2:
                                    return r2
                        return None
                    txt = find_text(d2)
                    if txt:
                        print(f"    ★ 找到正文 {len(txt)} 字符")
                        print(f"      开头: {txt[:120]}")
                    else:
                        print("      未找到长文本正文")
        except Exception as e:
            print(f"  {tmpl}")
            print(f"    -> 失败: {str(e).splitlines()[0]}")
