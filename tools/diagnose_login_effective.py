# -*- coding: utf-8 -*-
"""核对：当前 Cookie 是否真的能取到全文。

上一轮实测第 11 章仍是 preview。要分清两种可能：
  A. Cookie 不含 sessionid → 服务端本来就当未登录 → 取不到是正常的
  B. Cookie 含 sessionid 但被识别失败 → 我们的问题

顺便确认页面对该账号的实际授权状态。
"""
from __future__ import annotations

import io
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
os.environ["MUMU_DATA_DIR"] = r"X:\小说工程\wenshu\runtime\data"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import (  # noqa: E402
    FanqieClient, _extract_state, cookie_status, load_login_cookie,
)

ck = load_login_cookie()
print("=" * 74)
print("[1] 当前 Cookie 包含哪些字段")
print("=" * 74)
names = [p.split("=", 1)[0].strip() for p in ck.split(";") if "=" in p]
for n in names:
    mark = ""
    if n in ("sessionid", "sessionid_ss"):
        mark = "  ★ 这是登录态关键项"
    elif "passport" in n:
        mark = "  （passport 相关）"
    print(f"    {n}{mark}")

st = cookie_status()
print()
print(f"  configured   = {st['configured']}")
print(f"  has_sessionid= {st['has_sessionid']}   ← 决定能否取全文")

print()
print("=" * 74)
print("[2] 服务端认定的登录状态")
print("=" * 74)
client = FanqieClient(interval=1.2, cookie=ck)
html = client._get("https://fanqienovel.com/reader/7256994009498223144")
state = _extract_state(html)
if state:
    common = state.get("common") or {}
    for k in ("hasAuthentication", "hasSerialPermission", "userId", "loginStatus"):
        if k in common:
            print(f"    common.{k} = {common[k]!r}")
    reader = state.get("reader") or {}
    cd = reader.get("chapterData") or {}
    print(f"    第 1 章 isChapterLock = {cd.get('isChapterLock')!r}")

print()
print("=" * 74)
print("[3] 第 11 章的响应特征")
print("=" * 74)
book, chapters = client.fetch_book("7231424472606051384")
ref = [c for c in chapters if c.order == 11][0]
html11 = client._get(f"https://fanqienovel.com/reader/{ref.item_id}")
s11 = _extract_state(html11)
if s11:
    r11 = s11.get("reader") or {}
    cd11 = r11.get("chapterData") or {}
    print(f"    isChapterLock   = {cd11.get('isChapterLock')!r}")
    print(f"    chapterWordNumber = {cd11.get('chapterWordNumber')!r}")
    print(f"    needPay         = {cd11.get('needPay')!r}")
    content = cd11.get("content") or ""
    print(f"    content 长度    = {len(content)}")
    print(f"    content 结尾    = {content[-90:]!r}")
    # 页面是否有登录提示
    for kw in ("会员登录", "网页畅读", "SVIP", "登录后"):
        if kw in html11:
            print(f"    页面含 {kw!r} → 服务端仍按未登录处理")

print()
print("=" * 74)
print("[4] 判定")
print("=" * 74)
if not st["has_sessionid"]:
    print("""
  Cookie 不含 sessionid。

  番茄的登录态由 sessionid 承载。从开发者工具复制的这串里只有
  风控与埋点字段（x-web-secsdk-uid / s_v_web_id / novel_web_id 等），
  没有登录凭据 —— 可能是：
    - 只复制了部分 Cookie（sessionid 是 HttpOnly，某些面板需要手动展开）
    - 或者浏览器里当前并未登录番茄

  服务端因此按未登录处理，第 11 章起仍返回试读片段。
  这是预期行为，不是缺陷。
""")
else:
    print("  Cookie 含 sessionid 但仍取不到全文，需要进一步排查。")
