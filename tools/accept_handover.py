# -*- coding: utf-8 -*-
"""验收：浏览器交接流程。

模拟油猴脚本的行为，验证：
  1. 用 bookId 交接 → 登记成功
  2. 用 itemId 交接 → 反查书籍成功
  3. 书名不一致时如实提示
  4. 登录态未配置时明确说明只能取试读片段
  5. create_task=true 时真的建任务
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8010"
FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


def call(method, path, payload=None, timeout=300):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)[:150]


BOOK = "7231424472606051384"
ITEM = "7256994009498223144"    # 《惹金枝》第 1 章

print("=" * 74)
print("[0] 前置：登录态")
print("=" * 74)
st, body = call("GET", "/api/source/login")
login = json.loads(body) if st == 200 else {}
print(f"  configured={login.get('configured')}  has_sessionid={login.get('has_sessionid')}")
print(f"  note={login.get('note')}")

print()
print("=" * 74)
print("[1] 用 bookId 交接（详情页场景）")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {
    "book_id": BOOK,
    "title": "惹金枝",
    "author": "空留",
    "source_url": f"https://fanqienovel.com/page/{BOOK}",
})
check("请求成功", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    print(f"  书名    : {d['title']}")
    print(f"  章数    : {d['chapter_total']}")
    print(f"  登录态  : {d['login_ready']}")
    print(f"  提示    : {d['message']}")
    check("书名正确", d["title"] == "惹金枝", d["title"])
    check("章数合理", d["chapter_total"] == 550, str(d["chapter_total"]))
    check("书名一致时不报 mismatch", d["title_mismatch"] is False)
    check("未登录时提示取不到全文", "试读" in d["message"] or d["login_ready"])
else:
    print(f"  {body[:200]}")

print()
print("=" * 74)
print("[2] 用 itemId 交接（阅读页场景，需反查）")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {
    "item_id": ITEM,
    "title": "惹金枝",
    "source_url": f"https://fanqienovel.com/reader/{ITEM}",
})
check("请求成功", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    print(f"  反查到 bookId: {d['book_id']}")
    print(f"  书名        : {d['title']}")
    check("反查书籍正确", d["book_id"] == BOOK, d["book_id"])
    check("书名正确", d["title"] == "惹金枝", d["title"])

print()
print("=" * 74)
print("[3] 书名不一致时如实提示")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {
    "book_id": BOOK,
    "title": "错误的书名测试",
})
check("请求成功", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    print(f"  页面书名: {d['from_page_title']}")
    print(f"  平台书名: {d['title']}")
    print(f"  mismatch: {d['title_mismatch']}")
    print(f"  提示    : {d['message']}")
    check("检测到书名不一致", d["title_mismatch"] is True)
    check("提示里说明以平台为准", "以平台登记为准" in d["message"])
    check("书名取平台的值", d["title"] == "惹金枝", d["title"])

print()
print("=" * 74)
print("[4] 无效输入")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {})
check("空输入被拒", st == 400, f"status={st}")

st, body = call("POST", "/api/source/handover", {"book_id": "999999999999999999"})
check("不存在的书被拒", st in (404, 502), f"status={st}")

print()
print("=" * 74)
print("[5] create_task=true 时建任务")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {
    "book_id": BOOK,
    "title": "惹金枝",
    "create_task": True,
    "chapter_count": 3,
}, timeout=300)
check("请求成功", st == 200, f"status={st}")
task_id = None
if st == 200:
    d = json.loads(body)
    task_id = d.get("task_id")
    print(f"  任务: {task_id}")
    print(f"  提示: {d['message']}")
    check("返回了任务 ID", bool(task_id))
    check("提示里说明抓取范围", "前 3 章" in d["message"], d["message"])

if task_id:
    print()
    print("  等待抓取完成…")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 120:
        time.sleep(3)
        st, body = call("GET", f"/api/source/tasks/{task_id}")
        if st != 200:
            continue
        t = json.loads(body)
        if t["status"] in ("completed", "failed", "cancelled", "paused"):
            print(f"    {t['status']}  {t['ok_count']}/{t['total_chapters']}")
            check("任务完成", t["status"] == "completed", t["status"])
            check("抓了 3 章", t["total_chapters"] == 3, str(t["total_chapters"]))
            break

print()
print("=" * 74)
print("[6] 交接历史")
print("=" * 74)
st, body = call("GET", "/api/source/handover/summary?limit=5")
check("接口可用", st == 200, f"status={st}")
if st == 200:
    items = json.loads(body)
    print(f"  最近 {len(items)} 条")
    for t in items[:3]:
        print(f"    {t['book_title'][:20]:<22} {t['status']:<10} "
              f"{t['ok_count']}/{t['total_chapters']}")

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
