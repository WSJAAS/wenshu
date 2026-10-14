# -*- coding: utf-8 -*-
"""书源端到端验收：榜单 → 详情 → 取源任务 → 分章 → TXT → 交给拆书导入。

这是「123」三项的合并验收：
  1. 接入现有拆书导入
  2. 小批量实跑（验证限速）
  3. 为前端准备可用的接口
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
FAILS: list[str] = []


def call(method, path, payload=None, timeout=600):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}"


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


print("=" * 76)
print("[0] 模块状态")
print("=" * 76)
st, body = call("GET", "/api/source/status")
check("书源已启用", st == 200 and json.loads(body).get("enabled") is True, f"status={st}")
if st != 200:
    print("RESULT: FAIL（书源未启用，后续无法测试）")
    sys.exit(1)

print()
print("=" * 76)
print("[1] 榜单（含覆盖范围说明）")
print("=" * 76)
t0 = time.monotonic()
st, body = call("GET", "/api/source/rank?enrich=true", timeout=300)
el = time.monotonic() - t0
check("榜单接口可用", st == 200, f"status={st}")
if st != 200:
    print(f"  {body[:200]}")
else:
    d = json.loads(body)
    print(f"  用时 {el:.1f}s")
    print(f"  已取得 : {d['fetched']} 本")
    print(f"  平台声明: {d['declared_total']} 本")
    print(f"  是否完整: {d['is_complete']}")
    print(f"  说明   : {d['coverage_note']}")
    print(f"  时间口径: {d['platform_note']}")
    print()
    print(f"  {'书名':<24} {'作者':<14} {'在读':>9} {'字数':>9}  分类")
    print("  " + "-" * 74)
    for it in d["items"][:8]:
        print(f"  {it.get('title','')[:22]:<24} {it.get('author','')[:12]:<14} "
              f"{it.get('read_count',0):>9,} {it.get('word_count',0):>9,}  "
              f"{it.get('category','')}")

    check("条目含干净书名", all(it.get("title") for it in d["items"]),
          f"缺书名的: {sum(1 for it in d['items'] if not it.get('title'))}")
    check("如实标注覆盖范围", "已取得" in d["coverage_note"])
    check("标注了时间口径", "前一日" in d["platform_note"])
    check("未声称完整", d["is_complete"] is False or d["fetched"] >= d["declared_total"],
          f"is_complete={d['is_complete']}")

    # 记下第一本的 bookId 供后续用
    target_book = d["items"][0]["book_id"]
    target_title = d["items"][0]["title"]

print()
print("=" * 76)
print("[2] 详情与目录")
print("=" * 76)
st, body = call("GET", f"/api/source/book/{target_book}", timeout=300)
check("详情接口可用", st == 200, f"status={st}")
if st == 200:
    d = json.loads(body)
    b = d["book"]
    print(f"  书名: {b['title']}")
    print(f"  作者: {b['author']}")
    print(f"  分类: {b['category']}  标签: {b['tags']}")
    print(f"  字数: {b['word_count']:,}  在读: {b['read_count']:,}  状态: {b['status']}")
    print(f"  章数: {len(d['chapters'])}")
    print(f"  分卷: {', '.join(f'{v['name']}({v['count']})' for v in d['volumes'][:4])}")
    print(f"  锁定说明: {d['lock_field_note'][:70]}...")

    check("书名干净", bool(b["title"]))
    check("分类已解析", bool(b["category"]) and not any("{" in t for t in b["tags"]))
    check("目录非空", len(d["chapters"]) > 0, f"{len(d['chapters'])} 章")
    check("说明了锁定字段含义", "不代表不可读" in d["lock_field_note"])

print()
print("=" * 76)
print("[3] 取源任务（小批量实跑：12 章）")
print("=" * 76)
st, body = call("POST", "/api/source/tasks", {
    "book_id": target_book,
    "selection": {"preset": "first", "count": 12},
    "interval": 1.2,
}, timeout=300)
check("创建任务", st == 200, f"status={st}")
task_id = None
if st != 200:
    print(f"  {body[:200]}")
else:
    t = json.loads(body)
    task_id = t["task_id"]
    print(f"  任务ID: {task_id}")
    print(f"  状态  : {t['status']}  {t['message']}")
    print(f"  计划抓取: {t['total_chapters']} 章")

print()
print("  等待抓取完成（12 章 × 1.2s 间隔，约 20~30s）...")
if task_id:
    t0 = time.monotonic()
    last_done = -1
    final = None
    while time.monotonic() - t0 < 300:
        time.sleep(4)
        st, body = call("GET", f"/api/source/tasks/{task_id}", timeout=60)
        if st != 200:
            continue
        t = json.loads(body)
        if t["done_count"] != last_done:
            last_done = t["done_count"]
            print(f"    [{time.monotonic()-t0:5.1f}s] {t['status']:<10} "
                  f"{t['done_count']}/{t['total_chapters']}  "
                  f"成功{t['ok_count']} 失败{t['failed_count']}")
        if t["status"] in ("completed", "failed", "paused", "cancelled"):
            final = t
            break
    el = time.monotonic() - t0

    if final:
        print()
        print(f"  用时 {el:.1f}s")
        print(f"  最终状态: {final['status']}")
        print(f"  完整取得 {final['ok_count']} / {final['total_chapters']}")
        check("任务完成", final["status"] == "completed", f"status={final['status']}")
        # 注意：ok_count 只统计**完整取得**的章。
        # 选 12 章会跨过第 10 章的免费边界，第 11、12 章只拿到试读片段，
        # 因此 ok_count 少于 total 是**正确行为**，不是失败。
        check("完整取得的章数合理（不应超过总数）",
              final["ok_count"] <= final["total_chapters"],
              f"{final['ok_count']}/{final['total_chapters']}")
        check("跨过免费边界的章被标为未完整取得（而非谎报成功）",
              final["ok_count"] < final["total_chapters"],
              f"ok={final['ok_count']}，说明第 11 章起的截断被正确识别")
        # 限速验证：12 章 × 1.2s ≈ 13.2s 下限
        check("限速实际生效", el >= 12 * 1.2 * 0.85, f"{el:.1f}s >= {12*1.2*0.85:.1f}s")

print()
print("=" * 76)
print("[4] 分章结果与缺章清单")
print("=" * 76)
if task_id:
    st, body = call("GET", f"/api/source/tasks/{task_id}/chapters", timeout=120)
    check("分章接口可用", st == 200, f"status={st}")
    if st == 200:
        d = json.loads(body)
        rep = d["report"]
        print(f"  统计范围: {d.get('scope_note', '')}")
        print(f"  总章数: {rep['total']}  成功: {rep['ok']}  缺失: {rep['missing_count']}")
        print(f"  按状态: {rep['by_status']}")
        print()
        print(f"  {'章':<4} {'标题':<28} {'状态':<12} {'字数':>6} {'PUA前':>7} {'PUA后':>6}")
        print("  " + "-" * 70)
        for c in d["chapters"][:12]:
            print(f"  {c['order']:<4} {c['title'][:26]:<28} {c['status']:<12} "
                  f"{c['chars']:>6} {c['pua_before']:>7} {c['pua_after']:>6}")

        # 范围应只含选定的 12 章，未选中的不算缺失
        check("统计范围只含选定章", rep["total"] == 12, f"total={rep['total']}")
        # 第 11、12 章只拿到试读片段，因此计为缺失是**正确行为**：
        # 覆盖率必须反映「拿到多少全文」，不能把预览当成功。
        check("试读片段计入缺章（覆盖率不虚报）",
              rep["missing_count"] == rep["total"] - rep["ok"],
              f"缺失 {rep['missing_count']}，完整 {rep['ok']}")
        check("缺章原因是 preview 而非失败",
              all(s in ("ok", "preview") for s in rep["by_status"]),
              f"by_status={rep['by_status']}")
        check("说明了统计范围", "未纳入" in d.get("scope_note", ""))
        check("解码后无残留",
              all(c["pua_after"] == 0 for c in d["chapters"] if c["status"] == "ok"))
        check("记录了原始乱码数", all(c["pua_before"] > 0 for c in d["chapters"]
                                    if c["status"] == "ok"))

print()
print("=" * 76)
print("[5] 导出 TXT")
print("=" * 76)
if task_id:
    try:
        req = urllib.request.Request(f"{BASE}/api/source/tasks/{task_id}/txt")
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode("utf-8", "replace")
        print(f"  TXT 长度: {len(txt)} 字符")
        print(f"  行数    : {len(txt.splitlines())}")
        print()
        print("  前 10 行：")
        for ln in txt.splitlines()[:10]:
            print(f"    {ln[:74]}")
        check("TXT 非空", len(txt) > 1000, f"{len(txt)} 字符")
        check("含章节标题", "第001章" in txt or "第1章" in txt)
        check("段落保留", "\n\n" in txt)
    except Exception as e:
        check("导出 TXT", False, str(e)[:120])

print()
print("=" * 76)
print("[6] 交给拆书导入")
print("=" * 76)
if task_id:
    st, body = call("POST", f"/api/source/tasks/{task_id}/to-book-import", timeout=300)
    check("衔接接口可用", st == 200, f"status={st}")
    if st == 200:
        d = json.loads(body)
        print(f"  书名    : {d['book_title']}")
        print(f"  提交章节: {d['handed_chapters']} 章")
        print(f"  TXT 字数: {d['txt_chars']}")
        print(f"  说明    : {d['note']}")
        bi = d.get("book_import_task", {})
        print(f"  拆书任务: {bi.get('task_id')}  [{bi.get('status')}]")
        check("拆书任务已创建", bool(bi.get("task_id")), f"{bi.get('task_id')}")
        check("提交了正文", d["handed_chapters"] > 0, f"{d['handed_chapters']} 章")

        if bi.get("task_id"):
            # 查拆书任务状态（它可能还在跑，只报告不判定）
            time.sleep(4)
            st2, b2 = call("GET", f"/api/book-import/tasks/{bi['task_id']}", timeout=60)
            if st2 == 200:
                t2 = json.loads(b2)
                print(f"  拆书进度: {t2.get('status')} {t2.get('progress')}% "
                      f"{t2.get('message')}")
    else:
        print(f"  {body[:300]}")

print()
print("=" * 76)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 76)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
