# -*- coding: utf-8 -*-
"""确认番茄榜单的分页与分类机制。

已发现：
  - rank.total_num = 100（平台声明总数）
  - rank.defaultPage = 1
  - /rank/1 返回不同内容 → 路径式分页
  - rank.rankCategoryTypeList = {male, female} → 分类体系

目标：找到「取全 100 本」的翻页规律，以及分类榜单的 URL 形态。
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BASE = "https://fanqienovel.com"


def get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def state(html: str):
    i = html.find("window.__INITIAL_STATE__")
    if i < 0:
        return None
    b = html.find("{", i)
    blob = html[b:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            return json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
    return None


def rank_of(html):
    d = state(html)
    if not d:
        return None
    r = d.get("rank")
    return r if isinstance(r, dict) else None


print("=" * 76)
print("[1] rankCategoryTypeList 结构（分类体系）")
print("=" * 76)
st, html = get(f"{BASE}/rank")
r = rank_of(html)
rc = r.get("rankCategoryTypeList") or {}
for gender, groups in rc.items():
    print(f"\n  ── {gender} ──")
    if isinstance(groups, dict):
        print(f"     键: {', '.join(list(groups.keys())[:12])}")
        for k, v in list(groups.items())[:6]:
            if isinstance(v, list):
                print(f"     {k}: list({len(v)})")
                for item in v[:4]:
                    if isinstance(item, dict):
                        print(f"       {json.dumps(item, ensure_ascii=False)[:140]}")
            else:
                print(f"     {k}: {str(v)[:100]}")
    elif isinstance(groups, list):
        print(f"     list({len(groups)})")
        for item in groups[:6]:
            print(f"       {json.dumps(item, ensure_ascii=False)[:150]}")

print()
print("=" * 76)
print("[2] 分页规律：/rank/N 连取")
print("=" * 76)
seen_ids = {}
for page in range(1, 9):
    st, body = get(f"{BASE}/rank/{page}")
    if st != 200 or len(body) < 5000:
        print(f"  /rank/{page} -> {st}  {len(body)} 字节  （无内容）")
        continue
    rr = rank_of(body)
    if not rr:
        print(f"  /rank/{page} -> 解析失败")
        continue
    lst = rr.get("book_list") or []
    ids = [str(b.get("bookId")) for b in lst if isinstance(b, dict)]
    fresh = [i for i in ids if i not in seen_ids]
    for i in ids:
        seen_ids.setdefault(i, page)
    first = ""
    if lst and isinstance(lst[0], dict):
        bn = str(lst[0].get("bookName", ""))
        first = "".join("□" if 0xE000 <= ord(c) <= 0xF8FF else c for c in bn)[:16]
    print(f"  /rank/{page} -> 200  {len(lst):>2} 本  "
          f"新 {len(fresh):>2}  首本={first}")
    time.sleep(1.2)

print(f"\n  累计去重: {len(seen_ids)} 本（平台声明 {r.get('total_num')} 本）")

print()
print("=" * 76)
print("[3] 分类榜单 URL 探测")
print("=" * 76)
# 从分类体系里取 category_id 试探
cat_ids = []
for gender, groups in rc.items():
    if isinstance(groups, dict):
        for k, v in groups.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        cid = item.get("category_id") or item.get("categoryId") or item.get("id")
                        if cid is not None:
                            cat_ids.append((gender, k, cid, item.get("name") or item.get("title")))
print(f"  从分类体系取到 category_id: {len(cat_ids)} 个")
for c in cat_ids[:8]:
    print(f"    gender={c[0]} group={c[1]} id={c[2]} name={c[3]}")

# 试 URL 形态
print()
if cat_ids:
    g, k, cid, nm = cat_ids[0]
    cands = [
        f"/rank?category_id={cid}",
        f"/rank/{g}/{cid}",
        f"/rank/{g}?category_id={cid}",
        f"/rank?gender={0 if g=='female' else 1}&category_id={cid}",
        f"/rank/{cid}",
    ]
    for u in cands:
        st, body = get(f"{BASE}{u}")
        info = f"{st}  {len(body)} 字节"
        if st == 200 and len(body) > 5000:
            rr = rank_of(body)
            if rr and isinstance(rr.get("book_list"), list):
                info += f"  book_list={len(rr['book_list'])}  total_num={rr.get('total_num')}"
                rc2 = rr.get("rankCategoryTypeList")
                info += f"  categoryList={'有' if rc2 else '无'}"
        print(f"  {u:<52} -> {info}")
        time.sleep(1.0)

print()
print("=" * 76)
print("[4] 榜单页面上的分类 tab（从 HTML 里找链接）")
print("=" * 76)
import re
links = set(re.findall(r'href="(/rank[^"]*)"', html))
if links:
    for l in sorted(links)[:20]:
        print(f"  {l}")
else:
    print("  HTML 里没有 /rank 开头的 href（可能是前端路由）")

# 找 category 名与 id 的对应
print()
pairs = re.findall(r'\{"category_id":(\d+)[^}]{0,200}?"name":"([^"]{1,20})"', html)
if not pairs:
    pairs = re.findall(r'"category_id":(\d+)[^{]{0,120}"name":"([^"]{1,20})"', html)
print(f"  找到 category_id↔name 对: {len(pairs)} 组")
for cid, name in pairs[:15]:
    print(f"    {cid} = {name}")
