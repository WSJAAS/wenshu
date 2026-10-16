"""流式工具编排递归上限回归测试（docs/待办.md TODO-009 / 审计 06 P0-5、G2-1）。

## 在测什么

三个 provider（`openai_provider` / `gemini_provider` / `anthropic_provider`）的
`_generate_with_tools` 都是**自递归**，且原实现**没有任何 depth / max_rounds**：
递归与否只取决于模型是否继续返回 `tool_calls`。模型持续返回工具调用时，
递归永不停止，`messages` 每轮 append 也只增不减。

修完后有两道可验证的行为：

| 行为 | 断言 |
| :--- | :--- |
| 递归有上限 | 底层 `chat_completion_stream` 调用次数 ≤ `max_rounds + 1` |
| 收口轮强制关工具 | 最后一次请求 `tools is None` 且 `tool_choice == "none"` |
| 生成器不挂死 | 用例能正常迭代到结束（不是超时被 kill） |
| 正常路径不受影响 | 一次 tool_calls + 一次纯文本时，最终文本完整 yield 出来 |

另外 `ai_service.generate_text_stream` 的 `mcp_max_rounds` 原本是 **silent no-op**
（签名收下、docstring 声称生效、函数体从不读取），这里也一并钉住「真的下传」。

## 为什么用替身 client 而不是真 httpx

要验的是「编排的终止性」，不是 HTTP。真发请求既需要密钥也复现不了
「永远返回 tool_calls」这个病态输入。所以 provider 直接实例化（构造参数就是 client，
存下来不做校验），把 `chat_completion_stream` 换成可控的 async generator。

替身里带一个**熔断计数器**：万一递归上限被写坏，替身会在若干轮后主动抛错，
让用例**快速失败**而不是把 pytest 挂死 —— 否则这个缺陷的表现是「测试永远不返回」。
"""
from __future__ import annotations

import asyncio

import pytest

# 必须**先**导入 app.database：它会在 `Base` 建好之后完整导入 app.models。
# 若先导入 app.mcp，链路会变成
#   app.mcp.status_sync → app.models.mcp_plugin → app.models.project → app.database
#   → app.models（此时仍半初始化）→ ImportError: cannot import name 'Project'
# 这是既有的模块加载顺序耦合，不是本次改动引入的；测试侧按正确顺序导入即可。
import app.database  # noqa: F401  （导入副作用：解除 app.models 循环导入）

from app.mcp import mcp_client
from app.services.ai_providers.anthropic_provider import AnthropicProvider
from app.services.ai_providers.gemini_provider import GeminiProvider
from app.services.ai_providers.openai_provider import OpenAIProvider

# 熔断阈值：正常上限是 max_rounds + 1，超出这个余量说明终止逻辑失效。
RUNAWAY_CALL_LIMIT = 20

TOOL_CALL_CHUNK_RECURSIVE = {
    "tool_calls": [
        {
            "id": "call_demo",
            "type": "function",
            "function": {"name": "demo_plugin.echo", "arguments": "{}"},
        }
    ],
    "done": True,
    "finish_reason": "tool_calls",
}


class StubStreamClient:
    """可控的流式客户端替身。

    script 是一个可调用对象: `(call_index, kwargs) -> list[chunk]`。
    每次 `chat_completion_stream` 被调用都会记下 kwargs（含 tools / tool_choice /
    messages），这就是「底层请求次数」与「收口轮有没有关掉工具」的证据来源。
    """

    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = script

    async def chat_completion_stream(self, **kwargs):
        if len(self.calls) >= RUNAWAY_CALL_LIMIT:
            raise AssertionError(
                f"底层流式请求次数超过熔断阈值 {RUNAWAY_CALL_LIMIT}："
                "递归上限失效（TODO-009 回归）"
            )
        self.calls.append(kwargs)
        for chunk in self._script(len(self.calls) - 1, kwargs):
            yield chunk


def _always_tool_calls(call_index: int, kwargs: dict) -> list[dict]:
    """病态输入：每一轮都只返回工具调用，永远不给正文。"""
    return [dict(TOOL_CALL_CHUNK_RECURSIVE)]


