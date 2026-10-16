"""对外错误信息的安全化处理。

## 问题

``detail=f"...{str(e)}"`` 会把 Python 异常原文回显给 HTTP 客户端。
部分异常的 ``str()`` 会带出可执行文件绝对路径、数据目录、子进程日志尾部，
既是信息泄漏面，也让前端展示不稳定的内部实现细节。

## 对策

- 异常原文写入日志，带一个短 ``error_id`` 便于对照排查
- 回给客户端的是稳定的业务文案 + 该 ``error_id``
- 调用方可自定义 ``public`` 文案，保留「错误信息对用户友好」的能力
"""
from __future__ import annotations

import uuid

from app.logger import get_logger

logger = get_logger(__name__)


def safe_detail(
    exc: BaseException | str,
    *,
    public: str = "操作失败，请稍后重试或联系管理员",
    context: str = "",
) -> str:
    """把异常转成可安全回显给客户端的字符串。

    :param exc: 捕获到的异常，或已有字符串
    :param public: 面向用户的稳定文案
    :param context: 日志里的操作描述（如「创建伏笔失败」）
    :return: ``"<public>（错误编号 xxxxxxxx）"``
    """
    error_id = uuid.uuid4().hex[:8]
    logger.error(
        "%s [error_id=%s] %s: %s",
        context or "请求处理失败",
        error_id,
        type(exc).__name__ if isinstance(exc, BaseException) else "N/A",
        exc,
        exc_info=isinstance(exc, BaseException),
    )
    return f"{public}（错误编号 {error_id}）"
