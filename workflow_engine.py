#!/usr/bin/env python3
"""
cx2118-script-weaver v8 — Workflow Execution Engine (Enhanced)
================================================================

可视化节点式工作流引擎，支持多种内容类型的编排与执行。
包括 LLM 调用、代码沙箱、文件处理、条件分支、循环、Web 搜索、
网页抓取、文本嵌入、智能路由、子工作流、API 调用、Webhook 触发、
定时器、变量存储、注释节点等，通过拓扑排序实现有向无环图 (DAG)
依赖调度，支持并行层级执行提示，并以 SSE 事件流实时推送每个
节点的执行状态。

v8 新增功能
-----------
- 10 种新节点类型 (SEARCH, SCRAPER, EMBEDDING, ROUTER, SUBWORKFLOW,
  NOTE, VARIABLE, TIMER, WEBHOOK_TRIGGER, API_CALL)
- 连接曲线类型 (CurveType: straight / bezier / step / elbow)
- Prompt 模板连接模式 (PromptTemplate 自动注入上下文)
- 节点包导入/导出 (ZIP 格式, manifest.json + nodes.json + connections.json)
- 工作流目录/层级管理 (WorkflowFolder)
- 用户自定义 LLM API 模式 (直连客户端, 防止 API Key 暴露)
- 拓扑排序并行执行层级提示
- 3 种新连接类型 (EMBEDDING, BINARY, TABLE)

Architecture
-----------
WorkflowEngine  ←→  Storage  (持久化工作流状态)
WorkflowEngine  ←→  LLMClientFactory  (创建 LLM API 客户端)
WorkflowEngine  ←→  Sandbox  (安全执行用户代码)

All node execution is fully async.  The public ``execute`` method is
an async generator that yields SSE-style dicts so the caller can stream
progress to a front-end.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import io
import json
import logging
import re
import time
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)

_TRANSIENT_KEYWORDS = (
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
        any(t in err_str for t in _TRANSIENT_KEYWORDS)
        or any(t in type_name for t in _TRANSIENT_KEYWORDS)
    )


# ═══════════════════════════════════════════════════════════════════════════
# Enums — 枚举类型
# ═══════════════════════════════════════════════════════════════════════════

class NodeType(str, Enum):
    """所有支持的节点类型 — v8 扩展版"""

    # ---- 核心节点 (v7 遗留) ----
    PROMPT = "prompt"                     # 文本提示输入
    LLM = "llm"                           # LLM API 调用 (chat completion)
    CODE = "code"                         # 在沙箱中执行 Python 代码
    FILE_INPUT = "file_input"             # 文件 / 文档 / zip 输入
    IMAGE_INPUT = "image_input"           # 图片输入 (base64 或上传)
    VIDEO_INPUT = "video_input"           # 视频输入
    AUDIO_INPUT = "audio_input"           # 音频输入
    TEXT_TRANSFORM = "text_transform"     # 文本处理 (拆分、合并、格式化)
    FILE_OUTPUT = "file_output"           # 文件 / 文档生成
    IMAGE_OUTPUT = "image_output"         # 图片生成
    CONDITION = "condition"               # 条件分支
    MERGE = "merge"                       # 合并多个输入
    LOOP = "loop"                         # 迭代循环
    DELAY = "delay"                       # 延时
    HTTP = "http"                         # HTTP 请求
    OUTPUT = "output"                     # 最终输出

    # ---- v8 新增节点 ----
    SEARCH = "search"                     # Web 搜索节点 (查询 Bing/Google)
    SCRAPER = "scraper"                   # Web 抓取节点 (爬取 URL 内容)
    EMBEDDING = "embedding"               # 文本嵌入/向量化节点
    ROUTER = "router"                     # 智能路由节点 (LLM 条件路由)
    SUBWORKFLOW = "subworkflow"           # 嵌套子工作流引用
    NOTE = "note"                         # 注释/批注节点 (不执行)
    VARIABLE = "variable"                 # 全局变量存储节点
    TIMER = "timer"                       # 定时/延迟执行节点
    WEBHOOK_TRIGGER = "webhook_trigger"   # HTTP Webhook 输入触发器
    API_CALL = "api_call"                 # 通用 REST API 调用节点


class ConnectionType(str, Enum):
    """连接上承载的数据类型 — v8 扩展版"""
    TEXT = "text"
    CODE = "code"
    FILE = "file"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    JSON = "json"
    STREAM = "stream"
    ANY = "any"

    # ---- v8 新增 ----
    EMBEDDING = "embedding"               # 向量数据
    BINARY = "binary"                     # 二进制数据
    TABLE = "table"                       # 表格数据


class CurveType(str, Enum):
    """连接线曲线类型 — v8 新增

    用于前端渲染节点之间的连接线样式。
    """
    STRAIGHT = "straight"                 # 直线
    BEZIER = "bezier"                     # 三次贝塞尔曲线 (默认)
    STEP = "step"                         # 阶梯/正交折线
    ELBOW = "elbow"                       # 肘形连接器


# ═══════════════════════════════════════════════════════════════════════════
# Data Models — 数据模型
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PromptTemplate:
    """提示模板 — v8 新增

    当连接类型为 "prompt" 时，连接携带此模板，
    自动将上游节点的输出注入到下游 LLM/Prompt 节点中。

    Attributes
    ----------
    template : str
        模板字符串，使用 ``{{variable}}`` 作为占位符。
    variables : dict[str, str]
        变量名到上游节点端口名的映射。
        例如 ``{"context": "node_abc.text"}`` 表示将 node_abc 的
        text 端口输出注入到模板的 ``{{context}}`` 位置。
    auto_inject_context : bool
        是否自动注入上游所有输出为变量 (默认 True)。
    """
    template: str = ""
    variables: Dict[str, str] = field(default_factory=dict)
    auto_inject_context: bool = True


@dataclass
class WorkflowNode:
    """工作流中的单个节点"""
    id: str
    type: NodeType
    x: float = 0.0
    y: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)

    # ---- 运行时状态（不序列化） ----
    _inputs: Dict[str, Any] = field(default_factory=dict, repr=False)
    _outputs: Dict[str, Any] = field(default_factory=dict, repr=False)
    _status: str = "pending"  # pending | running | done | error

    # 循环节点专用：迭代计数
    _iteration: int = 0
    _loop_results: List[Dict[str, Any]] = field(default_factory=list, repr=False)

    # 全局变量节点专用 (v8)
    _variable_value: Any = None


@dataclass
class WorkflowConnection:
    """两个节点端口之间的有向连接 — v8 扩展版"""
    id: str
    source_node: str
    source_port: str
    target_node: str
    target_port: str
    connection_type: ConnectionType = ConnectionType.ANY
    curve_type: CurveType = CurveType.BEZIER  # v8: 连接线曲线类型

    # v8: prompt 模板 (可选，仅当连接类型相关时使用)
    prompt_template: Optional[PromptTemplate] = None


@dataclass
class WorkflowFolder:
    """工作流目录/文件夹 — v8 新增

    支持工作流的层级组织和管理。

    Attributes
    ----------
    id : str
        目录唯一标识符。
    name : str
        目录显示名称。
    parent_id : str
        父目录 ID，空字符串表示根目录。
    created_at : float
        创建时间戳。
    """
    id: str
    name: str = "New Folder"
    parent_id: str = ""
    created_at: float = field(default_factory=lambda: time.time())


@dataclass
class Workflow:
    """完整的工作流定义 — v8 扩展版"""
    id: str
    name: str = "New Workflow"
    nodes: Dict[str, WorkflowNode] = field(default_factory=dict)
    connections: List[WorkflowConnection] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())
    folder_id: str = ""  # v8: 所属目录 ID


# ═══════════════════════════════════════════════════════════════════════════
# Storage Interface (duck-typed; concrete impl provided by host project)
# ═══════════════════════════════════════════════════════════════════════════

class Storage:
    """
    抽象存储接口 —— 任何实现了这些方法的对象都可以作为引擎的后端。

    Methods
    -------
    save_workflow(session_id, workflow) -> None
    load_workflow(session_id, workflow_id) -> dict | None
    list_workflows(session_id) -> list[dict]
    delete_workflow(session_id, workflow_id) -> None
    save_node_result(session_id, workflow_id, node_id, data) -> None
    load_node_result(session_id, workflow_id, node_id) -> dict | None

    v8 新增 (可选实现，有默认 no-op 行为):
    save_folder(session_id, folder) -> None
    load_folder(session_id, folder_id) -> dict | None
    list_folders(session_id, parent_id) -> list[dict]
    delete_folder(session_id, folder_id) -> None
    """

    async def save_workflow(self, session_id: str, workflow: dict) -> None:
        raise NotImplementedError

    async def load_workflow(self, session_id: str, workflow_id: str) -> Optional[dict]:
        raise NotImplementedError

    async def list_workflows(self, session_id: str) -> List[dict]:
        raise NotImplementedError

    async def delete_workflow(self, session_id: str, workflow_id: str) -> None:
        raise NotImplementedError

    async def save_node_result(
        self, session_id: str, workflow_id: str, node_id: str, data: dict
    ) -> None:
        raise NotImplementedError

    async def load_node_result(
        self, session_id: str, workflow_id: str, node_id: str
    ) -> Optional[dict]:
        raise NotImplementedError

    # ---- v8: 目录存储 (可选) ----

    async def save_folder(self, session_id: str, folder: dict) -> None:
        """保存目录信息（可选实现）"""
        pass  # 默认 no-op，兼容旧存储后端

    async def load_folder(self, session_id: str, folder_id: str) -> Optional[dict]:
        """加载目录信息（可选实现）"""
        return None

    async def list_folders(self, session_id: str, parent_id: str = "") -> List[dict]:
        """列出子目录（可选实现）"""
        return []

    async def delete_folder(self, session_id: str, folder_id: str) -> None:
        """删除目录（可选实现）"""
        pass


# ═══════════════════════════════════════════════════════════════════════════
# LLM Client Factory Interface
# ═══════════════════════════════════════════════════════════════════════════

class LLMClient:
    """LLM API 客户端抽象"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> Union[str, AsyncGenerator[str, None]]:
        raise NotImplementedError


class LLMClientFactory:
    """
    LLM 客户端工厂 —— 根据提供商 / 模型名称创建客户端实例。

    Methods
    -------
    create(provider, model, **kwargs) -> LLMClient
    """

    def create(
        self, provider: str, model: str, **kwargs: Any
    ) -> LLMClient:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════
# Sandbox Interface
# ═══════════════════════════════════════════════════════════════════════════

