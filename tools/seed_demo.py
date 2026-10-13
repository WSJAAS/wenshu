# -*- coding: utf-8 -*-
"""建立演示数据：一部作品 + 若干章节，用于查看子代理调度。

章节内容刻意埋入几处一致性问题（人物年龄、时间线、姓名），
这样派发一致性检查子任务时能真实检出问题。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"


def call(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"ERR {type(e).__name__}: {e}"


CHAPTERS = [
    (1, "雨夜", """雨点砸在铁皮上，像一把钝刀反复地刮。

陈默今年二十八岁，在城南的旧货市场守着一间铺子。铺子不大，堆满了别人不要的东西。

他数着第三十七下，灯灭了。黑暗里有股霉味，混着铁锈的气息。

"又停电了。"他自言自语，摸黑去找那半截蜡烛。"""),

    (2, "来客", """门缝里探进半张脸，湿的。

"借个火。"那人说，声音沙哑得不像活人。

陈默把蜡烛递过去。火光里，他看清了对方的眼睛——很亮，亮得有些不正常。

"你从哪来？"

"北边。"那人接过蜡烛，指尖冰凉，"走了很久。"

陈默注意到，那人鞋上一点泥都没有。外面下了整夜的雨。"""),

    (3, "天亮", """天色泛白时，陈默终于在墙角找到了那枚纽扣。

上面沾着暗褐色的东西。他把它捏在指间，对着光看了很久。

他今年三十四岁——不，他记得自己二十八。可镜子里的那张脸，分明已经不再年轻。

门外传来脚步声。很轻，但很稳。

他吹灭了蜡烛。"""),

    (4, "旧账", """北边的风还是在吹。

那个来客再没有出现。陈默把纽扣收进抽屉最深处，压在账本下面。

账本上记着：三月初七，收铜锁一把。三月十二，出瓷碗三只。

最后一页的墨迹是新的。上面写着一行字，不是他的笔迹。

"灯灭了，就该有人来点。" """),
]


def main() -> int:
    st, body = call("GET", "/api/projects")
    if st != 200:
        print(f"服务不可用: {st} {body[:120]}")
        return 1

    projects = json.loads(body).get("items", [])
    existing = next((p for p in projects if p.get("title") == "演示·旧货铺"), None)

    if existing:
        pid = existing["id"]
        print(f"作品已存在: {pid}")
        st, ch = call("GET", f"/api/chapters/project/{pid}")
        got = json.loads(ch)
        if isinstance(got, dict):
            got = got.get("items", [])
        print(f"已有 {len(got)} 章")
        if len(got) >= 4:
            print("数据完整，无需重建")
            print(f"\n打开: http://127.0.0.1:8010/project/{pid}/chapters")
            return 0

    if not existing:
        st, body = call("POST", "/api/projects", {
            "title": "演示·旧货铺",
            "description": "用于查看子代理调度的演示作品。章节里埋了一致性问题。",
            "genre": "悬疑",
            "theme": "雨夜与旧物",
        })
        if st not in (200, 201):
            print(f"建作品失败: {st} {body[:200]}")
            return 1
        pid = json.loads(body)["id"]
        print(f"已建作品: {pid}")

    for num, title, content in CHAPTERS:
        st, body = call("POST", "/api/chapters", {
            "project_id": pid,
            "title": title,
            "chapter_number": num,
            "content": content.strip(),
            "status": "completed",
        })
        flag = "OK" if st in (200, 201) else f"FAIL({st})"
        print(f"  第{num}章 {title}  {flag}")
        if st not in (200, 201):
            print(f"    {body[:150]}")

    print()
    print("=" * 60)
    print("埋入的一致性问题（供子代理检出）：")
    print("  · 年龄矛盾：第1章 二十八岁 → 第3章 三十四岁，且自称记得二十八")
    print("  · 时间线矛盾：第1章 停电摸黑 → 第2章 递蜡烛时仍有火光")
    print("  · 细节矛盾：第2章 整夜大雨，鞋上却无泥")
    print("=" * 60)
    print()
    print(f"打开: http://127.0.0.1:8010/project/{pid}/chapters")
    print("再进「创作助手」，让它检查这几章的一致性。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
