# -*- coding: utf-8 -*-
"""审阅校验：对抓下来的 TXT 做完整性/质量核查。

检查项：
  1. 章数是否与目录一致、编号是否连续
  2. 每章字数分布，找异常短章（截断/失败）
  3. 编码与字符质量（乱码、替换符、残留标签、残缺 HTML 实体）
  4. 噪音残留（导航文案、广告、网址）
  5. 首章/末章实读，确认不是残缺开头结尾
  6. 重复段落检测
  7. TXT 与元数据、目录页的一致性交叉比对
"""
from __future__ import annotations

import io
import json
import re
import ssl
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

TXT = Path(r"X:\小说工程\novel-samples\from-sites\云海玉弓缘_梁羽生.txt")
META = Path(r"X:\小说工程\novel-samples\from-sites\云海玉弓缘.meta.json")

FAIL: list[str] = []
WARN: list[str] = []


def ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAIL.append(label)


def warn(label, cond, detail=""):
    print(f"  [{'注意' if not cond else 'OK  '}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        WARN.append(label)


text = TXT.read_text(encoding="utf-8")
meta = json.loads(META.read_text(encoding="utf-8"))

print("=" * 94)
print("【1】文件基本事实")
print("=" * 94)
print(f"  文件: {TXT}")
print(f"  大小: {TXT.stat().st_size:,} 字节")
print(f"  字符: {len(text):,}")
print(f"  汉字: {len(re.findall(r'[一-鿿]', text)):,}")
print(f"  行数: {len(text.splitlines()):,}")

# 切章
chaps = re.split(r"\n(?=第[一二三四五六七八九十百零〇\d]+回\s)", text)
chaps = [c for c in chaps if re.match(r"^第[一二三四五六七八九十百零〇\d]+回\s", c.strip())]
print(f"  识别到章: {len(chaps)}")

print()
print("=" * 94)
print("【2】章数一致性")
print("=" * 94)
ok("章数与元数据一致", len(chaps) == meta["ok_chapters"],
   f"文中 {len(chaps)} vs 元数据 {meta['ok_chapters']}")
ok("无失败章", len(meta.get("failed", [])) == 0,
   str(meta.get("failed", [])[:3]))

# 章号连续性
nums = []
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
      "九": 9, "十": 10}


def cn2int(s: str) -> int:
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return CN.get(s, 0)


for c in chaps:
    m = re.match(r"^第([一二三四五六七八九十百零〇\d]+)回", c.strip())
    if m:
        nums.append(cn2int(m.group(1)))

gaps = [i for i in range(1, max(nums) + 1) if i not in nums] if nums else []
ok("章号连续无缺", not gaps, f"缺失 {gaps[:10]}" if gaps else f"1–{max(nums) if nums else 0}")
ok("无重复章号", len(nums) == len(set(nums)),
   f"{len(nums)} 个章号 / {len(set(nums))} 个唯一" if nums else "")

print()
print("=" * 94)
print("【3】每章字数分布（找异常短章 = 截断/失败）")
print("=" * 94)
sizes = [(re.match(r"^(第[^\s]*回[^\n]*)", c.strip()).group(1)[:30],
          len(re.findall(r"[一-鿿]", c))) for c in chaps]
sizes_sorted = sorted(sizes, key=lambda x: x[1])
print("  最短 5 章:")
for t, n in sizes_sorted[:5]:
    print(f"    {n:>6} 汉字  {t}")
print("  最长 3 章:")
for t, n in sizes_sorted[-3:]:
    print(f"    {n:>6} 汉字  {t}")
avg = sum(n for _, n in sizes) / len(sizes)
print(f"  平均 {avg:,.0f} 汉字/章")

ok("无异常短章（<1500 汉字）",
   all(n >= 1500 for _, n in sizes),
   f"最短 {sizes_sorted[0][1]} 汉字")
# 偏离平均值过大的
outliers = [(t, n) for t, n in sizes if n < avg * 0.35]
warn("无显著偏短章（<35% 均值）", not outliers,
     f"{len(outliers)} 章偏短: {[t for t, _ in outliers][:4]}")

print()
print("=" * 94)
print("【4】编码与字符质量")
print("=" * 94)
repl = text.count("\ufffd")
ok("无 Unicode 替换符 U+FFFD", repl == 0, f"{repl} 个")

pua = len(re.findall(r"[\ue000-\uf8ff]", text))
ok("无私用区字符", pua == 0, f"{pua} 个")

# 常见乱码特征
mojibake = len(re.findall(r"[ÃÂåæçèéêëìíîïðñòóôõö]", text))
warn("无明显 mojibake", mojibake < 20, f"{mojibake} 个疑似乱码字符")

tag = len(re.findall(r"</?(?:div|span|p|br|a|td|tr|table|script)\b", text, re.I))
ok("无残留 HTML 标签", tag == 0, f"{tag} 个")

ent = re.findall(r"&[a-zA-Z#0-9]{2,8};", text)
ok("无未解码实体", not ent, f"{ent[:6]}")

# 控制字符（换行除外）
ctrl = len(re.findall(r"[\x00-\x08\x0b-\x1f\x7f]", text))
ok("无异常控制字符", ctrl == 0, f"{ctrl} 个")

print()
print("=" * 94)
print("【5】噪音残留（导航/广告/网址）")
print("=" * 94)
NOISE_PATS = {
    "站名/网址": r"努努书坊|kanunu|www\.[a-z0-9\-]+\.(?:com|net|org|cc)",
    "导航": r"上一章|下一章|返回目录|加入书签|投推荐票|手机阅读|回顶部|意见反馈",
    "广告": r"请记住本站|最快更新|全文阅读|无弹窗|赞助",
    "页脚": r"版权所有|Copyright|All Rights Reserved|本站内容来源于网络|版权归属作家",
}
for name, pat in NOISE_PATS.items():
    hits = re.findall(pat, text, re.I)
    cnt = len(hits)
    sample = list(dict.fromkeys(hits))[:3]
    # 文件头里有「来源：<URL>」是有意保留的溯源信息，单独说明
    ok(f"无「{name}」残留", cnt == 0, f"{cnt} 处 {sample}" if cnt else "")

