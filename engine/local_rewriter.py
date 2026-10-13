# -*- coding: utf-8 -*-
"""
本地规则重塑引擎 (Local Rule-Based Rewriter)

用途：在没有配置任何模型 API 时，对文本执行**真实、可复现、与输入相关**的改写。
它不是演示剧本——每一次改写都严格由输入文本的内容决定，逐条应用下列规则表，
输出长度与用词随输入变化。

设计取舍（重要，避免误解）：
    规则改写的能力上限是「替换已知的模板化表达 + 调整句长节奏 + 补足感官词」，
    它**不等于**大模型改写。它做不到真正的语义重写、上下文推理与文风塑造。
    因此它只适合做：
        1) 离线自查：快速看一遍文本里有哪些模板化表达、句子节奏是否机械；
        2) 流程验证：确认分块、断点续跑、docx 回写等工程链路通畅；
        3) 无网环境下的最小可用预处理。
    要获得交付说明中描述的“文字重塑、沉浸感、镜头张力”，必须配置真实模型。

论文规则与小说规则的差别，与 prompts/ 下的提示词取向保持一致：
    论文侧：去模板化连接词与空泛收束，保引用、保数据、控字数。
    小说侧：拆情绪标签为生理动作，补感官词，压连环虚词，控节奏。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ============================================================================
# 论文侧规则
# ============================================================================

# (正则, 替换内容或回调说明)。按顺序应用，先长后短，避免短模式吃掉长模式。
PAPER_RULES: tuple[tuple[str, str], ...] = (
    # 空泛收束：整句删除或改写成具体承接
    (r"综上所述[，,]\s*显而易见的是[，,]\s*", ""),
    (r"综上所述[，,]\s*", ""),
    (r"总而言之[，,]\s*", ""),
    (r"显而易见的是[，,]\s*", ""),
    (r"显而易见[，,]\s*", ""),
    (r"不可否认的是[，,]\s*", ""),
    (r"毋庸置疑[，,]\s*", ""),
    (r"不言而喻[，,]\s*", ""),
    (r"值得注意的是[，,]\s*", ""),
    (r"众所周知[，,]\s*", ""),
    (r"由此可见[，,]\s*", ""),
    # 三段式起笔
    (r"首先[，,]\s*", ""),
    (r"其次[，,]\s*", ""),
    (r"再次[，,]\s*", ""),
    (r"最后[，,]\s*", ""),
    (r"其一[，,]\s*", ""),
    (r"其二[，,]\s*", ""),
    # 空泛价值断言
    (r"具有广阔的应用前景", "在工程实践中具备可落地性"),
    (r"具有(?:重要|深远|积极)的(?:理论|实践)?意义", "对后续研究有参考价值"),
    (r"具有显著的理论价值与广阔的应用前景", "为动态环境下的协同控制提供了可复现的实现路径"),
    # 引语型陈词
    (r"随着([^，。]{0,24})的(?:飞速|快速|迅猛)?发展[，,]\s*", r"近年在\1领域，"),
    (r"随着([^，。]{0,20})的(?:不断)?(?:进行|发生|推移)[，,]\s*", r"\1推进过程中，"),
    # 强势动词平实化
    (r"开展([^，。]{0,12})的(?:管控与治理)?工作", r"\1"),
    (r"进行(?:统一)?管理", "管理"),
    (r"完成相关的参数配置与设定", "配置参数"),
    (r"开展对应的(?:数据)?处理作业", "处理数据"),
    (r"得以达成", "达成"),
    (r"落地实现", "实现"),
)

# 论文侧连接词替换：机械连接词 -> 语义承接或删除
PAPER_CONNECTOR_RULES: tuple[tuple[str, str], ...] = (
    (r"此外[，,]\s*", "另需说明的是，"),
    (r"然而[，,]", "但"),
    (r"因此[，,]", "故"),
    (r"同时[，,]", "并且，"),
    (r"另外[，,]", "此外，"),
    (r"进一步说[，,]\s*", ""),
    (r"具体而言[，,]", "具体来说，"),
    (r"与此同时[，,]", "同时，"),
    (r"换言之[，,]", "也就是，"),
)


# ============================================================================
# 小说侧规则
# ============================================================================

# 情绪标签 -> 生理/动作外化。这是“Show, Don't Tell”的规则化最小实现。
NOVEL_EMOTION_RULES: tuple[tuple[str, str], ...] = (
    (r"感到(?:十分|非常|无比|极其|特别)?恐惧", "后颈泛起一层扎人的寒栗"),
    (r"感到(?:十分|非常|无比|极其|特别)?害怕", "喉头猛地收紧"),
    (r"感到(?:十分|非常|无比|极其|特别)?愤怒", "后槽牙咬得生疼，下颌绷成一条硬线"),
    (r"感到(?:十分|非常|无比|极其|特别)?绝望", "眼底的光一点点熄灭，握着刀柄的手指关节发白"),
    (r"感到(?:十分|非常|无比|极其|特别)?难过", "胸口像被什么东西堵住，喘气发闷"),
    (r"感到(?:十分|非常|无比|极其|特别)?震惊", "瞳孔骤然收缩，呼吸顿了半拍"),
    (r"感到(?:十分|非常|无比|极其|特别)?高兴", "眉梢微微挑起，眼角松了下来"),
    (r"感到(?:十分|非常|无比|极其|特别)?疲惫", "眼皮沉得像灌了铅，肩胛骨一阵阵发酸"),
    (r"感到(?:十分|非常|无比|极其|特别)?无奈", "嘴唇动了动，最终什么也没说出口"),
    (r"感到(?:十分|非常|无比|极其|特别)?羞愧", "耳根烧起来，目光落到地面上"),
    (r"内心(?:深处)?(?:充满了|陷入了)(?:极度的)?(?:绝望|纠结|彷徨|痛苦|沉思)", "指节在袖口里反复攥紧又松开"),    (r"脸色(?:瞬间|立刻|顿时)?变得铁青", "血色从脸上退得一干二净"),
    (r"脸色(?:瞬间|立刻|顿时)?变得苍白", "脸色白得像一张没写过的纸"),
    (r"脸色(?:瞬间|立刻|顿时)?变得难看", "眉眼沉了下来"),
    (r"心中(?:暗暗|不禁)?(?:想到|思忖|感叹)", "心里转过一个念头"),
    (r"这一刻，时间仿佛凝固了", "空气像被冻住，连灰尘都停在半空"),
    (r"倒吸了一口(?:凉|冷)气", "一口气吸到一半卡在喉咙里"),
    (r"不容置疑的是[，,]\s*", ""),
)

# 说明文腔 / 翻译腔旁白
NOVEL_NARRATION_RULES: tuple[tuple[str, str], ...] = (
    (r"不可否认的是[，,]\s*", ""),
    (r"不得否认的是[，,]\s*", ""),
    (r"显而易见的是[，,]\s*", ""),
    (r"换句话说[，,]\s*", ""),
    (r"在这危急关头[，,]\s*", ""),
    (r"在这千钧一发之际[，,]\s*", ""),
    (r"随着时间的推移[，,]\s*", ""),
    (r"随着(?:夜色|夜幕|暮色|月光|天黑)的降临[，,]\s*", "夜色压下来，"),
    # 论文式收束出现在小说里同样出戏
    (r"综上所述[，,]\s*", ""),
    (r"总而言之[，,]\s*", ""),
    (r"毋庸置疑[，,]\s*", ""),
    # “随着 X 的完成/结束/到来”这类旁白式过渡
    (r"随着([^，。]{0,16})的(?:完成|结束|到来|推进)[，,]\s*", r"\1之后，"),
    (r"随着([^，。]{0,18})的(?:进行|发生|推移|不断增多)[，,]\s*", ""),
    (r"就在这时[，,]\s*", ""),
)

# 虚词拖沓压缩
NOVEL_DICTION_RULES: tuple[tuple[str, str], ...] = (
    (r"进行(?:了)?([\u4e00-\u9fff]{2,4})", r"\1"),
    (r"的的", "的"),
    (r"了了", "了"),
    (r"([的地得])的", r"\1"),
)

# 感官词补充表：按场景关键词触发，向句子中注入具体感知。
# 每组给出多个候选，按段落轮换取用，避免同一句感官描写在全文里重复堆叠。
SENSORY_INJECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"雨|雨水|雨天", (
        "雨点砸在铁皮雨棚上，一阵闷响压过一阵",
        "雨水顺着檐口连成一线，砸在肩上是冰的",
        "水汽裹着路灯的橘光，把街面泡成一片模糊",
    )),
    (r"夜(?:色|里|间|幕)", (
        "残存的微光只够勾出物体的轮廓，其余都沉在暗里",
        "风从楼缝里挤过来，带着凉意",
    )),
    (r"霓虹|灯", (
        "霓虹的光在积水里碎成一片一片",
        "招牌的余光斜切过来，把影子拉得很长",
    )),
    (r"电动车|骑车|车", (
        "车轮碾过水洼，溅起半人高的水线",
        "车把在掌心里震得发麻",
    )),
    (r"楼梯|爬楼|一层一层", (
        "楼道里的声控灯忽明忽暗，脚步声在空筒里回荡",
        "栏杆的漆皮硌手，铁锈的腥气贴上来",
    )),
    (r"脚步|走", (
        "鞋底在地砖上蹭出一声钝响",
    )),
    (r"冷|冻|湿透|湿", (
        "冷气顺着领口往下钻，衣服贴在背上又沉又凉",
    )),
)

# 同一段落里只补一条感官描写；全文同一候选句最多复用 2 次。
_SENSORY_REUSE_LIMIT = 2


@dataclass
class RuleRewriteResult:
    text: str
    applied: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _apply_rules(text: str, rules: tuple[tuple[str, str], ...], applied: list[str]) -> str:
    for pattern, replacement in rules:
        new_text, count = re.subn(pattern, replacement, text)
        if count:
            applied.append(f"{pattern} -> {replacement!r} ({count} 次)")
            text = new_text
    return text


def _tidy(text: str) -> str:
    """清理规则替换后残留的标点与连接问题。"""
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"^[，,。；;、]+", "", text)
    text = re.sub(r"[，,]([。！？；])", r"\1", text)
    text = re.sub(r"([。！？；])[，,]", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # 情绪替换后残留的悬挂连接词，例如「…松开与纠结。」
    text = re.sub(r"([\u4e00-\u9fff]{2,})与[、，,]?\s*(?:纠结|彷徨|痛苦|沉思)[。，,]", r"\1。", text)
    # 句末多余的「一样」「似的」等残留拖尾
    text = re.sub(r"一样([。！？])", r"\1", text)
    # 引号后紧跟句号：中文里「…。」"就已收束，追加的句号是多余的
    text = re.sub(r"([”\"])。", r"\1", text)
    text = re.sub(r"，。", "。", text)
    text = re.sub(r"。，", "。", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _protect_first_line(text: str) -> tuple[str, str]:
    """把首行标题式短行摘出来，避免被规则改写。

    很多小说的首行是章节标题，规则表里的“随着……的降临”之类替换会把标题
    改得像正文，例如「夜雨里的最后一单」被补成「夜雨里的最后一单。雨点砸在…」。
    """
    lines = text.split("\n", 1)
    if len(lines) == 2 and lines[0].strip() and len(lines[0].strip()) <= 24:
        return lines[0].strip(), lines[1]
    return "", text


def _inject_sensory(text: str, applied: list[str]) -> str:
    """在每个含场景关键词的段落补足一条感官描写。

    仅在**叙述性段落**上补足，跳过以下情形：
        · 短段落（标题、单句过渡）——补进去会像眉批；
        · 以引号结尾的段落（对白收尾）——补进去会打断对话节奏。

    每个段落最多补一条；同一候选句全文最多复用 _SENSORY_REUSE_LIMIT 次，
    超出后改用同组的其它候选，避免整篇反复出现同一句话。
    """
    usage: dict[str, int] = {}
    paragraphs = text.split("\n\n")
    output: list[str] = []

    # 找出最后一个非空段落下标：收束段通常承担点题作用，追加感官句会削弱余韵。
    non_empty_indexes = [i for i, p in enumerate(paragraphs) if p.strip()]
    closing_index = non_empty_indexes[-1] if non_empty_indexes else -1

    for position, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            output.append(paragraph)
            continue

        stripped = paragraph.rstrip()
        is_dialogue_tail = stripped.endswith(("”", '"'))
        too_short = len(stripped) < 40
        is_closing = position == closing_index

        addition = ""
        if not is_dialogue_tail and not too_short and not is_closing:
            for pattern, candidates in SENSORY_INJECTIONS:
                if not re.search(pattern, paragraph):
                    continue
                for candidate in candidates:
                    if usage.get(candidate, 0) < _SENSORY_REUSE_LIMIT:
                        addition = candidate
                        usage[candidate] = usage.get(candidate, 0) + 1
                        break
                if addition:
                    break

        if addition:
            applied.append(f"感官补足: {addition}")
            if stripped.endswith(("。", "！", "？", "…")):
                paragraph = stripped + addition + "。"
            else:
                paragraph = stripped + "。" + addition + "。"
        output.append(paragraph)

    return "\n\n".join(output)


def _rebalance_rhythm(text: str, applied: list[str]) -> str:
    """把过长的连续分句切开，制造长短句落差（论文与小说通用）。"""
    sentences = re.split(r"(?<=[。！？])", text)
    rebuilt: list[str] = []
    changed = 0
    for sentence in sentences:
        if len(sentence) > 62:
            clauses = re.split(r"(?<=[，；])", sentence)
            if len(clauses) >= 3:
                mid = len(clauses) // 2
                rebuilt.append("".join(clauses[:mid]))
                rebuilt.append("".join(clauses[mid:]))
                changed += 1
                continue
        rebuilt.append(sentence)
    if changed:
        applied.append(f"长句切分: {changed} 处")
    return "".join(rebuilt)


def rewrite_paper(text: str) -> RuleRewriteResult:
    """论文侧本地规则改写：去模板化 + 控字数 + 保引用与数据。"""
    if not text.strip():
        return RuleRewriteResult(text="", applied=[], stats={})

    applied: list[str] = []
    result = text
    result = _apply_rules(result, PAPER_RULES, applied)
    result = _apply_rules(result, PAPER_CONNECTOR_RULES, applied)
    result = _rebalance_rhythm(result, applied)
    result = _tidy(result)

    return RuleRewriteResult(
        text=result,
        applied=applied,
        stats={
            "rule_hits": len(applied),
            "original_length": len(text.strip()),
            "rewritten_length": len(result),
        },
    )


def rewrite_novel(text: str) -> RuleRewriteResult:
    """小说侧本地规则改写：情绪外化 + 感官补足 + 去说明腔 + 压虚词。"""
    if not text.strip():
        return RuleRewriteResult(text="", applied=[], stats={})

    title, body = _protect_first_line(text)
    applied: list[str] = []
    if title:
        applied.append(f"首行标题原样保留: {title!r}")

    result = body
    result = _apply_rules(result, NOVEL_EMOTION_RULES, applied)
    result = _apply_rules(result, NOVEL_NARRATION_RULES, applied)
    result = _apply_rules(result, NOVEL_DICTION_RULES, applied)
    result = _rebalance_rhythm(result, applied)
    result = _tidy(result)
    result = _inject_sensory(result, applied)
    result = _tidy(result)
    if title:
        result = f"{title}\n\n{result}"

    return RuleRewriteResult(
        text=result,
        applied=applied,
        stats={
            "rule_hits": len(applied),
            "original_length": len(text.strip()),
            "rewritten_length": len(result),
        },
    )


def rewrite_local(text: str, mode: str) -> RuleRewriteResult:
    """按模式分派到对应的本地规则改写。"""
    normalized = (mode or "").strip().lower()
    if normalized == "paper":
        return rewrite_paper(text)
    if normalized in ("novel", "novel_single"):
        return rewrite_novel(text)
    raise ValueError(f"Unsupported local rewrite mode: {mode}")
