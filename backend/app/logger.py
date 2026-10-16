"""统一日志配置模块 - Uvicorn风格"""
import json
import logging
import re
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any, Optional


DEFAULT_LOG_MESSAGE_MAX_CHARS = 2000
DEFAULT_LOG_PREVIEW_MAX_CHARS = 300
SENSITIVE_LOG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


# ============================================================
#  敏感键名判定
#
#  旧实现是 `sensitive_key in lower_key` 的子串匹配：只要键名里
#  出现 "auth" 就会被整体打码，导致 author / authority 这类正常
#  业务字段被误伤，真实信息反而被掩盖。
#
#  现在改为三步：
#    1) 键名归一化（apiKey / api-key / ApiKey / APIKey → api_key）
#    2) 归一化后的精确匹配（SENSITIVE_LOG_KEYS + 显式补充表）
#    3) 以分隔符为界的词根兜底（xxx_token / token_xxx 这类命名变体）
#  子串匹配被彻底去掉，author 不再命中 auth。
# ============================================================

# 驼峰边界：小写/数字 后接大写，或 连续大写 后接「大写+小写」的首字母
# 后者用于 APIKey → API_Key 这种连续大写前缀。
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _normalize_key(key: Any) -> str:
    """把键名归一化为小写下划线形式：apiKey / api-key / ApiKey / APIKey → api_key"""
    s = _CAMEL_BOUNDARY.sub("_", str(key))
    return s.replace("-", "_").strip().lower()


# 精确匹配集合（归一化后）
_SENSITIVE_NORMALIZED: frozenset[str] = frozenset(_normalize_key(k) for k in SENSITIVE_LOG_KEYS)

# 显式补充：SENSITIVE_LOG_KEYS 里只有 auth / authorization 这类泛化词，
# 精确匹配后具体键名（apiKey、secret_key、session_token、xxx_password…）
# 会整个漏掉，因此把这些常见凭据键名补成同一张精确表。
_EXTRA_SENSITIVE: frozenset[str] = frozenset({
    "auth",
    "authorize",
    "authorization",
    "authenticate",
    "authenticated",
    "authed",
    "authed_at",
    "auth_at",
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "secret_key",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "token",
    "password",
    "passwd",
    "pwd",
    "pass",
    "passphrase",
    "cookie",
    "cookies",
    "set_cookie",
    "credential",
    "credentials",
    "bearer",
    "private_key",
    "signing_key",
    "session_secret",
    "session_secret_key",
    "secret_key_base",
    "smtp_password",
    "cover_api_key",
})

# 兜底词根：只有在「整体等于该词根」或「被下划线分隔成独立一段」时才判定敏感。
# 这样 author（无分隔符）与 authority 都不会被 auth 命中。
_BOUNDARY_ROOTS: tuple[str, ...] = (
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "key",
    "apikey",
    "cookie",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "bearer",
)


def _is_sensitive_key(key: Any) -> bool:
    """判断键名是否为敏感凭据字段：归一化 → 精确匹配 → 分隔符为界的词根兜底。"""
    n = _normalize_key(key)
    if n in _SENSITIVE_NORMALIZED or n in _EXTRA_SENSITIVE:
        return True
    for root in _BOUNDARY_ROOTS:
        if n == root or n.endswith("_" + root) or n.startswith(root + "_"):
            return True
    return False


