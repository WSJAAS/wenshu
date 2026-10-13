# -*- coding: utf-8 -*-
"""
文本质量与特征诊断引擎 (Text Diagnostics Engine)
融合并升级自 AI_paper (纸研社) 与 baibaiAIGC 的核心算法：
1. AcademicPaperDiagnostics: 针对学术论文的无损守恒度与 AIGC 痕迹量化
2. NovelDiagnostics: 针对小说文学文本的文字润色度、感官丰富度与阅读体验评分
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


# ============================================================================
# 学术论文 AIGC 痕迹与严谨性诊断
# ============================================================================

PAPER_AI_PATTERNS = [
    (r"首先.*其次.*最后", "经典三段式模板过渡（首先/其次/最后）"),
    (r"综上所述", "空泛总结高频词（综上所述）"),
    (r"总而言之", "刻板机械总结（总而言之）"),
    (r"不可否认", "全知视角预设断言（不可否认）"),
    (r"毋庸置疑", "绝对化断言套话（毋庸置疑）"),
    (r"显而易见", "主观武断表达（显而易见）"),
    (r"众所周知", "空泛共识假定（众所周知）"),
    (r"不言而喻", "套路化论述修辞（不言而喻）"),
    (r"值得注意的是", "强行引起关注套话（值得注意的是）"),
    (r"由此可见", "简易推导机械过渡（由此可见）"),
    (r"不仅.*而且.*更", "典型递进排比句式（不仅……而且……更）"),
    (r"一方面.*另一方面", "对称机械对仗（一方面……另一方面）"),
    (r"具有(?:重要|深远|积极)的?(?:理论|实践)?意义", "空泛价值套话宣称"),
    (r"具有广阔的应用前景", "模板化收尾断言"),
    (r"随着.*的(?:飞速|快速|迅猛)?发展", "论文引言陈词滥调（随着……发展）"),
]

PAPER_CONNECTORS = (
    "因此", "然而", "同时", "此外", "另外", "由此可见", "综上", "进一步说",
    "相较之下", "具体而言", "与此同时", "毋庸讳言", "概括而言", "换言之",
)

CITATION_PATTERN = re.compile(r"\[(?:\d+(?:[-–,]\s*\d+)*)\]")

# 学术改写允许的字数浮动区间。issues 告警与 valid 判定共用同一组边界，
# 避免出现「报了膨胀告警却仍判通过」的自相矛盾。
LENGTH_RATIO_LOWER_BOUND = 0.75
LENGTH_RATIO_UPPER_BOUND = 1.25


@dataclass
class PaperDiagnosticsResult:
    score: int  # 0 - 100, 分数越高说明 AI 痕迹越重
    risk_level: str  # 低风险 (0-20), 中风险 (21-45), 高风险 (46-100)
    features: list[str] = field(default_factory=list)
    flagged_sentences: list[str] = field(default_factory=list)
    sentence_count: int = 0
    avg_sentence_len: float = 0.0
    sentence_len_variance: float = 0.0
    is_variance_robotic: bool = False
    connector_density: float = 0.0
    citation_count: int = 0
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperDiagnostics:
    """针对学术论文的 AIGC 痕迹诊断与完整性守卫"""

    @staticmethod
    def extract_citations(text: str) -> list[str]:
        return CITATION_PATTERN.findall(text or "")

    @classmethod
    def scan_ai_metrics(cls, text: str) -> PaperDiagnosticsResult:
        text = text or ""
        features: list[str] = []
        flagged_sentences: list[str] = []
        risk_score = 0

        # 1. 高频套话与模板模式扫描
        for pattern, desc in PAPER_AI_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                risk_score += min(len(matches) * 5, 15)
                features.append(f"{desc} (出现 {len(matches)} 次)")

        # 2. 句子粒度切分与长度方差分析（真实学术文章句长方差 >150，AI 常在 40~90 之间极其整齐）
        raw_sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) >= 5]
        sentence_count = len(raw_sentences)
        avg_len = 0.0
        variance = 0.0
        is_robotic_variance = False

        if sentence_count >= 5:
            lengths = [len(s) for s in raw_sentences]
            avg_len = round(sum(lengths) / sentence_count, 1)
            variance = round(sum((length - avg_len) ** 2 for length in lengths) / sentence_count, 1)
            if variance < 110:
                is_robotic_variance = True
                risk_score += 15
                features.append(f"句子长度过于均匀 (方差 {variance} < 110)，存在典型 AI 模板化输出特征")
            elif variance > 180:
                risk_score = max(0, risk_score - 5)  # 真实人类句法长短错落，给予宽免

        # 3. 连接词密度检测
        connector_hits = sum(text.count(conn) for conn in PAPER_CONNECTORS)
        connector_density = round(connector_hits / max(sentence_count, 1), 2)
        if sentence_count >= 4 and connector_density > 0.45:
            risk_score += 12
            features.append(f"转折/递进连接词密度偏高 ({connector_density}/句，共 {connector_hits} 处)，逻辑标记过于生硬")

        # 4. 抓取高危 AI 句
        high_risk_words = ["综上所述", "不可否认", "毋庸置疑", "显而易见", "众所周知", "值得注意的是", "总而言之", "不言而喻"]
        for sentence in raw_sentences[:30]:
            if any(w in sentence for w in high_risk_words):
                trimmed = sentence[:65] + "..." if len(sentence) > 65 else sentence
                flagged_sentences.append(trimmed)

        # 5. 提取学术引用
        citations = sorted(list(set(cls.extract_citations(text))))

        risk_score = max(0, min(100, risk_score))
        risk_level = "高风险" if risk_score >= 45 else ("中风险" if risk_score >= 20 else "低风险")

        return PaperDiagnosticsResult(
            score=risk_score,
            risk_level=risk_level,
            features=features,
            flagged_sentences=flagged_sentences[:8],
            sentence_count=sentence_count,
            avg_sentence_len=avg_len,
            sentence_len_variance=variance,
            is_variance_robotic=is_robotic_variance,
            connector_density=connector_density,
            citation_count=len(citations),
            citations=citations,
        )

    @classmethod
    def verify_integrity(cls, original_text: str, rewritten_text: str) -> dict[str, Any]:
        """校验改写前后的学术无损性：引用标记守恒、字数膨胀率、关键数据"""
        issues: list[str] = []

        # 1. 引用标记守恒校验
        orig_citations = set(cls.extract_citations(original_text))
        rewritten_citations = set(cls.extract_citations(rewritten_text))
        missing_citations = sorted(list(orig_citations - rewritten_citations))
        if missing_citations:
            issues.append(f"发现丢失文献引用标记: {', '.join(missing_citations)}")

        # 2. 字数膨胀/萎缩校验 (学术论文严禁大幅膨胀)
        #    阈值与 valid 判定使用同一组常量，避免出现「报了 issue 却仍判通过」。
        orig_len = len(original_text.strip())
        rewritten_len = len(rewritten_text.strip())
        ratio = round(rewritten_len / max(orig_len, 1), 2)
        if ratio > LENGTH_RATIO_UPPER_BOUND:
            issues.append(
                f"改写后字数膨胀过多 (+{int((ratio - 1) * 100)}%)，可能引入了非必要的口水化内容"
            )
        elif ratio < LENGTH_RATIO_LOWER_BOUND:
            issues.append(
                f"改写后字数过度压缩 (-{int((1 - ratio) * 100)}%)，可能漏失了关键论证或细节"
            )

        # 3. 数字与度量单位守恒抽样
        orig_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?", original_text))
        rewritten_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?", rewritten_text))
        missing_numbers = sorted(list(orig_numbers - rewritten_numbers))
        if len(missing_numbers) > 3:
            issues.append(f"发现关键数值可能发生漂移或遗漏: {missing_numbers[:5]}")

        is_valid = not issues
        return {
            "valid": is_valid,
            "issues": issues,
            "missing_citations": missing_citations,
            "original_length": orig_len,
            "rewritten_length": rewritten_len,
            "length_ratio": ratio,
        }


# ============================================================================
# 小说文学文本 润色度与阅读体验诊断
# ============================================================================

NOVEL_TELL_PATTERNS = [
    (r"感到(?:十分|非常|无比|极其|特别)?(?:愤怒|绝望|害怕|难过|伤心|恐惧|欣喜|高兴|震惊)", "简单告知情绪（Tell 而非 Show）"),
    (r"内心(?:深处)?(?:陷入了|充满了|感到)?(?:无尽的|极度的)?(?:纠结|彷徨|痛苦|绝望|沉思)", "抽象解说内心（缺乏具象外化）"),
    (r"脸色(?:瞬间|立刻|顿时)?变(?:得苍白|得难看|得铁青)", "套路化面部表情"),
    (r"心中(?:暗暗|不禁)?(?:感叹|思忖|惊讶|想到)", "直接上帝视角自白"),
    (r"这一刻，时间仿佛凝固了", "网文严重泛滥的套路陈词"),
    (r"倒吸了一口(?:凉|冷)气", "高频陈腐网络套话"),
    (r"不可否认的是|显而易见的是|换句话说", "小说中突兀的说明文/论文式插话"),
    (r"随着.*的(?:进行|发生|推移)", "生硬翻译腔旁白过渡"),
]

SENSORY_LEXICON = {
    "visual": (
        "阴影", "微光", "余晖", "幽暗", "猩红", "灰蒙蒙", "斑驳", "残阳", "刺目",
        "流光", "苍白", "模糊", "清冽", "冷冽", "黯淡", "寒芒", "倒影", "纹理",
        "刺眼", "白茫茫", "泥浆", "漆黑", "昏暗", "泛红", "水汽",
    ),
    "auditory": (
        "沉闷", "呼啸", "金铁", "低鸣", "风声", "沙沙", "喘息", "崩裂", "刺耳",
        "嗡鸣", "碎裂", "咆哮", "嘶哑", "咔嚓", "闷响", "轰然", "低语", "静寂",
        "尖叫", "急促", "轰鸣", "心跳", "破空", "震颤",
    ),
    "tactile_somatic": (
        "冰凉", "滚烫", "黏稠", "刺痛", "锋锐", "粗糙", "温热", "麻木", "战栗",
        "冷汗", "僵硬", "抽搐", "微颤", "瞳孔", "指节", "后背", "喉头", "牙关",
        "火辣辣", "湿透", "生疼", "发滑", "发烫", "冷水", "沉甸甸",
    ),
}


@dataclass
class NovelDiagnosticsResult:
    experience_score: int  # 0 - 100, 分数越高说明阅读体验越佳、文学质感越好
    rating: str  # 卓越 / 优秀 / 良好 / 扁平生硬
    show_vs_tell_ratio: float
    sensory_density: float
    tell_cliches: list[str] = field(default_factory=list)
    dialogue_ratio: float = 0.0
    short_sentence_ratio: float = 0.0
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NovelDiagnostics:
    """针对小说文学文本的润色度与阅读体验诊断"""

    @classmethod
    def scan_novel_metrics(cls, text: str) -> NovelDiagnosticsResult:
        text = text or ""
        total_len = len(text.strip())
        if total_len == 0:
            return NovelDiagnosticsResult(
                experience_score=50,
                rating="空白",
                show_vs_tell_ratio=1.0,
                sensory_density=0.0,
            )

        suggestions: list[str] = []
        tell_cliches: list[str] = []
        deductions = 0
        bonuses = 0

        # 1. 扫描抽象告知（Tell）与套路词
        tell_hits = 0
        for pattern, desc in NOVEL_TELL_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                tell_hits += len(matches)
                deductions += min(len(matches) * 10, 40)
                tell_cliches.append(f"{desc} (匹配: '{matches[0]}')")

        # 2. 统计五感与生理反应词（Show 属性）
        sensory_hits = 0
        for category, words in SENSORY_LEXICON.items():
            for word in words:
                count = text.count(word)
                if count:
                    sensory_hits += count

        # 每千字感官词密度
        sensory_density = round((sensory_hits / max(total_len, 100)) * 1000, 2)
        if sensory_density >= 12.0:
            bonuses += 20
        elif sensory_density >= 6.0:
            bonuses += 10
        else:
            suggestions.append("感官描摹（光影/声响/触觉/微表情）偏弱，场景画面感不足")
            deductions += 10

        # 3. 对白占比分析
        dialogue_chars = sum(len(d) for d in re.findall(r"“([^”]+)”|\"([^\"]+)\"", text))
        dialogue_ratio = round(dialogue_chars / max(total_len, 1), 2)
        if dialogue_ratio > 0.65:
            suggestions.append("台词对白占比偏高（>65%），缺乏动作穿插与神态铺垫，易变成两人站桩说话")
            deductions += 8
        elif 0.15 <= dialogue_ratio <= 0.45:
            bonuses += 10  # 叙事与对白平衡

        # 4. 节奏与短句冲击力分析 (以标点分割)
        sentences = [s.strip() for s in re.split(r"[，。！？；…\n]", text) if s.strip()]
        short_sentences = [s for s in sentences if len(s) <= 12]
        short_sentence_ratio = round(len(short_sentences) / max(len(sentences), 1), 2)

        if 0.25 <= short_sentence_ratio <= 0.85:
            bonuses += 10  # 句式利落或长短交错，符合网文快节奏与文学张力
        elif short_sentence_ratio < 0.15:
            suggestions.append("长难句过多，阅读节奏拖沓，缺乏作品的气口与断句张力")
            deductions += 8

        # 5. 连续“的/地/得/了”虚词拖沓检查
        de_le_hits = len(re.findall(r"[的地的得了]{3,}", text))
        if de_le_hits > 0:
            suggestions.append(f"发现 {de_le_hits} 处连环助词堆叠（如'...的...的地...'），行文略显拖沓")
            deductions += 6

        # 综合评分计算：基础 70 分，加奖励分与扣分
        final_score = 70 + bonuses - deductions
        final_score = max(20, min(98, final_score))

        rating = "卓越" if final_score >= 88 else ("优秀" if final_score >= 75 else ("良好" if final_score >= 60 else "扁平生硬"))

        # Show / Tell 比值：感官具象（Show）词数与「抽象告知」命中次数的比。
        # 两侧都为 0 时返回 0.0 —— 既无具象也无套话，说明这段文字本身贫瘠，
        # 不应该因为公式的两个 max(...,1) 而拿到看起来"平衡"的 1.0。
        if sensory_hits == 0 and tell_hits == 0:
            show_vs_tell_ratio = 0.0
            suggestions.append("既无感官具象也无明显套话，文本偏白描，建议补充场景细节")
        else:
            show_vs_tell_ratio = round(sensory_hits / max(tell_hits, 1), 2)

        return NovelDiagnosticsResult(
            experience_score=final_score,
            rating=rating,
            show_vs_tell_ratio=show_vs_tell_ratio,
            sensory_density=sensory_density,
            tell_cliches=tell_cliches[:6],
            dialogue_ratio=dialogue_ratio,
            short_sentence_ratio=short_sentence_ratio,
            suggestions=suggestions[:5],
        )