def _tool_calls_then_text(call_index: int, kwargs: dict) -> list[dict]:
    """正常输入：首轮调工具，收口轮给正文。"""
    if call_index == 0:
        return [dict(TOOL_CALL_CHUNK_RECURSIVE)]
    return [{"content": "最终答案"}, {"done": True, "finish_reason": "stop"}]


# 三个 provider 的构造方式一致（只吃一个 client），但调用签名不同：
# openai 无 system_prompt，gemini/anthropic 有。用工厂统一差异。
PROVIDER_FACTORIES = {
    "openai": lambda client: OpenAIProvider(client),
    "gemini": lambda client: GeminiProvider(client),
    "anthropic": lambda client: AnthropicProvider(client),
}


async def _drain(provider, client, *, max_rounds: int, tools: list) -> list:
    """把 provider.generate_stream 迭代干净，返回 yield 出来的原始块。

    用 `asyncio.wait_for` 兜一层超时：即使熔断在替身之外的地方被绕过，
    用例也会以失败结束，而不是挂住整个 pytest 会话。
    """
    async def _consume():
        kwargs = dict(
            prompt="测试提问",
            model="test-model",
            temperature=0.7,
            max_tokens=1024,
            tools=tools,
            user_id="user-1",
            max_rounds=max_rounds,
        )
        # gemini / anthropic 的 generate_stream 接受 system_prompt，openai 忽略多余参数
        if not isinstance(provider, OpenAIProvider):
            kwargs["system_prompt"] = "你是测试助手"

        chunks = []
        async for chunk in provider.generate_stream(**kwargs):
            chunks.append(chunk)
        return chunks

    return await asyncio.wait_for(_consume(), timeout=10)


@pytest.fixture(autouse=True)
def _stub_mcp(monkeypatch):
    """把 MCP 工具执行换成内存假实现。

    被测的是递归编排，不是 MCP 连接；真去连插件会引入不确定的外部依赖。
    provider 内部是 `from app.mcp import mcp_client` 再取属性，
    因此替换单例上的方法即可生效。
    """

    async def _fake_batch_call_tools(user_id, tool_calls, **kwargs):
        return [
            {
                "tool_call_id": tc.get("id", "call_demo"),
                "role": "tool",
                "name": tc["function"]["name"],
                "content": "工具结果-占位",
                "success": True,
            }
            for tc in tool_calls
        ]

    def _fake_build_tool_context(tool_results, format="markdown"):
        return "## 工具调用结果\n占位内容"

    monkeypatch.setattr(mcp_client, "batch_call_tools", _fake_batch_call_tools)
    monkeypatch.setattr(mcp_client, "build_tool_context", _fake_build_tool_context)
    yield


# ============================================================
#  核心用例：模型永远返回 tool_calls，递归必须有上限
# ============================================================
@pytest.mark.parametrize("provider_name", sorted(PROVIDER_FACTORIES))
def test_recursive_tool_calls_bounded_by_max_rounds(provider_name):
    tools = [{"type": "function", "function": {"name": "demo_plugin.echo"}}]
    client = StubStreamClient(_always_tool_calls)
    provider = PROVIDER_FACTORIES[provider_name](client)
    max_rounds = 3

    chunks = asyncio.run(_drain(provider, client, max_rounds=max_rounds, tools=tools))

    # 1) 底层请求次数有上限：max_rounds 轮工具调用 + 1 次收口请求。
    assert len(client.calls) <= max_rounds + 1, (
        f"{provider_name}: 底层请求 {len(client.calls)} 次，超过 max_rounds+1"
    )
    assert len(client.calls) == max_rounds + 1, (
        f"{provider_name}: 病态输入下应当正好用满 {max_rounds}+1 次请求，"
        f"实际 {len(client.calls)} 次"
    )

    # 2) 最后一次必须是「关掉工具的收口请求」。
    last = client.calls[-1]
    assert last.get("tool_choice") == "none", (
        f"{provider_name}: 收口轮 tool_choice 应为 'none'，实际 {last.get('tool_choice')!r}"
    )
    assert last.get("tools") is None, (
        f"{provider_name}: 收口轮不应再携带 tools，实际 {last.get('tools')!r}"
    )

    # 3) 收口之前每一轮都还允许工具（tool_choice="auto"），证明上限不是靠一开始就禁用工具。
    assert all(call.get("tool_choice") == "auto" for call in client.calls[:-1]), (
        f"{provider_name}: 收口前的请求应保持 tool_choice='auto'"
    )

    # 4) 生成器正常结束（没有异常、没有被超时打断），且不以 content 之外的东西充数。
    assert isinstance(chunks, list)