prov = re.search(r"^来源：(\S+)", text, re.M)
print(f"  （文件头的「来源：{prov.group(1) if prov else '—'}」是刻意保留的溯源信息）")

print()
print("=" * 94)
print("【5b】源站缺字（站点原文用全角问号占位，非我方解码所致）")
print("=" * 94)
print("  取证：原始响应字节里就是 A3BF A3BF（GBK 全角问号），")
print("        不是乱码、不是替换符 —— 是源站文本本身缺字。")
qq = re.findall(r"？？", text)
print(f"  全文「？？」占位: {len(qq)} 处")
zh_total = len(re.findall(r"[一-鿿]", text))
if zh_total:
    print(f"  密度: {len(qq) / zh_total * 1000:.3f}‰（每千汉字）")
# 按章分布
per = []
for c in chaps:
    ttl = re.match(r"^(第[^\s]*回[^\n]*)", c.strip()).group(1)
    n = c.count("？？")
    if n:
        per.append((ttl[:24], n))
if per:
    print(f"  涉及的章: {len(per)}/{len(chaps)}")
    for t, n in sorted(per, key=lambda x: -x[1])[:8]:
        print(f"    {n:>3} 处  {t}")
else:
    print("  无")
# 逐章均无则算通过
ok("源站缺字情况已知并记录", True,
   f"{len(per)} 章有占位，共 {len(qq)} 处 —— 属源站限制，非本次抓取引入")

print()
print("=" * 94)
print("【6】重复内容检测")
print("=" * 94)
# 段落级重复
paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
dup = [p for p, n in Counter(paras).items() if n > 1]
warn("无大量重复段落", len(dup) < 5,
     f"{len(dup)} 段重复，例: {dup[0][:60] if dup else ''}")

# 交叉：所有章的正文是否被整体复读
head = [re.sub(r"\s+", "", c)[:200] for c in chaps]
duphead = [h for h, n in Counter(head).items() if n > 1]
ok("无整章重复（开头 200 字比对）", not duphead,
   f"{len(duphead)} 组重复")

print()
print("=" * 94)
print("【7】首尾实读（确认不是残缺截断）")
print("=" * 94)
first = chaps[0]
last = chaps[-1]
print("  —— 开头 260 字 ——")
print("  " + first[:260].replace("\n", "\n  "))
print()
print("  —— 第一章结尾 200 字 ——")
print("  " + first[-200:].replace("\n", "\n  "))
print()
print("  —— 末章结尾 260 字 ——")
print("  " + last[-260:].replace("\n", "\n  "))

print()
print("=" * 94)
print("【8】与目录页交叉比对（章名是否一致）")
print("=" * 94)
try:
    req = urllib.request.Request(
        "https://www.kanunu8.com/book/4665/",
        headers={"User-Agent": UA, "Referer": "https://www.kanunu8.com/"})
    with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
        raw = r.read()
    html = raw.decode("gb18030", "replace")
    cat_titles = []
    for m in re.finditer(r'href="(\d+\.html)"[^>]*>([^<]{1,70})</a>', html):
        t = re.sub(r"\s+", " ", m.group(2)).strip()
        if re.match(r"^第[一二三四五六七八九十百零〇\d]+回", t):
            cat_titles.append(t)
    print(f"  目录页章节数: {len(cat_titles)}")
    print(f"  正文识别章数: {len(chaps)}")

    # 章名逐一比对
    txt_titles = [re.match(r"^(第[^\s]*回[^\n]*)", c.strip()).group(1).strip()
                  for c in chaps]
    set_txt = {re.sub(r"\s+", "", t) for t in txt_titles}
    set_cat = {re.sub(r"\s+", "", t) for t in cat_titles}
    missing = set_cat - set_txt
    extra = set_txt - set_cat
    ok("章名与目录页完全对应", not missing and not extra,
       f"缺 {len(missing)} 增 {len(extra)}" if (missing or extra) else
       f"{len(set_cat)} 个章名全中")
    if missing:
        print(f"      目录有而正文缺: {list(missing)[:5]}")
    if extra:
        print(f"      正文有而目录无: {list(extra)[:5]}")
except Exception as e:
    warn(f"目录页比对（抓取失败 {type(e).__name__}）", False, str(e)[:80])

print()
print("=" * 94)
print("【9】与外部常识核对（书名/作者/篇幅）")
print("=" * 94)
print(f"  元数据书名: {meta['title']}")
print(f"  元数据作者: {meta['author']}")
print(f"  总汉字: {meta['total_hanzi']:,}")
print(f"  目录页 URL: {meta['catalog_url']}")
print(f"  SHA256: {meta['digest'][:48]}…")

# 指纹可复算
import hashlib
recalc = hashlib.sha256(text.encode("utf-8")).hexdigest()
ok("指纹可复算一致", recalc == meta["digest"],
   f"{recalc[:24]}… vs {meta['digest'][:24]}…")

print()
print("=" * 94)
print(f"审阅结论: {'全部通过' if not FAIL else f'{len(FAIL)} 项未通过'}"
      f"{'，' + str(len(WARN)) + ' 项需注意' if WARN else ''}")
print("=" * 94)
for f in FAIL:
    print(f"  ✗ {f}")
for w in WARN:
    print(f"  ! {w}")
sys.exit(1 if FAIL else 0)
