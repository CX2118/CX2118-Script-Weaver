"""
web_search.py — 网络搜索模块（cx2118 Script Weaver v8）
========================================================
支持: Bing Web Search API / Google Custom Search JSON API / 自定义端点
特性: 异步搜索 · URL 内容抓取 · LLM 摘要集成 · 优雅错误处理
依赖: httpx (pip install httpx)
"""

import re
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 搜索引擎枚举
# ═══════════════════════════════════════════════════════════════

class SearchProvider(str, Enum):
    """支持的搜索引擎类型。"""
    BING = "bing"
    GOOGLE = "google"
    CUSTOM = "custom"


# ═══════════════════════════════════════════════════════════════
# 搜索结果数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    """单条搜索结果。"""
    url: str
    title: str
    snippet: str
    source: str = "bing"          # bing / google / custom
    rank: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "source": self.source,
            "rank": self.rank,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════
# 搜索配置数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class WebSearchConfig:
    """网络搜索配置。

    provider 说明:
      - "bing"   : Bing Web Search API，需提供 api_key
      - "google" : Google Custom Search JSON API，需提供 api_key 和 custom_endpoint（即 cx 引擎 ID）
      - "custom" : 自定义搜索端点，需提供 custom_endpoint（POST 接口地址）
    """
    enabled: bool = False
    provider: str = "bing"            # bing / google / custom
    api_key: str = ""
    custom_endpoint: str = ""          # Google 的 cx 或自定义搜索 API 地址
    max_results: int = 10
    timeout: float = 15.0

    def to_dict(self) -> dict:
        """序列化为字典，用于持久化存储。"""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "api_key": self.api_key,
            "custom_endpoint": self.custom_endpoint,
            "max_results": self.max_results,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WebSearchConfig":
        """从字典反序列化，缺失字段使用默认值。"""
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            provider=str(data.get("provider", "bing")),
            api_key=str(data.get("api_key", "")),
            custom_endpoint=str(data.get("custom_endpoint", "")),
            max_results=int(data.get("max_results", 10)),
            timeout=float(data.get("timeout", 15.0)),
        )

    def validate(self) -> list[str]:
        """校验配置是否合法，返回错误消息列表（空表示合法）。"""
        errors: list[str] = []
        if self.provider not in (SearchProvider.BING.value,
                                 SearchProvider.GOOGLE.value,
                                 SearchProvider.CUSTOM.value):
            errors.append(f"不支持的搜索引擎: {self.provider!r}")
        if self.provider == "bing" and not self.api_key:
            errors.append("Bing 搜索需要提供 api_key")
        if self.provider == "google":
            if not self.api_key:
                errors.append("Google 搜索需要提供 api_key")
            if not self.custom_endpoint:
                errors.append("Google 搜索需要提供 custom_endpoint（cx 引擎 ID）")
        if self.provider == "custom" and not self.custom_endpoint:
            errors.append("自定义搜索需要提供 custom_endpoint（API 地址）")
        if self.max_results < 1 or self.max_results > 50:
            errors.append(f"max_results 应在 1~50 之间，当前: {self.max_results}")
        if self.timeout < 1.0 or self.timeout > 120.0:
            errors.append(f"timeout 应在 1.0~120.0 之间，当前: {self.timeout}")
        return errors


# ═══════════════════════════════════════════════════════════════
# HTML 标签剥离工具
# ═══════════════════════════════════════════════════════════════

def _strip_html_tags(html: str) -> str:
    """使用正则剥离 HTML 标签，返回纯文本。

    注意: 这不是完整的 HTML 解析器，仅用于快速提取文本内容。
    对于生产级需求，建议使用 BeautifulSoup 等库。
    """
    if not html:
        return ""
    # 移除 <script> 和 <style> 块及其内容
    text = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", "", html, flags=re.IGNORECASE)
    # 移除所有 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # 解码常见 HTML 实体
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    # 合并多余空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════
# WebSearcher — 核心搜索类
# ═══════════════════════════════════════════════════════════════

