# -*- coding: utf-8 -*-
"""核查 AI 配置的实际状态。

日志显示书源 → 拆书的衔接流程里，AI 调用全部 401：
  Incorrect API key provided: sk-SET_ME

关键问题不是「没配 key」，而是「配了占位值却当作已配置」——
这会让人以为 AI 是通的，实际每次都在静默回退到规则推断。
"""
from __future__ import annotations

import io
import os
import sys

BE = r"X:\小说工程\wenshu\backend"
sys.path.insert(0, BE)
os.chdir(BE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

print("=" * 74)
print("[1] .env 里的 AI 配置")
print("=" * 74)
env_path = os.path.join(BE, ".env")
if os.path.isfile(env_path):
    with open(env_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            low = s.lower()
            if any(k in low for k in ("api_key", "base_url", "model", "provider",
                                      "openai", "deepseek", "claude", "gemini")):
                # 隐去 key 中段
                if "=" in s:
                    k, v = s.split("=", 1)
                    v = v.strip()
                    if "key" in k.lower() and len(v) > 12:
                        v = v[:8] + "…" + v[-4:]
                    print(f"  {k.strip()} = {v}")
else:
    print("  没有 .env")

print()
print("=" * 74)
print("[2] 数据库里的实际生效配置（运行时以库为准）")
print("=" * 74)
import asyncio  # noqa: E402


async def check_db():
    from sqlalchemy import select
    from app.database import get_engine
    from app.models.settings import Settings
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    engine = await get_engine("system")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        rows = (await db.execute(select(Settings))).scalars().all()
        if not rows:
            print("  settings 表为空")
            return None
        s = rows[0]
        fields = {}
        for attr in ("api_provider", "api_key", "api_base_url", "api_model",
                     "api_temperature", "api_max_tokens"):
            if hasattr(s, attr):
                v = getattr(s, attr)
                if attr == "api_key" and v and len(str(v)) > 12:
                    v = str(v)[:8] + "…" + str(v)[-4:]
                fields[attr] = v
        for k, v in fields.items():
            print(f"  {k:<18} = {v!r}")
        return fields


try:
    fields = asyncio.run(check_db())
except Exception as e:
    print(f"  读取失败: {type(e).__name__}: {e}")
    fields = None

print()
print("=" * 74)
print("[3] 占位值识别")
print("=" * 74)
PLACEHOLDERS = ("sk-SET_ME", "SET_ME", "your-api-key", "sk-xxx", "changeme",
                "placeholder", "TODO")


def is_placeholder(v) -> bool:
    if not v:
        return True
    s = str(v)
    return any(p.lower() in s.lower() for p in PLACEHOLDERS)


if fields:
    key = fields.get("api_key")
    ph = is_placeholder(key)
    print(f"  api_key = {key!r}")
    print(f"  是否占位值: {ph}")
    if ph:
        print()
        print("  ★ 结论：AI 未真正配置。当前所有 AI 能力都在静默回退。")
        print("     受影响的功能：")
        print("       - 拆书导入的「反向生成项目信息」（回退规则推断）")
        print("       - 拆书导入的「反向生成章节大纲」（回退规则大纲）")
        print("       - 章节生成")
        print("       - 子代理并行分析")
        print("       - 文本重塑的 AI 模式")
        print()
        print("     风险：流程不报错，但产出全是规则生成的，"
              "用户可能误以为 AI 已生效。")

print()
print("=" * 74)
print("[4] 检查：拆书流程失败时是否告知用户")
print("=" * 74)
p = os.path.join(BE, "app", "services", "book_import_service.py")
with open(p, encoding="utf-8") as f:
    src = f.read()

for marker, desc in (
    ("反向生成项目信息失败，回退规则推断", "项目信息回退"),
    ("反向生成章节大纲失败，回退规则大纲", "大纲回退"),
):
    if marker in src:
        # 看是否只有 logger，没有写进返回结果
        idx = src.find(marker)
        ctx = src[max(0, idx - 200): idx + 120]
        only_log = "logger.warning" in ctx
        print(f"  {desc}: 仅记日志（未告知用户）" if only_log else f"  {desc}: 有上报")

print()
print("  这说明：AI 不可用时流程静默降级，用户看不到任何提示。")
print("  拆书结果里应当带一个「本次未使用 AI，产出为规则推断」的标记。")
