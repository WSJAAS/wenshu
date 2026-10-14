# -*- coding: utf-8 -*-
"""实跑一次完整交接链路，供浏览器打开时对照。"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8010"


def call(method, path, payload=None, timeout=300):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return None, str(e)[:150]


print("[1] 模拟浏览器交接（用 itemId 走阅读页反查）")
item = "7256994009498223144"
st, body = call("POST", "/api/source/handover", {
    "item_id": item,
    "title": "惹金枝",
    "source_url": f"https://fanqienovel.com/reader/{item}",
})
print(f"    HTTP {st}")
if st == 200:
    d = json.loads(body)
    print(f"    反查 bookId : {d['book_id']}")
    print(f"    书名        : {d['title']}")
    print(f"    章数        : {d['chapter_total']}")
    print(f"    登录态就绪  : {d['login_ready']}")
    print(f"    提示        : {d['message']}")
else:
    print(f"    {body}")

print()
print("[2] 榜单")
st, body = call("GET", "/api/source/rank?enrich=false", timeout=120)
if st == 200:
    d = json.loads(body)
    print(f"    取得 {d['fetched']} 本，补取成功 {d['enriched']}")
    for it in d["items"][:5]:
        cat = it.get("category") or "—"
        print(f"    {it['title'][:22]:<24} {it['read_count']:>9,}  {cat}")

print()
print("[3] 交接历史")
st, body = call("GET", "/api/source/handover/summary?limit=5")
if st == 200:
    items = json.loads(body)
    print(f"    共 {len(items)} 条")
    for t in items[:5]:
        dig = (t.get("chapters_digest") or "")[:10]
        print(f"    {t['book_title'][:18]:<20} {t['status']:<10} "
              f"{t['ok_count']}/{t['total_chapters']:<5} {dig}")

print()
print("[4] 安装指引")
st, body = call("GET", "/api/source/handover/guide")
if st == 200:
    d = json.loads(body)
    print(f"    脚本地址: {d['userscript_url']}")
    for i, s in enumerate(d["steps"], 1):
        print(f"    {i}. {s}")