class WebSearcher:
    """异步网络搜索器。

    支持三种搜索引擎:
      - Bing Web Search API
      - Google Custom Search JSON API
      - 自定义搜索端点

    使用示例::

        config = WebSearchConfig(enabled=True, provider="bing", api_key="YOUR_KEY")
        searcher = WebSearcher(config)

        results = await searcher.search("Python async programming")
        for r in results:
            print(f"[{r.rank}] {r.title} — {r.url}")

        await searcher.close()
    """

    # ---- 各搜索引擎的 API 端点 ----
    _BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
    _GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, config: WebSearchConfig, http_client: Optional[httpx.AsyncClient] = None):
        """
        Args:
            config: 搜索配置
            http_client: 外部传入的 httpx.AsyncClient（可选）；
                          若为 None 则自动创建，并在 close() 时自动关闭。
        """
        self._config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "cx2118-ScriptWeaver/8.0"},
        )

    # ---- 搜索入口 ----

    async def search(self, query: str, num_results: int = 10) -> list[SearchResult]:
        """执行网络搜索，返回结果列表。

        根据 config.provider 自动路由到对应的搜索引擎。
        任何异常都会被捕获并记录警告日志，返回空列表。

        Args:
            query: 搜索关键词
            num_results: 期望的最大结果数量（实际可能更少）
        """
        # 限制请求结果数不超过配置上限
        num_results = min(num_results, self._config.max_results)

        try:
            provider = self._config.provider.lower().strip()
            if provider == SearchProvider.BING.value:
                return await self._search_bing(query, num_results)
            elif provider == SearchProvider.GOOGLE.value:
                return await self._search_google(query, num_results)
            elif provider == SearchProvider.CUSTOM.value:
                return await self._search_custom(query, num_results)
            else:
                logger.warning("不支持的搜索引擎: %s", provider)
                return []
        except Exception as exc:
            logger.warning("搜索失败 [%s] query=%r: %s", self._config.provider, query, exc)
            return []

    # ---- Bing 搜索 ----

    async def _search_bing(self, query: str, num_results: int) -> list[SearchResult]:
        """调用 Bing Web Search API。

        文档: https://learn.microsoft.com/en-us/bing/search-apis/bing-web-search/
        """
        if not self._config.api_key:
            logger.warning("Bing 搜索未配置 api_key，跳过搜索")
            return []

        params = {"q": query, "count": num_results, "responseFilter": "Webpages"}
        headers = {"Ocp-Apim-Subscription-Key": self._config.api_key}

        resp = await self._client.get(
            self._BING_ENDPOINT,
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        web_pages = data.get("webPages", {})
        for idx, item in enumerate(web_pages.get("value", [])):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("name", ""),
                snippet=item.get("snippet", ""),
                source="bing",
                rank=idx + 1,
            ))
        return results

    # ---- Google 搜索 ----

    async def _search_google(self, query: str, num_results: int) -> list[SearchResult]:
        """调用 Google Custom Search JSON API。

        文档: https://developers.google.com/custom-search/v1/overview
        config.api_key   → API Key
        config.custom_endpoint → cx（搜索引擎 ID）
        """
        if not self._config.api_key or not self._config.custom_endpoint:
            logger.warning("Google 搜索未配置 api_key 或 cx，跳过搜索")
            return []

        params = {
            "key": self._config.api_key,
            "cx": self._config.custom_endpoint,
            "q": query,
            "num": num_results,
        }

        resp = await self._client.get(
            self._GOOGLE_ENDPOINT,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        for idx, item in enumerate(data.get("items", [])):
            results.append(SearchResult(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                source="google",
                rank=idx + 1,
            ))
        return results

    # ---- 自定义搜索端点 ----

    async def _search_custom(self, query: str, num_results: int) -> list[SearchResult]:
        """调用自定义搜索端点。

        期望: POST {custom_endpoint}，请求体 {"query": ..., "num": ...}
        响应: {"results": [{"url": ..., "title": ..., "snippet": ...}, ...]}
        """
        if not self._config.custom_endpoint:
            logger.warning("自定义搜索未配置 custom_endpoint，跳过搜索")
            return []

        payload = {"query": query, "num": num_results}

        resp = await self._client.post(
            self._config.custom_endpoint,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        for idx, item in enumerate(data.get("results", [])):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                source="custom",
                rank=idx + 1,
            ))
        return results

    # ---- URL 内容抓取 ----

    async def crawl_url(self, url: str, timeout: float = 20.0) -> str:
        """抓取指定 URL 的文本内容。

        自动跟随重定向，剥离 HTML 标签后返回纯文本。
        最大返回 50000 个字符，超出部分会被截断。

        Args:
            url: 目标 URL
            timeout: 单次请求超时（秒）

        Returns:
            纯文本内容；失败时返回空字符串。
        """
        try:
            resp = await self._client.get(
                url,
                timeout=httpx.Timeout(timeout, connect=10.0),
                follow_redirects=True,
                headers={
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "User-Agent": "cx2118-ScriptWeaver/8.0 (Bot)",
                },
            )
            resp.raise_for_status()

            # 优先使用 UTF-8 解码，回退到自动检测
            content_type = resp.headers.get("content-type", "")
            if "text/" not in content_type and "application/json" not in content_type and "application/xml" not in content_type:
                logger.debug("crawl_url: 不支持的 Content-Type '%s'，尝试解码", content_type)

            text = resp.text
            if not text:
                return ""

            # 如果是 HTML，剥离标签
            if "<html" in text.lower()[:2000] or "<!doctype" in text.lower()[:2000]:
                text = _strip_html_tags(text)

            # 限制最大字符数
            max_chars = 50000
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n... [内容已截断，超过 50000 字符限制]"

            return text.strip()

        except httpx.TimeoutException:
            logger.warning("crawl_url 超时: %s (timeout=%.1fs)", url, timeout)
            return ""
        except httpx.HTTPStatusError as exc:
            logger.warning("crawl_url HTTP 错误 %s: %s — %s", exc.response.status_code, url, exc)
            return ""
        except Exception as exc:
            logger.warning("crawl_url 失败: %s — %s", url, exc)
            return ""

    # ---- 资源管理 ----

    async def close(self):
        """关闭 HTTP 客户端。

        仅当客户端由本实例创建时（未从外部传入）才会关闭，
        避免影响外部共享的连接池。
        """
        if self._owns_client:
            try:
                await self._client.aclose()
            except Exception:
                pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()


