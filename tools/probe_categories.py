# -*- coding: utf-8 -*-
"""核实完整分类体系 + 分类榜单的可取性。

目标：
  1. 取全部 male/female 分类（name + id）
  2. 每个分类 × type 维度，能取到多少本
  3. 汇总去重后总量 —— 判断「完整榜单」的现实规模
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
        "Referer": f"{BASE}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def rank_of(html):
    i = html.find("window.__INITIAL_STATE__")
    if i < 0:
        return None
    b = html.find("{", i)
    blob = html[b:]
    for end in range(len(blob), 0, -1):
        if blob[end - 1] != "}":
            continue
        try:
            d = json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
        r = d.get("rank")
        return r if isinstance(r, dict) else None
    return None


print("=" * 78)
print("[1] 完整分类体系")
print("=" * 78)
st, html = get(f"{BASE}/rank")
r = rank_of(html)
cats_by_gender = r.get("rankCategoryTypeList") or {}

total_cats = 0
catalog = {}
for gender, groups in cats_by_gender.items():
    if not isinstance(groups, list):
        continue
    print(f"\n  ── {gender} （{len(groups)} 类）──")
    catalog[gender] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if not cid or not name:
            continue
        catalog[gender].append({"id": cid, "name": name})
        total_cats += 1
        print(f"     {cid:<6} {name}")

print(f"\n  分类总数: {total_cats}")

print()
print("=" * 78)
print("[2] 抽样验证：分类 × type 维度")
print("=" * 78)
# gender 编码：0=女频 1=男频（从 URL 形态推断，实测确认）
g_map = {"female": 0, "male": 1}

all_ids: dict[str, dict] = {}
probes = []
for gender, items in catalog.items():
    g = g_map.get(gender, 0)
    for item in items[:3]:   # 抽样前 3 个分类
        for t in (1, 2):
            probes.append((gender, g, item, t))

print(f"  抽样 {len(probes)} 个组合")
for gender, g, item, t in probes:
    u = f"{BASE}/rank/{g}_{t}_{item['id']}"
    st, body = get(u)
    rr = rank_of(body)
    lst = (rr or {}).get("book_list") or []
    ids = []
    for b in lst:
        if isinstance(b, dict):
            bid = str(b.get("bookId"))
            ids.append(bid)
            if bid not in all_ids:
                all_ids[bid] = {
                    "book_id": bid,
                    "title_raw": str(b.get("bookName") or ""),
                    "author": str(b.get("author") or ""),
                    "category": str(b.get("category") or ""),
                    "word_count": b.get("wordNumber"),
                    "read_count": b.get("readCount"),
                    "cover_url": str(b.get("thumbUri") or ""),
                    "sources": [],
                }
            all_ids[bid]["sources"].append({
                "gender": gender, "type": t,
                "category_id": item["id"], "category_name": item["name"],
            })
    tn = (rr or {}).get("total_num")
    print(f"  {gender:<7} t={t} {item['name']:<10} -> {len(lst):>2} 本  total={tn}  "
          f"累计去重 {len(all_ids)}")
    time.sleep(1.1)

print()
print("=" * 78)
print("[3] 结论")
print("=" * 78)
print(f"  分类总数        : {total_cats}")
print(f"  若全抓（37类×2维）: 约 {total_cats * 2} 次请求")
print(f"  单组合返回      : 10 本")
print(f"  理论上限        : {total_cats * 2 * 10} 个位置")
print(f"  抽样去重后      : {len(all_ids)} 本（来自 {len(probes)} 个组合）")

if len(all_ids) > 0:
    ratio = len(all_ids) / (len(probes) * 10)
    print(f"  去重率          : {1 - ratio:.0%}（跨组合重复比例）")
    print(f"  全抓预估去重总量: 约 {int(total_cats * 2 * 10 * ratio)} 本")

# 存一份分类表供实现使用
from pathlib import Path
out = Path(r"X:\小说工程\wenshu\runtime\rank_categories.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n  分类表已存: {out}")
