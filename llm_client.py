"""
llm_client.py — LLM 统一客户端（OpenAI 兼容协议）
===============================================
支持: OpenAI / DeepSeek / SiliconFlow / MiMo / 任意 OpenAI-Compatible API
特性: 流式输出 · reasoning_content 兼容 · 可配置超时 · 优雅关闭 · 自动重试
"""

import asyncio
import logging
from typing import AsyncGenerator
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS = (
    "500", "502", "503", "529", "Unknown error",
    "timeout", "Timeout", "Connection", "ECONNRESET",
    "ConnectError", "ConnectTimeout", "ReadTimeout",
    "RemoteProtocolError", "ReadError", "WriteError",
    "DNS", "NameResolution", "ENOTFOUND",
    "api_connection_error", "api_timeout",
    "connection_reset", "broken_pipe",
)


def _is_transient_error(exc: Exception) -> bool:
    """检查是否为可重试的暂时性错误。"""
    err_str = str(exc)
    type_name = type(exc).__name__
    return (
        any(t in err_str for t in TRANSIENT_ERRORS)
        or any(t in type_name for t in TRANSIENT_ERRORS)
    )


class LLMClient:
    """统一异步 LLM 客户端。"""

    def __init__(self, client: AsyncOpenAI, model: str, timeout: float = 60.0):
        self._client = client
        self._model = model
        self._timeout = timeout

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str = "",
        retries: int = 3,
    ) -> str:
        """非流式调用，返回完整文本。自动重试暂时性错误。"""
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + list(messages)

        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                choice = resp.choices[0]
                content = choice.message.content or ""

                reasoning = getattr(choice.message, "reasoning_content", None)
                if reasoning:
                    content = reasoning + ("\n\n" + content if content else "")

                return content
            except Exception as e:
                last_exc = e
                if _is_transient_error(e) and attempt < retries:
                    wait = min(attempt * 3, 30)
                    logger.warning(
                        "[LLMClient] chat attempt %d/%d failed: %s, retry %ds",
                        attempt, retries, str(e)[:150], wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        raise last_exc  # type: ignore[misc]

    async def stream_chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        system_prompt: str = "",
        retries: int = 3,
    ) -> AsyncGenerator[str, None]:
        """流式调用，逐 token 生成。兼容 reasoning_content。自动重试暂时性错误。"""
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + list(messages)

        last_exc = None
        for attempt in range(1, retries + 1):
            stream_gen = None
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                stream_gen = stream

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        yield rc

                    if delta.content:
                        yield delta.content

                stream_gen = None
                return
            except Exception as e:
                stream_gen = None
                last_exc = e
                if _is_transient_error(e) and attempt < retries:
                    wait = min(attempt * 3, 30)
                    logger.warning(
                        "[LLMClient] stream_chat attempt %d/%d failed: %s, retry %ds",
                        attempt, retries, str(e)[:150], wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
            finally:
                if stream_gen is not None:
                    try:
                        await stream_gen.aclose()
                    except Exception:
                        pass

        raise last_exc  # type: ignore[misc]

    async def close(self):
        """优雅关闭连接。"""
        try:
            await self._client.close()
        except Exception:
            pass


def create_client_from_config(cfg: dict, model: str = "") -> LLMClient:
    """
    从 provider 配置字典创建 LLMClient。

    cfg 结构示例:
    {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "sk-xxx",
        "models": ["deepseek-ai/DeepSeek-V2.5"],
        "default_model": "deepseek-ai/DeepSeek-V2.5",
        "timeout": 60
    }
    """
    import httpx

    base_url = cfg.get("base_url", "")
    api_key = cfg.get("api_key", "") or "sk-placeholder"
    timeout = float(cfg.get("timeout", 60))

    hc = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout + 60, connect=30),
        follow_redirects=True,
    )

    client = AsyncOpenAI(
        base_url=base_url if base_url else None,
        api_key=api_key,
        http_client=hc,
    )

    resolved_model = model or cfg.get("default_model", "") or "gpt-4o-mini"
    return LLMClient(client, resolved_model, timeout=timeout)
