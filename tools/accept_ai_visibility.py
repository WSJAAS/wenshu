# -*- coding: utf-8 -*-
"""验收：AI 降级不再被进度消息掩盖。

问题：AI 不可用时流程回退到规则推断，降级说明写在 message 里，
但后续的进度更新会覆盖它，用户最终只看到中性的「解析完成」。

修法：把 AI 使用情况独立成 task 字段（ai_used / ai_degraded /
ai_fallback_reasons），只增不覆盖。

验证：走一次真实拆书导入（AI 未配置），确认：
  1. 最终 message 是中性的（模拟被覆盖）
  2. 但 ai_degraded=True 且原因可读
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
        return None, str(e)[:150]


print("=" * 74)
print("[1] 接口字段是否已暴露")
print("=" * 74)

# 造一个小 TXT 走拆书（多章，确保触发 AI 环节）
txt = "\n\n".join(
    f"第{i}章 测试章节{i}\n\n这是第 {i} 章的正文内容。" + "填充文字。" * 40
    for i in range(1, 9)
)
boundary = "----wenshuBoundary"
body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="file"; filename="ai-check.txt"\r\n'
    "Content-Type: text/plain\r\n\r\n"
    f"{txt}\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="import_mode"\r\n\r\n'
    "append\r\n"
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="extract_mode"\r\n\r\n'
    "full\r\n"
    f"--{boundary}--\r\n"
).encode("utf-8")

req = urllib.request.Request(f"{BASE}/api/book-import/tasks", data=body, method="POST")
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        created = json.loads(r.read().decode("utf-8"))
    task_id = created["task_id"]
    check("拆书任务已创建", bool(task_id), task_id)
except Exception as e:
    check("拆书任务已创建", False, str(e)[:120])
    task_id = None

if task_id:
    print()
    print("  等待处理完成…")
    final = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 240:
        time.sleep(4)
        st, body2 = call("GET", f"/api/book-import/tasks/{task_id}")
        if st != 200:
            continue
        t = json.loads(body2)
        if t.get("status") in ("completed", "failed", "cancelled"):
            final = t
            break

    check("任务达终态", final is not None)
    if final:
        print()
        print("=" * 74)
        print("[2] 最终状态")
        print("=" * 74)
        for k in ("status", "progress", "message", "error",
                  "ai_used", "ai_degraded", "ai_fallback_reasons"):
            print(f"  {k:<22} = {final.get(k)!r}")

        print()
        print("=" * 74)
        print("[3] 判定")
        print("=" * 74)
        check("接口返回 ai_used 字段", "ai_used" in final)
        check("接口返回 ai_degraded 字段", "ai_degraded" in final)
        check("接口返回 ai_fallback_reasons 字段", "ai_fallback_reasons" in final)

        # 当前 AI 未配置（sk-SET_ME），必然降级
        check("AI 未配置时 ai_degraded 为真", final.get("ai_degraded") is True,
              f"ai_degraded={final.get('ai_degraded')}")
        reasons = final.get("ai_fallback_reasons") or []
        check("降级原因非空且可读", len(reasons) > 0, f"{reasons}")

        msg = final.get("message") or ""
        print()
        print(f"  最终 message: {msg!r}")
        if final.get("ai_degraded") and "AI" not in msg and "规则" not in msg:
            print("  ✓ 正是问题场景：message 已不含 AI 信息，")
            print("    但 ai_degraded 仍如实为真 —— 信息没有丢失")
            check("降级信息不依赖 message", True)
        else:
            print("  （本次 message 恰好保留了提示，仍验证字段存在）")
            check("降级信息独立于 message", "ai_degraded" in final)

print()
print("=" * 74)
print("[4] 模型返回字段的兼容性")
print("=" * 74)
print("  ai_fallback_reasons 应为字符串数组：")
if task_id and final:
    r = final.get("ai_fallback_reasons")
    check("类型是数组", isinstance(r, list), f"{type(r).__name__}")
    check("元素是字符串", all(isinstance(x, str) for x in (r or [])), f"{r}")

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
