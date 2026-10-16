"""AI 服务配置管理"""
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HTTPClientConfig:
    """HTTP 客户端配置"""
    connect_timeout: float = 90.0
    read_timeout: float = 300.0
    write_timeout: float = 90.0
    pool_timeout: float = 90.0
    max_keepalive_connections: int = 50
    max_connections: int = 100
    keepalive_expiry: float = 60.0


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 0.2
    max_delay: float = 10.0
    exponential_base: int = 2
    non_retryable_status_codes: tuple = field(default_factory=lambda: (400, 401, 403, 404, 422))


@dataclass
class RateLimitConfig:
    """限流配置"""
    max_concurrent_requests: int = 5
    request_delay: float = 0.2


@dataclass
class AIClientConfig:
    """AI 客户端完整配置"""
    http: HTTPClientConfig = field(default_factory=HTTPClientConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)


# 单次输出上限：未命中按 DEFAULT 兜底。
#
# 只收录能核实的官方上限值；查不到的一律不入表、走 DEFAULT 兜底。
# 不要为了"表看起来完整"编数字：一个偏低的猜测值会重新引入静默截断
# （比上游返回 400 更难发现），那正是本次要修的问题。
MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    # OpenAI（platform.openai.com/docs/models）
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4.1": 32768,
    "gpt-4.1-mini": 32768,
    "gpt-4.1-nano": 32768,
    "o4-mini": 100000,
    "gpt-4-turbo": 4096,
    "gpt-3.5-turbo": 4096,
    # DeepSeek（api-docs.deepseek.com）
    "deepseek-chat": 8192,
    "deepseek-reasoner": 65536,
    # Gemini（ai.google.dev/gemini-api/docs/models）
    # 注意：数据库实际在用的 gemini-3.8-flash 不在此表内 —— 它是中转站
    # （166068.xyz）的命名，真实上限无法核实，按 DEFAULT 兜底处理。
    "gemini-2.0-flash": 8192,
    "gemini-2.5-flash": 65536,
    "gemini-2.5-pro": 65536,
    "gemini-1.5-flash": 8192,
    "gemini-1.5-pro": 8192,
    # Anthropic（docs.anthropic.com）
    "claude-3-5-sonnet": 8192,
    "claude-3-5-sonnet-20241022": 8192,
    "claude-3-7-sonnet": 64000,
    "claude-sonnet-4": 64000,
    "claude-opus-4": 32000,
    # 其他常见（通义千问 Dashscope / 智谱 Z.AI）
    "qwen-max": 8192,
    "glm-4": 4096,
}


# 单次输出上限：未命中按 DEFAULT 兜底。
#
# 为什么 DEFAULT 是 32768 而不是更低：
#   - 它只在"模型不在表里"时生效，用户在 Settings 可自由填任意模型名，
#     表永远不可能覆盖全
#   - 定得太低会造成**静默截断**（长章节被砍掉一半却没报错），比上游返回 400 更难发现
#   - 本项目写正文路径自身上限是 16000（chapters.py 的 max(2000, min(..., 16000))），
#     32768 不会误伤它
#   - 上游若真不支持该值，会返回 400（显式失败），比静默截断好
DEFAULT_MAX_OUTPUT_TOKENS: int = 32768


# --- 收敛告警降噪 ---
#
# 存量脏值往往是**固定的**（例如 DB 里写死的 max_tokens=1000000），
# 每次调用都打一条 warning 会把日志刷屏，真正的异常反而被淹没。
# 这里按 (model, 请求值, 生效上限) 三元组去重，同一组合只告警一次。
#
# 线程安全权衡：CPython 下 set.add / in 受 GIL 保护本身是原子的，
# 单独用裸 set 也够用；这里仍加一把 Lock，代价可忽略，
# 换来的是在"检查-写入"这个复合序列上语义明确，不依赖 GIL 实现细节。
# 缓存规模有界：键来自模型名与 token 数的组合，取值空间很小，不会无限增长。
_WARNED_CLAMP_COMBOS: set = set()
_WARNED_CLAMP_LOCK = threading.Lock()


def should_warn_max_tokens_clamp(model: str, requested, limit: int) -> bool:
    """同一 (model, requested, limit) 组合第一次调用返回 True，之后返回 False。"""
    key = (model, requested, limit)
    with _WARNED_CLAMP_LOCK:
        if key in _WARNED_CLAMP_COMBOS:
            return False
        _WARNED_CLAMP_COMBOS.add(key)
        return True


def clamp_max_output_tokens(model: str, max_tokens) -> int:
    """把请求的 max_tokens 收敛到模型上限；max_tokens 为 None 时返回该模型上限。"""
    limit = MODEL_MAX_OUTPUT_TOKENS.get(model, DEFAULT_MAX_OUTPUT_TOKENS)
    return min(max_tokens, limit) if max_tokens is not None else limit


# 全局默认配置
default_config = AIClientConfig()