# ============================================================
#  值级脱敏：无键名的裸字符串
# ============================================================
# 键名层脱敏（_sanitize_for_log）只对 dict 的 key 生效，够不着
# `safe_preview(response.text, 1000)` 这类整段上游响应体，
# 也够不着 _log_raw_response_body 按 ERROR 分片打出的原始 body。
# 实测泄漏样本（runtime/logs/wenshu.log，59 处 sk-SET_ME）：
#   {"error":{"message":"Incorrect API key provided: sk-SET_ME. ..."}}
# 即：API Key 出现在**值**里，与任何键名无关（日志里 "api_key": 形状 0 次）。
# 所以这里按值的形态做模式替换，作为键名层之外的第二道防线。
#
# 误伤控制（本任务核心难点）：
#   * 一律用「前置非字母数字」断言代替裸 \b —— \b 在 risk-assessment /
#     task-scheduler 内部不成立（前面是 i / a 这类词字符），所以这两个险例
#     天然不会命中；真正需要防的是**词首**的 sk-。
#   * sk- 分两档：长 body（>=16，真实 OpenAI/Anthropic 密钥长度）无条件匹配；
#     短 body（4~15）要求体内至少含一个大写字母 / 数字 / 下划线，
#     这样 sk-SET_ME（大写+下划线，实测泄漏样本）命中，而 sk-learn /
#     sk-etch 这类小写英文词不命中。
#   * 十六进制密钥要求两侧都不是标识符字符，避免吃掉「32 位 hex 命名的
#     文件名」这类正常 token。
#   * 不做泛化高熵检测：正常文本里的长英文单词、base64 图片片段误伤率太高。
_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI 风格长密钥：sk-xxx / sk-proj-xxx / sk-ant-xxx
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}(?![A-Za-z0-9])"), "sk-***REDACTED***"),
    # OpenAI 风格短占位密钥：sk-SET_ME / sk-LEAK12345（体内必须含大写/数字/下划线）
    (
        re.compile(
            r"(?<![A-Za-z0-9])sk-(?=[A-Za-z0-9_\-]{4,15}(?![A-Za-z0-9]))"
            r"(?=[A-Za-z0-9_\-]*[A-Z0-9_])[A-Za-z0-9_\-]+"
        ),
        "sk-***REDACTED***",
    ),
    # Google AI：AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{16,}(?![A-Za-z0-9])"), "AIza***REDACTED***"),
    # Bearer token
    (re.compile(r"(?i)(?<![A-Za-z0-9])(Bearer)\s+([A-Za-z0-9_\-\.=]{12,})"), r"\1 ***REDACTED***"),
    # 常见 key 形态：32 位以上 hex（session secret 等）
    (re.compile(r"(?<![0-9A-Za-z_\-])[0-9a-f]{32,}(?![0-9A-Za-z_\-])"), "***REDACTED_HEX***"),
    # 内联的 key=value / "key":"value" 形态（值里是长随机串）
    # 键名与分隔符之间允许一个引号：json 里是 `"password": "xxx"`，
    # 键名的收尾引号必须吃掉，否则整条规则在 JSON 上永远不命中。
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9])(api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|"
            r"secret[_-]?key|password|passwd|pwd)([\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9_\-\.=+/]{6,})"
        ),
        r"\1\2***REDACTED***",
    ),
)


def _redact_secret_values(text: str) -> str:
    """对裸字符串做值级密钥替换。

    只在上过模式的形态上动手，不做泛化的高熵检测 —— 后者误伤率太高
    （正常文本里的长英文单词、base64 图片片段）。
    替换结果不会再被任一模式重新命中，因此幂等。
    """
    if not text:
        return text
    out = text
    for pat, repl in _SECRET_VALUE_PATTERNS:
        out = pat.sub(repl, out)
    return out


# 只输出结构摘要、不输出正文的键（归一化后比较）
_SUMMARY_KEYS: frozenset[str] = frozenset({
    "content",
    "prompt",
    "system_prompt",
    "chapter_content",
    "messages",
    "arguments",
})


def _truncate_text(text: str, max_chars: Optional[int] = DEFAULT_LOG_PREVIEW_MAX_CHARS) -> str:
    """截断长文本，保留原始长度便于排查。

    这里也是**所有字符串出口的收口点**（safe_preview / safe_json_preview /
    UvicornFormatter.format 都经过它），所以在截断前先做值级脱敏。
    顺序必须是「先脱敏再截断」：密钥若跨在截断边界上，先截断会把它切成
    不匹配任何模式的残片，从而漏掉。
    """
    text = _redact_secret_values(text)
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated, length={len(text)}]"


def safe_preview(value: Any, max_chars: int = DEFAULT_LOG_PREVIEW_MAX_CHARS) -> str:
    """生成安全预览，避免长文本直接进入日志。"""
    if value is None:
        return "None"
    return _truncate_text(str(value), max_chars)


