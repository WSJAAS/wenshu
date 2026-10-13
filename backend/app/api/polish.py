"""润色 API —— AI 去味与文本重塑。

两类能力：

1. **模型改写**：走 AI_DENOISING 提示词模板，调用用户配置的模型。
2. **本地规则改写**：走 engine/local_rewriter.py 的规则表，不调用模型，离线可用。

原稿与候选稿严格分离：本接口只返回候选稿，绝不回写原稿。

上游该路由存在于 `app/api/polish.py`，但从未在 main.py 注册，实际不可达；
本工程补齐注册，并扩展了体检、本地改写、人工采纳与版本回退。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logger import get_logger
from app.models.generation_history import GenerationHistory
from app.services.ai_service import AIService
from app.services.prompt_service import PromptService, prompt_service
from app.services.rewrite_engine import (
    diagnose,
    engine_info,
    rewrite_local,
    verify_integrity,
)
from app.api.settings import get_user_ai_service
from app.config import DATA_DIR

router = APIRouter(prefix="/polish", tags=["文本重塑"])
logger = get_logger(__name__)

# 采纳稿落盘目录：放在数据目录下，不写进代码目录
ACCEPT_DIR = DATA_DIR / "accepted"


# ============================================================
#  请求 / 响应模型
# ============================================================
class PolishRequest(BaseModel):
    """改写请求。

    兼容两种字段名：前端历史用法传 text，上游 schema 用 original_text。
    两者都接受，优先取 original_text。
    """
    original_text: Optional[str] = Field(None, description="原始文本")
    text: Optional[str] = Field(None, description="原始文本（兼容字段）")
    mode: str = Field("novel", description="novel / paper")
    style: Optional[str] = Field(None, description="风格提示")
    project_id: Optional[str] = Field(None, description="项目ID（可选，用于记录历史）")
    provider: Optional[str] = Field(None, description="AI提供商")
    model: Optional[str] = Field(None, description="AI模型")
    temperature: Optional[float] = Field(0.8, description="温度参数，建议 0.7-0.9")
    use_model: bool = Field(True, description="是否调用模型；false 则走本地规则")
    max_retries: int = Field(1, description="模型失败重试次数上限")

    def resolved_text(self) -> str:
        return (self.original_text or self.text or "").strip()


class PolishResponse(BaseModel):
    original_text: str
    polished_text: str
    word_count_before: int
    word_count_after: int
    engine: str
    engine_note: Optional[str] = None
    applied_rules: Optional[list] = None
    diagnostics_before: Optional[dict] = None
    diagnostics_after: Optional[dict] = None
    integrity: Optional[dict] = None


class AcceptRequest(BaseModel):
    text: str
    mode: str = "novel"
    task_id: Optional[str] = None


class DiagnoseRequest(BaseModel):
    text: str
    mode: str = "novel"


# ============================================================
#  改写
# ============================================================
@router.post("", response_model=PolishResponse, summary="文本重塑（原稿→候选稿）")
async def polish_text(
    request: PolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service),
):
    """把文本改写为候选稿。**不改动原稿。**

    原稿与候选稿分离是本接口的核心约束：改写失败时不会覆盖原文，
    调用方拿到的是独立的候选稿，需人工采纳后才进入保存流程。
    """
    original_text = request.resolved_text()
    if not original_text:
        raise HTTPException(status_code=400, detail="文本为空")

    user_id = getattr(http_request.state, "user_id", None)
    engine_name = "local_rules"
    note: Optional[str] = None
    applied: list = []

    # 改写前体检
    diag_before = diagnose(original_text, request.mode)
    diagnostics_before = diag_before.get("metrics") if diag_before.get("ok") else None

    polished_text = ""
    if request.use_model:
        try:
            template = await PromptService.get_template("AI_DENOISING", user_id, db)
            prompt = PromptService.format_prompt(template, original_text=original_text)

            polished_text = await user_ai_service.generate_text(
                prompt=prompt,
                provider=request.provider,
                model=request.model,
                temperature=request.temperature,
                max_tokens=max(len(original_text) * 2, 1024),
            )
            engine_name = "model"
        except Exception as e:
            logger.error(f"模型改写失败，回退本地规则: {e}")
            note = f"模型改写失败（{type(e).__name__}），已回退本地规则改写。"
            polished_text = ""
    else:
        note = "按请求使用本地规则改写，未调用模型。"

    if not polished_text.strip():
        # 回退：本地规则改写，输出完全由输入决定
        local = rewrite_local(original_text, request.mode)
        if not local.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"改写失败：模型与本地引擎均不可用（{local.get('error')}）",
            )
        polished_text = local["text"]
        applied = local.get("applied", [])
        if engine_name == "model":
            engine_name = "local_rules"
        if not note:
            note = "本地规则改写：逐条应用规则表，能力有限，不等同于模型改写。"

    diag_after = diagnose(polished_text, request.mode)
    diagnostics_after = diag_after.get("metrics") if diag_after.get("ok") else None

    integrity = None
    if request.mode == "paper":
        vi = verify_integrity(original_text, polished_text)
        integrity = vi.get("integrity") if vi.get("ok") else None

    # 记录生成历史（仅当提供了项目）
    if request.project_id:
        try:
            history = GenerationHistory(
                project_id=request.project_id,
                generation_type="polish",
                prompt=f"原文: {original_text[:100]}...",
                result=polished_text,
                provider=request.provider or "default",
                model=request.model or "default",
            )
            db.add(history)
            await db.commit()
        except Exception as e:
            logger.warning(f"生成历史写入失败（不影响结果）: {e}")

    return PolishResponse(
        original_text=original_text,
        polished_text=polished_text,
        word_count_before=len(original_text),
        word_count_after=len(polished_text),
        engine=engine_name,
        engine_note=note,
        applied_rules=applied[:40] if applied else None,
        diagnostics_before=diagnostics_before,
        diagnostics_after=diagnostics_after,
        integrity=integrity,
    )


@router.post("/batch", summary="批量文本重塑")
async def polish_batch(
    texts: list[str],
    project_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    use_model: bool = True,
    mode: str = "novel",
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    user_ai_service: AIService = Depends(get_user_ai_service),
):
    """逐条改写多个文本。每条独立处理，互不复用结果。"""
    if not texts:
        raise HTTPException(status_code=400, detail="文本列表为空")

    user_id = getattr(http_request.state, "user_id", None) if http_request else None
    results = []

    for idx, text in enumerate(texts):
        text = (text or "").strip()
        if not text:
            results.append({"index": idx, "original": "", "polished": "", "skipped": True})
            continue

        polished = ""
        if use_model:
            try:
                template = await PromptService.get_template("AI_DENOISING", user_id, db)
                prompt = PromptService.format_prompt(template, original_text=text)
                polished = await user_ai_service.generate_text(
                    prompt=prompt, provider=provider, model=model
                )
            except Exception as e:
                logger.error(f"第 {idx + 1} 条模型改写失败: {e}")
                polished = ""

        if not polished.strip():
            local = rewrite_local(text, mode)
            polished = local.get("text", "") if local.get("ok") else ""

        results.append({
            "index": idx,
            "original": text,
            "polished": polished,
            "word_count_before": len(text),
            "word_count_after": len(polished),
        })

    return {"total": len(results), "results": results}


# ============================================================
#  体检
# ============================================================
@router.post("/diagnose", summary="文本体检（不改写）")
async def polish_diagnose(request: DiagnoseRequest):
    """只做指标体检，不触碰文本。"""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="文本为空")
    result = diagnose(request.text, request.mode)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=f"体检失败：{result.get('error')}")
    return {"metrics": result["metrics"]}


@router.post("/rewrite-local", summary="本地规则改写（不调用模型）")
async def polish_rewrite_local(request: DiagnoseRequest):
    """纯本地规则改写，离线可用，输出完全由输入决定。"""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="文本为空")
    result = rewrite_local(request.text, request.mode)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=f"改写失败：{result.get('error')}")
    return {
        "text": result["text"],
        "applied_count": len(result.get("applied", [])),
        "applied": result.get("applied", [])[:40],
        "stats": result.get("stats", {}),
    }


# ============================================================
#  人工采纳与版本回退
# ============================================================
@router.post("/accept", summary="人工采纳候选稿")
async def polish_accept(request: AcceptRequest):
    """把候选稿落盘为采纳版，并保留版本链供回退。

    只写采纳目录，不触碰原稿。
    """
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")

    ACCEPT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{request.mode}_{stamp}" + (f"_{request.task_id}" if request.task_id else "")
    target = ACCEPT_DIR / f"{name}.txt"
    target.write_text(text, encoding="utf-8")

    history = _load_history(request.mode)
    entry = {
        "name": name,
        "path": str(target),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "chars": len(text),
        "task_id": request.task_id or "",
    }
    history.append(entry)
    _save_history(request.mode, history)

    previous = history[-2] if len(history) >= 2 else None
    return {
        "ok": True,
        "saved": entry,
        "previous": previous,
        "can_rollback": previous is not None,
        "versions": len(history),
    }


@router.post("/rollback", summary="回退到上一版")
async def polish_rollback(mode: str = "novel"):
    """取回上一版采纳稿。"""
    history = _load_history(mode)
    if len(history) < 2:
        raise HTTPException(status_code=400, detail="只有一版记录，没有可回退的上一版")

    previous = history[-2]
    path = DATA_DIR / "accepted" / f"{previous['name']}.txt" if not previous.get("path") else None
    prev_path = path if path else __import__("pathlib").Path(previous["path"])
    if not prev_path.is_file():
        raise HTTPException(status_code=404, detail=f"上一版文件已不存在: {prev_path}")

    return {
        "ok": True,
        "text": prev_path.read_text(encoding="utf-8"),
        "version": previous,
    }


@router.get("/versions", summary="列出采纳历史")
async def polish_versions(mode: str = "novel"):
    """列出某个模式的采纳版本。"""
    history = _load_history(mode)
    return {"total": len(history), "items": history}


# ============================================================
#  引擎信息
# ============================================================
@router.get("/engine", summary="引擎可用状态")
async def polish_engine():
    """报告文本重塑引擎是否可用及其位置。"""
    return engine_info()


# ============================================================
#  内部：版本历史读写
# ============================================================
def _history_file(mode: str):
    return ACCEPT_DIR / f"{mode}_history.json"


def _load_history(mode: str) -> list:
    import json

    f = _history_file(mode)
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(mode: str, history: list) -> None:
    import json

    _history_file(mode).write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
