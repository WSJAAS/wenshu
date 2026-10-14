# -*- coding: utf-8 -*-
"""查清 _extract_state 为何在带 Cookie 的页面上失败。

页面 HTML 正常（title/description 都在），但返回 None。
对比带/不带 Cookie 时 __INITIAL_STATE__ 段的差异。
"""
from __future__ import annotations

import io
import json
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
os.environ["MUMU_DATA_DIR"] = r"X:\小说工程\wenshu\runtime\data"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.services.fanqie_source import FanqieClient, load_login_cookie  # noqa: E402

ITEM = "7256994009498223144"
cookie = load_login_cookie()


def probe(label, ck):
    print("=" * 74)
    print(label)
    print("=" * 74)
    client = FanqieClient(interval=1.2, cookie=ck)
    html = client._get(f"https://fanqienovel.com/reader/{ITEM}")
    print(f"  HTML 长度: {len(html):,}")

    key = "window.__INITIAL_STATE__"
    start = html.find(key)
    print(f"  {key} 位置: {start}")
    if start < 0:
        print("  ✗ 找不到 state 标记")
        return

    brace = html.find("{", start)
    print(f"  首个 {{ 位置: {brace}")
    blob = html[brace:]
    print(f"  blob 长度: {len(blob):,}")

    # 手动做一次：从右往左找
    end_found = None
    tried = 0
    for end in range(min(len(blob), 3_000_000), 0, -1):
        if blob[end - 1] != "}":
            continue
        tried += 1
        if tried > 5000:
            print(f"  ✗ 试了 {tried} 次仍未解析成功（触顶）")
            break
        try:
            d = json.loads(blob[:end])
            end_found = end
            print(f"  ✓ 解析成功，结束位置 {end}，试了 {tried} 次")
            print(f"    顶层键: {list(d.keys())[:10]}")
            break
        except json.JSONDecodeError:
            continue
    else:
        print(f"  ✗ 全部回溯完仍未成功（共试 {tried} 次）")

    if end_found is None:
        # 找最后一个 } 的位置看差多远
        last = blob.rfind("}")
        print(f"  最后一个 }} 在 {last}（blob 长 {len(blob)}）")
        # 从最大 } 开始试一次
        try:
            d = json.loads(blob[:last + 1])
            print(f"  ✓ 用最后一个 }} 直接解析成功")
        except json.JSONDecodeError as e:
            print(f"  ✗ 最后一个 }} 也失败: {str(e)[:100]}")
            # 看 error 位置附近
            pos = getattr(e, "pos", None)
            if pos:
                lo = max(0, pos - 80)
                print(f"    错误位置 {pos} 附近: {blob[lo:pos+80]!r}")
        # 统计 } 数量
        print(f"  blob 里 '}}' 数量: {blob.count('}')}")
    print()


probe("不带 Cookie", "")
probe("带 Cookie", cookie)