def _sanitize_for_log(value: Any, depth: int = 0) -> Any:
    """递归清理日志对象，避免敏感字段和正文全文输出。

    覆盖结构：dict（含键名精确匹配与别名后处理）、list/tuple、set/frozenset、
    以及带 __dict__ 的普通对象；字符串统一走 safe_preview 截断。
    """
    if depth > 4:
        return f"<{type(value).__name__}>"

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                sanitized[key_str] = "***REDACTED***"
            elif _normalize_key(key_str) in _SUMMARY_KEYS:
                sanitized[key_str] = summarize_log_value(item)
            else:
                sanitized[key_str] = _sanitize_for_log(item, depth + 1)
        return sanitized

    if isinstance(value, (list, tuple)):
        return [_sanitize_for_log(item, depth + 1) for item in value[:10]] + (
            [f"... {len(value) - 10} more items"] if len(value) > 10 else []
        )

    # set / frozenset：元素本身没有键名，无法按键名判定。
    # 元素若是 dict 仍递归打码；若是标量则按「未知来源字符串」处理，
    # 走 safe_preview 截断，避免长正文进日志。
    if isinstance(value, (set, frozenset)):
        items = list(value)
        return {
            _sanitize_for_log(item, depth + 1)
            if isinstance(item, (dict, list, tuple, set, frozenset))
            else safe_preview(str(item))
            for item in items[:10]
        }

    if isinstance(value, str):
        return safe_preview(value)

    # 普通对象（数据类实例等）：有 __dict__ 就递归其字段，命中敏感键名同样打码
    if hasattr(value, "__dict__") and getattr(value, "__dict__", None):
        return _sanitize_for_log(vars(value), depth + 1)

    return value


def safe_json_preview(value: Any, max_chars: int = 500) -> str:
    """生成可读 JSON 安全预览，无法序列化时退回字符串预览。"""
    try:
        text = json.dumps(_sanitize_for_log(value), ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return _truncate_text(text, max_chars)


def summarize_log_value(value: Any) -> str:
    """返回值的结构摘要，不输出正文内容。"""
    if value is None:
        return "None"
    if isinstance(value, str):
        return f"str(length={len(value)})"
    if isinstance(value, dict):
        fields = []
        for key, item in list(value.items())[:20]:
            if isinstance(item, str):
                fields.append(f"{key}:str(length={len(item)})")
            elif isinstance(item, (list, tuple, set)):
                fields.append(f"{key}:{type(item).__name__}(length={len(item)})")
            elif isinstance(item, dict):
                fields.append(f"{key}:dict(keys={len(item)})")
            else:
                fields.append(f"{key}:{type(item).__name__}")
        suffix = f", +{len(value) - 20} keys" if len(value) > 20 else ""
        return f"dict(keys={len(value)}, fields=[{', '.join(fields)}{suffix}])"
    if isinstance(value, (list, tuple, set)):
        item_types = sorted({type(item).__name__ for item in value})
        return f"{type(value).__name__}(length={len(value)}, item_types={item_types})"
    return type(value).__name__


class UvicornFormatter(logging.Formatter):
    """Uvicorn风格的日志格式化器"""
    
    # 日志级别颜色（ANSI转义码）
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
    }
    RESET = '\033[0m'
    
    def __init__(self, use_colors: bool = True, max_message_chars: int = DEFAULT_LOG_MESSAGE_MAX_CHARS):
        """
        初始化格式化器
        
        Args:
            use_colors: 是否使用颜色（控制台输出使用，文件输出不使用）
        """
        super().__init__()
        self.use_colors = use_colors
        self.max_message_chars = max_message_chars
    
    def format(self, record):
        """格式化日志记录为 Uvicorn 风格"""
        # 获取日志级别名称
        levelname = record.levelname
        
        # 添加颜色（如果启用且终端支持）
        if self.use_colors and sys.stderr.isatty():
            colored_level = f"{self.COLORS.get(levelname, '')}{levelname}{self.RESET}"
        else:
            colored_level = levelname
        
        # 添加请求追踪ID（如果存在）
        request_id = getattr(record, 'request_id', None)
        request_id_str = f" [{request_id}]" if request_id else ""
        
        # 格式化时间戳 (YYYY-MM-DD HH:MM:SS)
        timestamp = self.formatTime(record, self.datefmt)
        
        message = _truncate_text(record.getMessage(), self.max_message_chars)

        # 异常堆栈必须保留：getMessage() 不含 exc_info，
        # 丢掉的话 ERROR 行后面永远没有 Traceback，
        # 排障时只能看到一句 'NoneType' has no attribute 'strip'
        # 而不知道崩在哪一行（本次排障实测踩坑）。
        if record.exc_info and record.exc_info[0] is not None:
            import traceback as _tb
            tb_text = "".join(_tb.format_exception(*record.exc_info))
            # 堆栈也截断，但给足空间：定位行号通常在前几行
            message = f"{message}\n{_truncate_text(tb_text, max(self.max_message_chars, 8000))}"

        # Uvicorn风格格式: INFO:     [2024-01-01 12:00:00] module_name - message [request_id]
        # 注意：INFO后面有5个空格，保持对齐
        return f"{colored_level}:     [{timestamp}] {record.name}{request_id_str} - {message}"


