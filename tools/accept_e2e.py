# -*- coding: utf-8 -*-
"""
文枢 · 端到端验收

验证四件事：
  1. 无登录直接可用
  2. 主页与静态资源正常
  3. 文本重塑：原稿/候选稿分离、体检、本地规则
  4. 人工采纳与版本回退
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
FAILURES: list[str] = []


def call(method, path, payload=None, timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), {}
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}", {}


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


ORIGINAL = (
    "他感到十分疲惫，内心深处充满了极度的绝望与纠结。"
    "夜雨下个不停，他倒吸了一口凉气，不禁打了个寒颤。"
)
ORIGINAL_B = (
    "她非常难过，眼中流露出深深的悲伤。风停了，"
    "她感到一阵说不出的痛苦，心中五味杂陈。"
)

# ---------- 1. 无登录 ----------
print("=" * 72)
print("[1] 无登录直接可用")
print("=" * 72)
st, body, _ = call("GET", "/api/auth/user")
check("读取当前用户无需登录", st == 200, f"status={st}")
if st == 200:
    try:
        u = json.loads(body)
        print(f"       身份: {u.get('display_name')} ({u.get('username')})")
        check("已回退为本地身份", u.get("username") == "local", f"username={u.get('username')}")
    except Exception as e:
        check("用户信息可解析", False, str(e))

st, body, _ = call("GET", "/api/projects")
check("作品列表无需登录", st == 200, f"status={st}")

# ---------- 2. 页面 ----------
print()
print("=" * 72)
print("[2] 页面与静态资源")
print("=" * 72)
st, body, _ = call("GET", "/")
check("主页 200", st == 200, f"status={st}")
if st == 200:
    import re
    m = re.search(r"<title>(.*?)</title>", body)
    title = m.group(1) if m else ""
    check("标题已更名", "文枢" in title, f"title={title}")
    check("标题不含旧名", "MuMu" not in title)

# ---------- 3. 改写 ----------
print()
print("=" * 72)
print("[3] 文本重塑（本地规则，不依赖模型）")
print("=" * 72)
st, body, _ = call("POST", "/api/polish", {
    "original_text": ORIGINAL, "mode": "novel", "use_model": False,
})
check("改写接口可用", st == 200, f"status={st}")
if st == 200:
    r = json.loads(body)
    check("原稿原样返回", r.get("original_text") == ORIGINAL)
    cand = r.get("polished_text", "")
    check("产出候选稿", bool(cand) and cand != ORIGINAL, f"len={len(cand)}")
    check("引擎标注为本地规则", r.get("engine") == "local_rules", f"engine={r.get('engine')}")
    check("返回改写前体检", bool(r.get("diagnostics_before")))
    check("返回改写后体检", bool(r.get("diagnostics_after")))
    print(f"       原稿: {ORIGINAL[:36]}...")
    print(f"       候选: {cand[:36]}...")
    db = r.get("diagnostics_before") or {}
    da = r.get("diagnostics_after") or {}
    if "experience_score" in db and "experience_score" in da:
        print(f"       体验分: {db['experience_score']} → {da['experience_score']}")

# ---------- 4. 任务隔离 ----------
print()
print("=" * 72)
print("[4] 任务隔离：两篇不同稿件不复用")
print("=" * 72)
st, b1, _ = call("POST", "/api/polish", {"original_text": ORIGINAL, "mode": "novel", "use_model": False})
st, b2, _ = call("POST", "/api/polish", {"original_text": ORIGINAL_B, "mode": "novel", "use_model": False})
if st == 200:
    c1 = json.loads(b1).get("polished_text", "")
    c2 = json.loads(b2).get("polished_text", "")
    check("两稿候选不同", c1 != c2)
    check("乙稿未混入甲稿", "雨" not in c2 or "她" in c2)
    print(f"       A: {c1[:32]}...")
    print(f"       B: {c2[:32]}...")

# ---------- 5. 体检 ----------
print()
print("=" * 72)
print("[5] 独立体检接口")
print("=" * 72)
st, body, _ = call("POST", "/api/polish/diagnose", {"text": ORIGINAL, "mode": "novel"})
check("体检接口可用", st == 200, f"status={st}")
if st == 200:
    m = json.loads(body).get("metrics", {})
    check("返回指标", bool(m), f"字段数={len(m)}")
    for k in list(m.keys())[:5]:
        print(f"       {k} = {m[k]}")

# ---------- 6. 采纳与回退 ----------
print()
print("=" * 72)
print("[6] 人工采纳与版本回退")
print("=" * 72)
st0, b0, _ = call("POST", "/api/polish/accept", {"text": "基线占位", "mode": "novel"})
base = json.loads(b0).get("versions", 0) if st0 == 200 else 0
print(f"       采纳前版本数 = {base}")

st, body, _ = call("POST", "/api/polish/accept", {"text": c1, "mode": "novel", "task_id": "e2e-A"})
check("采纳甲稿候选", st == 200, f"status={st}")
r1 = json.loads(body) if st == 200 else {}

st, body, _ = call("POST", "/api/polish/accept", {"text": c2, "mode": "novel", "task_id": "e2e-B"})
check("采纳乙稿候选", st == 200, f"status={st}")
r2 = json.loads(body) if st == 200 else {}
check("版本递增", r2.get("versions") == base + 2, f"versions={r2.get('versions')}")
check("可回退", r2.get("can_rollback") is True)

st, body, _ = call("POST", "/api/polish/rollback?mode=novel")
check("回退接口可用", st == 200, f"status={st}")
if st == 200:
    rb = json.loads(body)
    check("回退到甲稿", rb.get("text") == c1)
    check("回退内容非当前版", rb.get("text") != c2)
    print(f"       回退到: {str(rb.get('text'))[:34]}...")

# ---------- 7. 引擎状态 ----------
print()
print("=" * 72)
print("[7] 引擎状态")
print("=" * 72)
st, body, _ = call("GET", "/api/polish/engine")
check("引擎接口可用", st == 200, f"status={st}")
if st == 200:
    e = json.loads(body)
    check("引擎可用", e.get("available") is True, f"dir={e.get('dir')}")
    print(f"       目录: {e.get('dir')}")

print()
print("=" * 72)
print("RESULT:", "PASS" if not FAILURES else f"FAIL ({len(FAILURES)})")
print("=" * 72)
for f in FAILURES:
    print("  - " + f)
sys.exit(0 if not FAILURES else 1)