class Sandbox:
    """
    代码沙箱接口 —— 在隔离环境中安全执行用户提供的 Python 代码。

    Methods
    -------
    execute(code, inputs, timeout, files_in) -> dict
        返回 {"stdout": ..., "stderr": ..., "files": [...]}
    """

    async def execute(
        self,
        code: str,
        inputs: Dict[str, Any],
        *,
        timeout: float = 30.0,
        files_in: Optional[Dict[str, bytes]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════
# Helper utilities — 辅助工具函数
# ═══════════════════════════════════════════════════════════════════════════

def _ts() -> float:
    """当前 Unix 时间戳（秒）"""
    return time.time()


def _uid() -> str:
    """生成短 UUID"""
    return uuid.uuid4().hex[:12]


def _topological_sort(
    nodes: Dict[str, WorkflowNode],
    connections: List[WorkflowConnection],
) -> List[str]:
    """
    对节点进行拓扑排序，保证依赖节点在被依赖节点之后执行。
    若检测到环路则抛出 ValueError。
    """
    in_degree: Dict[str, int] = {nid: 0 for nid in nodes}
    adjacency: Dict[str, List[str]] = defaultdict(list)

    for conn in connections:
        if conn.source_node in nodes and conn.target_node in nodes:
            adjacency[conn.source_node].append(conn.target_node)
            in_degree[conn.target_node] += 1

    queue: List[str] = [nid for nid, deg in in_degree.items() if deg == 0]
    order: List[str] = []

    while queue:
        # 按节点 ID 排序保证确定性
        queue.sort()
        nid = queue.pop(0)
        order.append(nid)
        for successor in adjacency[nid]:
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(order) != len(nodes):
        cycle_nodes = set(nodes) - set(order)
        raise ValueError(
            f"Workflow contains a cycle involving nodes: {cycle_nodes}"
        )

    return order


def _topological_sort_parallel(
    nodes: Dict[str, WorkflowNode],
    connections: List[WorkflowConnection],
) -> List[List[str]]:
    """
    拓扑排序 —— 返回分层列表，每层内的节点可并行执行。v8 新增。

    Returns
    -------
    list[list[str]]
        外层列表为执行层级（按顺序），内层列表为该层级可并行的节点 ID。
        例如 ``[[n1, n2], [n3], [n4, n5]]`` 表示 n1,n2 先并行，然后 n3，
        最后 n4,n5 并行。

    Raises
    ------
    ValueError
        若检测到环路。
    """
    in_degree: Dict[str, int] = {nid: 0 for nid in nodes}
    adjacency: Dict[str, List[str]] = defaultdict(list)

    for conn in connections:
        if conn.source_node in nodes and conn.target_node in nodes:
            adjacency[conn.source_node].append(conn.target_node)
            in_degree[conn.target_node] += 1

    current_layer: List[str] = [
        nid for nid, deg in in_degree.items() if deg == 0
    ]
    current_layer.sort()
    layers: List[List[str]] = []
    processed: int = 0

    while current_layer:
        layers.append(current_layer)
        processed += len(current_layer)
        next_layer: List[str] = []
        for nid in current_layer:
            for successor in adjacency[nid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    next_layer.append(successor)
        next_layer.sort()
        current_layer = next_layer

    if processed != len(nodes):
        cycle_nodes = set(nodes) - set(nid for layer in layers for nid in layer)
        raise ValueError(
            f"Workflow contains a cycle involving nodes: {cycle_nodes}"
        )

    return layers


def _gather_inputs(
    node_id: str,
    connections: List[WorkflowConnection],
    node_outputs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    收集目标节点所有输入端口的值。
    返回 {target_port: data} 。
    如果多个连接指向同一端口，列表中的数据会合并为 list。
    """
    inputs: Dict[str, Any] = {}
    for conn in connections:
        if conn.target_node != node_id:
            continue
        src_outputs = node_outputs.get(conn.source_node, {})
        data = src_outputs.get(conn.source_port)
        if data is None:
            continue
        if conn.target_port in inputs:
            existing = inputs[conn.target_port]
            if isinstance(existing, list):
                existing.append(data)
            else:
                inputs[conn.target_port] = [existing, data]
        else:
            inputs[conn.target_port] = data
    return inputs


def render_prompt_template(
    template: PromptTemplate,
    context: Dict[str, Any],
) -> str:
    """
    渲染 Prompt 模板 —— 将 ``{{variable}}`` 占位符替换为实际值。v8 新增。

    Parameters
    ----------
    template : PromptTemplate
        包含模板字符串和变量映射。
    context : dict
        上下文数据，键为变量名，值为实际数据。
        当 ``auto_inject_context=True`` 时，context 中的所有键
        均可作为模板变量使用。

    Returns
    -------
    str
        渲染后的提示文本。

    Examples
    --------
    >>> pt = PromptTemplate(
    ...     template="分析以下文本: {{text}}\\n上下文: {{context}}",
    ...     auto_inject_context=True,
    ... )
    >>> render_prompt_template(pt, {"text": "你好世界", "context": "测试"})
    '分析以下文本: 你好世界\\n上下文: 测试'
    """

    def _replace_var(match: re.Match) -> str:
        var_name = match.group(1).strip()
        # 先查 variables 映射，再查 context
        mapped_key = template.variables.get(var_name, var_name)
        value = context.get(mapped_key)
        if value is None:
            value = context.get(var_name)
        if value is None:
            return match.group(0)  # 未找到则保留原占位符
        return str(value)

    result = re.sub(r'\{\{\s*(\w+(?:\.\w+)*)\s*\}\}', _replace_var, template.template)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Serialization — 序列化 / 反序列化
# ═══════════════════════════════════════════════════════════════════════════

def _workflow_to_dict(wf: Workflow) -> dict:
    """将 Workflow 对象序列化为可存储的 dict"""
    nodes_dict: Dict[str, dict] = {}
    for nid, node in wf.nodes.items():
        nodes_dict[nid] = {
            "id": node.id,
            "type": node.type.value,
            "x": node.x,
            "y": node.y,
            "config": copy.deepcopy(node.config),
        }
    conns: List[dict] = []
    for c in wf.connections:
        conn_dict: Dict[str, Any] = {
            "id": c.id,
            "source_node": c.source_node,
            "source_port": c.source_port,
            "target_node": c.target_node,
            "target_port": c.target_port,
            "connection_type": c.connection_type.value,
            "curve_type": c.curve_type.value,  # v8
        }
        # v8: 序列化 prompt_template
        if c.prompt_template is not None:
            conn_dict["prompt_template"] = {
                "template": c.prompt_template.template,
                "variables": c.prompt_template.variables,
                "auto_inject_context": c.prompt_template.auto_inject_context,
            }
        conns.append(conn_dict)
    return {
        "id": wf.id,
        "name": wf.name,
        "nodes": nodes_dict,
        "connections": conns,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
        "folder_id": wf.folder_id,  # v8
    }


def _dict_to_workflow(data: dict) -> Workflow:
    """从存储的 dict 还原 Workflow 对象"""
    nodes: Dict[str, WorkflowNode] = {}
    for nid, nd in data.get("nodes", {}).items():
        nodes[nid] = WorkflowNode(
            id=nd["id"],
            type=NodeType(nd["type"]),
            x=nd.get("x", 0),
            y=nd.get("y", 0),
            config=nd.get("config", {}),
        )
    connections: List[WorkflowConnection] = []
    for cd in data.get("connections", []):
        # v8: 解析 curve_type
        curve_type = CurveType(cd.get("curve_type", "bezier"))
        # v8: 解析 prompt_template
        prompt_template = None
        pt_data = cd.get("prompt_template")
        if pt_data:
            prompt_template = PromptTemplate(
                template=pt_data.get("template", ""),
                variables=pt_data.get("variables", {}),
                auto_inject_context=pt_data.get("auto_inject_context", True),
            )
        connections.append(WorkflowConnection(
            id=cd["id"],
            source_node=cd["source_node"],
            source_port=cd["source_port"],
            target_node=cd["target_node"],
            target_port=cd["target_port"],
            connection_type=ConnectionType(cd.get("connection_type", "any")),
            curve_type=curve_type,
            prompt_template=prompt_template,
        ))
    return Workflow(
        id=data["id"],
        name=data.get("name", "New Workflow"),
        nodes=nodes,
        connections=connections,
        created_at=data.get("created_at", 0),
        updated_at=data.get("updated_at", 0),
        folder_id=data.get("folder_id", ""),  # v8
    )


def _folder_to_dict(folder: WorkflowFolder) -> dict:
    """将 WorkflowFolder 对象序列化为可存储的 dict"""
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "created_at": folder.created_at,
    }


def _dict_to_folder(data: dict) -> WorkflowFolder:
    """从存储的 dict 还原 WorkflowFolder 对象"""
    return WorkflowFolder(
        id=data["id"],
        name=data.get("name", "New Folder"),
        parent_id=data.get("parent_id", ""),
        created_at=data.get("created_at", 0),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Emit helper — 将回调统一封装为 async
# ═══════════════════════════════════════════════════════════════════════════

EmitCallback = Optional[Callable[[dict], Coroutine[Any, Any, None]]]


async def _emit(callback: EmitCallback, event: dict) -> None:
    """安全调用 emit 回调"""
    if callback is not None:
        try:
            await callback(event)
        except Exception:
            logger.exception("emit callback error: %s", event.get("type"))


# ═══════════════════════════════════════════════════════════════════════════
# Direct LLM Client — 直连 LLM 客户端 (v8 新增)
# ═══════════════════════════════════════════════════════════════════════════

class _DirectLLMClient(LLMClient):
    """
    直连 LLM 客户端 —— 使用用户提供的 API 凭据直接创建 OpenAI 兼容客户端。

    此客户端绕过 LLMClientFactory，防止 API Key 暴露在 Python 脚本中。
    仅在节点 config 中提供了 user_provider / user_model / user_api_key 时使用。
    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
    ) -> None:
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client: Optional[Any] = None

    async def _ensure_client(self) -> Any:
        """延迟初始化 AsyncOpenAI 客户端"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise RuntimeError(
                    "openai package is required for direct LLM client. "
                    "Install with: pip install openai"
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url or None,
            )
        return self._client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
        retries: int = 3,
    ) -> Union[str, AsyncGenerator[str, None]]:
        client = await self._ensure_client()

        if stream:
            last_exc = None
            for attempt in range(1, retries + 1):
                try:
                    response = await client.chat.completions.create(
                        model=self._model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                    )

                    async def _stream_gen() -> AsyncGenerator[str, None]:
                        async for chunk in response:
                            delta = chunk.choices[0].delta.content if chunk.choices else None
                            if delta:
                                yield delta

                    return _stream_gen()
                except Exception as exc:
                    last_exc = exc
                    if _is_transient_error(exc) and attempt < retries:
                        wait = min(attempt * 3, 30)
                        logger.warning(
                            "[DirectLLM] stream attempt %d/%d failed: %s, retry %ds",
                            attempt, retries, str(exc)[:150], wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
            raise last_exc  # type: ignore[misc]
        else:
            last_exc = None
            for attempt in range(1, retries + 1):
                try:
                    response = await client.chat.completions.create(
                        model=self._model,
                        messages=messages,  # type: ignore[arg-type]
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=False,
                    )
                    return response.choices[0].message.content or ""
                except Exception as exc:
                    last_exc = exc
                    if _is_transient_error(exc) and attempt < retries:
                        wait = min(attempt * 3, 30)
                        logger.warning(
                            "[DirectLLM] chat attempt %d/%d failed: %s, retry %ds",
                            attempt, retries, str(exc)[:150], wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise
            raise last_exc  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# WorkflowEngine
# ═══════════════════════════════════════════════════════════════════════════

class WorkflowEngine:
    """
    核心工作流引擎 —— 管理工作流的生命周期并执行节点图。

    Parameters
    ----------
    storage : Storage
        持久化后端。
    llm_client_factory : LLMClientFactory
        创建 LLM 客户端的工厂。
    sandbox : Sandbox
        代码执行沙箱。
    """

    def __init__(
        self,
        storage: Storage,
        llm_client_factory: LLMClientFactory,
        sandbox: Sandbox,
    ) -> None:
        self.storage = storage
        self.llm_factory = llm_client_factory
        self.sandbox = sandbox

        # 运行中的执行状态：workflow_id -> ExecutionState
        self._executions: Dict[str, _ExecutionState] = {}

        # v8: 全局变量存储 (跨节点共享)
        self._global_variables: Dict[str, Any] = {}

        # v8: Webhook 触发器缓存 (webhook_id -> pending payload)
        self._webhook_payloads: Dict[str, Dict[str, Any]] = {}

    # -----------------------------------------------------------------------
    # CRUD — 工作流管理
    # -----------------------------------------------------------------------

    async def create_workflow(
        self, session_id: str, name: str = "New Workflow",
        folder_id: str = "",
    ) -> dict:
        """创建一个新的空工作流并持久化。v8 支持 folder_id 参数。"""
        wf = Workflow(id=_uid(), name=name, folder_id=folder_id)
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)
        return _workflow_to_dict(wf)

    async def get_workflow(self, session_id: str, workflow_id: str) -> Optional[dict]:
        """根据 ID 获取工作流"""
        data = await self.storage.load_workflow(session_id, workflow_id)
        return data

    async def list_workflows(self, session_id: str) -> List[dict]:
        """列出当前会话下的所有工作流"""
        return await self.storage.list_workflows(session_id)

    async def delete_workflow(self, session_id: str, workflow_id: str) -> None:
        """删除指定工作流"""
        self._executions.pop(workflow_id, None)
        await self.storage.delete_workflow(session_id, workflow_id)

    # -----------------------------------------------------------------------
    # Folder Management — 目录管理 (v8 新增)
    # -----------------------------------------------------------------------

    async def create_folder(
        self, session_id: str, name: str, parent_id: str = "",
    ) -> dict:
        """
        创建工作流目录。

        Parameters
        ----------
        session_id : str
            会话 ID。
        name : str
            目录名称。
        parent_id : str
            父目录 ID，空字符串表示根目录。

        Returns
        -------
        dict
            目录信息。
        """
        folder = WorkflowFolder(id=_uid(), name=name, parent_id=parent_id)
        await self.storage.save_folder(session_id, folder.id, folder.name, folder.parent_id)
        return _folder_to_dict(folder)

    async def list_folders(
        self, session_id: str, parent_id: str = "",
    ) -> List[dict]:
        """
        列出指定父目录下的子目录。

        Parameters
        ----------
        session_id : str
            会话 ID。
        parent_id : str
            父目录 ID，空字符串表示根目录。

        Returns
        -------
        list[dict]
            子目录列表。
        """
        return await self.storage.list_folders(session_id, parent_id)

    async def delete_folder(
        self, session_id: str, folder_id: str,
    ) -> None:
        """
        删除目录。目录下的工作流不会被自动删除。

        Parameters
        ----------
        session_id : str
            会话 ID。
        folder_id : str
            目录 ID。
        """
        await self.storage.delete_folder(session_id, folder_id)

    async def move_workflow(
        self,
        session_id: str,
        workflow_id: str,
        folder_id: str,
    ) -> dict:
        """
        将工作流移动到指定目录。

        Parameters
        ----------
        session_id : str
            会话 ID。
        workflow_id : str
            工作流 ID。
        folder_id : str
            目标目录 ID，空字符串表示移到根目录。

        Returns
        -------
        dict
            更新后的工作流信息。
        """
        wf = await self._load_workflow(session_id, workflow_id)
        wf.folder_id = folder_id
        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)
        return _workflow_to_dict(wf)

    # -----------------------------------------------------------------------
    # Node Management — 节点管理
    # -----------------------------------------------------------------------

    async def add_node(
        self,
        session_id: str,
        workflow_id: str,
        node_type: str,
        x: float = 0,
        y: float = 0,
        config: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """向工作流中添加一个节点"""
        wf = await self._load_workflow(session_id, workflow_id)
        nid = _uid()
        node = WorkflowNode(
            id=nid,
            type=NodeType(node_type),
            x=x,
            y=y,
            config=config or {},
        )
        wf.nodes[nid] = node
        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)
        return {
            "id": node.id,
            "type": node.type.value,
            "x": node.x,
            "y": node.y,
            "config": node.config,
        }

    async def update_node(
        self,
        session_id: str,
        workflow_id: str,
        node_id: str,
        updates: Dict[str, Any],
    ) -> dict:
        """更新节点的配置 / 位置等属性"""
        wf = await self._load_workflow(session_id, workflow_id)
        node = wf.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found in workflow {workflow_id}")

        if "x" in updates:
            node.x = float(updates["x"])
        if "y" in updates:
            node.y = float(updates["y"])
        if "config" in updates:
            node.config = copy.deepcopy(updates["config"])
        if "type" in updates:
            node.type = NodeType(updates["type"])

        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)
        return {
            "id": node.id,
            "type": node.type.value,
            "x": node.x,
            "y": node.y,
            "config": node.config,
        }

    async def remove_node(
        self, session_id: str, workflow_id: str, node_id: str
    ) -> None:
        """删除节点及其所有连接"""
        wf = await self._load_workflow(session_id, workflow_id)
        if node_id not in wf.nodes:
            raise KeyError(f"Node {node_id} not found")
        wf.nodes.pop(node_id)
        # 同时移除与该节点相关的所有连接
        wf.connections = [
            c
            for c in wf.connections
            if c.source_node != node_id and c.target_node != node_id
        ]
        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)

    async def get_node(
        self, session_id: str, workflow_id: str, node_id: str
    ) -> dict:
        """获取单个节点信息"""
        wf = await self._load_workflow(session_id, workflow_id)
        node = wf.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found")
        return {
            "id": node.id,
            "type": node.type.value,
            "x": node.x,
            "y": node.y,
            "config": node.config,
        }

    # -----------------------------------------------------------------------
    # Connection Management — 连接管理
    # -----------------------------------------------------------------------

    async def add_connection(
        self,
        session_id: str,
        workflow_id: str,
        source_node: str,
        source_port: str,
        target_node: str,
        target_port: str,
        conn_type: str = "any",
        curve_type: str = "bezier",  # v8: 曲线类型参数
        prompt_template: Optional[Dict[str, Any]] = None,  # v8
    ) -> dict:
        """在两个节点端口之间添加一条有向连接。v8 扩展版。"""
        wf = await self._load_workflow(session_id, workflow_id)
        # 验证节点存在
        if source_node not in wf.nodes:
            raise KeyError(f"Source node {source_node} not found")
        if target_node not in wf.nodes:
            raise KeyError(f"Target node {target_node} not found")

        # v8: 解析 prompt_template
        pt_obj = None
        if prompt_template is not None:
            pt_obj = PromptTemplate(
                template=prompt_template.get("template", ""),
                variables=prompt_template.get("variables", {}),
                auto_inject_context=prompt_template.get("auto_inject_context", True),
            )

        conn = WorkflowConnection(
            id=_uid(),
            source_node=source_node,
            source_port=source_port,
            target_node=target_node,
            target_port=target_port,
            connection_type=ConnectionType(conn_type),
            curve_type=CurveType(curve_type),  # v8
            prompt_template=pt_obj,  # v8
        )

        # 检查是否已经存在相同连接（防止重复）
        for existing in wf.connections:
            if (
                existing.source_node == conn.source_node
                and existing.source_port == conn.source_port
                and existing.target_node == conn.target_node
                and existing.target_port == conn.target_port
            ):
                return {
                    "id": existing.id,
                    "source_node": existing.source_node,
                    "source_port": existing.source_port,
                    "target_node": existing.target_node,
                    "target_port": existing.target_port,
                    "connection_type": existing.connection_type.value,
                    "curve_type": existing.curve_type.value,
                }

        wf.connections.append(conn)
        wf.updated_at = _ts()

        # 在保存之前检查是否会形成环路
        try:
            _topological_sort(wf.nodes, wf.connections)
        except ValueError as exc:
            # 回滚：移除刚添加的连接
            wf.connections.pop()
            raise ValueError(f"Cannot add connection: would create a cycle. {exc}")

        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)
        return {
            "id": conn.id,
            "source_node": conn.source_node,
            "source_port": conn.source_port,
            "target_node": conn.target_node,
            "target_port": conn.target_port,
            "connection_type": conn.connection_type.value,
            "curve_type": conn.curve_type.value,
        }

    async def remove_connection(
        self, session_id: str, workflow_id: str, connection_id: str
    ) -> None:
        """删除一条连接"""
        wf = await self._load_workflow(session_id, workflow_id)
        original_len = len(wf.connections)
        wf.connections = [c for c in wf.connections if c.id != connection_id]
        if len(wf.connections) == original_len:
            raise KeyError(f"Connection {connection_id} not found")
        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)

    # -----------------------------------------------------------------------
    # Node Package Import/Export — 节点包导入导出 (v8 新增)
    # -----------------------------------------------------------------------

    async def export_node_package(
        self,
        session_id: str,
        workflow_id: str,
        node_ids: List[str],
        *,
        package_name: str = "",
        package_description: str = "",
        package_version: str = "1.0.0",
        package_author: str = "",
    ) -> bytes:
        """
        导出选定节点及其连接为 ZIP 包。

        Parameters
        ----------
        session_id : str
            会话 ID。
        workflow_id : str
            工作流 ID。
        node_ids : list[str]
            要导出的节点 ID 列表。
        package_name : str
            包名称 (可选)。
        package_description : str
            包描述 (可选)。
        package_version : str
            包版本 (默认 "1.0.0")。
        package_author : str
            作者 (可选)。

        Returns
        -------
        bytes
            ZIP 文件的二进制数据。

        包格式
        ------
        manifest.json     — 包元数据
        nodes.json         — 节点定义
        connections.json   — 节点间的连接
        """
        wf = await self._load_workflow(session_id, workflow_id)

        node_id_set = set(node_ids)

        # 筛选指定节点
        export_nodes: Dict[str, dict] = {}
        for nid in node_ids:
            node = wf.nodes.get(nid)
            if node is None:
                logger.warning("Export: node %s not found, skipping", nid)
                continue
            export_nodes[nid] = {
                "id": node.id,
                "type": node.type.value,
                "x": node.x,
                "y": node.y,
                "config": copy.deepcopy(node.config),
            }

        # 筛选两端均在导出节点内的连接
        export_connections: List[dict] = []
        for c in wf.connections:
            if c.source_node in node_id_set and c.target_node in node_id_set:
                conn_dict: Dict[str, Any] = {
                    "id": c.id,
                    "source_node": c.source_node,
                    "source_port": c.source_port,
                    "target_node": c.target_node,
                    "target_port": c.target_port,
                    "connection_type": c.connection_type.value,
                    "curve_type": c.curve_type.value,
                }
                if c.prompt_template is not None:
                    conn_dict["prompt_template"] = {
                        "template": c.prompt_template.template,
                        "variables": c.prompt_template.variables,
                        "auto_inject_context": c.prompt_template.auto_inject_context,
                    }
                export_connections.append(conn_dict)

        # 构建 manifest
        manifest = {
            "name": package_name or f"package_{_uid()}",
            "description": package_description,
            "version": package_version,
            "author": package_author,
            "created_at": _ts(),
            "node_count": len(export_nodes),
            "connection_count": len(export_connections),
        }

        # 打包为 ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("nodes.json", json.dumps(export_nodes, ensure_ascii=False, indent=2))
            zf.writestr(
                "connections.json",
                json.dumps(export_connections, ensure_ascii=False, indent=2),
            )

        return buf.getvalue()

    async def import_node_package(
        self,
        session_id: str,
        workflow_id: str,
        zip_data: bytes,
    ) -> Dict[str, Any]:
        """
        从 ZIP 包导入节点到工作流。

        Parameters
        ----------
        session_id : str
            会话 ID。
        workflow_id : str
            目标工作流 ID。
        zip_data : bytes
            ZIP 包的二进制数据。

        Returns
        -------
        dict
            包含以下键:
            - "manifest": 包元数据
            - "imported_nodes": 导入的节点 ID 列表
            - "imported_connections": 导入的连接 ID 列表
            - "node_id_mapping": 旧 ID → 新 ID 的映射

        Raises
        ------
        ValueError
            ZIP 包格式不正确或缺少必要文件。
        """
        wf = await self._load_workflow(session_id, workflow_id)

        # 解析 ZIP 包
        buf = io.BytesIO(zip_data)
        try:
            with zipfile.ZipFile(buf, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    raise ValueError("Invalid package: missing manifest.json")
                if "nodes.json" not in zf.namelist():
                    raise ValueError("Invalid package: missing nodes.json")

                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                nodes_data = json.loads(zf.read("nodes.json").decode("utf-8"))
                connections_data = (
                    json.loads(zf.read("connections.json").decode("utf-8"))
                    if "connections.json" in zf.namelist()
                    else []
                )
        except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid package format: {exc}") from exc

        # ID 映射: 旧 ID → 新 ID
        id_mapping: Dict[str, str] = {}
        imported_node_ids: List[str] = []
        imported_conn_ids: List[str] = []

        # 导入节点 (使用新 ID 避免冲突)
        for old_nid, nd in nodes_data.items():
            new_nid = _uid()
            id_mapping[old_nid] = new_nid
            new_node = WorkflowNode(
                id=new_nid,
                type=NodeType(nd["type"]),
                x=nd.get("x", 0) + 50,  # 偏移避免重叠
                y=nd.get("y", 0) + 50,
                config=nd.get("config", {}),
            )
            wf.nodes[new_nid] = new_node
            imported_node_ids.append(new_nid)

        # 导入连接 (使用映射后的新 ID)
        for cd in connections_data:
            new_source = id_mapping.get(cd["source_node"])
            new_target = id_mapping.get(cd["target_node"])
            if new_source is None or new_target is None:
                logger.warning(
                    "Import: skipping connection %s (missing endpoint mapping)",
                    cd.get("id", "?"),
                )
                continue

            pt_obj = None
            pt_data = cd.get("prompt_template")
            if pt_data:
                pt_obj = PromptTemplate(
                    template=pt_data.get("template", ""),
                    variables=pt_data.get("variables", {}),
                    auto_inject_context=pt_data.get("auto_inject_context", True),
                )

            new_conn = WorkflowConnection(
                id=_uid(),
                source_node=new_source,
                source_port=cd["source_port"],
                target_node=new_target,
                target_port=cd["target_port"],
                connection_type=ConnectionType(cd.get("connection_type", "any")),
                curve_type=CurveType(cd.get("curve_type", "bezier")),
                prompt_template=pt_obj,
            )
            wf.connections.append(new_conn)
            imported_conn_ids.append(new_conn.id)

        wf.updated_at = _ts()

        # 保存前验证无环路
        try:
            _topological_sort(wf.nodes, wf.connections)
        except ValueError as exc:
            # 回滚导入
            for nid in imported_node_ids:
                wf.nodes.pop(nid, None)
            wf.connections = [
                c for c in wf.connections if c.id not in set(imported_conn_ids)
            ]
            raise ValueError(f"Cannot import: would create a cycle. {exc}")

        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)

        return {
            "manifest": manifest,
            "imported_nodes": imported_node_ids,
            "imported_connections": imported_conn_ids,
            "node_id_mapping": id_mapping,
        }

    # -----------------------------------------------------------------------
    # Webhook Management — Webhook 管理 (v8 新增)
    # -----------------------------------------------------------------------

    def register_webhook_payload(
        self, webhook_id: str, payload: Dict[str, Any],
    ) -> None:
        """
        注册 Webhook 触发器的待处理载荷。

        Parameters
        ----------
        webhook_id : str
            Webhook 唯一标识符。
        payload : dict
            Webhook 接收到的载荷数据。
        """
        self._webhook_payloads[webhook_id] = {
            "payload": payload,
            "received_at": _ts(),
        }

    # -----------------------------------------------------------------------
    # Execution — 工作流执行
    # -----------------------------------------------------------------------

    async def execute(
        self,
        session_id: str,
        workflow_id: str,
        emit_callback: EmitCallback = None,
    ) -> AsyncGenerator[dict, None]:
        """
        执行工作流并逐步产出 SSE 事件。

        Yields
        ------
        dict
            {"type": "node_start", ...}
            {"type": "node_output", ...}
            {"type": "node_done", ...}
            {"type": "node_error", ...}
            {"type": "workflow_done", ...}
            {"type": "parallel_layers", "layers": [...]}  (v8)
        """
        wf = await self._load_workflow(session_id, workflow_id)

        # 初始化执行状态
        state = _ExecutionState(workflow_id=workflow_id)
        self._executions[workflow_id] = state

        # v8: 重置全局变量 (可选：每次执行清空)
        # self._global_variables.clear()

        # 拓扑排序
        try:
            order = _topological_sort(wf.nodes, wf.connections)
        except ValueError as exc:
            yield {"type": "workflow_error", "error": str(exc)}
            self._executions.pop(workflow_id, None)
            return

        # v8: 计算并行层级并通知前端
        try:
            parallel_layers = _topological_sort_parallel(wf.nodes, wf.connections)
            await _emit(emit_callback, {
                "type": "parallel_layers",
                "layers": parallel_layers,
                "total_layers": len(parallel_layers),
            })
        except ValueError:
            pass  # 如果有环路，上面已经捕获

        # 所有节点的输出收集器
        all_outputs: Dict[str, Dict[str, Any]] = {}
        final_results: Dict[str, Any] = {}

        for node_id in order:
            # 检查是否已取消
            if state.stopped:
                yield {"type": "workflow_cancelled"}
                break

            node = wf.nodes[node_id]
            node._status = "running"
            node._iteration = 0
            node._loop_results = []

            try:
                # 如果是循环节点，需要特殊处理
                if node.type == NodeType.LOOP:
                    outputs = await self._execute_loop(
                        wf=wf,
                        node=node,
                        all_outputs=all_outputs,
                        state=state,
                        emit_callback=emit_callback,
                    )
                elif node.type == NodeType.ROUTER:
                    # v8: Router 节点需要 emit_callback 来路由输出
                    inputs = _gather_inputs(node_id, wf.connections, all_outputs)
                    node._inputs = inputs

                    await _emit(emit_callback, {
                        "type": "node_start",
                        "node_id": node_id,
                        "node_type": node.type.value,
                    })

                    outputs = await self._exec_router(
                        node, inputs, emit_callback
                    )
                else:
                    # 收集输入
                    inputs = _gather_inputs(node_id, wf.connections, all_outputs)
                    node._inputs = inputs

                    # v8: 如果有 prompt_template 类型的输入连接，自动注入
                    inputs = await self._apply_prompt_templates(
                        node_id, wf.connections, all_outputs, inputs
                    )

                    await _emit(emit_callback, {
                        "type": "node_start",
                        "node_id": node_id,
                        "node_type": node.type.value,
                    })

                    # 执行节点
                    outputs = await self._execute_node(
                        wf, node, inputs, emit_callback
                    )

                node._outputs = outputs
                node._status = "done"
                all_outputs[node_id] = outputs

                # 持久化中间结果
                await self.storage.save_node_result(
                    session_id, workflow_id, node_id, outputs
                )

                # 逐端口发送输出事件
                for port_name, port_data in outputs.items():
                    await _emit(emit_callback, {
                        "type": "node_output",
                        "node_id": node_id,
                        "port": port_name,
                        "data": port_data,
                    })

                await _emit(emit_callback, {
                    "type": "node_done",
                    "node_id": node_id,
                    "outputs": outputs,
                })

                # 收集最终输出（OUTPUT 节点）
                if node.type == NodeType.OUTPUT:
                    final_results[node_id] = outputs

            except Exception as exc:
                node._status = "error"
                logger.exception(
                    "Error executing node %s (%s)", node_id, node.type.value
                )
                await _emit(emit_callback, {
                    "type": "node_error",
                    "node_id": node_id,
                    "error": str(exc),
                })

                # 持久化错误信息
                await self.storage.save_node_result(
                    session_id, workflow_id, node_id, {"_error": str(exc)}
                )

                # 错误仍然写入空输出，以免下游全部断裂
                all_outputs[node_id] = {}

        # 执行完成
        await _emit(emit_callback, {
            "type": "workflow_done",
            "results": final_results,
        })
        self._executions.pop(workflow_id, None)

    async def _apply_prompt_templates(
        self,
        node_id: str,
        connections: List[WorkflowConnection],
        all_outputs: Dict[str, Dict[str, Any]],
        inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        应用 prompt 模板连接的自动注入。v8 新增。

        遍历指向当前节点的所有连接，如果连接携带 PromptTemplate，
        则将上游节点的输出渲染到模板中并注入到输入中。
        """
        enriched_inputs = copy.deepcopy(inputs)
        for conn in connections:
            if conn.target_node != node_id:
                continue
            if conn.prompt_template is None:
                continue

            src_outputs = all_outputs.get(conn.source_node, {})
            # 构建上下文：将源节点所有端口输出作为变量
            context: Dict[str, Any] = {}
            if conn.prompt_template.auto_inject_context:
                for port_name, port_data in src_outputs.items():
                    context[port_name] = port_data
                    # 也支持 "source_node_id.port_name" 格式
                    context[f"{conn.source_node}.{port_name}"] = port_data

            # 添加模板中显式映射的变量
            for var_name, port_ref in conn.prompt_template.variables.items():
                if "." in port_ref:
                    ref_node, ref_port = port_ref.split(".", 1)
                    ref_outputs = all_outputs.get(ref_node, {})
                    context[var_name] = ref_outputs.get(ref_port)
                else:
                    context[var_name] = src_outputs.get(port_ref)

            # 渲染模板
            rendered = render_prompt_template(conn.prompt_template, context)

            # 将渲染结果注入到目标端口
            if conn.target_port not in enriched_inputs:
                enriched_inputs[conn.target_port] = rendered
            else:
                # 追加到已有输入
                existing = enriched_inputs[conn.target_port]
                if isinstance(existing, str):
                    enriched_inputs[conn.target_port] = f"{existing}\n{rendered}"
                elif isinstance(existing, list):
                    existing.append(rendered)

        return enriched_inputs

    async def _execute_loop(
        self,
        wf: Workflow,
        node: WorkflowNode,
        all_outputs: Dict[str, Dict[str, Any]],
        state: _ExecutionState,
        emit_callback: EmitCallback,
    ) -> Dict[str, Any]:
        """
        执行循环节点：迭代执行其下游子图，最多 max_iterations 次。

        Loop 节点的 config:
            max_iterations: int = 10
            condition: str  (可选，Python 表达式，返回 False 时停止)

        输出端口:
            "result" — 最后一次迭代的合并结果
            "iterations" — 每次迭代的结果列表
        """
        max_iter = node.config.get("max_iterations", 10)
        condition_expr = node.config.get("condition")
        results: List[Dict[str, Any]] = []

        for i in range(max_iter):
            if state.stopped:
                break

            node._iteration = i
            await _emit(emit_callback, {
                "type": "node_start",
                "node_id": node.id,
                "node_type": "loop",
                "iteration": i,
            })

            # 收集 loop 节点的输入
            inputs = _gather_inputs(node.id, wf.connections, all_outputs)

            # 添加迭代上下文
            inputs["__iteration__"] = i
            inputs["__previous_result__"] = results[-1] if results else None

            # 执行条件判断
            if condition_expr:
                try:
                    cond_result = await self._eval_condition(condition_expr, inputs)
                    if not cond_result:
                        break
                except Exception as exc:
                    logger.warning(
                        "Loop condition evaluation failed at iteration %d: %s",
                        i, exc,
                    )
                    break

            results.append({"iteration": i, "inputs": inputs})

            await _emit(emit_callback, {
                "type": "node_output",
                "node_id": node.id,
                "port": "_iteration",
                "data": {"iteration": i, "inputs": inputs},
            })

        node._loop_results = results
        last_result = results[-1] if results else {}

        return {
            "result": last_result,
            "iterations": results,
        }

    async def _execute_node(
        self,
        workflow: Workflow,
        node: WorkflowNode,
        inputs: Dict[str, Any],
        emit_callback: EmitCallback,
    ) -> Dict[str, Any]:
        """
        根据节点类型执行具体逻辑并返回输出端口数据。
        v8 扩展版 — 包含所有新节点类型的路由。
        """
        node_type = node.type

        # ---- v7 遗留节点 ----
        if node_type == NodeType.PROMPT:
            return await self._exec_prompt(node, inputs)
        elif node_type == NodeType.LLM:
            return await self._exec_llm(node, inputs, emit_callback)
        elif node_type == NodeType.CODE:
            return await self._exec_code(node, inputs)
        elif node_type == NodeType.FILE_INPUT:
            return await self._exec_file_input(node, inputs)
        elif node_type == NodeType.IMAGE_INPUT:
            return await self._exec_image_input(node, inputs)
        elif node_type == NodeType.VIDEO_INPUT:
            return await self._exec_video_input(node, inputs)
        elif node_type == NodeType.AUDIO_INPUT:
            return await self._exec_audio_input(node, inputs)
        elif node_type == NodeType.TEXT_TRANSFORM:
            return await self._exec_text_transform(node, inputs)
        elif node_type == NodeType.FILE_OUTPUT:
            return await self._exec_file_output(node, inputs)
        elif node_type == NodeType.IMAGE_OUTPUT:
            return await self._exec_image_output(node, inputs)
        elif node_type == NodeType.CONDITION:
            return await self._exec_condition(node, inputs)
        elif node_type == NodeType.MERGE:
            return await self._exec_merge(node, inputs)
        elif node_type == NodeType.DELAY:
            return await self._exec_delay(node, inputs)
        elif node_type == NodeType.HTTP:
            return await self._exec_http(node, inputs)
        elif node_type == NodeType.OUTPUT:
            return await self._exec_output(node, inputs)
        elif node_type == NodeType.LOOP:
            # 循环节点在 execute() 中由 _execute_loop 处理
            return {"result": None, "iterations": []}

        # ---- v8 新增节点 ----
        elif node_type == NodeType.SEARCH:
            return await self._exec_search(node, inputs)
        elif node_type == NodeType.SCRAPER:
            return await self._exec_scraper(node, inputs)
        elif node_type == NodeType.EMBEDDING:
            return await self._exec_embedding(node, inputs)
        elif node_type == NodeType.SUBWORKFLOW:
            return await self._exec_subworkflow(node, inputs)
        elif node_type == NodeType.NOTE:
            return await self._exec_note(node, inputs)
        elif node_type == NodeType.VARIABLE:
            return await self._exec_variable(node, inputs)
        elif node_type == NodeType.TIMER:
            return await self._exec_timer(node, inputs)
        elif node_type == NodeType.WEBHOOK_TRIGGER:
            return await self._exec_webhook_trigger(node, inputs)
        elif node_type == NodeType.API_CALL:
            return await self._exec_api_call(node, inputs)
        elif node_type == NodeType.ROUTER:
            # Router 节点在 execute() 中单独处理
            return {"text": ""}

        else:
            raise NotImplementedError(f"Unknown node type: {node_type}")

    # ═══════════════════════════════════════════════════════════════════════
    # Node Executors — v7 遗留节点执行逻辑
    # ═══════════════════════════════════════════════════════════════════════

    async def _exec_prompt(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        PROMPT 节点：将 config.prompt 中的文本作为输出。
        如果有上游连接到 text 端口，可以与之合并。
        """
        prompt = node.config.get("prompt", "")
        # 如果有输入文本，追加到 prompt 后面
        input_text = inputs.get("text", "")
        if input_text:
            if isinstance(input_text, list):
                input_text = "\n".join(str(t) for t in input_text)
            prompt = f"{prompt}\n{input_text}"
        return {"text": prompt}

    async def _exec_llm(
        self,
        node: WorkflowNode,
        inputs: Dict[str, Any],
        emit_callback: EmitCallback,
    ) -> Dict[str, Any]:
        """
        LLM 节点：调用大语言模型 API。v8 增强版。

        config:
            provider: str          — 提供商 (e.g. "openai")
            model: str             — 模型名称
            system_prompt: str     — 系统提示 (可选)
            temperature: float     — 温度 (默认 0.7)
            max_tokens: int        — 最大 token 数 (默认 2048)
            stream: bool           — 是否流式输出 (默认 False)

            v8 新增:
            user_provider: str     — 用户自定义提供商
            user_model: str        — 用户自定义模型
            user_api_key: str      — 用户自定义 API Key
            user_base_url: str     — 用户自定义 API 基础 URL

        输入端口:
            text — 用户消息 / 对话上下文

        输出端口:
            text   — 完整回复文本
            tokens — (仅流式时) token 流的标记
        """
        provider = node.config.get("provider", "openai")
        model = node.config.get("model", "gpt-4o-mini")
        system_prompt = node.config.get("system_prompt", "")
        temperature = float(node.config.get("temperature", 0.7))
        max_tokens = int(node.config.get("max_tokens", 2048))
        stream = bool(node.config.get("stream", False))

        # v8: 检查是否使用用户自定义 API 凭据
        user_provider = node.config.get("user_provider", "")
        user_model = node.config.get("user_model", "")
        user_api_key = node.config.get("user_api_key", "")
        user_base_url = node.config.get("user_base_url", "")

        # 构造消息列表
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # 输入文本作为 user message
        user_content = ""
        text_input = inputs.get("text", "")
        if text_input:
            if isinstance(text_input, list):
                user_content = "\n".join(str(t) for t in text_input)
            else:
                user_content = str(text_input)

        if not user_content:
            user_content = node.config.get("prompt", "")

        if user_content:
            messages.append({"role": "user", "content": user_content})

        if not messages:
            messages.append({"role": "user", "content": ""})

        # v8: 创建 LLM 客户端（优先使用用户自定义凭据）
        if user_api_key:
            # 使用直连客户端，防止 API Key 暴露在 Python 脚本中
            client = await self._create_direct_llm_client(
                provider=user_provider or provider,
                model=user_model or model,
                api_key=user_api_key,
                base_url=user_base_url,
            )
        else:
            client = self.llm_factory.create(provider, model)

        if stream:
            # 流式输出
            full_text = ""
            token_count = 0
            async for token in client.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ):
                full_text += token
                token_count += 1
                await _emit(emit_callback, {
                    "type": "node_output",
                    "node_id": node.id,
                    "port": "tokens",
                    "data": token,
                    "token_count": token_count,
                })
            return {"text": full_text, "tokens": full_text}
        else:
            # 非流式
            result = await client.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            return {"text": result, "tokens": ""}

    async def _create_direct_llm_client(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
    ) -> LLMClient:
        """
        创建直连 LLM 客户端。v8 新增。

        使用用户提供的 API 凭据直接创建 OpenAI 兼容客户端，
        绕过 LLMClientFactory，防止 API Key 暴露在 Python 脚本中。

        Parameters
        ----------
        provider : str
            提供商名称。
        model : str
            模型名称。
        api_key : str
            API Key。
        base_url : str
            API 基础 URL (可选)。

        Returns
        -------
        LLMClient
            直连 LLM 客户端实例。
        """
        return _DirectLLMClient(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    async def _exec_code(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        CODE 节点：在沙箱中执行 Python 代码。

        config:
            code: str            — Python 代码
            timeout: float       — 超时秒数 (默认 30)

        输入端口:
            code — 可选的代码覆盖
            text — 可用作 stdin / 上下文

        输出端口:
            result — stdout
            error  — stderr
            files  — 生成的文件列表
        """
        code = inputs.get("code") or node.config.get("code", "")
        if isinstance(code, list):
            code = "\n".join(str(c) for c in code)

        if not code.strip():
            return {"result": "", "error": "No code provided", "files": []}

        timeout = float(node.config.get("timeout", 30))

        # 准备输入文件
        files_in: Optional[Dict[str, bytes]] = None
        file_input = inputs.get("file")
        if file_input:
            files_in = {
                "input": file_input.encode()
                if isinstance(file_input, str)
                else file_input
            }

        result = await self.sandbox.execute(
            code=code,
            inputs=inputs,
            timeout=timeout,
            files_in=files_in,
        )

        return {
            "result": result.get("stdout", ""),
            "error": result.get("stderr", ""),
            "files": result.get("files", []),
        }

    async def _exec_file_input(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        FILE_INPUT 节点：读取上传的文件。

        config:
            filename: str  — 文件名
            file_ref: str  — 存储中的文件引用 ID

        输出端口:
            file — 文件引用 (dict with ref, filename, mime_type)
            text — 如果是文本文件，返回内容
        """
        filename = node.config.get("filename", "unknown")
        file_ref = node.config.get("file_ref", "")
        file_data = inputs.get("file")

        file_info = {
            "filename": filename,
            "ref": file_ref,
            "data": file_data,
            "mime_type": node.config.get("mime_type", "application/octet-stream"),
        }

        # 尝试提取文本内容
        text_content = ""
        if file_data:
            if isinstance(file_data, str):
                text_content = file_data
            elif isinstance(file_data, bytes):
                try:
                    text_content = file_data.decode("utf-8")
                except UnicodeDecodeError:
                    text_content = "<binary file>"

        return {"file": file_info, "text": text_content}

    async def _exec_image_input(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        IMAGE_INPUT 节点：处理图片输入。

        config:
            filename: str  — 文件名
            base64_data: str  — base64 编码的图片数据
            analyze: bool  — 是否使用 VLM 分析图片 (默认 False)

        输出端口:
            image       — base64 图片数据
            description — (仅 analyze=True 时) VLM 生成的图片描述
        """
        base64_data = node.config.get("base64_data", "") or inputs.get("image", "")
        filename = node.config.get("filename", "image.png")
        analyze = bool(node.config.get("analyze", False))

        image_info = {
            "filename": filename,
            "base64": base64_data,
            "mime_type": node.config.get("mime_type", "image/png"),
        }

        description = ""
        if analyze and base64_data:
            try:
                vlm_provider = node.config.get("vlm_provider", "openai")
                vlm_model = node.config.get("vlm_model", "gpt-4o-mini")
                client = self.llm_factory.create(vlm_provider, vlm_model)
                messages = [
                    {
                        "role": "user",
                        "content": json.dumps({
                            "type": "image_analysis",
                            "image_base64": base64_data[:100] + "...",
                            "instruction": "Describe this image in detail.",
                        }),
                    }
                ]
                description = await client.chat(messages, stream=False)
            except Exception as exc:
                description = f"Image analysis failed: {exc}"

        return {"image": image_info, "description": description}

    async def _exec_video_input(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        VIDEO_INPUT 节点：处理视频输入。

        config:
            filename: str
            video_ref: str  — 存储引用

        输出端口:
            video — 视频信息 (dict)
            text  — 如果有字幕 / 描述
        """
        filename = node.config.get("filename", "video.mp4")
        video_ref = node.config.get("video_ref", "")

        video_info = {
            "filename": filename,
            "ref": video_ref,
            "mime_type": node.config.get("mime_type", "video/mp4"),
        }

        text_content = inputs.get("text", "")
        if isinstance(text_content, list):
            text_content = "\n".join(str(t) for t in text_content)

        return {"video": video_info, "text": text_content}

    async def _exec_audio_input(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        AUDIO_INPUT 节点：处理音频输入。

        config:
            filename: str
            audio_ref: str

        输出端口:
            audio — 音频信息 (dict)
            text  — 如果有转录文本
        """
        filename = node.config.get("filename", "audio.mp3")
        audio_ref = node.config.get("audio_ref", "")

        audio_info = {
            "filename": filename,
            "ref": audio_ref,
            "mime_type": node.config.get("mime_type", "audio/mpeg"),
        }

        text_content = inputs.get("text", "")
        if isinstance(text_content, list):
            text_content = "\n".join(str(t) for t in text_content)

        return {"audio": audio_info, "text": text_content}

    async def _exec_text_transform(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        TEXT_TRANSFORM 节点：文本处理。

        config:
            operation: str   — split | merge | replace | format | extract_json | template
            params: dict     — 操作参数

        操作说明:
            split:    params.separator (默认 "\\n")
            merge:    params.separator (默认 "\\n"), 将 list 输入合并为字符串
            replace:  params.pattern, params.replacement
            format:   params.template (Python format string, 可引用 {text}, {input}, etc.)
            extract_json: 从文本中提取 JSON 对象
            template: params.template (Jinja-like 简易模板)
        """
        operation = node.config.get("operation", "merge")
        params = node.config.get("params", {})

        # 获取输入文本
        text_input = inputs.get("text", "")
        if isinstance(text_input, list):
            text_input = "\n".join(str(t) for t in text_input)
        text_input = str(text_input)

        result = text_input

        if operation == "split":
            separator = params.get("separator", "\n")
            parts = text_input.split(separator)
            result = json.dumps(parts, ensure_ascii=False)

        elif operation == "merge":
            # 输入可能是列表，合并为字符串
            if isinstance(inputs.get("text"), list):
                separator = params.get("separator", "\n")
                result = separator.join(str(t) for t in inputs["text"])

        elif operation == "replace":
            pattern = params.get("pattern", "")
            replacement = params.get("replacement", "")
            if pattern:
                result = text_input.replace(pattern, replacement)

        elif operation == "format":
            template = params.get("template", "{text}")
            try:
                ctx = dict(inputs)
                ctx["text"] = text_input
                result = template.format(**ctx)
            except (KeyError, IndexError) as exc:
                result = f"[Format error: {exc}]"

        elif operation == "extract_json":
            json_patterns = [
                r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
                r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]',
                r'\{[^{}]+\}',
                r'\[[^\[\]]+\]',
            ]
            for pat in json_patterns:
                matches = re.findall(pat, text_input)
                for match in matches:
                    try:
                        parsed = json.loads(match)
                        result = json.dumps(parsed, ensure_ascii=False, indent=2)
                        break
                    except json.JSONDecodeError:
                        continue
                else:
                    continue
                break

        elif operation == "template":
            # 简易模板：替换 {{ variable }} 为 inputs 中的值
            template = params.get("template", text_input)

            def _replace_var(m: re.Match) -> str:
                var_name = m.group(1).strip()
                value = inputs.get(var_name, params.get(var_name, m.group(0)))
                return str(value)

            result = re.sub(r'\{\{\s*(\w+)\s*\}\}', _replace_var, template)

        return {"text": result}

    async def _exec_file_output(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        FILE_OUTPUT 节点：生成文件输出。

        config:
            filename: str    — 输出文件名
            format: str      — 格式 (txt, json, csv, md)

        输入端口:
            text — 文件内容
            data — 结构化数据 (用于 JSON/CSV)

        输出端口:
            result — 文件信息 (dict with content, filename)
        """
        filename = node.config.get("filename", "output.txt")
        fmt = node.config.get("format", "txt")

        content = inputs.get("text", "")
        if isinstance(content, list):
            content = "\n".join(str(c) for c in content)
        content = str(content)

        if fmt == "json":
            data = inputs.get("data", content)
            try:
                if isinstance(data, str):
                    parsed = json.loads(data)
                    content = json.dumps(parsed, ensure_ascii=False, indent=2)
                else:
                    content = json.dumps(data, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, TypeError):
                pass

        elif fmt == "csv":
            data = inputs.get("data")
            if data and isinstance(data, list):
                import csv
                output = io.StringIO()
                if data and isinstance(data[0], dict):
                    writer = csv.DictWriter(output, fieldnames=data[0].keys())
                    writer.writeheader()
                    writer.writerows(data)
                else:
                    writer = csv.writer(output)
                    for row in data:
                        writer.writerow(row)
                content = output.getvalue()

        elif fmt == "md":
            pass

        file_info = {
            "filename": filename,
            "content": content,
            "format": fmt,
            "size": len(content),
        }

        return {"result": file_info, "file": file_info}

    async def _exec_image_output(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        IMAGE_OUTPUT 节点：图片生成输出。

        config:
            filename: str
            width: int
            height: int

        输入端口:
            image — 图片数据 (base64)

        输出端口:
            result — 图片信息
        """
        image_data = inputs.get("image", "")
        filename = node.config.get("filename", "output.png")

        result = {
            "filename": filename,
            "image": image_data,
            "width": node.config.get("width", 0),
            "height": node.config.get("height", 0),
        }

        return {"result": result}

    async def _exec_condition(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        CONDITION 节点：条件分支判断。

        config:
            expression: str  — Python 表达式，在沙箱中求值

        输入端口:
            text / json — 用于求值的上下文

        输出端口:
            true  — 条件为真时的数据
            false — 条件为假时的数据
        """
        expression = node.config.get("expression", "True")
        is_true = await self._eval_condition(expression, inputs)

        if is_true:
            return {"true": inputs.get("text", True), "false": None}
        else:
            return {"true": None, "false": inputs.get("text", False)}

    async def _eval_condition(
        self, expression: str, context: Dict[str, Any]
    ) -> bool:
        """
        在沙箱中安全地求值条件表达式。
        支持简单的比较、逻辑运算和变量引用。
        """
        safe_globals: Dict[str, Any] = {
            "__builtins__": {
                "True": True,
                "False": False,
                "None": None,
                "len": len,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "list": list,
                "dict": dict,
                "isinstance": isinstance,
                "hasattr": hasattr,
                "getattr": getattr,
            }
        }
        safe_locals: Dict[str, Any] = {
            "text": context.get("text", ""),
            "input": context.get("text", ""),
            "data": context.get("data", context),
            "iteration": context.get("__iteration__", 0),
        }

        # 在沙箱中执行以确保安全
        try:
            code = f"result = ({expression})"
            result = await self.sandbox.execute(
                code=code,
                inputs=safe_locals,
                timeout=5.0,
            )
            stdout = result.get("stdout", "")
            if stdout.strip():
                return bool(ast.literal_eval(stdout.strip()))
            return False
        except Exception:
            # 回退：直接 eval（仅限简单表达式）
            try:
                tree = ast.parse(expression, mode="eval")
                for n in ast.walk(tree):
                    if isinstance(n, ast.Call):
                        return False
                return bool(eval(expression, safe_globals, safe_locals))  # noqa: S307
            except Exception:
                return False

    async def _exec_merge(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        MERGE 节点：合并多个输入为单个文本。

        config:
            separator: str   — 分隔符 (默认 "\\n")
            template: str   — 模板 (可选，支持 {{text_0}}, {{text_1}}, ...)

        输入端口:
            (多个 text 端口或任意端口)

        输出端口:
            text — 合并后的文本
        """
        separator = node.config.get("separator", "\n")
        template = node.config.get("template", "")

        # 收集所有输入值
        values: List[str] = []
        for key, value in inputs.items():
            if key.startswith("_"):
                continue
            if isinstance(value, list):
                values.extend(str(v) for v in value)
            else:
                values.append(str(value))

        if template:
            def _replace_var(m: re.Match) -> str:
                idx = int(m.group(1))
                return values[idx] if idx < len(values) else ""
            result = re.sub(r'\{\{\s*text_(\d+)\s*\}\}', _replace_var, template)
        else:
            result = separator.join(values)

        return {"text": result}

    async def _exec_delay(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        DELAY 节点：等待指定时间后继续。

        config:
            seconds: float  — 延迟秒数 (默认 1)

        输出端口:
            text — 输入数据原样传递
        """
        seconds = float(node.config.get("seconds", 1.0))
        await asyncio.sleep(seconds)
        return {"text": inputs.get("text", "")}

    async def _exec_http(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        HTTP 节点：发起 HTTP 请求。

        config:
            url: str          — 请求 URL
            method: str       — HTTP 方法 (默认 GET)
            headers: dict     — 请求头
            body: str         — 请求体
            timeout: float    — 超时秒数 (默认 30)

        输入端口:
            text — 可作为 URL / body 的动态值

        输出端口:
            text  — 响应体文本
            json  — 解析后的 JSON (如果响应是 JSON)
            status — HTTP 状态码
        """
        import aiohttp  # type: ignore

        url = node.config.get("url", "")
        method = node.config.get("method", "GET").upper()
        headers = node.config.get("headers", {})
        body = node.config.get("body", "")
        timeout_sec = float(node.config.get("timeout", 30))

        # 动态替换 URL 中的输入变量
        text_input = inputs.get("text", "")
        if isinstance(text_input, str) and text_input:
            url = url.replace("{input}", text_input)

        result_text = ""
        result_json: Any = None
        status_code = 0

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body if method in ("POST", "PUT", "PATCH") else None,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ) as resp:
                    status_code = resp.status
                    result_text = await resp.text()
                    try:
                        result_json = json.loads(result_text)
                    except json.JSONDecodeError:
                        result_json = None
        except asyncio.TimeoutError:
            result_text = f"[HTTP Timeout after {timeout_sec}s]"
        except Exception as exc:
            result_text = f"[HTTP Error: {exc}]"

        return {
            "text": result_text,
            "json": result_json,
            "status": status_code,
        }

    async def _exec_output(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        OUTPUT 节点：最终输出，收集所有输入并保存。

        config:
            format: str  — 输出格式 (默认 "text")

        输入端口:
            (任意)

        输出端口:
            result — 收集到的所有输入
        """
        result: Dict[str, Any] = {}
        for key, value in inputs.items():
            if key.startswith("_"):
                continue
            result[key] = value

        if len(result) == 1 and "text" in result:
            return {"result": result["text"]}

        return {"result": result}

    # ═══════════════════════════════════════════════════════════════════════
    # Node Executors — v8 新增节点执行逻辑
    # ═══════════════════════════════════════════════════════════════════════

    async def _exec_search(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        SEARCH 节点：Web 搜索。v8 新增。

        config:
            query: str          — 搜索查询 (可用 {{variable}} 占位符)
            provider: str       — 搜索提供商 ("bing" | "google" | "generic")
            max_results: int    — 最大结果数 (默认 5)
            api_url: str        — 自定义搜索 API URL
            api_key: str        — 搜索 API Key (可选)

        输入端口:
            text — 可作为查询的动态值

        输出端口:
            text  — 搜索结果摘要文本
            json  — 结构化搜索结果列表
        """
        query_template = node.config.get("query", "")
        provider = node.config.get("provider", "generic")
        max_results = int(node.config.get("max_results", 5))
        api_url = node.config.get("api_url", "")
        api_key = node.config.get("api_key", "")

        # 构建查询：替换模板中的输入变量
        query = query_template
        text_input = inputs.get("text", "")
        if text_input:
            query = query.replace("{{input}}", str(text_input))
            if not query:
                query = str(text_input)

        # 通过 HTTP 调用搜索 API
        search_results: List[Dict[str, Any]] = []

        if api_url:
            # 使用自定义 API
            try:
                import aiohttp

                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {"query": query, "max_results": max_results}

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        api_url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list):
                                search_results = data[:max_results]
                            elif isinstance(data, dict):
                                search_results = data.get("results", [])[:max_results]
            except Exception as exc:
                logger.warning("Search API error: %s", exc)
                search_results = [{"error": str(exc)}]
        else:
            # 无 API URL 时返回模拟结果
            search_results = [
                {
                    "title": f"[Search Result {i+1}]",
                    "url": f"https://example.com/result/{i+1}",
                    "snippet": f"模拟搜索结果 — 查询: {query[:80]}",
                }
                for i in range(min(max_results, 3))
            ]

        # 生成摘要文本
        summary_parts: List[str] = []
        for r in search_results:
            if isinstance(r, dict):
                title = r.get("title", "")
                snippet = r.get("snippet", r.get("text", ""))
                url = r.get("url", "")
                if title:
                    summary_parts.append(f"- {title}: {snippet}")
                elif snippet:
                    summary_parts.append(f"- {snippet}")

        summary = "\n".join(summary_parts) if summary_parts else "无搜索结果"

        return {
            "text": summary,
            "json": search_results,
        }

    async def _exec_scraper(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        SCRAPER 节点：网页抓取。v8 新增。

        config:
            url: str           — 目标 URL
            selector: str      — CSS 选择器 (可选，提取特定内容)
            max_chars: int     — 最大字符数 (默认 50000)
            headers: dict      — 自定义请求头

        输入端口:
            text — 可作为 URL 的动态值

        输出端口:
            text  — 抓取到的文本内容
            html  — 原始 HTML (截断)
            json  — 结构化提取结果
        """
        url = node.config.get("url", "")
        selector = node.config.get("selector", "")
        max_chars = int(node.config.get("max_chars", 50000))
        custom_headers = node.config.get("headers", {})

        # 动态 URL
        text_input = inputs.get("text", "")
        if isinstance(text_input, str) and text_input:
            url = url.replace("{input}", text_input)
            if not url:
                url = text_input

        raw_html = ""
        extracted_text = ""
        structured_data: Any = None

        try:
            import aiohttp

            req_headers = {
                "User-Agent": "Mozilla/5.0 (compatible; ScriptWeaver/8.0)",
                **custom_headers,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=req_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    raw_html = await resp.text()

            # 截断原始 HTML
            truncated_html = raw_html[:max_chars * 2]

            # 简单 HTML → 文本提取 (去除标签)
            extracted_text = re.sub(
                r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE,
            )
            extracted_text = re.sub(
                r'<style[^>]*>.*?</style>', '', extracted_text, flags=re.DOTALL | re.IGNORECASE,
            )
            extracted_text = re.sub(r'<[^>]+>', ' ', extracted_text)
            extracted_text = re.sub(r'\s+', ' ', extracted_text).strip()
            extracted_text = extracted_text[:max_chars]

            # 如果有 CSS 选择器，尝试结构化提取
            if selector:
                structured_data = {
                    "selector": selector,
                    "note": "CSS selector extraction requires a DOM library (e.g. BeautifulSoup). "
                            "Install for full functionality.",
                    "raw_text_preview": extracted_text[:500],
                }

        except asyncio.TimeoutError:
            extracted_text = f"[Scraper Timeout]"
        except Exception as exc:
            extracted_text = f"[Scraper Error: {exc}]"

        return {
            "text": extracted_text,
            "html": raw_html[:max_chars] if raw_html else "",
            "json": structured_data,
        }

    async def _exec_embedding(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        EMBEDDING 节点：文本嵌入/向量化。v8 新增。

        config:
            model: str          — 嵌入模型名称
            provider: str       — 提供商
            dimensions: int     — 向量维度 (默认 1536)

        输入端口:
            text — 要嵌入的文本

        输出端口:
            embedding — 向量数据 (list[float])
            text      — 原始文本
            dimensions — 向量维度
        """
        model = node.config.get("model", "text-embedding-3-small")
        provider = node.config.get("provider", "openai")
        dimensions = int(node.config.get("dimensions", 1536))

        text_input = inputs.get("text", "")
        if isinstance(text_input, list):
            text_input = "\n".join(str(t) for t in text_input)
        text_input = str(text_input)

        # 尝试通过 LLM factory 获取嵌入
        embedding: List[float] = []
        try:
            client = self.llm_factory.create(provider, model)
            # 如果 LLM 客户端支持 embed 方法
            if hasattr(client, "embed") and callable(client.embed):
                embedding = await client.embed(text_input, dimensions=dimensions)  # type: ignore
            else:
                # 生成零向量作为占位符 (实际使用时需接入真正的嵌入服务)
                logger.warning(
                    "LLM client does not support embed(), "
                    "returning zero vector placeholder"
                )
                embedding = [0.0] * dimensions
        except Exception as exc:
            logger.warning("Embedding generation failed: %s", exc)
            embedding = [0.0] * dimensions

        return {
            "embedding": embedding,
            "text": text_input,
            "dimensions": dimensions,
        }

    async def _exec_router(
        self,
        node: WorkflowNode,
        inputs: Dict[str, Any],
        emit_callback: EmitCallback,
    ) -> Dict[str, Any]:
        """
        ROUTER 节点：LLM 智能路由。v8 新增。

        使用 LLM 分析输入并决定将数据路由到哪个输出端口。

        config:
            criteria_prompt: str   — 路由判断提示 (给 LLM 的路由指令)
            output_ports: list     — 可选输出端口名称列表
                e.g. ["positive", "negative", "neutral"]
            provider: str          — LLM 提供商 (默认 "openai")
            model: str             — LLM 模型 (默认 "gpt-4o-mini")
            system_prompt: str     — 系统提示 (可选)

        输入端口:
            text — 要路由的数据

        输出端口:
            动态端口 — 根据 LLM 判断结果输出到对应端口
                e.g. {"positive": data, "negative": null, "neutral": null}
            routed_port — LLM 选择的目标端口名称
            reasoning   — LLM 的路由推理过程
        """
        criteria_prompt = node.config.get("criteria_prompt", "")
        output_ports = node.config.get("output_ports", ["default"])
        provider = node.config.get("provider", "openai")
        model = node.config.get("model", "gpt-4o-mini")
        system_prompt = node.config.get("system_prompt", "")

        text_input = inputs.get("text", "")
        if isinstance(text_input, list):
            text_input = "\n".join(str(t) for t in text_input)

        # 构建路由判断消息
        routing_instruction = (
            f"{criteria_prompt}\n\n"
            f"Available output ports: {', '.join(output_ports)}\n"
            f"Input text: {text_input[:2000]}\n\n"
            f"Respond with ONLY ONE of these port names: {', '.join(output_ports)}\n"
            f"Also briefly explain your reasoning."
        )

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": routing_instruction})

        # 调用 LLM（带重试）
        routed_port = output_ports[0]  # 默认
        reasoning = ""

        max_retries = 3
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                client = self.llm_factory.create(provider, model)
                llm_response = await client.chat(messages, stream=False, temperature=0.1)

                # 解析 LLM 响应：提取端口名称
                reasoning = llm_response
                response_lower = llm_response.lower()

                for port_name in output_ports:
                    if port_name.lower() in response_lower:
                        routed_port = port_name
                        break

                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if _is_transient_error(exc) and attempt < max_retries:
                    wait = min(attempt * 3, 30)
                    logger.warning(
                        "Router LLM call attempt %d/%d failed: %s, retry %ds",
                        attempt, max_retries, str(exc)[:150], wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.warning("Router LLM call failed: %s, using default port", exc)
                reasoning = f"LLM routing failed: {exc}"

        # 构建输出：仅在路由目标端口输出数据
        outputs: Dict[str, Any] = {}
        for port_name in output_ports:
            if port_name == routed_port:
                outputs[port_name] = text_input
            else:
                outputs[port_name] = None

        outputs["routed_port"] = routed_port
        outputs["reasoning"] = reasoning

        await _emit(emit_callback, {
            "type": "node_output",
            "node_id": node.id,
            "port": "routing_decision",
            "data": {
                "routed_port": routed_port,
                "reasoning": reasoning[:200],
            },
        })

        return outputs

    async def _exec_subworkflow(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        SUBWORKFLOW 节点：嵌套子工作流引用。v8 新增。

        config:
            workflow_id: str    — 子工作流 ID
            input_mapping: dict — 输入端口映射 {"子工作流端口": "当前端口"}
            output_mapping: dict — 输出端口映射 {"子工作流端口": "当前端口"}

        输入端口:
            (动态) — 根据 input_mapping 映射

        输出端口:
            (动态) — 根据 output_mapping 映射
            result — 子工作流最终结果
        """
        sub_workflow_id = node.config.get("workflow_id", "")
        input_mapping = node.config.get("input_mapping", {})
        output_mapping = node.config.get("output_mapping", {})

        if not sub_workflow_id:
            return {
                "result": {"error": "No sub-workflow ID specified"},
                "text": "",
            }

        # 注意：子工作流的完整执行需要 session_id，
        # 这里提供一个基础框架，实际使用时需要从上下文中获取
        logger.info(
            "SubWorkflow node %s: referencing workflow %s (execution requires session context)",
            node.id, sub_workflow_id,
        )

        # 将输入映射到子工作流的预期输入
        mapped_inputs: Dict[str, Any] = {}
        for sub_port, current_port in input_mapping.items():
            mapped_inputs[sub_port] = inputs.get(current_port)

        return {
            "result": {
                "sub_workflow_id": sub_workflow_id,
                "mapped_inputs": mapped_inputs,
                "note": "Sub-workflow execution requires session context. "
                        "Use WorkflowEngine.execute() with the sub-workflow ID.",
            },
            "text": json.dumps(mapped_inputs, ensure_ascii=False),
        }

    async def _exec_note(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        NOTE 节点：注释/批注节点。v8 新增。

        不执行任何逻辑，仅将 config.note 中的文本作为输出。
        用于工作流文档化和说明。

        config:
            note: str  — 注释文本
            color: str — 注释颜色 (可选，用于前端显示)

        输出端口:
            text — 注释文本
        """
        note_text = node.config.get("note", "")
        return {"text": note_text}

    async def _exec_variable(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        VARIABLE 节点：全局变量存储。v8 新增。

        支持设置和获取全局变量值。变量可在不同节点间共享。

        config:
            variable_name: str  — 变量名
            default_value: Any  — 默认值
            scope: str          — 变量作用域 ("workflow" | "global")

        输入端口:
            text — 新的变量值 (如果提供，则更新变量)

        输出端口:
            text — 当前变量值
            json — 变量的结构化表示
        """
        variable_name = node.config.get("variable_name", "")
        default_value = node.config.get("default_value", "")
        scope = node.config.get("scope", "workflow")

        if not variable_name:
            variable_name = f"var_{node.id}"

        # 检查是否有输入值需要更新
        new_value = inputs.get("text")
        if new_value is not None:
            if isinstance(new_value, list):
                new_value = new_value[-1]  # 取最后一个值
            node._variable_value = new_value
            # 存储到全局变量表
            self._global_variables[variable_name] = new_value
        else:
            # 读取已存储的值或使用默认值
            stored_value = self._global_variables.get(variable_name)
            if stored_value is not None:
                node._variable_value = stored_value
            else:
                node._variable_value = default_value

        value = node._variable_value
        value_str = str(value) if not isinstance(value, str) else value

        return {
            "text": value_str,
            "json": {
                "name": variable_name,
                "value": value,
                "scope": scope,
            },
        }

    async def _exec_timer(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        TIMER 节点：定时/延迟执行。v8 新增。

        config:
            delay_seconds: float  — 延迟秒数 (默认 0)
            schedule: str        — 调度表达式 (可选, "cron" 或 "every Ns")
            repeat: int          — 重复次数 (默认 1, 0=无限)
            trigger_time: float  — 特定触发时间戳 (可选)

        输入端口:
            text — 通过的数据 (原样输出)

        输出端口:
            text    — 传递的数据
            elapsed — 实际等待时间 (秒)
            count   — 触发次数
        """
        delay_seconds = float(node.config.get("delay_seconds", 0))
        repeat = int(node.config.get("repeat", 1))
        trigger_time = node.config.get("trigger_time", 0)

        # 如果有特定触发时间，计算延迟
        if trigger_time and trigger_time > 0:
            now = _ts()
            if trigger_time > now:
                delay_seconds = trigger_time - now

        elapsed = 0.0
        count = 0

        # 支持重复触发
        actual_repeat = max(1, repeat) if repeat > 0 else 1
        for _ in range(actual_repeat):
            if delay_seconds > 0:
                start = _ts()
                await asyncio.sleep(delay_seconds)
                elapsed += _ts() - start
            count += 1

        text_data = inputs.get("text", "")
        if isinstance(text_data, list):
            text_data = "\n".join(str(t) for t in text_data)

        return {
            "text": text_data,
            "elapsed": round(elapsed, 3),
            "count": count,
        }

    async def _exec_webhook_trigger(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        WEBHOOK_TRIGGER 节点：HTTP Webhook 输入触发器。v8 新增。

        等待外部 Webhook 请求的到来，并返回其载荷。

        config:
            webhook_id: str     — Webhook 唯一标识符
            timeout: float      — 等待超时秒数 (默认 300)
            expected_method: str — 期望的 HTTP 方法 (可选)
            expected_path: str   — 期望的 URL 路径 (可选)

        输入端口:
            (无 — 数据来自外部 HTTP 请求)

        输出端口:
            text — Webhook 载荷的文本表示
            json — Webhook 载荷的原始 JSON 数据
            headers — HTTP 请求头
        """
        webhook_id = node.config.get("webhook_id", f"wh_{node.id}")
        timeout_sec = float(node.config.get("timeout", 300))

        # 等待 Webhook 载荷
        payload_data = self._webhook_payloads.pop(webhook_id, None)

        if payload_data is None:
            # 轮询等待 (简单实现)
            waited = 0.0
            poll_interval = 0.5
            while waited < timeout_sec:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                payload_data = self._webhook_payloads.pop(webhook_id, None)
                if payload_data is not None:
                    break

            if payload_data is None:
                return {
                    "text": f"[Webhook Timeout: no request received within {timeout_sec}s]",
                    "json": None,
                    "headers": {},
                }

        payload = payload_data.get("payload", {})
        headers = payload_data.get("headers", {})

        payload_text = ""
        if isinstance(payload, str):
            payload_text = payload
        else:
            payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

        return {
            "text": payload_text,
            "json": payload,
            "headers": headers,
        }

    async def _exec_api_call(
        self, node: WorkflowNode, inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        API_CALL 节点：通用 REST API 调用。v8 新增。

        与 HTTP 节点的区别：支持模板化的 URL/Body，更灵活的配置。

        config:
            method: str         — HTTP 方法 (GET/POST/PUT/DELETE/PATCH)
            url: str            — URL 模板 (支持 {{variable}} 占位符)
            headers: dict       — 请求头 (支持 {{variable}} 模板)
            body_template: str  — 请求体模板 (JSON 字符串，支持 {{variable}})
            timeout: float      — 超时秒数 (默认 30)
            auth_type: str      — 认证类型 ("none" | "bearer" | "basic")
            auth_token: str     — 认证令牌

        输入端口:
            text — 可注入到模板的文本
            json — 可注入到模板的结构化数据

        输出端口:
            text   — 响应体文本
            json   — 解析后的 JSON
            status — HTTP 状态码
            headers — 响应头
        """
        import aiohttp  # type: ignore

        method = node.config.get("method", "GET").upper()
        url_template = node.config.get("url", "")
        headers_template = node.config.get("headers", {})
        body_template = node.config.get("body_template", "")
        timeout_sec = float(node.config.get("timeout", 30))
        auth_type = node.config.get("auth_type", "none")
        auth_token = node.config.get("auth_token", "")

        # 收集模板变量
        template_vars: Dict[str, str] = {}
        text_input = inputs.get("text", "")
        if text_input:
            if isinstance(text_input, list):
                text_input = "\n".join(str(t) for t in text_input)
            template_vars["input"] = str(text_input)
        json_input = inputs.get("json") or inputs.get("data")
        if json_input:
            template_vars["data"] = json.dumps(
                json_input, ensure_ascii=False
            ) if not isinstance(json_input, str) else json_input

        # 渲染 URL
        url = url_template
        for var_name, var_value in template_vars.items():
            url = url.replace(f"{{{{{var_name}}}}}", var_value)

        # 渲染请求头
        headers: Dict[str, str] = {}
        for k, v in headers_template.items():
            rendered_val = str(v)
            for var_name, var_value in template_vars.items():
                rendered_val = rendered_val.replace(
                    f"{{{{{var_name}}}}}", var_value
                )
            headers[k] = rendered_val

        # 添加认证头
        if auth_type == "bearer" and auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif auth_type == "basic" and auth_token:
            import base64
            headers["Authorization"] = f"Basic {base64.b64encode(auth_token.encode()).decode()}"

        # 渲染请求体
        body = body_template
        for var_name, var_value in template_vars.items():
            body = body.replace(f"{{{{{var_name}}}}}", var_value)

        result_text = ""
        result_json: Any = None
        status_code = 0
        resp_headers: Dict[str, str] = {}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body if method in ("POST", "PUT", "PATCH") else None,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ) as resp:
                    status_code = resp.status
                    resp_headers = dict(resp.headers)
                    result_text = await resp.text()
                    try:
                        result_json = json.loads(result_text)
                    except json.JSONDecodeError:
                        result_json = None
        except asyncio.TimeoutError:
            result_text = f"[API Call Timeout after {timeout_sec}s]"
        except Exception as exc:
            result_text = f"[API Call Error: {exc}]"

        return {
            "text": result_text,
            "json": result_json,
            "status": status_code,
            "headers": resp_headers,
        }

    # -----------------------------------------------------------------------
    # File Handling — 文件管理
    # -----------------------------------------------------------------------

    async def register_file(
        self,
        session_id: str,
        workflow_id: str,
        node_id: str,
        filename: str,
        file_data: bytes,
        mime_type: str = "application/octet-stream",
    ) -> dict:
        """
        注册一个文件到指定节点。更新节点的 config 使其引用该文件。

        Returns
        -------
        dict
            文件引用信息
        """
        wf = await self._load_workflow(session_id, workflow_id)
        node = wf.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found")

        # 生成文件引用 ID
        file_ref = hashlib.sha256(file_data).hexdigest()[:16]

        # 更新节点配置
        node.config["filename"] = filename
        node.config["file_ref"] = file_ref
        node.config["mime_type"] = mime_type

        wf.updated_at = _ts()
        await self.storage.save_workflow(session_id, wf.id, _workflow_to_dict(wf), wf.folder_id)

        # 保存文件到存储
        await self.storage.save_node_result(
            session_id, workflow_id, node_id,
            {
                "_file": {
                    "ref": file_ref,
                    "filename": filename,
                    "mime_type": mime_type,
                    "size": len(file_data),
                }
            },
        )

        return {
            "ref": file_ref,
            "filename": filename,
            "mime_type": mime_type,
            "size": len(file_data),
        }

    async def get_result_file(
        self, session_id: str, workflow_id: str, node_id: str
    ) -> Optional[dict]:
        """获取节点的执行结果文件"""
        return await self.storage.load_node_result(
            session_id, workflow_id, node_id
        )

    # -----------------------------------------------------------------------
    # Internal Helpers — 内部辅助方法
    # -----------------------------------------------------------------------

    async def _load_workflow(
        self, session_id: str, workflow_id: str
    ) -> Workflow:
        """从存储中加载并反序列化工作流"""
        data = await self.storage.load_workflow(session_id, workflow_id)
        if data is None:
            raise KeyError(f"Workflow {workflow_id} not found")
        return _dict_to_workflow(data)

    def stop_workflow(self, workflow_id: str) -> bool:
        """
        请求停止正在执行的工作流。
        循环节点和其他长时间运行的节点会在下一次迭代中检查此标志。

        Returns
        -------
        bool
            是否成功设置了停止标志
        """
        state = self._executions.get(workflow_id)
        if state is not None:
            state.stopped = True
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Execution State — 执行运行时状态
# ═══════════════════════════════════════════════════════════════════════════

class _ExecutionState:
    """跟踪正在执行的工作流的运行时状态"""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self.started_at: float = _ts()
        self.stopped: bool = False
        self.completed_nodes: List[str] = []
        self.error_nodes: Dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════════════
# In-Memory Storage (for testing / development)
# ═══════════════════════════════════════════════════════════════════════════

class InMemoryStorage(Storage):
    """
    内存存储实现 —— 适用于开发和测试。
    所有数据仅在进程生命周期内有效。v8 扩展版（含目录支持）。
    """

    def __init__(self) -> None:
        self._workflows: Dict[str, Dict[str, dict]] = defaultdict(dict)
        self._results: Dict[str, Dict[str, Dict[str, dict]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        # v8: 目录存储
        self._folders: Dict[str, Dict[str, dict]] = defaultdict(dict)

    async def save_workflow(self, session_id: str, workflow: dict) -> None:
        self._workflows[session_id][workflow["id"]] = copy.deepcopy(workflow)

    async def load_workflow(
        self, session_id: str, workflow_id: str
    ) -> Optional[dict]:
        return copy.deepcopy(
            self._workflows[session_id].get(workflow_id)
        )

    async def list_workflows(self, session_id: str) -> List[dict]:
        return list(self._workflows[session_id].values())

    async def delete_workflow(self, session_id: str, workflow_id: str) -> None:
        self._workflows[session_id].pop(workflow_id, None)
        self._results[session_id].pop(workflow_id, None)

    async def save_node_result(
        self, session_id: str, workflow_id: str, node_id: str, data: dict
    ) -> None:
        self._results[session_id][workflow_id][node_id] = copy.deepcopy(data)

    async def load_node_result(
        self, session_id: str, workflow_id: str, node_id: str
    ) -> Optional[dict]:
        return copy.deepcopy(
            self._results[session_id][workflow_id].get(node_id)
        )

    # ---- v8: 目录操作 ----

    async def save_folder(self, session_id: str, folder: dict) -> None:
        self._folders[session_id][folder["id"]] = copy.deepcopy(folder)

    async def load_folder(
        self, session_id: str, folder_id: str
    ) -> Optional[dict]:
        return copy.deepcopy(
            self._folders[session_id].get(folder_id)
        )

    async def list_folders(
        self, session_id: str, parent_id: str = ""
    ) -> List[dict]:
        all_folders = list(self._folders[session_id].values())
        return [f for f in all_folders if f.get("parent_id", "") == parent_id]

    async def delete_folder(self, session_id: str, folder_id: str) -> None:
        self._folders[session_id].pop(folder_id, None)


# ═══════════════════════════════════════════════════════════════════════════
# Stub LLM Client (for testing / development)
# ═══════════════════════════════════════════════════════════════════════════

class _StubLLMClient(LLMClient):
    """模拟 LLM 客户端，返回固定文本"""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = False,
    ) -> Union[str, AsyncGenerator[str, None]]:
        user_msg = ""
        for m in messages:
            if m["role"] == "user":
                user_msg = m["content"]

        response = f"[Stub LLM Response] Received: {user_msg[:100]}"

        if stream:
            async def _stream() -> AsyncGenerator[str, None]:
                words = response.split(" ")
                for word in words:
                    yield word + " "
                    await asyncio.sleep(0.05)
            return _stream()
        else:
            return response


class _StubLLMFactory(LLMClientFactory):
    """模拟 LLM 客户端工厂"""

    def create(self, provider: str, model: str, **kwargs: Any) -> LLMClient:
        return _StubLLMClient()


# ═══════════════════════════════════════════════════════════════════════════
# Stub Sandbox (for testing / development)
# ═══════════════════════════════════════════════════════════════════════════

class _StubSandbox(Sandbox):
    """
    模拟代码沙箱 —— 使用受限的 exec 执行代码。
    注意：此实现仅用于测试，生产环境应使用真正的隔离沙箱。
    """

    async def execute(
        self,
        code: str,
        inputs: Dict[str, Any],
        *,
        timeout: float = 30.0,
        files_in: Optional[Dict[str, bytes]] = None,
    ) -> Dict[str, Any]:
        stdout_buf: List[str] = []
        stderr_buf: List[str] = []

        safe_globals: Dict[str, Any] = {
            "__builtins__": {
                "print": lambda *a, **kw: stdout_buf.append(
                    " ".join(str(x) for x in a)
                ),
                "range": range,
                "len": len,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "list": list,
                "dict": dict,
                "set": set,
                "tuple": tuple,
                "sorted": sorted,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "min": min,
                "max": max,
                "sum": sum,
                "abs": abs,
                "round": round,
                "isinstance": isinstance,
                "type": type,
                "json": __import__("json"),
                "re": __import__("re"),
                "math": __import__("math"),
                "datetime": __import__("datetime"),
                "True": True,
                "False": False,
                "None": None,
            }
        }
        safe_locals: Dict[str, Any] = {
            "input_text": inputs.get("text", ""),
            "input_data": inputs.get("data", {}),
            "input": inputs,
        }
        for k, v in inputs.items():
            if not k.startswith("_") and isinstance(v, (str, int, float, bool, list, dict)):
                safe_locals[k] = v

        try:
            exec_result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: exec(code, safe_globals, safe_locals),  # noqa: S102
                ),
                timeout=timeout,
            )
            result_var = safe_locals.get("result")
            if result_var is not None:
                stdout_buf.append(str(result_var))
        except asyncio.TimeoutError:
            stderr_buf.append(f"Execution timed out after {timeout}s")
        except Exception as exc:
            stderr_buf.append(f"{type(exc).__name__}: {exc}")

        return {
            "stdout": "\n".join(stdout_buf),
            "stderr": "\n".join(stderr_buf),
            "files": [],
        }


# ═══════════════════════════════════════════════════════════════════════════
# __main__ — 测试与演示
# ═══════════════════════════════════════════════════════════════════════════

async def _demo() -> None:
    """
    运行演示：创建并执行多种工作流，展示 v8 新增功能。

    Usage
    -----
        python workflow_engine.py
    """
    # 初始化组件
    storage = InMemoryStorage()
    llm_factory = _StubLLMFactory()
    sandbox = _StubSandbox()
    engine = WorkflowEngine(storage, llm_factory, sandbox)

    session_id = "demo-session-v8"

    print("=" * 60)
    print("  cx2118-script-weaver v8 — Workflow Engine Demo")
    print("=" * 60)

    # ─── 1. 基础工作流 (Prompt → LLM → Output) ─────────────────────────
    print("\n[1] Creating basic workflow (Prompt → LLM → Output)...")
    wf = await engine.create_workflow(session_id, "Demo Workflow")
    wf_id = wf["id"]
    print(f"    Created: {wf_id} ({wf['name']})")

    prompt_node = await engine.add_node(
        session_id, wf_id, "prompt", x=100, y=200,
        config={"prompt": "请用中文写一首关于春天的短诗。"},
    )
    prompt_id = prompt_node["id"]

    llm_node = await engine.add_node(
        session_id, wf_id, "llm", x=350, y=200,
        config={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.9,
            "max_tokens": 256,
            "stream": False,
        },
    )
    llm_id = llm_node["id"]

    output_node = await engine.add_node(
        session_id, wf_id, "output", x=600, y=200,
    )
    output_id = output_node["id"]

    # 使用 v8 的 bezier 曲线类型连接
    conn1 = await engine.add_connection(
        session_id, wf_id,
        source_node=prompt_id, source_port="text",
        target_node=llm_id, target_port="text",
        conn_type="text",
        curve_type="bezier",
    )
    print(f"    Connection: {prompt_id}:text → {llm_id}:text (curve={conn1['curve_type']})")

    await engine.add_connection(
        session_id, wf_id,
        source_node=llm_id, source_port="text",
        target_node=output_id, target_port="text",
        conn_type="text",
        curve_type="step",
    )

    # ─── 2. 目录管理 ─────────────────────────────────────────────────────
    print("\n[2] Folder management...")
    folder1 = await engine.create_folder(session_id, "我的项目")
    print(f"    Created folder: {folder1['id']} ({folder1['name']})")
    folder2 = await engine.create_folder(session_id, "子目录", parent_id=folder1["id"])
    print(f"    Created subfolder: {folder2['id']} ({folder2['name']})")
    folders = await engine.list_folders(session_id, parent_id="")
    print(f"    Root folders: {len(folders)}")

    # 移动工作流到目录
    moved = await engine.move_workflow(session_id, wf_id, folder1["id"])
    print(f"    Moved workflow to folder: folder_id={moved.get('folder_id')}")

    # ─── 3. 执行工作流 ───────────────────────────────────────────────────
    print("\n[3] Executing basic workflow...")

    async def emit(event: dict) -> None:
        etype = event.get("type", "?")
        if etype == "node_start":
            print(f"    ▶ Node start: {event['node_id']} ({event['node_type']})")
        elif etype == "node_output":
            data_preview = str(event.get("data", ""))[:80]
            print(f"    → Output [{event['node_id']}:{event['port']}]: {data_preview}...")
        elif etype == "node_done":
            print(f"    ✔ Node done: {event['node_id']}")
        elif etype == "node_error":
            print(f"    ✘ Node error: {event['node_id']}: {event.get('error')}")
        elif etype == "workflow_done":
            print(f"\n    🎉 Workflow completed!")
            results = event.get("results", {})
            for nid, res in results.items():
                print(f"    Result [{nid}]: {str(res)[:200]}")
        elif etype == "workflow_error":
            print(f"    ✘ Workflow error: {event.get('error')}")
        elif etype == "parallel_layers":
            layers = event.get("layers", [])
            print(f"    📊 Parallel layers: {[len(l) for l in layers]}")

    async for event in engine.execute(session_id, wf_id, emit_callback=emit):
        pass

    # ─── 4. v8 新节点演示: Variable + Note ───────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Variable + Note + Timer")
    print("=" * 60)

    wf2 = await engine.create_workflow(session_id, "Variable Demo")
    wf2_id = wf2["id"]

    var_node = await engine.add_node(
        session_id, wf2_id, "variable", x=100, y=100,
        config={
            "variable_name": "greeting",
            "default_value": "Hello from v8!",
        },
    )
    var_id = var_node["id"]

    note_node = await engine.add_node(
        session_id, wf2_id, "note", x=100, y=200,
        config={"note": "这是一个注释节点，不会执行任何逻辑。"},
    )
    note_id = note_node["id"]

    timer_node = await engine.add_node(
        session_id, wf2_id, "timer", x=300, y=100,
        config={"delay_seconds": 0.1},
    )
    timer_id = timer_node["id"]

    var_out = await engine.add_node(session_id, wf2_id, "output", x=500, y=100)
    var_out_id = var_out["id"]

    await engine.add_connection(
        session_id, wf2_id, var_id, "text", timer_id, "text", "text",
    )
    await engine.add_connection(
        session_id, wf2_id, timer_id, "text", var_out_id, "text", "text",
    )

    print("\n[4] Executing variable + timer workflow...")
    async for event in engine.execute(session_id, wf2_id, emit_callback=emit):
        pass

    # ─── 5. v8 搜索节点演示 ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Search Node")
    print("=" * 60)

    wf3 = await engine.create_workflow(session_id, "Search Demo")
    wf3_id = wf3["id"]

    search_node = await engine.add_node(
        session_id, wf3_id, "search", x=100, y=100,
        config={
            "query": "Python workflow engine",
            "provider": "generic",
            "max_results": 3,
        },
    )
    search_id = search_node["id"]

    search_output = await engine.add_node(session_id, wf3_id, "output", x=400, y=100)
    search_out_id = search_output["id"]

    await engine.add_connection(
        session_id, wf3_id, search_id, "text", search_out_id, "text", "text",
    )

    print("\n[5] Executing search workflow...")
    async for event in engine.execute(session_id, wf3_id, emit_callback=emit):
        pass

    # ─── 6. v8 节点包导出/导入演示 ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Node Package Export/Import")
    print("=" * 60)

    # 导出节点包
    zip_data = await engine.export_node_package(
        session_id, wf2_id,
        node_ids=[var_id, timer_id, var_out_id],
        package_name="variable-timer-pkg",
        package_description="Variable + Timer + Output 子工作流包",
        package_version="1.0.0",
        package_author="cx2118",
    )
    print(f"\n[6] Exported package: {len(zip_data)} bytes")

    # 导入到新工作流
    wf_import = await engine.create_workflow(session_id, "Imported Workflow")
    wf_import_id = wf_import["id"]
    import_result = await engine.import_node_package(
        session_id, wf_import_id, zip_data,
    )
    print(f"    Imported {len(import_result['imported_nodes'])} nodes")
    print(f"    Imported {len(import_result['imported_connections'])} connections")
    print(f"    ID mapping: {import_result['node_id_mapping']}")

    # ─── 7. v8 Prompt 模板连接演示 ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Prompt Template Connection")
    print("=" * 60)

    wf_pt = await engine.create_workflow(session_id, "Prompt Template Demo")
    wf_pt_id = wf_pt["id"]

    prompt_src = await engine.add_node(
        session_id, wf_pt_id, "prompt", x=100, y=100,
        config={"prompt": "Python 是一种优秀的编程语言"},
    )
    prompt_src_id = prompt_src["id"]

    llm_target = await engine.add_node(
        session_id, wf_pt_id, "llm", x=400, y=100,
        config={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "system_prompt": "你是一个翻译助手。",
        },
    )
    llm_target_id = llm_target["id"]

    pt_output = await engine.add_node(session_id, wf_pt_id, "output", x=700, y=100)
    pt_out_id = pt_output["id"]

    # 使用 PromptTemplate 连接
    await engine.add_connection(
        session_id, wf_pt_id,
        source_node=prompt_src_id, source_port="text",
        target_node=llm_target_id, target_port="text",
        conn_type="text",
        prompt_template={
            "template": "请将以下文本翻译为英文:\n{{text}}",
            "auto_inject_context": True,
        },
    )
    await engine.add_connection(
        session_id, wf_pt_id,
        source_node=llm_target_id, source_port="text",
        target_node=pt_out_id, target_port="text",
        conn_type="text",
    )

    print("\n[7] Executing prompt template workflow...")
    async for event in engine.execute(session_id, wf_pt_id, emit_callback=emit):
        pass

    # ─── 8. v8 API Call 节点演示 ───────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: API Call Node")
    print("=" * 60)

    wf_api = await engine.create_workflow(session_id, "API Call Demo")
    wf_api_id = wf_api["id"]

    api_node = await engine.add_node(
        session_id, wf_api_id, "api_call", x=100, y=100,
        config={
            "method": "GET",
            "url": "https://httpbin.org/uuid",
            "timeout": 10,
        },
    )
    api_node_id = api_node["id"]

    api_output = await engine.add_node(session_id, wf_api_id, "output", x=400, y=100)
    api_out_id = api_output["id"]

    await engine.add_connection(
        session_id, wf_api_id, api_node_id, "json", api_out_id, "text", "json",
    )

    print("\n[8] Executing API call workflow...")
    async for event in engine.execute(session_id, wf_api_id, emit_callback=emit):
        pass

    # ─── 9. 拓扑排序并行层级测试 ────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Parallel Topological Sort")
    print("=" * 60)

    wf_par = await engine.create_workflow(session_id, "Parallel Demo")
    wf_par_id = wf_par["id"]

    # 创建多个独立节点 (应并行执行)
    par_nodes = []
    for i in range(4):
        n = await engine.add_node(
            session_id, wf_par_id, "prompt", x=100 + i * 150, y=100,
            config={"prompt": f"并行节点 {i+1}"},
        )
        par_nodes.append(n)

    # 创建汇聚节点
    merge_node = await engine.add_node(
        session_id, wf_par_id, "merge", x=300, y=300,
    )
    merge_id = merge_node["id"]

    par_out = await engine.add_node(
        session_id, wf_par_id, "output", x=500, y=300,
    )
    par_out_id = par_out["id"]

    for n in par_nodes:
        await engine.add_connection(
            session_id, wf_par_id, n["id"], "text", merge_id, "text", "text",
        )
    await engine.add_connection(
        session_id, wf_par_id, merge_id, "text", par_out_id, "text", "text",
    )

    # 直接测试并行排序函数
    from workflow_engine import _topological_sort_parallel as _tsp
    par_wf = await engine._load_workflow(session_id, wf_par_id)
    layers = _tsp(par_wf.nodes, par_wf.connections)
    print(f"    Parallel layers: {layers}")
    print(f"    Layer sizes: {[len(l) for l in layers]}")

    print("\n[9] Executing parallel workflow...")
    async for event in engine.execute(session_id, wf_par_id, emit_callback=emit):
        pass

    # ─── 10. 路由节点演示 ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  v8 Feature Demo: Router Node")
    print("=" * 60)

    wf_router = await engine.create_workflow(session_id, "Router Demo")
    wf_router_id = wf_router["id"]

    router_prompt = await engine.add_node(
        session_id, wf_router_id, "prompt", x=100, y=100,
        config={"prompt": "今天天气真好，我想出去玩"},
    )
    router_prompt_id = router_prompt["id"]

    router_node = await engine.add_node(
        session_id, wf_router_id, "router", x=350, y=100,
        config={
            "criteria_prompt": "判断输入文本的情感倾向",
            "output_ports": ["positive", "negative", "neutral"],
        },
    )
    router_node_id = router_node["id"]

    pos_output = await engine.add_node(
        session_id, wf_router_id, "output", x=600, y=50,
    )
    neg_output = await engine.add_node(
        session_id, wf_router_id, "output", x=600, y=150,
    )

    await engine.add_connection(
        session_id, wf_router_id,
        router_prompt_id, "text", router_node_id, "text", "text",
    )
    await engine.add_connection(
        session_id, wf_router_id,
        router_node_id, "positive", pos_output["id"], "text", "text",
    )
    await engine.add_connection(
        session_id, wf_router_id,
        router_node_id, "negative", neg_output["id"], "text", "text",
    )

    print("\n[10] Executing router workflow...")
    async for event in engine.execute(session_id, wf_router_id, emit_callback=emit):
        pass

    # ─── 11. 清理 ─────────────────────────────────────────────────────────
    print("\n[11] Cleaning up...")
    for wf_data in await engine.list_workflows(session_id):
        await engine.delete_workflow(session_id, wf_data["id"])
    for f in await engine.list_folders(session_id):
        await engine.delete_folder(session_id, f["id"])
        # 递归删除子目录
        for sf in await engine.list_folders(session_id, parent_id=f["id"]):
            await engine.delete_folder(session_id, sf["id"])
    remaining = await engine.list_workflows(session_id)
    print(f"    Remaining workflows: {len(remaining)}")
    remaining_folders = await engine.list_folders(session_id)
    print(f"    Remaining folders: {len(remaining_folders)}")

    print("\n" + "=" * 60)
    print("  All v8 demos completed successfully! ✓")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_demo())
