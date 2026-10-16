"""Anthropic 客户端"""
from typing import Any, AsyncGenerator, Dict, Optional

from anthropic import AsyncAnthropic

from app.logger import get_logger
from app.services.ai_config import (
    AIClientConfig,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MODEL_MAX_OUTPUT_TOKENS,
    clamp_max_output_tokens,
    default_config,
    should_warn_max_tokens_clamp,
)

logger = get_logger(__name__)


class AnthropicClient:
    """Anthropic API 客户端"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, config: Optional[AIClientConfig] = None):
        self.config = config or default_config
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**kwargs)

    @staticmethod
    def _convert_tools(tools: list) -> list:
        """同时接受项目内通用的 OpenAI function tool 格式。"""
        converted = []
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if function:
                converted.append({
                    "name": function.get("name"),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters") or {
                        "type": "object",
                        "properties": {},
                    },
                })
            else:
                converted.append(tool)
        return converted

    async def chat_completion(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 收敛 max_tokens：存量脏值直接透传会被上游 400/422
        limit = MODEL_MAX_OUTPUT_TOKENS.get(model, DEFAULT_MAX_OUTPUT_TOKENS)
        effective_max_tokens = clamp_max_output_tokens(model, max_tokens)
        if effective_max_tokens != max_tokens and should_warn_max_tokens_clamp(
            model, max_tokens, limit
        ):
            logger.warning(
                "max_tokens %s 超出模型 %s 的上限 %s，已收敛为 %s（同组合仅告警一次）",
                max_tokens, model, limit, effective_max_tokens,
            )

        kwargs = {
            "model": model,
            "max_tokens": effective_max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            if tool_choice == "required":
                kwargs["tool_choice"] = {"type": "any"}
            elif tool_choice == "auto":
                kwargs["tool_choice"] = {"type": "auto"}

        response = await self.client.messages.create(**kwargs)

        tool_calls = []
        content = ""
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": block.input},
                })
            elif block.type == "text":
                content += block.text

        usage = getattr(response, "usage", None)
        return {
            "content": content,
            "tool_calls": tool_calls if tool_calls else None,
            "finish_reason": response.stop_reason,
            "usage": {
                "prompt_tokens": getattr(usage, "input_tokens", None),
                "completion_tokens": getattr(usage, "output_tokens", None),
                "total_tokens": (
                    (getattr(usage, "input_tokens", 0) or 0) +
                    (getattr(usage, "output_tokens", 0) or 0)
                ) if usage else None,
            },
        }

    async def chat_completion_stream(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式生成，支持工具调用
        
        Yields:
            Dict with keys:
            - content: str - 文本内容块
            - tool_calls: list - 工具调用列表（如果有）
            - done: bool - 是否结束
        """
        # 收敛 max_tokens：存量脏值直接透传会被上游 400/422
        limit = MODEL_MAX_OUTPUT_TOKENS.get(model, DEFAULT_MAX_OUTPUT_TOKENS)
        effective_max_tokens = clamp_max_output_tokens(model, max_tokens)
        if effective_max_tokens != max_tokens and should_warn_max_tokens_clamp(
            model, max_tokens, limit
        ):
            logger.warning(
                "max_tokens %s 超出模型 %s 的上限 %s，已收敛为 %s（同组合仅告警一次）",
                max_tokens, model, limit, effective_max_tokens,
            )

        kwargs = {
            "model": model,
            "max_tokens": effective_max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            if tool_choice == "required":
                kwargs["tool_choice"] = {"type": "any"}
            elif tool_choice == "auto":
                kwargs["tool_choice"] = {"type": "auto"}

        try:
            async with self.client.messages.stream(**kwargs) as stream:
                try:
                    tool_calls: list = []
                    finish_reason: Optional[str] = None
                    done_emitted = False

                    async for event in stream:
                        # 事件字段随 SDK 版本变化，一律 getattr 兜底，不做直接属性访问
                        event_type = getattr(event, "type", None)

                        if event_type == "content_block_start":
                            # tool_use 块的 id / name 只在这里出现
                            block = getattr(event, "content_block", None)
                            if getattr(block, "type", None) == "tool_use":
                                tool_calls.append({
                                    "id": getattr(block, "id", "") or "",
                                    "type": "function",
                                    "function": {
                                        "name": getattr(block, "name", "") or "",
                                        "arguments": "",
                                    },
                                })

                        elif event_type == "content_block_delta":
                            # 文本增量与工具参数增量都在 delta 里，事件顶层没有 text / partial_json
                            delta = getattr(event, "delta", None)
                            delta_type = getattr(delta, "type", None)
                            if delta_type in ("text_delta", "text"):
                                text = getattr(delta, "text", "") or ""
                                if text:
                                    yield {"content": text}
                            elif delta_type in ("input_json_delta", "tool_use_delta"):
                                partial = getattr(delta, "partial_json", "") or ""
                                if partial and tool_calls:
                                    tool_calls[-1]["function"]["arguments"] += partial

                        elif event_type == "message_delta":
                            # stop_reason 在 delta 下，RawMessageDeltaEvent 顶层没有该字段
                            delta = getattr(event, "delta", None)
                            reason = getattr(delta, "stop_reason", None)
                            if reason:
                                finish_reason = reason
                                if tool_calls:
                                    yield {"tool_calls": tool_calls}
                                yield {"done": True, "finish_reason": finish_reason}
                                done_emitted = True

                        elif event_type == "message_stop":
                            # 兜底：有的上游/代理不回 message_delta 的 stop_reason，
                            # 不能让调用方静默收不到收尾信号
                            if not done_emitted:
                                if tool_calls:
                                    yield {"tool_calls": tool_calls}
                                yield {"done": True, "finish_reason": finish_reason}
                                done_emitted = True

                    # 兜底：流被异常截断且连 message_stop 都没有时，仍要给出收尾信号
                    if not done_emitted:
                        if tool_calls:
                            yield {"tool_calls": tool_calls}
                        yield {"done": True, "finish_reason": finish_reason}
                except GeneratorExit:
                    # 生成器被关闭，这是正常的清理过程
                    logger.debug("Anthropic 流式响应生成器被关闭(GeneratorExit)")
                    raise
                except Exception as iter_error:
                    logger.error(f"Anthropic 流式响应迭代出错: {str(iter_error)}")
                    raise
        except GeneratorExit:
            # 重新抛出GeneratorExit，让调用方处理
            raise
        except Exception as e:
            logger.error(f"Anthropic 流式请求出错: {str(e)}")
            raise
