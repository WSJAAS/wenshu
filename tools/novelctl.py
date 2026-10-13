# -*- coding: utf-8 -*-
"""文枢 novelctl —— 统一命令行入口。

把创作管理与文本重塑收敛到一条命令，`--json` 供脚本调用。

用法示例：
    novelctl.py status                     平台状态
    novelctl.py projects                   列出作品
    novelctl.py new "作品名" --genre 悬疑    建立作品
    novelctl.py chapters <项目ID>           列出章节
    novelctl.py export <项目ID> -o 稿件.txt  导出
    novelctl.py polish -f 稿件.txt -o 候选.txt   润色（不改原稿）
    novelctl.py accept -f 候选.txt               人工采纳
    novelctl.py rollback                         版本回退
    novelctl.py diagnose -f 稿件.txt             文本体检
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.getenv("WENSHU_URL", "http://127.0.0.1:8000")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def req(method, path, payload=None, timeout=300):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}"


def out(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg):
    print(f"{RED}✗ {msg}{RESET}", file=sys.stderr)
    sys.exit(1)


def ok(msg):
    print(f"{GREEN}✓{RESET} {msg}")


def read_input(args) -> str:
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            die(f"文件不存在: {p}")
        return p.read_text(encoding="utf-8")
    if args.text:
        return args.text
    die("需要 -f 文件 或 -t 文本")
    return ""


# ============================================================
def cmd_status(a):
    st, body = req("GET", "/health", timeout=10)
    up = st == 200
    eng = {}
    if up:
        s2, b2 = req("GET", "/api/polish/engine", timeout=10)
        if s2 == 200:
            eng = json.loads(b2)
    if a.json:
        out({"up": up, "url": BASE, "engine": eng}, True)
        return
    print("=" * 56)
    print("  文枢 Wenshu · 状态")
    print("=" * 56)
    print(f"  {'●' if up else '○'} 服务   {BASE}")
    print(f"  {'●' if eng.get('available') else '○'} 引擎   {eng.get('dir', '未找到')}")
    print(f"  {'（无登录模式）' if up else ''}")


def cmd_projects(a):
    st, body = req("GET", "/api/projects")
    if st != 200:
        die(f"读取作品失败 {st}: {body[:200]}")
    d = json.loads(body)
    items = d.get("items", d if isinstance(d, list) else [])
    if a.json:
        out({"total": len(items), "items": items}, True)
        return
    print(f"共 {len(items)} 部作品")
    for it in items:
        print(f"  {it.get('id')}  {it.get('title')}  [{it.get('status')}]  {it.get('current_words', 0)} 字")


def cmd_new(a):
    st, body = req("POST", "/api/projects",
                   {"title": a.title, "genre": a.genre or "", "description": a.description or ""})
    if st not in (200, 201):
        die(f"建作品失败 {st}: {body[:300]}")
    p = json.loads(body)
    if a.json:
        out(p, True)
    else:
        ok(f"已建立: {p.get('title')}")
        print(f"   project_id = {p.get('id')}")


def cmd_chapters(a):
    st, body = req("GET", f"/api/chapters/project/{a.project_id}")
    if st != 200:
        die(f"读取章节失败 {st}: {body[:200]}")
    items = json.loads(body)
    if isinstance(items, dict):
        items = items.get("items", [])
    if a.json:
        out({"total": len(items), "items": items}, True)
        return
    print(f"共 {len(items)} 章")
    for it in items:
        print(f"  第{it.get('chapter_number')}章  {it.get('title')}  [{it.get('status')}]  "
              f"{len(it.get('content') or '')} 字符")


def cmd_export(a):
    st, body = req("GET", f"/api/projects/{a.project_id}/export")
    if st != 200:
        die(f"导出失败 {st}: {body[:200]}")
    if a.output:
        p = Path(a.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        ok(f"已导出 {len(body)} 字符 -> {p}")
    else:
        print(body)


def cmd_polish(a):
    text = read_input(a)
    st, body = req("POST", "/api/polish", {
        "original_text": text, "mode": a.mode, "use_model": not a.offline,
    })
    if st != 200:
        die(f"润色失败 {st}: {body[:300]}")
    r = json.loads(body)
    if a.output:
        p = Path(a.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(r.get("polished_text", ""), encoding="utf-8")
    if a.json:
        out(r if not a.output else {**r, "saved_to": str(a.output)}, True)
        return
    ok(f"改写完成  引擎={r.get('engine')}  "
       f"{r.get('word_count_before')} → {r.get('word_count_after')} 字符")
    print(f"{DIM}--- 原稿 ---{RESET}")
    print(text[:300])
    print(f"{DIM}--- 候选稿 ---{RESET}")
    print(r.get("polished_text", "")[:300])
    db = r.get("diagnostics_before") or {}
    da = r.get("diagnostics_after") or {}
    if "experience_score" in db and "experience_score" in da:
        print(f"{DIM}体验分: {db['experience_score']} → {da['experience_score']}{RESET}")
    if r.get("engine_note"):
        print(f"{YELLOW}{r['engine_note']}{RESET}")
    if a.output:
        ok(f"候选稿已保存 -> {a.output}")


def cmd_diagnose(a):
    text = read_input(a)
    st, body = req("POST", "/api/polish/diagnose", {"text": text, "mode": a.mode})
    if st != 200:
        die(f"体检失败 {st}: {body[:200]}")
    m = json.loads(body).get("metrics", {})
    if a.json:
        out(m, True)
        return
    print("文本体检")
    for k, v in m.items():
        if isinstance(v, list):
            v = "；".join(str(x) for x in v[:3]) if v else "—"
        print(f"  {k:<24} {v}")


def cmd_accept(a):
    text = read_input(a)
    st, body = req("POST", "/api/polish/accept",
                   {"text": text, "mode": a.mode, "task_id": a.task_id or ""})
    if st != 200:
        die(f"采纳失败 {st}: {body[:200]}")
    r = json.loads(body)
    if a.json:
        out(r, True)
        return
    ok(f"已采纳  第 {r.get('versions')} 版")
    print(f"   {r.get('saved', {}).get('path')}")
    if r.get("can_rollback"):
        print(f"{DIM}   可回退: novelctl rollback{RESET}")


def cmd_rollback(a):
    st, body = req("POST", f"/api/polish/rollback?mode={a.mode}")
    if st != 200:
        die(f"回退失败 {st}: {body[:200]}")
    r = json.loads(body)
    if a.output:
        Path(a.output).write_text(r.get("text", ""), encoding="utf-8")
    if a.json:
        out(r, True)
        return
    ok("已回退到上一版")
    print(r.get("text", "")[:400])
    if a.output:
        ok(f"已保存 -> {a.output}")


def cmd_versions(a):
    st, body = req("GET", f"/api/polish/versions?mode={a.mode}")
    if st != 200:
        die(f"读取版本失败 {st}")
    d = json.loads(body)
    if a.json:
        out(d, True)
        return
    print(f"共 {d.get('total', 0)} 版")
    for v in d.get("items", []):
        print(f"  {v.get('name')}  {v.get('chars')} 字符  {v.get('saved_at')}")


# ============================================================
def build_parser():
    p = argparse.ArgumentParser(
        prog="novelctl",
        description="文枢 · 统一命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--json", action="store_true", help="JSON 输出（供脚本调用）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="平台状态").set_defaults(f=cmd_status)
    sub.add_parser("projects", help="列出作品").set_defaults(f=cmd_projects)

    s = sub.add_parser("new", help="建立作品")
    s.add_argument("title"); s.add_argument("--genre", default=""); s.add_argument("--description", default="")
    s.set_defaults(f=cmd_new)

    s = sub.add_parser("chapters", help="列出章节")
    s.add_argument("project_id"); s.set_defaults(f=cmd_chapters)

    s = sub.add_parser("export", help="导出作品 TXT")
    s.add_argument("project_id"); s.add_argument("-o", "--output")
    s.set_defaults(f=cmd_export)

    s = sub.add_parser("polish", help="润色：产候选稿，不改原稿")
    s.add_argument("-f", "--file"); s.add_argument("-t", "--text")
    s.add_argument("-m", "--mode", default="novel", choices=["novel", "paper"])
    s.add_argument("-o", "--output")
    s.add_argument("--offline", action="store_true", help="只用本地规则，不调模型")
    s.set_defaults(f=cmd_polish)

    s = sub.add_parser("diagnose", help="文本体检")
    s.add_argument("-f", "--file"); s.add_argument("-t", "--text")
    s.add_argument("-m", "--mode", default="novel", choices=["novel", "paper"])
    s.set_defaults(f=cmd_diagnose)

    s = sub.add_parser("accept", help="人工采纳")
    s.add_argument("-f", "--file"); s.add_argument("-t", "--text")
    s.add_argument("-m", "--mode", default="novel", choices=["novel", "paper"])
    s.add_argument("--task-id", default="")
    s.set_defaults(f=cmd_accept)

    s = sub.add_parser("rollback", help="版本回退")
    s.add_argument("-m", "--mode", default="novel", choices=["novel", "paper"])
    s.add_argument("-o", "--output")
    s.set_defaults(f=cmd_rollback)

    s = sub.add_parser("versions", help="采纳历史")
    s.add_argument("-m", "--mode", default="novel", choices=["novel", "paper"])
    s.set_defaults(f=cmd_versions)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.f(args)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
