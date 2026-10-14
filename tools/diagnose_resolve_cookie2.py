# -*- coding: utf-8 -*-
"""在服务相同的环境下复现反查失败。

上一版诊断脚本没设 MUMU_DATA_DIR，所以 load_login_cookie() 返回空，
两种测试都没带 Cookie，看不出问题。

这一版显式设置环境变量，复现服务里的真实行为。
"""
from __future__ import annotations

import io
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
DATA = r"X:\小说工程\wenshu\runtime\data"
os.environ["MUMU_DATA_DIR"] = DATA
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import (  # noqa: E402
    FanqieClient, _extract_state, load_login_cookie,
)

ITEM = "7256994009498223144"

cookie = load_login_cookie()
print(f"load_login_cookie() → {len(cookie)} 字节")

# 检查不可见字符
print()
print("Cookie 里的不可见字符检查:")
SUSPECT = {
    "\u200b": "零宽空格 U+200B",
    "\u200c": "零宽非连接符 U+200C",
    "\u200d": "零宽连接符 U+200D",
    "\ufeff": "BOM U+FEFF",
    "\u00a0": "不换行空格 U+00A0",
    "\u3000": "全角空格 U+3000",
}
found_any = False
for ch, name in SUSPECT.items():
    n = cookie.count(ch)
    if n:
        found_any = True
        print(f"  ⚠ 含 {name} × {n}")
        # 显示上下文
        idx = cookie.find(ch)
        ctx = cookie[max(0, idx - 30): idx + 30]
        print(f"     上下文: {ctx!r}")
if not found_any:
    print("  ✓ 无不可见字符")

print()
print("解析出的 Cookie 名:")
names = []
for part in cookie.split(";"):
    if "=" in part:
        n = part.split("=", 1)[0]
        names.append(n)
        if n != n.strip() or any(ord(c) > 0x7F for c in n):
            print(f"  ⚠ 可疑名: {n!r}")
for n in names[:12]:
    print(f"    {n!r}")
print()
print("=" * 72)
print("带真实 Cookie 反查")
print("=" * 72)
client = FanqieClient(interval=1.2, cookie=cookie)
bid = client.resolve_book_by_item(ITEM)
print(f"  resolve 结果: {bid!r}")
if bid:
    print("  ✓ 成功")
else:
    print("  ✗ 失败 —— 复现了服务端的问题")
    # 看页面到底返回了什么
    try:
        html = client._get(f"https://fanqienovel.com/reader/{ITEM}")
        print(f"  HTML: {len(html):,} 字节")
        state = _extract_state(html)
        print(f"  state: {'有' if state else '无'}")
        if state:
            r = state.get("reader")
            print(f"  reader: {type(r).__name__}")
            if isinstance(r, dict):
                print(f"    reader 键: {list(r.keys())}")
                cd = r.get("chapterData")
                print(f"    chapterData: {type(cd).__name__} = {str(cd)[:100]}")
            # common 段常带登录/风控信息
            common = state.get("common") or {}
            if isinstance(common, dict):
                for k in ("hasAuthentication", "hasSerialPermission", "userId", "isLogin"):
                    if k in common:
                        print(f"    common.{k} = {common[k]!r}")
        else:
            print(f"  页面开头: {html[:300]!r}")
    except Exception as e:
        print(f"  取页异常: {type(e).__name__}: {e}")
