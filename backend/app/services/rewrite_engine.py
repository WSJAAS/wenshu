"""文本重塑引擎桥接层。

把仓库根目录 `engine/` 下的改写与体检能力接入后端。

`engine/` 是独立的纯 Python 模块集合，只依赖标准库（改写与体检部分）
与 litellm（模型调用部分），不依赖 FastAPI 或本项目的任何代码，
因此这里只做一件事：把它的目录挂到 sys.path，然后转发调用。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)

# engine/ 位于仓库根目录。
# 本文件路径为 <root>/backend/app/services/rewrite_engine.py，
# 因此向上四级（parents[3]）才是 <root>。
ENGINE_DIR = Path(__file__).resolve().parents[3] / "engine"


def _ensure_engine_on_path() -> bool:
    """把 engine/ 加入导入路径，返回目录是否可用。"""
    if not ENGINE_DIR.is_dir():
        logger.warning(f"未找到引擎目录: {ENGINE_DIR}")
        return False
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    return True


@lru_cache(maxsize=1)
def engine_available() -> bool:
    """引擎是否可用（只探测一次）。"""
    if not _ensure_engine_on_path():
        return False
    try:
        import local_rewriter  # noqa: F401
        import text_diagnostics  # noqa: F401
    except Exception as e:
        logger.error(f"引擎导入失败: {e}")
        return False
    return True


# ============================================================
#  本地规则改写（不调用模型）
# ============================================================
def rewrite_local(text: str, mode: str = "novel") -> dict[str, Any]:
    """用本地规则表改写文本。

    输出完全由输入决定，不调用任何模型，因此离线可用。
    """
    if not engine_available():
        return {"ok": False, "error": "引擎不可用"}

    from local_rewriter import rewrite_local as _rewrite

    try:
        result = _rewrite(text, mode)
    except Exception as e:
        logger.error(f"本地改写失败: {e}")
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "text": result.text,
        "applied": list(getattr(result, "applied", []) or []),
        "stats": dict(getattr(result, "stats", {}) or {}),
    }


# ============================================================
#  文本体检
# ============================================================
def diagnose(text: str, mode: str = "novel") -> dict[str, Any]:
    """对文本做体检，返回指标字典。"""
    if not engine_available():
        return {"ok": False, "error": "引擎不可用"}

    try:
        if mode == "paper":
            from text_diagnostics import PaperDiagnostics

            result = PaperDiagnostics.scan_ai_metrics(text)
        else:
            from text_diagnostics import NovelDiagnostics

            result = NovelDiagnostics.scan_novel_metrics(text)
    except Exception as e:
        logger.error(f"体检失败: {e}")
        return {"ok": False, "error": str(e)}

    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    return {"ok": True, "metrics": payload}


def verify_integrity(original: str, rewritten: str) -> dict[str, Any]:
    """校验改写前后的事实完整性（仅论文模式有对应实现）。"""
    if not engine_available():
        return {"ok": False, "error": "引擎不可用"}
    try:
        from text_diagnostics import PaperDiagnostics

        return {"ok": True, "integrity": PaperDiagnostics.verify_integrity(original, rewritten)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
#  引擎信息
# ============================================================
def engine_info() -> dict[str, Any]:
    """返回引擎可用状态与路径，供状态接口展示。"""
    return {
        "available": engine_available(),
        "dir": str(ENGINE_DIR),
    }
