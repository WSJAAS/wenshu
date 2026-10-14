# -*- coding: utf-8 -*-
"""验证前端所依赖的全部接口契约（前端 SPA，无法用无头浏览器测）。

逐条调用前端会用的接口，确认字段与类型匹配，
并检查前端代码里的字段引用都能在响应里找到。
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:8010"
FAILS: list[str] = []


def call(method, path, payload=None, timeout=600):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}"


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


print("=" * 74)
print("[1] 前端包内是否含书源页面")
print("=" * 74)
st, html = call("GET", "/")
js = re.search(r"/assets/(index-[^\"]+\.js)", html)
check("首页可取", st == 200 and bool(js))
if js:
    st2, bundle = call("GET", f"/assets/{js.group(1)}", timeout=60)
    check("包可下载", st2 == 200, f"{len(bundle)} 字节")
    for kw in ("在线书源", "开始抓取", "交给拆书导入", "导出 TXT",
               "字体混淆", "开篇若干章", "指定卷", "整本"):
        check(f"含文案「{kw}」", kw in bundle)

print()
print("=" * 74)
print("[2] 接口契约（前端 field 逐一核对）")
print("=" * 74)
st, body = call("GET", "/api/source/status")
check("status 可用", st == 200)
if st == 200:
    d = json.loads(body)
    for k in ("enabled", "module", "note"):
        check(f"status.{k}", k in d)

st, body = call("GET", "/api/source/rank?enrich=false", timeout=120)
check("rank 可用", st == 200)
rank = json.loads(body) if st == 200 else {}
if st == 200:
    for k in ("items", "fetched", "declared_total", "enriched",
              "enrich_failed", "garbled_titles", "coverage_note",
              "is_complete", "platform_note"):
        check(f"rank.{k}", k in rank)
    if rank.get("items"):
        it = rank["items"][0]
        for k in ("book_id", "title", "author", "read_count", "word_count"):
            check(f"rank.items[].{k}", k in it)

book_id = rank["items"][0]["book_id"] if rank.get("items") else None

if book_id:
    st, body = call("GET", f"/api/source/book/{book_id}", timeout=180)
    check("book 可用", st == 200)
    if st == 200:
        d = json.loads(body)
        for k in ("book", "chapters", "volumes", "lock_field_note"):
            check(f"book.{k}", k in d)
        for k in ("book_id", "title", "author", "category", "tags",
                  "word_count", "read_count", "status"):
            check(f"book.book.{k}", k in d.get("book", {}))
        if d.get("chapters"):
            ch = d["chapters"][0]
            for k in ("item_id", "title", "volume", "order", "is_locked", "need_pay"):
                check(f"chapters[].{k}", k in ch)

print()
print("=" * 74)
print("[3] 创建任务 → 轮询 → 分章（前端主流程）")
print("=" * 74)
task_id = None
if book_id:
    st, body = call("POST", "/api/source/tasks", {
        "book_id": book_id,
        "selection": {"preset": "first", "count": 6, "volume": "", "item_ids": []},
        "interval": 1.2,
    }, timeout=300)
    check("createTask 可用", st == 200, f"status={st}")
    if st == 200:
        t = json.loads(body)
        task_id = t["task_id"]
        for k in ("task_id", "status", "message", "book_id", "book_title",
                  "total_chapters", "done_count", "ok_count", "failed_count"):
            check(f"task.{k}", k in t)

if task_id:
    print()
    print("  轮询至完成…")
    t0 = time.monotonic()
    final = None
    while time.monotonic() - t0 < 240:
        time.sleep(3)
        st, body = call("GET", f"/api/source/tasks/{task_id}")
        if st != 200:
            continue
        t = json.loads(body)
        if t["status"] in ("completed", "failed", "paused", "cancelled"):
            final = t
            break
    check("任务达终态", final is not None, f"status={final['status'] if final else '超时'}")
    if final:
        check("任务完成", final["status"] == "completed")
        check("全部成功", final["ok_count"] == final["total_chapters"],
              f"{final['ok_count']}/{final['total_chapters']}")

    st, body = call("GET", f"/api/source/tasks/{task_id}/chapters")
    check("chapters 可用", st == 200)
    if st == 200:
        d = json.loads(body)
        for k in ("task", "chapters", "report", "scope_note"):
            check(f"chapters.{k}", k in d)
        rep = d.get("report", {})
        for k in ("total", "ok", "missing_count", "by_status", "missing"):
            check(f"report.{k}", k in rep)
        if d.get("chapters"):
            c = d["chapters"][0]
            for k in ("order", "item_id", "title", "volume", "status",
                      "error", "chars", "pua_before", "pua_after"):
                check(f"chapters[].{k}", k in c)

    st, body = call("GET", f"/api/source/tasks/{task_id}/txt", timeout=120)
    check("txt 导出可用", st == 200 and len(body) > 500, f"{len(body)} 字符")

    st, body = call("POST", f"/api/source/tasks/{task_id}/to-book-import", timeout=300)
    check("交接拆书可用", st == 200, f"status={st}")
    if st == 200:
        d = json.loads(body)
        for k in ("ok", "book_title", "handed_chapters", "txt_chars",
                  "book_import_task", "note"):
            check(f"handoff.{k}", k in d)

print()
print("=" * 74)
print("[4] 关闭态行为")
print("=" * 74)
print("  （当前已启用，无法直接测关闭态；验证代码路径存在）")
src = Path(r"X:\小说工程\wenshu\backend\app\api\source.py").read_text(encoding="utf-8")
check("有开关函数", "_source_enabled" in src)
check("关闭时返回 403", 'status_code=403' in src)
check("默认关闭", "FANQIE_ENABLED: bool = False" in
      Path(r"X:\小说工程\wenshu\backend\app\config.py").read_text(encoding="utf-8"))

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
