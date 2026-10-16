"""Anthropic Provider"""
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.config import settings as app_settings
from app.logger import get_logger
from app.services.ai_clients.anthropic_client import AnthropicClient
from .base_provider import BaseAIProvider

logger = get_logger(__name__)


class AnthropicProvider(BaseAIProvider):
    """Anthropic 提供商"""

    def __init__(self, client: AnthropicClient):
        self.client = client

    async def generate(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        messages = [{"role": "user", "content": prompt}]
        return await self.client.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def generate_stream(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        user_id: Optional[str] = None,
        max_rounds: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        # 如果有工具，使用真正的流式工具调用
        if tools:
            logger.debug(f"🔧 AnthropicProvider: 有 {len(tools)} 个工具，使用流式处理")
            messages = [{"role": "user", "content": prompt}]
            actual_tool_choice = tool_choice if tool_choice else "auto"
            
            tool_calls_buffer = []
            
            async for chunk in self.client.chat_completion_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                tools=tools,
                tool_choice=actual_tool_choice,
            ):
                # 检查是否有工具调用
                if chunk.get("tool_calls"):
                    tool_calls_buffer.extend(chunk["tool_calls"])
                    logger.debug(f"🔧 收到工具调用: {len(chunk['tool_calls'])} 个")
                
                # 检查是否结束
                if chunk.get("done"):
                    if tool_calls_buffer:
                        logger.info(f"🔧 流式结束，处理 {len(tool_calls_buffer)} 个工具调用")
                        from app.mcp import mcp_client
                        actual_user_id = user_id or ""
                        tool_results = await mcp_client.batch_call_tools(
                            user_id=actual_user_id,
                            tool_calls=tool_calls_buffer
                        )
                        # 将工具结果注入到上下文中
                        tool_context = mcp_client.build_tool_context(tool_results, format="markdown")
                        
                        # 构建最终提示词，要求AI基于工具结果回答
                        final_prompt = f"{prompt}\n\n{tool_context}\n\n请基于以上工具查询结果，给出完整详细的回答。"
                        final_messages = [{"role": "user", "content": final_prompt}]
                        
                        # 递归调用生成最终结果（首轮请求已消耗 depth=0，故从 1 开始）
                        async for final_chunk in self._generate_with_tools(
                            final_messages, model, temperature, max_tokens, system_prompt, tools, user_id,
                            max_rounds=max_rounds, depth=1,
                        ):
                            yield final_chunk
                    if chunk.get("finish_reason"):
                        yield {"finish_reason": chunk.get("finish_reason"), "done": True}
                    break

                if chunk.get("usage"):
                    yield {"usage": chunk.get("usage")}
                
                # 输出文本内容
                if chunk.get("content"):
                    yield chunk["content"]
            return
        
        # 无工具时普通流式生成
        messages = [{"role": "user", "content": prompt}]
        async for chunk in self.client.chat_completion_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        ):
            if isinstance(chunk, dict):
                if chunk.get("usage"):
                    yield {"usage": chunk.get("usage")}
                if chunk.get("finish_reason"):
                    yield {"finish_reason": chunk.get("finish_reason")}
                if chunk.get("content"):
                    yield chunk["content"]
            else:
                yield chunk

    async def _generate_with_tools(
        self,
        messages: list,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str] = None,
        tools: list = None,
        user_id: Optional[str] = None,
        max_rounds: Optional[int] = None,
        depth: int = 0,
    ) -> AsyncGenerator[str, None]:
        """辅助方法：带工具的流式生成

        递归深度受 max_rounds 限制：达到上限后不再提供工具
        （tools=None + tool_choice="none"），只让模型基于已有工具结果收口作答，
        避免模型持续返回 tool_calls 造成的无限递归。
        """
        rounds_limit = app_settings.mcp_max_rounds if max_rounds is None else max_rounds
        if rounds_limit is None or rounds_limit < 0:
            rounds_limit = app_settings.mcp_max_rounds
        at_limit = depth >= rounds_limit
        if at_limit:
            logger.warning(
                f"🔧 Anthropic 流式工具调用达到上限 depth={depth}/{rounds_limit}，"
                "强制关闭工具（tool_choice=none）收口作答"
            )
        tool_calls_buffer = []
        
        async for chunk in self.client.chat_completion_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            tools=None if at_limit else tools,
            tool_choice="none" if at_limit else "auto",
        ):
            if at_limit:
                # 收口轮：工具已关闭，任何残留 tool_calls 一律忽略（本分支不再递归）
                if chunk.get("tool_calls"):
                    logger.warning("🔧 收口轮仍返回 tool_calls，已忽略并终止工具编排")
                if chunk.get("done"):
                    if chunk.get("finish_reason"):
                        yield {"finish_reason": chunk.get("finish_reason"), "done": True}
                    break

                if chunk.get("usage"):
                    yield {"usage": chunk.get("usage")}

                if chunk.get("content"):
                    yield chunk["content"]
                continue

            if chunk.get("tool_calls"):
                tool_calls_buffer.extend(chunk["tool_calls"])
                logger.debug(f"🔧 _generate_with_tools 收到工具调用: {len(chunk['tool_calls'])} 个")
            
            if chunk.get("done"):
                if tool_calls_buffer:
                    from app.mcp import mcp_client
                    actual_user_id = user_id or ""
                    tool_results = await mcp_client.batch_call_tools(
                        user_id=actual_user_id,
                        tool_calls=tool_calls_buffer
                    )
                    tool_context = mcp_client.build_tool_context(tool_results, format="markdown")
                    
                    messages.append({"role": "user", "content": f"{tool_context}\n\n请基于以上工具查询结果，给出完整详细的回答。"})
                    
                    async for final_chunk in self._generate_with_tools(
                        messages, model, temperature, max_tokens, system_prompt, tools, user_id,
                        max_rounds=rounds_limit, depth=depth + 1,
                    ):
                        yield final_chunk
                if chunk.get("finish_reason"):
                    yield {"finish_reason": chunk.get("finish_reason"), "done": True}
                break

            if chunk.get("usage"):
                yield {"usage": chunk.get("usage")}
            
            if chunk.get("content"):
                yield chunk["content"]