# ═══════════════════════════════════════════════════════════════
# 搜索 + 摘要集成辅助函数
# ═══════════════════════════════════════════════════════════════

async def search_and_summarize(
    query: str,
    searcher: WebSearcher,
    llm_client=None,
    max_chars: int = 4000,
) -> dict:
    """搜索网络并（可选）使用 LLM 生成摘要。

    工作流程:
      1. 调用 searcher.search() 获取搜索结果
      2. 将结果拼接为文本
      3. 若提供了 llm_client，调用 LLM 生成中文摘要
      4. 返回结构化字典

    Args:
        query: 搜索关键词
        searcher: WebSearcher 实例
        llm_client: 可选的 LLM 客户端（需支持 .chat(messages) 接口）
        max_chars: 传递给 LLM 的最大文本长度

    Returns:
        {"query": str, "results": list[dict], "summary": str|None}

    使用示例::

        results = await search_and_summarize(
            "Python asyncio 最佳实践",
            searcher=my_searcher,
            llm_client=my_llm,  # 可选
        )
    """
    # 执行搜索
    raw_results = await searcher.search(query)

    # 序列化结果
    result_dicts = [r.to_dict() for r in raw_results]

    # 拼接摘要上下文
    summary: Optional[str] = None
    if raw_results and llm_client is not None:
        context_parts: list[str] = []
        total_chars = 0

        for r in raw_results:
            part = f"[{r.rank}] {r.title}\n   {r.url}\n   {r.snippet}\n"
            if total_chars + len(part) > max_chars:
                # 尝试截断当前条目
                remaining = max_chars - total_chars
                if remaining > 50:
                    context_parts.append(part[:remaining] + "...")
                break
            context_parts.append(part)
            total_chars += len(part)

        context_text = "\n".join(context_parts)

        try:
            summary = await llm_client.chat(
                messages=[{
                    "role": "user",
                    "content": (
                        f"请根据以下搜索结果，用中文对「{query}」做一个简洁、准确的摘要。"
                        f"要求：涵盖主要观点，不超过 300 字。\n\n"
                        f"搜索结果:\n{context_text}"
                    ),
                }],
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("LLM 摘要生成失败: %s", exc)
            summary = None

    return {
        "query": query,
        "results": result_dicts,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════

def create_searcher_from_config(cfg: dict) -> Optional[WebSearcher]:
    """从配置字典创建 WebSearcher 实例。

    若 enabled 为 False 或配置校验失败，返回 None。

    cfg 示例::
        {
            "enabled": true,
            "provider": "bing",
            "api_key": "your-bing-key",
            "custom_endpoint": "",
            "max_results": 10,
            "timeout": 15.0
        }
    """
    if not cfg or not cfg.get("enabled", False):
        return None

    config = WebSearchConfig.from_dict(cfg)
    errors = config.validate()
    if errors:
        for err in errors:
            logger.warning("WebSearch 配置错误: %s", err)
        return None

    return WebSearcher(config)
