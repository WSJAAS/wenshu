# -*- coding: utf-8 -*-
"""验收：任务隔离与内容指纹。

对应「任务和任务直接互不干扰、多本书相互隔离」的要求。

验证点：
  1. 两个任务持有**独立**的限速器（互不等待）
  2. 模块级全局时钟已彻底移除
  3. 内容指纹：不同内容不同值、相同内容相同值
  4. 严格校验：字段缺失 / 键值不一致都明确报错，不静默填空
  5. 真实双任务并发跑，互相不串内容
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

FAILS: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)


print("=" * 74)
print("[1] 限速器归属：每个客户端独立")
print("=" * 74)
import app.main  # noqa: F401  先初始化
from app.services.fanqie_source import FanqieClient, RateLimiter  # noqa: E402
import app.services.fanqie_source as fs  # noqa: E402

c1 = FanqieClient(interval=1.0)
c2 = FanqieClient(interval=1.0)
check("两个客户端持有不同 limiter", c1.limiter is not c2.limiter)
check("limiter 是 RateLimiter 实例", isinstance(c1.limiter, RateLimiter))
check("模块级 _last_request_at 已移除", not hasattr(fs, "_last_request_at"))
check("模块级 _throttle_sync 已移除", not hasattr(fs, "_throttle_sync"))

print()
print("  实测：两个客户端各自计时，互不等待")
l1 = RateLimiter(0.5, 0.0)
l2 = RateLimiter(0.5, 0.0)

t0 = time.monotonic()
l1.wait()      # 首次不等待
l2.wait()      # 若共享时钟，这里会被 l1 拖住
first = time.monotonic() - t0
check("另一客户端的首次调用不被拖慢", first < 0.2, f"{first*1000:.0f}ms")

t1 = time.monotonic()
l1.wait()      # 应等满 0.5s
gap1 = time.monotonic() - t1
check("同客户端第二次调用被限速", gap1 >= 0.45, f"{gap1*1000:.0f}ms")

print()
print("  实测：两个客户端并发计时，总耗时应接近单倍而非叠加")
la = RateLimiter(0.6, 0.0)
lb = RateLimiter(0.6, 0.0)
la.wait()
lb.wait()

async def worker(rl, n):
    for _ in range(n):
        await asyncio.to_thread(rl.wait)


async def run_both():
    await asyncio.gather(worker(la, 3), worker(lb, 3))


t2 = time.monotonic()
asyncio.run(run_both())
span = time.monotonic() - t2

# 理论值：
#   每个 limiter 各跑 3 次，首次不等待 → 各等 2 次 × 0.6s = 1.2s
#   隔离生效 → 两者并行，总耗时 ≈ 1.2s（加上线程调度开销）
#   共享时钟 → 6 次调用线性排队，首次不等待 = 5 × 0.6 = 3.0s
#
# 判据用「明显小于共享时钟的值」而非逼近 1.2s，
# 因为 asyncio.to_thread 的线程调度本身有开销。
shared_clock_would_be = 5 * 0.6      # 3.0s
isolated_expected = 2 * 0.6          # 1.2s
threshold = (isolated_expected + shared_clock_would_be) / 2   # 2.1s

check(
    "并发不互相拖慢（明显优于共享时钟）",
    span < threshold,
    f"{span:.2f}s < {threshold:.2f}s（隔离≈{isolated_expected}s，共享≈{shared_clock_would_be}s）",
)

print()
print("=" * 74)
print("[2] 内容指纹")
print("=" * 74)
from app.api.source import _content_digest, _load_chapters, SourceTask  # noqa: E402
from app.services.fanqie_source import ChapterContent  # noqa: E402

r_a = {
    "c1": ChapterContent(item_id="c1", status="ok", text="第一章正文"),
    "c2": ChapterContent(item_id="c2", status="ok", text="第二章正文"),
}
r_b = {
    "c1": ChapterContent(item_id="c1", status="ok", text="另一本书第一章"),
    "c2": ChapterContent(item_id="c2", status="ok", text="另一本书第二章"),
}
d_a1 = _content_digest(r_a)
d_a2 = _content_digest(r_a)
d_b = _content_digest(r_b)

check("指纹是 64 位十六进制", len(d_a1) == 64 and all(c in "0123456789abcdef" for c in d_a1))
check("相同内容指纹相同", d_a1 == d_a2)
check("不同内容指纹不同", d_a1 != d_b)

# 顺序无关
r_a_reordered = {"c2": r_a["c2"], "c1": r_a["c1"]}
check("指纹与字典顺序无关", _content_digest(r_a_reordered) == d_a1)

# 单字改动也能检出
r_tweak = {
    "c1": ChapterContent(item_id="c1", status="ok", text="第一章正文。"),
    "c2": ChapterContent(item_id="c2", status="ok", text="第二章正文"),
}
check("一个标点的改动也能检出", _content_digest(r_tweak) != d_a1)

# 状态变化也能检出
r_status = {
    "c1": ChapterContent(item_id="c1", status="failed", text="第一章正文"),
    "c2": ChapterContent(item_id="c2", status="ok", text="第二章正文"),
}
check("状态变化也能检出", _content_digest(r_status) != d_a1)

print()
print("=" * 74)
print("[3] 严格校验：不静默填空")
print("=" * 74)


def mk(json_str: str) -> SourceTask:
    t = SourceTask(id="t1", user_id="u", book_id="b")
    t.chapters_json = json_str
    return t


# 正常读取
t = mk(json.dumps({"c1": {"item_id": "c1", "status": "ok", "text": "正文"}}, ensure_ascii=False))
got = _load_chapters(t)
check("正常数据可读取", len(got) == 1 and got["c1"].text == "正文")

# 缺必填字段
t = mk('{"c1": {"text": "只有正文"}}')
try:
    _load_chapters(t)
    check("缺 status 应报错", False, "却通过了")
except ValueError as e:
    check("缺 status 明确报错", "缺少必填字段" in str(e), str(e)[:60])

# 键值不一致
t = mk('{"c1": {"item_id": "c99", "status": "ok"}}')
try:
    _load_chapters(t)
    check("键值不一致应报错", False, "却通过了")
except ValueError as e:
    check("键值不一致明确报错", "不一致" in str(e), str(e)[:60])

# 非法 JSON
t = mk("{不是 JSON")
try:
    _load_chapters(t)
    check("非法 JSON 应报错", False, "却通过了")
except ValueError as e:
    check("非法 JSON 明确报错", "不是合法 JSON" in str(e), str(e)[:60])

# 类型错误
t = mk('["数组"]')
try:
    _load_chapters(t)
    check("顶层类型错误应报错", False, "却通过了")
except ValueError as e:
    check("顶层类型错误明确报错", "应为对象" in str(e), str(e)[:60])

# 空值容错（空任务应正常返回空）
t = mk("{}")
check("空任务返回空字典", _load_chapters(t) == {})

print()
print("=" * 74)
print("[4] 真实双任务并发：内容不串")
print("=" * 74)
BASE = "http://127.0.0.1:8010"


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
        return None, str(e)[:120]


st, body = call("GET", "/api/source/status")
if st != 200 or not json.loads(body).get("enabled"):
    print("  书源未启用，跳过并发实测")
else:
    # 取两本不同的书
    st, body = call("GET", "/api/source/rank?enrich=false", timeout=120)
    items = json.loads(body).get("items", []) if st == 200 else []
    ids = [it["book_id"] for it in items[:2] if it.get("book_id")]

    if len(ids) < 2:
        print("  榜单书数不足，跳过")
    else:
        # 同时建两个任务
        tasks = []
        for bid in ids:
            st, body = call("POST", "/api/source/tasks", {
                "book_id": bid,
                "selection": {"preset": "first", "count": 4, "volume": "", "item_ids": []},
                "interval": 1.0,
            }, timeout=300)
            if st == 200:
                tasks.append((bid, json.loads(body)["task_id"]))
            time.sleep(0.5)

        check("两个任务都创建成功", len(tasks) == 2, f"{len(tasks)} 个")

        if len(tasks) == 2:
            print("  两任务并发跑，等待完成…")
            finals = {}
            t0 = time.monotonic()
            while time.monotonic() - t0 < 300:
                time.sleep(4)
                done = 0
                for bid, tid in tasks:
                    st, body = call("GET", f"/api/source/tasks/{tid}")
                    if st == 200:
                        t = json.loads(body)
                        finals[bid] = t
                        if t["status"] in ("completed", "failed", "paused", "cancelled"):
                            done += 1
                if done == 2:
                    break

            for bid, t in finals.items():
                print(f"    {t['book_title'][:22]:<24} {t['status']:<10} "
                      f"{t['ok_count']}/{t['total_chapters']}  "
                      f"digest={t.get('chapters_digest','')[:12]}…")

            ok = [t for t in finals.values() if t["status"] == "completed"]
            check("两个任务都完成", len(ok) == 2, f"{len(ok)}/2")

            digests = [t.get("chapters_digest", "") for t in finals.values()]
            check("两任务指纹非空", all(digests), f"{digests}")
            check("两任务指纹不同（内容确实隔离）", digests[0] != digests[1])

            # 交叉检查：各自分章接口返回的正文应属于自己那本书
            cross_clean = True
            detail = []
            for bid, t in finals.items():
                tid = t["task_id"]
                st, body = call("GET", f"/api/source/tasks/{tid}/chapters")
                if st != 200:
                    continue
                d = json.loads(body)
                titles = [c["title"] for c in d.get("chapters", [])]
                detail.append(f"{t['book_title'][:14]}: {len(titles)} 章")
                # 指纹应重算一致
                if d.get("task", {}).get("chapters_digest") != t.get("chapters_digest"):
                    cross_clean = False
            check("分章接口返回的指纹与任务一致", cross_clean, "；".join(detail))

print()
print("=" * 74)
print("RESULT:", "PASS" if not FAILS else f"FAIL ({len(FAILS)})")
print("=" * 74)
for f in FAILS:
    print("  - " + f)
sys.exit(0 if not FAILS else 1)
