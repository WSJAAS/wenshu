"""番茄字体码表 —— 把私用区乱码还原为原文。

## 背景

番茄阅读页的正文用一套自定义字体做**编码层**混淆：正文里的字被换成私用区
码位（U+E3E8..U+E55B），浏览器靠那套字体把它们渲染成正确字形。所以：

- 抓到的 HTML 里，约一半的汉字是私用区码位
- 详情页书名、章节目录**不受影响**（那是服务端直接渲染的干净文本）
- 同一套字体全站通用（实测三章字体 URL 与字形表完全一致）

## 还原方式

查表。码位按顺序对应一个字符数组，共 372 个位置（`CODE_ST..CODE_ED`）。
表里有 `?` 表示该码位在字体里没有字形，正文不会用到，命中时保持原样。

## 为什么不用字形比对

字形比对（见 `tools/probe_glyph_match.py`）能自动重建映射，实测最近邻
一致率 95%。但它是**推算**，需要比对运算且可能误判。查表是**直接对应**，
零运算零误差。字形比对保留为兜底：一旦番茄换字体导致查表失效，
可用它自动重建新表。

## 表的来源与校验

基础表来自 naiyQAQ/fanqie-assistant（GPL-3.0），该项目的码位区间与字体名
与本项目实测完全一致。已用以下方式独立验证：

- 码表长度 372 与应有长度一致
- 字形最近邻一致率 95.0%，前三命中 99.4%
- 真实章节端到端解码，残留私用区字符为 0

校验脚本见 `tools/verify_table.py` 与 `tools/verify_table2.py`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ============================================================
#  码位区间
# ============================================================
# 实测字体 cmap 范围即为 0xE3E8..0xE55B
CODE_ST = 58344   # 0xE3E8
CODE_ED = 58715   # 0xE55B
TABLE_SIZE = CODE_ED - CODE_ST + 1   # 372

# 无字形占位符：该码位字体里没有，正文不会出现
NO_GLYPH = "?"


# ============================================================
#  码表
# ============================================================
# 按 CODE_ST..CODE_ED 顺序排列。每行 24 个字符，便于人工核对。
_TABLE_ROWS = [
    "D在主特家军然表场4要只v和?6别还g现儿岁??此象月3出战工相",
    "o男直失世F都平文什VO将真T那当?会立些u是十张学气大爱两命全",
    "后东性通被1它乐接而感车山公了常以何可话先pi叫轻M士w着变尔快",
    "l个说少色里安花远7难师放t报认面道S?克地度I好机U民写把万同",
    "水新没书电吃像斯5为y白几日教看但第加候作上拉住有法r事应位利你",
    "声身国问马女他Y比父xAHNsX边美对所金活回意到z从j知又内因",
    "点Q三定8Rb正或夫向德听更?得告并本q过记L让打f人就者去原满",
    "体做经K走如孩cG给使物?最笑部?员等受k行一条果动光门头见往自",
    "解成处天能于名其发总母的死手入路进心来h时力多开已许d至由很界n",
    "小与Z想代么分生口再妈望次西风种带J?实情才这?E我神格长觉间年",
    "眼无不亲关结0友信下却重己老2音字m呢明之前高PB目太e9起稜她",
    "也W用方子英每理便四数期中C外样a海们任",
]


def _build_table() -> list[str]:
    """把分行表拍平成一维数组，并校验长度。"""
    flat = list("".join(_TABLE_ROWS))
    if len(flat) != TABLE_SIZE:
        # 长度不符说明表抄漏了，整表会错位。宁可不解码，也不能输出错字。
        raise RuntimeError(
            f"码表长度异常：{len(flat)}，应为 {TABLE_SIZE}。"
            "整表会错位，拒绝使用。请核对 _TABLE_ROWS。"
        )
    return flat


TABLE: list[str] = _build_table()
"""码位 → 字符。索引 = 码位 - CODE_ST。"""


# ============================================================
#  解码
# ============================================================
@dataclass
class DecodeResult:
    """一次解码的结果。"""

    text: str
    """解码后的文本。无法还原的私用区字符保持原样。"""

    replaced: int
    """被替换的字符数。"""

    unresolved: int
    """仍属私用区、未能还原的字符数。>0 表示解码不完整。"""

    def is_clean(self) -> bool:
        return self.unresolved == 0


def decode(text: str) -> DecodeResult:
    """把正文里的私用区字符还原为原文。

    逐字符处理：
      - 落在码位区间且表里有字 → 替换
      - 表里是 `?`（字体无字形）→ 保持原样
      - 不在区间 → 保持原样

    返回的 `unresolved` 是判定依据：**大于 0 说明还有没还原的字**，
    调用方应把它标为「乱码」而不是「成功」，避免错字进入后续分析。
    """
    out: list[str] = []
    replaced = 0

    for ch in text:
        cp = ord(ch)
        if CODE_ST <= cp <= CODE_ED:
            mapped = TABLE[cp - CODE_ST]
            if mapped != NO_GLYPH:
                out.append(mapped)
                replaced += 1
                continue
        out.append(ch)

    result = "".join(out)
    unresolved = count_pua(result)
    return DecodeResult(text=result, replaced=replaced, unresolved=unresolved)


def count_pua(text: str) -> int:
    """统计私用区字符数。用于解码前后的校验。"""
    return sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)


def has_pua(text: str) -> bool:
    """文本里是否还有私用区字符。"""
    return count_pua(text) > 0


# ============================================================
#  正文清洗
# ============================================================
# 番茄正文是 <p>段落</p> 拼成的
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)


def html_to_text(html: str) -> str:
    """把正文 HTML 转成纯文本，**保留段落与换行**。

    注意：不能简单地把所有标签删掉再拼成一行 —— 那会丢掉段落结构，
    拆书分析拿到的就是一整坨文本了。

    处理顺序：
      1. 把 <br> 换成换行
      2. 按 <p> 切段，段间用空行分隔
      3. 段内剩下的标签去掉，并把连续空白压成一个空格
    """
    if not html:
        return ""

    # 先处理 <br>
    work = _BR_RE.sub("\n", html)

    # 按 <p> 提取段落
    paras = _PARA_RE.findall(work)

    if not paras:
        # 没有 <p>（例如纯文本或别的结构），退化为去标签
        paras = [_TAG_RE.sub("", work)]

    cleaned: list[str] = []
    for p in paras:
        # 段内可能还有嵌套标签
        p = _TAG_RE.sub("", p)
        # 段内换行保留，但把行首行尾空格去掉
        lines = [ln.strip() for ln in p.splitlines()]
        block = "\n".join(ln for ln in lines if ln)
        # 把连续空格压成一个
        block = re.sub(r"[ \t\u3000]{2,}", " ", block).strip()
        if block:
            cleaned.append(block)

    return "\n\n".join(cleaned)


def extract_body(html: str) -> DecodeResult:
    """正文一步到位：剥标签（保留段落）+ 解码。

    这是抓取流程实际调用的入口。
    """
    plain = html_to_text(html)
    return decode(plain)


# ============================================================
#  自检
# ============================================================
def self_check() -> dict:
    """返回码表状态，供健康检查与界面展示。"""
    return {
        "table_size": len(TABLE),
        "expected_size": TABLE_SIZE,
        "code_range": [f"U+{CODE_ST:04X}", f"U+{CODE_ED:04X}"],
        "no_glyph_slots": sum(1 for c in TABLE if c == NO_GLYPH),
    }


__all__ = [
    "CODE_ST",
    "CODE_ED",
    "TABLE_SIZE",
    "NO_GLYPH",
    "TABLE",
    "DecodeResult",
    "decode",
    "count_pua",
    "has_pua",
    "html_to_text",
    "extract_body",
    "self_check",
]
