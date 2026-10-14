# -*- coding: utf-8 -*-
"""探测番茄的登录凭据机制。

目标：搞清要用登录态抓全文，需要携带什么。
  1. 站点会下发哪些 Cookie
  2. 登录态的 Cookie 叫什么名字
  3. 是否有基于 header 的替代方式

不涉及任何凭据窃取：只看公开下发的 Cookie 名与 JS 里的引用。
"""
from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def head(url: str, timeout: int = 25):
    """取响应头，看 Set-Cookie。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,*/*;q=0.8",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""
    except Exception as e:
        return None, {}, str(e)[:150]


print("=" * 76)
print("[1] 站点下发的 Cookie")
print("=" * 76)
for path in ("/", "/rank", "/reader/7256994009498223144"):
    st, hdr, body = head(f"{BASE}{path}")
    print(f"\n  {path}  → {st}  {len(body)} 字节")
    # 收集所有 Set-Cookie
    raw = []
    for k, v in hdr.items():
        if k.lower() == "set-cookie":
            raw.append(v)
    if raw:
        for c in raw:
            name = c.split("=")[0].strip()
            attrs = []
            for part in c.split(";"):
                p = part.strip().lower()
                if p.startswith(("domain=", "path=", "secure", "httponly", "samesite=")):
                    attrs.append(part.strip())
            print(f"    Set-Cookie: {name}   [{', '.join(attrs)}]")
    else:
        print("    （无 Set-Cookie）")

print()
print("=" * 76)
print("[2] JS 里引用的 Cookie 名（找登录相关的）")
print("=" * 76)

# 下载主 bundle 搜索
BUNDLES = [
    "https://lf-fe.fqnovelstatic.com/obj/novel-fanqie-fe/toutiao/muye/js/muye_5f7ec5dd.js",
]

CANDIDATES = (
    "sessionid", "session_id", "sid_tt", "sid_guard", "uid_tt", "passport",
    "odin_tt", "ttwid", "msToken", "s_v_web_id", "token", "authorization",
)

found_names: set[str] = set()
for url in BUNDLES:
    st, _, js = head(url)
    if not js:
        print(f"  下载失败: {url}")
        continue
    print(f"\n  扫描 {url.split('/')[-1]}  ({len(js):,} 字符)")
    for cand in CANDIDATES:
        hits = len(re.findall(re.escape(cand), js, re.I))
        if hits:
            found_names.add(cand)
            print(f"    含 {cand!r}: {hits} 次")

    # 找 Cookie 读写
    for m in re.finditer(r'document\.cookie\s*=\s*["\'`]([^"\'`=]{3,40})=', js):
        found_names.add(m.group(1))
    cookies_assigned = set(re.findall(r'document\.cookie\s*=\s*["\'`]([^"\'`=]{3,40})=', js))
    if cookies_assigned:
        print(f"    document.cookie 写入的名字: {sorted(cookies_assigned)[:10]}")

    # 找 getCookie('xxx') 调用
    getc = set(re.findall(r'getCookie\(["\'`]([^"\'`]{3,40})["\'`]\)', js))
    if getc:
        print(f"    getCookie() 读取的名字: {sorted(getc)[:12]}")

print()
print("=" * 76)
print("[3] 关键 Cookie 名（综合）")
print("=" * 76)
for n in sorted(found_names):
    print(f"  {n}")

print()
print("=" * 76)
print("[4] 判定：登录态的可能载体")
print("=" * 76)
LOGIN_COOKIES = [n for n in found_names
                 if any(k in n.lower() for k in ("session", "sid", "passport", "token", "uid", "ttwid"))]
if LOGIN_COOKIES:
    print("  可能是登录凭据的 Cookie：")
    for n in sorted(LOGIN_COOKIES):
        print(f"    {n}")
    print()
    print("  说明：番茄属于字节系，登录态通常由 sessionid 系列承载，")
    print("        另有 ttwid / msToken 等风控参数。")
    print("        具体哪几个必需，只有带真实登录态实测才能确定。")
else:
    print("  未在 JS 里找到明显的登录 Cookie 名。")

print()
print("=" * 76)
print("[5] 建议：让用户提供什么")
print("=" * 76)
print("""
  最稳妥的方式是让用户提供**浏览器里该站点的完整 Cookie 串**，
  而不是只挑某一个字段。原因：

    - 登录态可能由多个 Cookie 共同构成
    - 风控参数（ttwid / msToken / s_v_web_id）往往也必需
    - 平台改版时字段会变，整体提供比逐个挑选更耐用

  获取方式（用户在浏览器里操作）：
    1. 登录 fanqienovel.com
    2. F12 → Application/存储 → Cookies → https://fanqienovel.com
    3. 全选复制；或用控制台执行 document.cookie

  注意：document.cookie 拿不到 HttpOnly 的项，
  因此更可靠的是开发者工具里的 Cookies 面板。
""")