# 全局标志，防止重复初始化
_logging_configured = False

def setup_logging(
    level: str = "INFO",
    log_to_file: bool = False,
    log_file_path: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 30,
    message_max_chars: int = DEFAULT_LOG_MESSAGE_MAX_CHARS,
):
    """
    配置统一的 Uvicorn 风格日志系统
    
    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file: 是否输出到文件
        log_file_path: 日志文件路径
        max_bytes: 单个日志文件最大字节数（默认10MB）
        backup_count: 保留的备份文件数量（默认30个）
        message_max_chars: 单条日志消息最大字符数（默认2000）
    """
    global _logging_configured
    
    # 如果已经配置过，直接返回
    if _logging_configured:
        return logging.getLogger()
    
    # 获取根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # 清除已有的处理器，避免重复
    root_logger.handlers.clear()
    
    if message_max_chars <= 0:
        message_max_chars = DEFAULT_LOG_MESSAGE_MAX_CHARS

    # 1. 创建控制台处理器（带颜色）
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_formatter = UvicornFormatter(use_colors=True, max_message_chars=message_max_chars)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # 2. 创建文件处理器（如果启用）
    if log_to_file and log_file_path:
        # 确保日志目录存在
        log_file = Path(log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用RotatingFileHandler实现日志轮转
        file_handler = RotatingFileHandler(
            filename=log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, level.upper()))
        
        # 文件日志不使用颜色
        file_formatter = UvicornFormatter(use_colors=False, max_message_chars=message_max_chars)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        
        # 记录日志配置信息
        root_logger.info(f"日志文件输出已启用: {log_file_path}")
        root_logger.info(f"日志轮转配置: 单文件最大{max_bytes / 1024 / 1024:.1f}MB, 保留{backup_count}个备份")
        root_logger.info(f"单条日志消息最大长度: {message_max_chars}字符")
    
    # 配置第三方库的日志级别
    _configure_third_party_loggers()
    
    # 标记为已配置
    _logging_configured = True
    
    return root_logger


def _configure_third_party_loggers():
    """配置第三方库的日志级别"""
    # SQLAlchemy - 禁用SQL日志
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.pool').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.dialects').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.orm').setLevel(logging.WARNING)
    
    # aiosqlite - 异步SQLite，禁用DEBUG日志
    logging.getLogger('aiosqlite').setLevel(logging.WARNING)
    
    # Watchfiles - 开发时的文件监控，降低级别
    logging.getLogger('watchfiles').setLevel(logging.WARNING)
    
    # httpx/httpcore - HTTP客户端，禁用DEBUG日志
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    
    # openai/anthropic - AI客户端库
    logging.getLogger('openai').setLevel(logging.WARNING)
    logging.getLogger('anthropic').setLevel(logging.WARNING)
    
    # 应用模块 - AI 统计日志需要保留 INFO 级别输出
    logging.getLogger('app.services.ai_service').setLevel(logging.INFO)
    logging.getLogger('app.api.wizard').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志器
    
    Args:
        name: 日志器名称，通常使用 __name__
        
    Returns:
        配置好的日志器实例
    """
    return logging.getLogger(name)
