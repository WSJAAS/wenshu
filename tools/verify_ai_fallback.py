# -*- coding: utf-8 -*-
"""核查 AI 降级是否真的传达到用户可见的层。

上一版脚本因为把上下文截断到 120 字符，漏看了后面的
_set_task_state(message="AI生成失败，使用规则推断项目信息")，
误判为「静默降级」。这里做一次不截断的核查。

要查的是：那条 message 是否真的能到达用户，还是会在后续
状态更新里被覆盖成一句中性的话。
"""
from __future__ import annotations

import io
import os
import re
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
p = os.path.join(BE, "app", "services", "book_import_service.py")
src = open(p, encoding="utf-8").read()
lines = src.splitlines()

print("=" * 76)
print("[1] 所有 _set_task_state 调用中的 message")
print("=" * 76)
calls = []
for i, line in enumerate(lines, 1):
    if "_set_task_state(" in line:
        # 收集这次调用直到右括号平衡
        buf = []
        depth = 0
        for j in range(i - 1, min(len(lines), i + 12)):
            buf.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            if depth <= 0 and j > i - 1:
                break
        text = " ".join(x.strip() for x in buf)
        m = re.search(r'message\s*=\s*(f?)"([^"]*)"', text)
        if m:
            calls.append((i, m.group(2)))
        else:
            calls.append((i, "(非字面量)"))

for ln, msg in calls:
    flag = ""
    if any(k in msg for k in ("失败", "回退", "规则", "未", "降级")):
        flag = "   ← 有告知"
    print(f"  {ln:>5}: {msg[:70]}{flag}")

print()
print("=" * 76)
print("[2] 关键：降级之后，message 会不会被覆盖")
print("=" * 76)
# 找降级点之后是否还有 set_task_state
fallback_lines = [ln for ln, m in calls if any(k in m for k in ("失败", "回退", "规则"))]
print(f"  含降级说明的调用行号: {fallback_lines}")
for fl in fallback_lines:
    after = [(ln, m) for ln, m in calls if ln > fl]
    if after:
        nxt = after[0]
        print(f"    行 {fl} 之后最近的一次更新: 行 {nxt[0]}  message={nxt[1][:56]}")
        if not any(k in nxt[1] for k in ("失败", "回退", "规则", "未")):
            print(f"      ⚠ 该更新不含降级说明，可能覆盖掉提示")
        else:
            print(f"      ✓ 仍保留降级说明")
    else:
        print(f"    行 {fl} 之后没有更新，说明会保留")

print()
print("=" * 76)
print("[3] 终态 message 是什么")
print("=" * 76)
# 找 status="completed" 的调用
for i, line in enumerate(lines, 1):
    if 'status="completed"' in line or "status='completed'" in line:
        buf = []
        depth = 0
        for j in range(i - 1, min(len(lines), i + 12)):
            buf.append(lines[j])
            depth += lines[j].count("(") - lines[j].count(")")
            if depth <= 0 and j > i - 1:
                break
        text = " ".join(x.strip() for x in buf)
        m = re.search(r'message\s*=\s*(f?)"([^"]*)"', text)
        msg = m.group(2) if m else "(非字面量)"
        print(f"  行 {i}: completed message = {msg[:70]}")

print()
print("=" * 76)
print("[4] 前端读取哪个字段")
print("=" * 76)
fe = os.path.join(r"X:\小说工程\wenshu\frontend\src", "pages", "BookImport.tsx")
fsrc = open(fe, encoding="utf-8").read()
for kw in ("taskStatus.message", ".message", "AI生成失败", "规则推断"):
    n = fsrc.count(kw)
    print(f"  前端 {kw!r}: {n} 次")
# 看 BookImportTask 类型定义
tp = os.path.join(r"X:\小说工程\wenshu\frontend\src", "types", "index.ts")
tsrc = open(tp, encoding="utf-8").read()
m = re.search(r"interface BookImportTask\s*\{(.*?)\}", tsrc, re.S)
if m:
    print()
    print("  BookImportTask 字段:")
    for f in m.group(1).splitlines():
        f = f.strip()
        if f and not f.startswith("/"):
            print(f"    {f}")
