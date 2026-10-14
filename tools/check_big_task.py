# -*- coding: utf-8 -*-
"""核查那个 550 章整本任务的实际结果。

日志显示它完成了：「取源任务 da35b6b5… 结束：完成：550/550 章」
这正是此前标注「未验证」的整本抓取。核实真实数据。
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = r"X:\小说工程\wenshu\runtime\data\wenshu.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=" * 76)
print("[1] 全部取源任务")
print("=" * 76)
cur.execute("""
    SELECT id, book_title, status, total_chapters, ok_count, failed_count,
           done_count, message, chapters_digest, requested_ranges, created_at,
           chapters_json
    FROM source_tasks ORDER BY created_at DESC
""")
rows = cur.fetchall()
print(f"  共 {len(rows)} 个任务")
print()
print(f"  {'任务':<10} {'书名':<20} {'状态':<10} {'成功/总':<10} {'失败':<6} 指纹")
print("  " + "-" * 76)
for r in rows:
    title = (r["book_title"] or "")[:18]
    ratio = f"{r['ok_count']}/{r['total_chapters']}"
    dig = (r["chapters_digest"] or "")[:10]
    print(f"  {r['id'][:8]:<10} {title:<20} {r['status']:<10} {ratio:<10} "
          f"{r['failed_count']:<6} {dig}")

# 找那个大任务
big = [r for r in rows if (r["total_chapters"] or 0) > 100]
print()
print("=" * 76)
print(f"[2] 整本任务详情（{len(big)} 个）")
print("=" * 76)
for r in big:
    print(f"  任务   : {r['id']}")
    print(f"  书名   : {r['book_title']}")
    print(f"  状态   : {r['status']}")
    print(f"  进度   : done={r['done_count']}  ok={r['ok_count']}  "
          f"failed={r['failed_count']}  total={r['total_chapters']}")
    print(f"  消息   : {r['message']}")
    print(f"  指纹   : {r['chapters_digest']}")
    print(f"  选章范围: {r['requested_ranges']}")
    print(f"  创建时间: {r['created_at']}")

    # 看逐章结果
    ch = json.loads(r["chapters_json"] or "{}")
    print(f"  逐章记录: {len(ch)} 条")

    if ch:
        from collections import Counter
        statuses = Counter(v.get("status") for v in ch.values())
        print(f"  状态分布: {dict(statuses)}")

        chars = [v.get("chars", 0) for v in ch.values()]
        if chars:
            print(f"  正文字数: 总计 {sum(chars):,}  平均 {sum(chars)//len(chars):,}  "
                  f"最少 {min(chars)}  最多 {max(chars):,}")

        # 残留检查
        resid = [v for v in ch.values() if (v.get("pua_after") or 0) > 0]
        print(f"  解码残留: {len(resid)} 章有残留"
              + ("  ← 有问题" if resid else "  ✓ 全部干净"))

        # 空正文检查
        empty = [k for k, v in ch.items()
                 if v.get("status") == "ok" and not (v.get("text") or "").strip()]
        print(f"  状态ok但正文为空: {len(empty)} 章"
              + ("  ← 有问题" if empty else "  ✓ 无"))

        # 抽样看正文
        ok_items = [(k, v) for k, v in ch.items() if v.get("status") == "ok"]
        if ok_items:
            print()
            print("  抽样正文（前 3 章各取开头）：")
            for k, v in ok_items[:3]:
                t = (v.get("text") or "").replace("\n", " ")[:70]
                print(f"    [{v.get('order')}] {v.get('title','')[:20]:<22} {t}")
            print()
            print("  抽样正文（末 2 章）：")
            for k, v in ok_items[-2:]:
                t = (v.get("text") or "").replace("\n", " ")[:70]
                print(f"    [{v.get('order')}] {v.get('title','')[:20]:<22} {t}")

print()
print("=" * 76)
print("[3] 那个 404 的请求")
print("=" * 76)
print("""
日志里有这条：
  GET /api/source/tasks/da35b6b5-0000-0000-0000-000000000000  → 404

任务 ID 被截断补零了（真实是 da35b6b5-87c7-477b-973a-2dfd5c6a8a4e）。
这是前端的轮询在任务 ID 不完整时发出的请求，属于调用侧问题，
不是后端缺陷 —— 后端正确返回了 404。
""")

con.close()
