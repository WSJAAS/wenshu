# -*- coding: utf-8 -*-
"""验收：书源 → 拆书模块的自动衔接。

用户要求：交出来之后发给拆书模块，拆书模块接到书之后应该有反应。

验证：
  1. 浏览器交接（默认 create_task=true）→ 自动抓取
  2. 抓完自动交给拆书，不需要再点按钮
  3. 取源任务上能看到拆书的状态（import_task_id / import_status）
  4. 拆书任务真的开始跑，并有可见进度
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


ITEM = "7256994009498223144"   # 《惹金枝》第 1 章

print("=" * 74)
print("[1] 浏览器交接（默认即抓取 + 自动交接）")
print("=" * 74)
st, body = call("POST", "/api/source/handover", {
    "item_id": ITEM,
    "title": "惹金枝",
    "chapter_count": 8,      # 8 章都在免费范围内，能拿到全文
    "source_url": f"https://fanqienovel.com/reader/{ITEM}",
}, timeout=300)
check("交接请求成功", st == 200, f"status={st}")

task_id = None
if st == 200:
    d = json.loads(body)
    task_id = d.get("task_id")
    print(f"    书名    : {d['title']}")
    print(f"    章数    : {d['chapter_total']}")
    print(f"    自动交接: {d.get('auto_import')}")
    print(f"    消息    : {d['message']}")
    check("默认就创建了取源任务", bool(task_id), str(task_id))
    check("默认开启自动交接", d.get("auto_import") is True)
    check("消息里说明了会自动交接", "自动交给拆书" in d["message"], d["message"])
else:
    print(f"    {body[:200]}")

if not task_id:
    print("\nRESULT: FAIL（无任务，无法继续）")
    sys.exit(1)

print()
print("=" * 74)
print("[2] 等待取源完成，观察是否自动交接")
print("=" * 74)
final = None
t0 = time.monotonic()
while time.monotonic() - t0 < 300:
    time.sleep(4)
    st, body = call("GET", f"/api/source/tasks/{task_id}")
    if st != 200:
        continue
    t = json.loads(body)
    if t["status"] in ("completed", "failed", "cancelled", "paused"):
        final = t
        break

check("取源任务达终态", final is not None)
if final:
    print(f"    状态    : {final['status']}")
    print(f"    抓取    : {final['ok_count']}/{final['total_chapters']} 章")
    print(f"    消息    : {final['message']}")
    print(f"    import_status : {final.get('import_status')!r}")
    print(f"    import_task_id: {final.get('import_task_id')!r}")
    print(f"    import_message: {final.get('import_message')!r}")

    print()
    check("取源完成", final["status"] == "completed", final["status"])
    check("无需手工点按钮即已交接（import_status=handed）",
          final.get("import_status") == "handed",
          f"import_status={final.get('import_status')}")
    check("记录了拆书任务 ID", bool(final.get("import_task_id")),
          final.get("import_task_id") or "空")
    check("交接说明里有章数与字数", bool(final.get("import_message")),
          final.get("import_message") or "")

bi_id = final.get("import_task_id") if final else None

print()
print("=" * 74)
print("[3] 拆书模块的反应")
print("=" * 74)
if bi_id:
    check("能查到拆书任务", True, bi_id)
    bi_final = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 300:
        st, body = call("GET", f"/api/book-import/tasks/{bi_id}")
        if st != 200:
            time.sleep(3)
            continue
        t = json.loads(body)
        if t["status"] in ("completed", "failed", "cancelled"):
            bi_final = t
            break
        time.sleep(3)

    if bi_final:
        print(f"    状态    : {bi_final['status']}")
        print(f"    进度    : {bi_final['progress']}%")
        print(f"    消息    : {bi_final.get('message')}")
        print(f"    AI 使用 : ai_used={bi_final.get('ai_used')}  "
              f"ai_degraded={bi_final.get('ai_degraded')}")
        check("拆书任务跑起来并到达终态", True, bi_final["status"])
        check("拆书任务成功", bi_final["status"] == "completed", bi_final["status"])
        check("拆书有进度信息", isinstance(bi_final.get("progress"), int))

        # 能预览说明真的解析出了结构
        st, body = call("GET", f"/api/book-import/tasks/{bi_id}/preview")
        check("能取到拆书预览", st == 200, f"status={st}")
        if st == 200:
            pv = json.loads(body)
            ch = pv.get("chapters") or []
            ol = pv.get("outlines") or []
            sug = pv.get("project_suggestion") or {}
            print(f"    预览章节: {len(ch)} 章")
            print(f"    预览大纲: {len(ol)} 条")
            print(f"    建议书名: {sug.get('title')}")
            check("拆书解析出了章节", len(ch) > 0, f"{len(ch)} 章")
            check("拆书生成了大纲", len(ol) > 0, f"{len(ol)} 条")
    else:
        check("拆书任务到达终态", False, "超时")
else:
    check("有拆书任务 ID", False, "取源侧未记录")

print()
print("=" * 74)
print("[4] 交接失败时的痕迹")
print("=" * 74)
print("  （正常情况下此处为空；失败时 import_status 应为 failed 并带原因）")
st, body = call("GET", "/api/source/tasks?limit=5")
if st == 200:
    for t in json.loads(body)[:5]:
        flag = t.get("import_status") or "未交接"
        print(f"    {t['book_title'][:16]:<18} {t['status']:<10} "
              f"{flag:<10} {t.get('import_message','')[:44]}")

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