@pytest.mark.parametrize("provider_name", sorted(PROVIDER_FACTORIES))
def test_max_rounds_from_ai_service_is_plumbed_down(provider_name):
    """`ai_service.generate_text_stream` 收到的 mcp_max_rounds 必须真正生效。

    修复前该参数是 silent no-op：签名收下、docstring 声称生效、函数体从不读取。
    这里用 `max_rounds=1` 触发更快收敛，若参数仍被丢弃，provider 会退回全局默认 3，
    请求次数会明显多于 1+1。
    """
    from app.services.ai_service import AIService

    tools = [{"type": "function", "function": {"name": "demo_plugin.echo"}}]
    client = StubStreamClient(_always_tool_calls)
    provider = PROVIDER_FACTORIES[provider_name](client)

    svc = AIService(api_provider="openai", api_key="test-key", api_base_url="http://127.0.0.1:9/v1")
    svc._openai_provider = provider
    svc._gemini_provider = provider
    svc._anthropic_provider = provider

    # 无工具时 provider 会走「无工具普通流式」分支，就体现不出 max_rounds；
    # 所以让工具加载稳定返回一份工具列表，把链路固定在「有工具」分支上。
    async def _drive():
        svc._cached_tools = tools
        svc._tools_loaded = True
        svc._enable_mcp = True
        svc.user_id = "user-1"

        async def _fake_prepare(auto_mcp=True, force_refresh=False):
            return tools

        svc._prepare_mcp_tools = _fake_prepare
        async for _ in svc.generate_text_stream(
            prompt="测试提问",
            provider=provider_name,
            model="test-model",
            mcp_max_rounds=1,
        ):
            pass

    asyncio.run(asyncio.wait_for(_drive(), timeout=10))

    assert len(client.calls) == 2, (
        f"{provider_name}: mcp_max_rounds=1 时应为 1 轮工具 + 1 次收口；"
        f"实际 {len(client.calls)} 次请求（参数可能仍未下传）"
    )
    assert client.calls[-1].get("tool_choice") == "none"


# ============================================================
#  对照用例：正常路径不受 depth 机制影响
# ============================================================
@pytest.mark.parametrize("provider_name", sorted(PROVIDER_FACTORIES))
def test_normal_path_still_streams_final_text(provider_name):
    tools = [{"type": "function", "function": {"name": "demo_plugin.echo"}}]
    client = StubStreamClient(_tool_calls_then_text)
    provider = PROVIDER_FACTORIES[provider_name](client)

    chunks = asyncio.run(_drain(provider, client, max_rounds=3, tools=tools))

    # 首轮工具调用 + 一次纯文本回答 = 两次底层请求（远未触及上限）。
    assert len(client.calls) == 2, f"{provider_name}: 正常路径请求次数应为 2，实际 {len(client.calls)}"

    texts = [c for c in chunks if isinstance(c, str)]
    assert "最终答案" in "".join(texts), (
        f"{provider_name}: 正常路径的最终文本未被完整 yield，实际 {texts!r}"
    )

    # 正常路径不应出现收口轮的强制禁用（模型自己停了，不需要兜底）。
    assert all(call.get("tool_choice") == "auto" for call in client.calls), (
        f"{provider_name}: 正常路径不应触发收口轮，实际 tool_choice={[c.get('tool_choice') for c in client.calls]!r}"
    )
