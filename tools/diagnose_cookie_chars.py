# -*- coding: utf-8 -*-
"""查清 Cookie 文件里那些「不干净的段名」到底是什么字符。"""
from __future__ import annotations

import io
import os
import sys
import unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

path = r"X:\小说工程\wenshu\runtime\data\fanqie_cookie.txt"
raw = open(path, encoding="utf-8").read()

print(f"文件长度: {len(raw)} 字符")
print()
print("逐段检查段名:")
for part in raw.split(";"):
    if "=" not in part:
        continue
    name = part.split("=", 1)[0]
    clean = name.strip()
    if name != clean:
        # 找出多出来的字符
        extra = [c for c in name if c not in clean]
        print(f"  {name!r}")
        for c in extra:
            try:
                nm = unicodedata.name(c)
            except ValueError:
                nm = "(无名称)"
            print(f"      多出: U+{ord(c):04X}  {nm}  category={unicodedata.category(c)}")

print()
print("=" * 70)
print("结论")
print("=" * 70)
print("""
如果 category 是 Cf（格式字符）或 Zs（空格分隔符），
说明是复制时带进来的不可见字符。

normalize_cookie 应该覆盖它们。若仍有残留，
说明遇到的是没列进替换表的字符，需要按 category 过滤而不是按具体字符。
""")
