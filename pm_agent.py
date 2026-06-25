#!/usr/bin/env python3
"""
pm_agent.py — PM Agent 系统模块（cx2118 Script Weaver v9.2）
═══════════════════════════════════════════════════════════════════════
项目管理 AI Agent 架构，提供多项目隔离管理、节点级 Prompt 注入、
Agent Mode 对话系统以及与 Storage 持久化层的集成。

核心组件:
  PMProject          — 单个项目状态容器（需求/结构/文件/对话历史）
  PMAgentManager     — 多项目管理器（创建/删除/切换/持久化）
  AgentMode          — Agent 模式封装（系统提示 + 流式对话）
  build_node_prompt  — 全局 Prompt 注入（节点级全量上下文）

用法示例:
    from pm_agent import PMAgentManager

    mgr = PMAgentManager()
    proj = mgr.create_project("我的项目", "这是一个示例项目")
    mgr.set_active(proj.id)
    mgr.save(storage)          # 持久化到 SQLite
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

__version__ = "9.3.0"
__all__ = [
    "PMProject",
    "PMAgentManager",
    "AgentMode",
    "build_node_prompt",
    "ProjectStatus",
]

# ═══════════════════════════════════════════════════════════════════════
# Logging — 日志配置
# ═══════════════════════════════════════════════════════════════════════

logger = logging.getLogger("cx2118.pm_agent")

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ═══════════════════════════════════════════════════════════════════════
# Helpers — 辅助工具
# ═══════════════════════════════════════════════════════════════════════

_WORKSPACE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace"))


def _now() -> float:
    """返回当前 UTC 时间戳（秒）"""
    return time.time()


def _new_id() -> str:
    """生成短 UUID（12 位十六进制）"""
    return uuid.uuid4().hex[:12]


def _safe_json_dumps(obj: Any) -> str:
    """安全序列化为 JSON，处理不可序列化类型"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("JSON 序列化失败: %s", exc)
        return "{}"


def _safe_json_loads(text: str, default: Any = None) -> Any:
    """安全反序列化 JSON"""
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("JSON 解析失败: %s", exc)
        return default if default is not None else {}


def _truncate(text: str, max_chars: int, suffix: str = "...(truncated)") -> str:
    """截断文本到指定长度"""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


# ═══════════════════════════════════════════════════════════════════════
# Enums — 枚举类型
# ═══════════════════════════════════════════════════════════════════════

class ProjectStatus(str, Enum):
    """项目状态"""
    IDLE = "idle"           # 空闲
    ACTIVE = "active"       # 进行中
    COMPLETED = "completed"   # 已完成


# ═══════════════════════════════════════════════════════════════════════
# PMProject — 单个项目状态管理
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PMProject:
    """
    单个项目状态容器 — 管理项目的完整生命周期数据。

    每个项目拥有独立的需求文档、结构规划、文件系统、对话历史、
    编码产出以及 PM 专属对话和 Director 子代理对话记录。

    Attributes
    ----------
    id : str
        项目唯一标识符
    name : str
        项目名称
    description : str
        项目描述
    created_at : float
        创建时间戳
    updated_at : float
        最后更新时间戳
    conversation_history : list[dict]
        主对话历史（user/assistant 消息列表）
    files : dict[str, str]
        项目文件字典（文件名 -> 内容）
    requirements : str
        需求文档
    structure : dict
        项目结构规划（JSON）
    code : dict[str, str]
        代码产出字典（类型 -> 内容，如 python/html/css）
    status : ProjectStatus
        项目当前状态
    pm_conversation : list[dict]
        PM 专属聊天历史（独立于主对话）
    director_conversations : dict[str, list[dict]]
        每个代理的独立对话记录（agent_name -> 消息列表）
    """

    id: str = field(default_factory=_new_id)
    name: str = ""
    description: str = ""
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    conversation_history: list[dict] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    requirements: str = ""
    structure: dict = field(default_factory=dict)
    code: dict[str, str] = field(default_factory=dict)
    status: ProjectStatus = ProjectStatus.IDLE
    pm_conversation: list[dict] = field(default_factory=list)
    director_conversations: dict[str, list[dict]] = field(default_factory=dict)

    # ── 对话管理 ───────────────────────────────────────────────

    def add_message(self, role: str, content: str) -> None:
        """
        添加一条消息到主对话历史。

        Parameters
        ----------
        role : str
            消息角色（'user' / 'assistant' / 'system'）
        content : str
            消息内容
        """
        if not role or not isinstance(role, str):
            logger.warning("add_message: 无效的 role=%r", role)
            return
        self.conversation_history.append({
            "role": role.strip(),
            "content": content,
            "timestamp": _now(),
        })
        self.updated_at = _now()

    def add_pm_message(self, role: str, content: str) -> None:
        """
        添加一条消息到 PM 专属对话历史。

        Parameters
        ----------
        role : str
            消息角色
        content : str
            消息内容
        """
        if not role or not isinstance(role, str):
            logger.warning("add_pm_message: 无效的 role=%r", role)
            return
        self.pm_conversation.append({
            "role": role.strip(),
            "content": content,
            "timestamp": _now(),
        })
        self.updated_at = _now()

    def add_director_message(self, agent_name: str, role: str, content: str) -> None:
        """
        添加一条消息到指定 Director 子代理的对话历史。

        Parameters
        ----------
        agent_name : str
            子代理名称（如 'python_coder', 'html_coder'）
        role : str
            消息角色
        content : str
            消息内容
        """
        if not agent_name or not role:
            logger.warning("add_director_message: 缺少 agent_name 或 role")
            return
        if agent_name not in self.director_conversations:
            self.director_conversations[agent_name] = []
        self.director_conversations[agent_name].append({
            "role": role.strip(),
            "content": content,
            "timestamp": _now(),
        })
        self.updated_at = _now()

    def get_director_conversation(self, agent_name: str) -> list[dict]:
        """获取指定子代理的对话历史"""
        return list(self.director_conversations.get(agent_name, []))

    # ── 文件管理 ───────────────────────────────────────────────

    def update_file(self, filename: str, content: str) -> None:
        """
        更新或创建项目文件。

        Parameters
        ----------
        filename : str
            文件名
        content : str
            文件内容
        """
        if not filename:
            logger.warning("update_file: 文件名为空")
            return
        self.files[filename] = content
        self.updated_at = _now()

    def remove_file(self, filename: str) -> bool:
        """
        删除项目文件。

        Returns
        -------
        bool
            是否成功删除（文件存在时为 True）
        """
        if filename in self.files:
            del self.files[filename]
            self.updated_at = _now()
            return True
        return False

    def get_file(self, filename: str) -> str:
        """获取文件内容，不存在则返回空字符串"""
        return self.files.get(filename, "")

    def list_files(self) -> list[dict]:
        """列出所有项目文件信息"""
        return [
            {
                "name": fname,
                "size": len(content),
                "lines": len(content.splitlines()),
            }
            for fname, content in self.files.items()
        ]

    # ── 代码管理 ───────────────────────────────────────────────

    def update_code(self, code_type: str, content: str) -> None:
        """
        更新代码产出。

        Parameters
        ----------
        code_type : str
            代码类型（'python', 'html', 'css', 'javascript' 等）
        content : str
            代码内容
        """
        self.code[code_type] = content
        self.updated_at = _now()

    # ── 上下文构建 ────────────────────────────────────────────

    def get_context(self, max_chars: int = 8000) -> str:
        """
        构建项目的完整上下文字符串，用于 AI 注入。

        按优先级拼接：需求 → 结构 → 文件 → 对话历史，
        总长度不超过 max_chars。

        Parameters
        ----------
        max_chars : int
            最大字符数（默认 8000）

        Returns
        -------
        str
            格式化的项目上下文
        """
        parts: list[str] = []
        total = 0
        limit = max_chars

        # 1. 需求文档（优先级最高，保留最多空间）
        if self.requirements:
            budget = min(int(limit * 0.3), limit - total, 3000)
            chunk = _truncate(self.requirements, budget)
            if chunk:
                parts.append(f"## 📋 项目需求\n{chunk}")
                total += len(chunk)

        # 2. 项目结构
        if self.structure:
            remaining = limit - total
            if remaining > 200:
                struct_text = _safe_json_dumps(self.structure)
                budget = min(int(limit * 0.2), remaining, 2000)
                chunk = _truncate(struct_text, budget)
                if chunk:
                    parts.append(f"## 📐 项目结构\n```json\n{chunk}\n```")
                    total += len(chunk)

        # 3. 项目文件（关键文件摘要）
        if self.files:
            remaining = limit - total
            if remaining > 200:
                file_parts: list[str] = []
                file_budget = remaining
                for fname, fcontent in list(self.files.items()):
                    if file_budget <= 100:
                        break
                    preview = _truncate(fcontent, min(800, file_budget))
                    file_parts.append(
                        f"### 📄 {fname}\n```\n{preview}\n```"
                    )
                    file_budget -= len(preview)
                if file_parts:
                    combined = "\n".join(file_parts)
                    parts.append(f"## 📁 项目文件\n{combined}")
                    total += len(combined)

        # 4. 代码产出
        if self.code:
            remaining = limit - total
            if remaining > 200:
                code_parts: list[str] = []
                code_budget = remaining
                for ctype, ccontent in list(self.code.items()):
                    if code_budget <= 100:
                        break
                    preview = _truncate(ccontent, min(600, code_budget))
                    lang = ctype if ctype in ("python", "html", "css", "javascript") else ""
                    code_parts.append(
                        f"### 💻 {ctype}\n```{lang}\n{preview}\n```"
                    )
                    code_budget -= len(preview)
                if code_parts:
                    combined = "\n".join(code_parts)
                    parts.append(f"## 🖥️ 代码产出\n{combined}")
                    total += len(combined)

        # 5. 最近对话历史（提供上下文连贯性）
        if self.conversation_history:
            remaining = limit - total
            if remaining > 200:
                recent = self.conversation_history[-10:]
                conv_lines: list[str] = []
                conv_budget = remaining
                for msg in recent:
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    line = f"**{role}**: {_truncate(content, min(200, conv_budget))}"
                    conv_lines.append(line)
                    conv_budget -= len(line)
                    if conv_budget <= 50:
                        break
                if conv_lines:
                    combined = "\n".join(conv_lines)
                    parts.append(f"## 💬 最近对话\n{combined}")

        if not parts:
            return ""

        header = (
            f"# 项目: {self.name or '未命名'} "
            f"(ID: {self.id}, 状态: {self.status.value})"
        )
        return f"{header}\n\n" + "\n\n".join(parts)

    # ── 节点 Prompt 注入 ───────────────────────────────────────

    def inject_node_prompt(
        self,
        node_name: str,
        extra_context: str = "",
    ) -> str:
        """
        为编码节点生成通用/全量 Prompt。

        该方法会自动收集项目上下文并构建一个完整的节点工作指令，
        告诉 AI 它是什么节点、应该做什么、有哪些可用资源。

        Parameters
        ----------
        node_name : str
            节点名称
        extra_context : str
            额外上下文信息（如上游节点输出、用户指令等）

        Returns
        -------
        str
            完整的节点级 Prompt
        """
        context = self.get_context(max_chars=6000)
        prompt = (
            f"## 🤖 节点工作指令\n\n"
            f"你当前正在执行节点 **「{node_name}」**。\n\n"
            f"### 项目信息\n"
            f"- 项目名称: {self.name or '未命名'}\n"
            f"- 项目 ID: {self.id}\n"
            f"- 项目状态: {self.status.value}\n\n"
        )

        # 如果有需求文档
        if self.requirements:
            prompt += f"### 需求要点\n{_truncate(self.requirements, 1500)}\n\n"

        # 如果有项目结构
        if self.structure:
            struct_summary = _safe_json_dumps(self.structure)
            prompt += f"### 项目结构\n```\n{_truncate(struct_summary, 800)}\n```\n\n"

        # 已有代码产出
        if self.code:
            prompt += "### 已有代码\n"
            for ctype, ccontent in self.code.items():
                if ccontent:
                    preview = _truncate(ccontent, 400)
                    prompt += f"**{ctype}**: ```\n{preview}\n```\n\n"

        # 已有文件
        if self.files:
            prompt += "### 项目文件\n"
            for fname in list(self.files.keys())[:20]:
                prompt += f"- `{fname}`\n"
            prompt += "\n"

        # 额外上下文
        if extra_context:
            prompt += f"### 当前输入/指令\n{extra_context}\n\n"

        # 工作指引
        prompt += (
            "### 工作指引\n"
            "1. 仔细阅读上面的项目上下文\n"
            "2. 根据你的节点任务完成对应工作\n"
            "3. 输出应该直接可用，不要有多余说明\n"
            "4. 如果需要修改已有代码，输出完整的修改后版本\n"
            "5. 保持与项目中其他文件/模块的一致性\n"
        )

        return prompt

    # ── 序列化/反序列化 ────────────────────────────────────────

    def to_dict(self) -> dict:
        """将项目状态序列化为字典（用于持久化）"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "conversation_history": self.conversation_history,
            "files": self.files,
            "requirements": self.requirements,
            "structure": self.structure,
            "code": self.code,
            "status": self.status.value,
            "pm_conversation": self.pm_conversation,
            "director_conversations": self.director_conversations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PMProject:
        """
        从字典还原项目状态。

        Parameters
        ----------
        data : dict
            序列化的项目数据

        Returns
        -------
        PMProject
            还原后的项目实例
        """
        status_str = data.get("status", "idle")
        try:
            status = ProjectStatus(status_str)
        except ValueError:
            status = ProjectStatus.IDLE
            logger.warning("未知的项目状态 '%s'，回退到 idle", status_str)

        return cls(
            id=data.get("id", _new_id()),
            name=data.get("name", ""),
            description=data.get("description", ""),
            created_at=data.get("created_at", _now()),
            updated_at=data.get("updated_at", _now()),
            conversation_history=data.get("conversation_history", []),
            files=data.get("files", {}),
            requirements=data.get("requirements", ""),
            structure=data.get("structure", {}),
            code=data.get("code", {}),
            status=status,
            pm_conversation=data.get("pm_conversation", []),
            director_conversations=data.get("director_conversations", {}),
        )

    def summary(self) -> dict:
        """获取项目摘要"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "file_count": len(self.files),
            "code_types": list(self.code.keys()),
            "conversation_length": len(self.conversation_history),
            "pm_conversation_length": len(self.pm_conversation),
            "director_agents": list(self.director_conversations.keys()),
            "has_requirements": bool(self.requirements),
            "has_structure": bool(self.structure),
        }


# ═══════════════════════════════════════════════════════════════════════
# PMAgentManager — 多项目管理器
# ═══════════════════════════════════════════════════════════════════════

def _compress_read_history(conv: list[dict]):
    """保留最近6轮工具调用的完整记录，更早的轮次只保留摘要。"""
    tool_rounds = []  # (start_idx, end_idx) of each tool round
    current_round_start = None
    for i, m in enumerate(conv):
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "assistant" and "<tool_call>" in content:
            if current_round_start is None:
                current_round_start = i
        elif role == "user" and content.startswith("工具执行结果:"):
            if current_round_start is not None:
                tool_rounds.append((current_round_start, i))
                current_round_start = None
    if len(tool_rounds) <= 6:
        return
    # Compress rounds older than the last 6
    to_compress = tool_rounds[:-6]
    for start, end in to_compress:
        for i in range(start, end + 1):
            m = conv[i]
            content = m.get("content", "")
            if len(content) > 200:
                m["content"] = content[:200] + "\n... (此轮工具调用已压缩)"

class PMAgentManager:
    """
    多项目管理器 — 管理多个独立项目，支持创建、删除、切换和持久化。

    每个 PM 只管理自己项目的对话，实现会话隔离。
    活跃项目只有一个，切换时会自动保存当前项目状态。

    Parameters
    ----------
    None

    Attributes
    ----------
    projects : dict[str, PMProject]
        所有项目的映射（project_id -> PMProject）
    active_project_id : str | None
        当前活跃项目 ID
    """

    def __init__(self) -> None:
        self.projects: dict[str, PMProject] = {}
        self.active_project_id: str | None = None

    # ── 项目 CRUD ──────────────────────────────────────────────

    def create_project(self, name: str, description: str = "") -> PMProject:
        """
        创建一个新项目。

        Parameters
        ----------
        name : str
            项目名称
        description : str
            项目描述

        Returns
        -------
        PMProject
            新创建的项目实例
        """
        project = PMProject(
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
        )
        self.projects[project.id] = project
        # 自动设为活跃项目
        self.active_project_id = project.id
        logger.info(
            "项目已创建: id=%s name='%s'", project.id, project.name,
        )
        return project

    def get_project(self, project_id: str) -> PMProject | None:
        """
        根据 ID 获取项目。

        Parameters
        ----------
        project_id : str
            项目 ID

        Returns
        -------
        PMProject | None
            项目实例，不存在时返回 None
        """
        return self.projects.get(project_id)

    def list_projects(self) -> list[dict]:
        """
        列出所有项目摘要，按更新时间降序排列。

        Returns
        -------
        list[dict]
            项目摘要列表
        """
        summaries = [p.summary() for p in self.projects.values()]
        summaries.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        # 标记活跃项目
        for s in summaries:
            s["is_active"] = (s["id"] == self.active_project_id)
        return summaries

    def delete_project(self, project_id: str) -> bool:
        """
        删除一个项目。

        如果删除的是活跃项目，自动清除 active_project_id。

        Parameters
        ----------
        project_id : str
            项目 ID

        Returns
        -------
        bool
            是否成功删除
        """
        if project_id not in self.projects:
            logger.warning("删除失败: 项目 %s 不存在", project_id)
            return False
        del self.projects[project_id]
        if self.active_project_id == project_id:
            self.active_project_id = None
        logger.info("项目已删除: id=%s", project_id)
        return True

    # ── 活跃项目管理 ────────────────────────────────────────────

    def set_active(self, project_id: str) -> bool:
        """
        设置活跃项目。

        Parameters
        ----------
        project_id : str
            目标项目 ID

        Returns
        -------
        bool
            是否成功切换
        """
        if project_id not in self.projects:
            logger.warning("切换失败: 项目 %s 不存在", project_id)
            return False
        old_id = self.active_project_id
        self.active_project_id = project_id
        logger.info("活跃项目切换: %s -> %s", old_id, project_id)
        return True

    def get_active(self) -> PMProject | None:
        """
        获取当前活跃项目。

        Returns
        -------
        PMProject | None
            活跃项目实例，无活跃项目时返回 None
        """
        if self.active_project_id is None:
            return None
        return self.projects.get(self.active_project_id)

    async def get_active_safe(self) -> PMProject | None:
        """
        安全获取活跃项目（异步版本，兼容 async 调用链）。

        Returns
        -------
        PMProject | None
            活跃项目实例
        """
        return self.get_active()

    # ── 批量操作 ───────────────────────────────────────────────

    def get_all_projects(self) -> list[PMProject]:
        """获取所有项目实例列表"""
        return list(self.projects.values())

    def project_count(self) -> int:
        """获取项目总数"""
        return len(self.projects)

    def clear_all(self) -> int:
        """
        清除所有项目。

        Returns
        -------
        int
            被清除的项目数量
        """
        count = len(self.projects)
        self.projects.clear()
        self.active_project_id = None
        logger.info("已清除所有项目: %d 个", count)
        return count

    # ── 持久化 — Storage 集成 ─────────────────────────────────

    def save(self, storage: Any) -> None:
        """
        将所有项目状态保存到 Storage。

        每个项目保存为独立的 session entry，mode='pm_project'。
        同时保存管理器元数据（活跃项目 ID、项目列表）。

        Parameters
        ----------
        storage : Storage
            cx2118 Script Weaver 的 Storage 实例
        """
        if storage is None:
            logger.warning("save: storage 为 None，跳过持久化")
            return

        # 保存每个项目
        saved_ids: list[str] = []
        for project_id, project in self.projects.items():
            try:
                project_data = project.to_dict()
                # 确保 session 存在
                if hasattr(storage, "_ensure_session"):
                    storage._ensure_session(project_id)
                else:
                    # 回退: 使用 new_session 或 load_session
                    if not storage.load_session(project_id):
                        pass  # session 不存在时由后续 save 创建

                # 保存项目数据为 pipeline_state（复用现有表结构）
                state_data = {
                    "phase": project.status.value,
                    "pm_project_data": project_data,
                }
                storage.save_pipeline_state(project_id, state_data)

                # 保存需求文档
                if project.requirements:
                    storage.save_requirements(project_id, project.requirements)

                # 保存项目结构
                if project.structure:
                    storage.save_plan(
                        project_id,
                        structure=project.structure,
                        raw=json.dumps(project.structure, ensure_ascii=False),
                    )

                # 保存代码文件
                for code_type, code_content in project.code.items():
                    if code_content:
                        storage.save_code(project_id, code_type, code_content)

                # 保存 PM 对话
                for msg in project.pm_conversation:
                    role = msg.get("role", "pm")
                    content = msg.get("content", "")
                    storage.save_message(project_id, f"pm_{role}", content)

                # 保存 Director 子代理对话
                for agent_name, messages in project.director_conversations.items():
                    for msg in messages:
                        role = msg.get("role", agent_name)
                        content = msg.get("content", "")
                        storage.save_message(
                            project_id,
                            f"director_{agent_name}_{role}",
                            content,
                        )

                saved_ids.append(project_id)

            except Exception as exc:
                logger.error(
                    "保存项目 %s 失败: %s", project_id, exc, exc_info=True,
                )

        # 保存管理器元数据到 settings
        try:
            manager_meta = {
                "active_project_id": self.active_project_id,
                "project_ids": saved_ids,
                "version": __version__,
            }
            storage.save_settings("pm_agent_manager", manager_meta)
            logger.info("PM Agent Manager 已保存: %d 个项目", len(saved_ids))

        except Exception as exc:
            logger.error("保存管理器元数据失败: %s", exc)

    def load(self, storage: Any) -> int:
        """
        从 Storage 加载所有项目状态。

        Parameters
        ----------
        storage : Storage
            cx2118 Script Weaver 的 Storage 实例

        Returns
        -------
        int
            成功加载的项目数量
        """
        if storage is None:
            logger.warning("load: storage 为 None，跳过加载")
            return 0

        loaded_count = 0

        # 1. 加载管理器元数据
        try:
            manager_meta = storage.load_settings("pm_agent_manager")
            if not manager_meta:
                logger.info("未找到 PM Agent Manager 元数据")
                return 0

            project_ids = manager_meta.get("project_ids", [])
            self.active_project_id = manager_meta.get("active_project_id")

        except Exception as exc:
            logger.error("加载管理器元数据失败: %s", exc)
            return 0

        # 2. 逐个加载项目
        for project_id in project_ids:
            try:
                # 从 pipeline_state 恢复项目数据
                state_data = storage.load_pipeline_state(project_id)
                project_data = state_data.get("pm_project_data", {})

                if not project_data:
                    # 尝试从 session + 其他表重建
                    session_info = storage.load_session(project_id)
                    if not session_info:
                        logger.warning("项目 %s 的 session 不存在，跳过", project_id)
                        continue
                    project_data = self._rebuild_project_data(storage, project_id)

                if not project_data:
                    continue

                project = PMProject.from_dict(project_data)

                # 补充从独立表加载的数据（如果 to_dict 中没有覆盖到）
                if not project.requirements:
                    project.requirements = storage.load_requirements(project_id) or ""

                if not project.structure:
                    plan_data = storage.load_plan(project_id)
                    if plan_data and plan_data.get("structure"):
                        project.structure = plan_data["structure"]

                if not project.code:
                    all_code = storage.load_all_code(project_id)
                    project.code = dict(all_code)

                self.projects[project.id] = project
                loaded_count += 1
                logger.debug("项目已加载: id=%s name='%s'", project.id, project.name)

            except Exception as exc:
                logger.error(
                    "加载项目 %s 失败: %s", project_id, exc, exc_info=True,
                )

        logger.info("PM Agent Manager 已加载: %d / %d 个项目", loaded_count, len(project_ids))
        return loaded_count

    def _rebuild_project_data(self, storage: Any, session_id: str) -> dict:
        """
        从 Storage 的各个表中重建项目数据（回退方案）。

        当 pipeline_state 中没有 pm_project_data 时，尝试从
        sessions、requirements、plans、code_files 等表重建。
        """
        try:
            session_info = storage.load_session(session_id)
            if not session_info:
                return {}

            requirements = storage.load_requirements(session_id) or ""
            plan_data = storage.load_plan(session_id)
            structure = plan_data.get("structure", {}) if plan_data else {}
            all_code = storage.load_all_code(session_id)

            # 加载完整对话历史（包括 tool_call / tool_result）
            conversation = storage.load_conversation(session_id, limit=200)

            # 从 conversation 中分离 pm_conversation
            # 存储时 role 格式为 pm_user / pm_assistant / pm_tool_call / pm_tool_result
            pm_conversation = []
            for msg in conversation:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role.startswith("pm_"):
                    actual_role = role[3:]  # 去掉 pm_ 前缀
                    pm_conversation.append({"role": actual_role, "content": content})
                elif role in ("user", "assistant"):
                    # 兼容旧格式
                    pm_conversation.append({"role": role, "content": content})

            return {
                "id": session_id,
                "name": session_info.get("name", ""),
                "description": "",
                "created_at": session_info.get("created_at", _now()),
                "updated_at": session_info.get("updated_at", _now()),
                "conversation_history": conversation,
                "files": {},
                "requirements": requirements,
                "structure": structure,
                "code": dict(all_code),
                "status": session_info.get("mode", "idle"),
                "pm_conversation": pm_conversation,
                "director_conversations": {},
            }

        except Exception as exc:
            logger.error("重建项目数据失败 (%s): %s", session_id, exc)
            return {}

    # ── 状态查询 ───────────────────────────────────────────────

    def get_status(self) -> dict:
        """获取管理器状态摘要"""
        active = self.get_active()
        return {
            "total_projects": len(self.projects),
            "active_project_id": self.active_project_id,
            "active_project_name": active.name if active else None,
            "project_ids": list(self.projects.keys()),
            "version": __version__,
        }


# ═══════════════════════════════════════════════════════════════════════
# AgentMode — Agent 模式封装
# ═══════════════════════════════════════════════════════════════════════

class AgentMode:
    """
    Agent 模式 — 封装 PM Agent 的系统提示、工具集和对话能力。

    每个 AgentMode 实例绑定一个 PMProject，提供完整的 AI Agent 交互能力：
    - 构建 Agent 系统提示（角色定义 + 能力说明 + 项目上下文）
    - 流式对话接口（支持 SSE 回调）
    - 工具定义（read_file, write_file, search_web, call_llm, list_directory）

    Parameters
    ----------
    project : PMProject
        绑定的项目实例
    agent_name : str
        Agent 名称（如 'pm_agent', 'director_agent'）
    agent_description : str
        Agent 的角色描述
    """

    def __init__(
        self,
        project: PMProject,
        agent_name: str = "pm_agent",
        agent_description: str = "",
        sandbox=None,
    ) -> None:
        self.project = project
        self.agent_name = agent_name
        self.agent_description = agent_description or f"项目管理 Agent ({agent_name})"
        self.sandbox = sandbox  # CodeSandbox instance

        # 可用工具定义
        self.tools: list[dict] = [
            {
                "name": "list_workspace",
                "description": "列出 workspace 文件夹中所有文件（不含隐藏文件），返回文件名和大小。这是你了解项目结构的第一步。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "get_cwd",
                "description": "获取当前工作目录和 workspace 路径。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "read_file",
                "description": "读取文件完整内容（带行号）。所有文件操作都在共享 workspace 文件夹中进行。返回内容带行号前缀如 '1: code'。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名，如 main.py, index.html"},
                    },
                    "required": ["filename"],
                },
            },
            {
                "name": "read_file_lines",
                "description": "读取文件指定行范围。适用于大文件的局部查看。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "start": {"type": "integer", "description": "起始行号(从1开始)"},
                        "end": {"type": "integer", "description": "结束行号(不含)"},
                    },
                    "required": ["filename"],
                },
            },
            {
                "name": "create_file",
                "description": "创建新文件。如果文件已存在则报错。适用于新建文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "新文件名"},
                        "content": {"type": "string", "description": "文件初始内容"},
                    },
                    "required": ["filename", "content"],
                },
            },
            {
                "name": "write_file",
                "description": "覆盖写入文件完整内容。文件不存在则创建，已存在则覆盖。适用于重写整个文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名"},
                        "content": {"type": "string", "description": "文件完整内容（会覆盖原内容）"},
                    },
                    "required": ["filename", "content"],
                },
            },
            {
                "name": "patch_file",
                "description": "精确修改文件：找到指定文本并替换为新文本。适用于局部修改，不需要重写整个文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "find": {"type": "string", "description": "要精确匹配的原始文本"},
                        "replace": {"type": "string", "description": "替换后的文本"},
                    },
                    "required": ["filename", "find", "replace"],
                },
            },
            {
                "name": "append_file",
                "description": "向文件末尾追加内容，不修改已有内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "content": {"type": "string", "description": "要追加的内容"},
                    },
                    "required": ["filename", "content"],
                },
            },
            {
                "name": "rename_file",
                "description": "重命名文件。参数: old_name, new_name",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "old_name": {"type": "string", "description": "原文件名"},
                        "new_name": {"type": "string", "description": "新文件名"},
                    },
                    "required": ["old_name", "new_name"],
                },
            },
            {
                "name": "delete_file",
                "description": "删除文件。文件不存在则报错。操作不可逆。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "要删除的文件名"},
                    },
                    "required": ["filename"],
                },
            },
            {
                "name": "run_code",
                "description": "在临时沙箱中执行 Python 代码并返回输出。用于测试代码片段。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "要执行的 Python 代码"},
                    },
                    "required": ["code"],
                },
            },
            {
                "name": "done",
                "description": "任务完成，输出最终总结。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "完成总结"},
                    },
                    "required": ["summary"],
                },
            },
            {
                "name": "search_skill",
                "description": "搜索技能文件夹中的参考文档。输入关键词，返回匹配的技能文件内容。用于查找编码规范、最佳实践、错误修复方案等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词，如 'flask 路由', 'async 错误处理', 'pandas 数据清洗'"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "write_skill",
                "description": "编写新的技能文件。写入后需要人工预览通过才会生效。适用于沉淀编码经验、最佳实践、错误修复方案。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "文件名，如 flask_routing.md"},
                        "title": {"type": "string", "description": "技能标题"},
                        "keywords": {"type": "string", "description": "关键词，逗号分隔，如 'flask,routing,decorator'"},
                        "content": {"type": "string", "description": "技能正文内容（Markdown 格式）"},
                    },
                    "required": ["filename", "title", "keywords", "content"],
                },
            },
            {
                "name": "git_init",
                "description": "初始化 Git 仓库并做首次提交。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "git_commit",
                "description": "提交当前所有修改。",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "提交信息"}},
                    "required": ["message"],
                },
            },
            {
                "name": "git_log",
                "description": "查看最近 10 条提交记录。",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "git_rollback",
                "description": "回滚到指定提交。",
                "parameters": {
                    "type": "object",
                    "properties": {"commit": {"type": "string", "description": "如 HEAD~1 或 commit hash"}},
                },
            },
            {
                "name": "import_zip",
                "description": "从 URL 导入 zip 项目包。",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "zip 下载地址"}},
                    "required": ["url"],
                },
            },
            {
                "name": "scan_design_specs",
                "description": "扫描 workspace 中的设计规范文件（.md, .txt, .json, .html），读取内容并自动提取关键词和摘要。用于发现可用的设计规范并生成技能文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "文件名匹配模式，如 'design', 'spec', '规范'，默认扫描所有规范类文件"},
                        "max_files": {"type": "integer", "description": "最多扫描几个文件，默认5"},
                    },
                },
            },
            {
                "name": "generate_skill_from_spec",
                "description": "从设计规范文件自动生成技能文件。读取指定规范文件，提取关键信息生成 skill 文件，写入 workspace/skills/pending/ 目录等待人工审核。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "spec_file": {"type": "string", "description": "设计规范文件名，如 design_spec.md"},
                        "skill_name": {"type": "string", "description": "生成的技能文件名，如 html_design_rules.md"},
                    },
                    "required": ["spec_file"],
                },
            },
        ]

    # ── 系统提示构建 ───────────────────────────────────────────

    def build_agent_system_prompt(self) -> str:
        """
        构建完整的 Agent 系统提示。

        系统提示包含:
        1. Agent 角色定义和能力说明
        2. 可用工具列表及使用方法
        3. 项目上下文注入
        4. 输出格式要求
        5. 通用 Prompt 注入（节点感知）

        Returns
        -------
        str
            完整的系统提示字符串
        """
        ws_files = ', '.join(list(self.project.files.keys())[:15]) or '暂无'
        # 动态获取可用技能列表
        skill_list = ""
        try:
            from pathlib import Path as _P
            _skills_dir = _P(__file__).parent / "workspace" / "skills"
            if _skills_dir.exists():
                _skill_files = [f.stem for f in _skills_dir.glob("*.md") if not f.name.startswith(".")]
                if _skill_files:
                    skill_list = "\n".join(f"  - `{s}`" for s in _skill_files[:20])
        except Exception:
            pass

        prompt = f"""# 你是「{self.project.name or '未命名'}」项目的编程助手

你负责文件维护和代码修改。与 Build 模式共享 workspace 文件夹。
所有文件操作都在 workspace/ 目录中，Build 创建的文件你能看到，你创建的文件 Build 也能看到。

## 核心定位

你是高效的编程助手，像一个经验丰富的开发者：
- **自主决策**：遇到问题先自己解决，不要频繁问用户。能推断的就推断，能搜的就搜
- **批量操作**：一次可以调用多个工具完成一个完整步骤（如先 list_workspace 再 read_file）
- **主动学习**：编写代码前自动搜索相关技能文档获取参考
- **精确执行**：用 patch_file 精确修改，不要整个文件重写

## ⚠️ 最重要的规则

**所有文件操作必须通过工具调用完成，绝对不要在回复中直接输出代码！**

错误示例（禁止）：
```
好的，我来创建文件：
```html
<!DOCTYPE html>
...
```
```

正确示例：
```
已搜索到设计规范，现在创建文件。
<tool_call>write_file
filename=login.html
content=<!DOCTYPE html>...
</tool_call>
```

**不要在回复中贴代码块。所有代码必须通过 write_file/create_file/patch_file 工具写入。**

## 说话方式

你是直接对用户说话，不要用第三人称或自言自语：
- ❌ "我写一个代码作为回复" 
- ❌ "用户需要登录页面，我应该创建..."
- ✅ "已搜索到设计规范，现在创建登录页面"
- ✅ "好的，基于设计规范创建 login.html"

简洁直接，像专业开发者对同事说话：
- "已读取 main.py，在第10行添加 greet 函数"
- "已搜索 HTML 设计规范，创建登录页面"
- "删除旧的 test.py"

**输出格式：必须使用换行符分段。每句话或每个要点独占一行。列表项用 - 或数字开头。代码用 ``` 包裹。绝对不要把所有内容挤在一行里。**

## 工作流程

```
用户指令 → search_skill(搜技能) + read_file(读代码) → 确认后 write_file → done
```

**关键点：**
- 你可以在一轮中连续调用多个工具
- 只有 write_file/create_file/delete_file 需要等用户确认
- read_file、search_skill、list_workspace 可以直接调用
- 遇到问题先 search_skill 搜索解决方案，不要问用户

## 工具详情

### 查看类（直接调用，无需确认）

**list_workspace** — 列出 workspace 所有文件
- 参数：无

**read_file** — 读取文件完整内容
- 参数：`filename`（必填）

**read_file_lines** — 读取指定行范围
- 参数：`filename`（必填）、`start`（起始行）、`end`（结束行，不含）

### 技能类（直接调用，无需确认）

**search_skill** — 搜索技能文档
- 参数：`query`（必填）— 搜索关键词
- 自动搜索场景：
  - 写 HTML/CSS/JS → 搜 "html 设计规范"
  - 写 Python → 搜相关技术关键词
  - 遇到报错 → 搜错误信息关键词

**write_skill** — 沉淀经验为技能文件
- 参数：`filename`（必填）、`title`（必填）、`keywords`（必填）、`content`（必填）

### 文件操作类

**create_file** — 创建新文件（文件已存在会报错）
- 参数：`filename`（必填）、`content`（必填）

**write_file** — 覆盖写入（不存在则创建，已存在则覆盖）
- 参数：`filename`（必填）、`content`（必填）
- **需等用户确认后调用**

**patch_file** — 精确替换（优先使用）
- 参数：`filename`（必填）、`find`（必填）、`replace`（必填）

**append_file** — 追加到文件末尾
- 参数：`filename`（必填）、`content`（必填）

**delete_file** — 删除（不可逆）
- 参数：`filename`（必填）

### 执行类

**run_code** — 执行 Python 代码片段
- 参数：`code`（必填）

**done** — 任务完成
- 参数：`summary`（必填）

## 工具调用格式

一轮可以调用多个工具：

<tool_call>search_skill
query=html 设计规范
</tool_call>

<tool_call>read_file
filename=index.html
</tool_call>

## 场景示例

### 示例1：用户说"做一个登录页面"
你直接说："已搜索到设计规范，现在创建登录页面"
然后调用：
<tool_call>search_skill
query=html 设计规范
</tool_call>

<tool_call>read_file
filename=index.html
</tool_call>

用户确认后：
<tool_call>write_file
filename=login.html
content=<!DOCTYPE html>...
</tool_call>

### 示例2：用户说"给 main.py 加个 greet 函数"
你直接说："已读取 main.py，在第10行添加 greet 函数"
然后调用：
<tool_call>read_file
filename=main.py
</tool_call>

用户确认后：
<tool_call>patch_file
filename=main.py
find=if __name__ == "__main__":
replace=def greet(name):
    return f"Hello, {{name}}!"


if __name__ == "__main__":
    print(greet("World"))
</tool_call>

### 示例3：用户说"删除旧的 test.py"
你直接说："test.py 内容是XXX，确认删除？"
用户确认后：
<tool_call>delete_file
filename=test.py
</tool_call>

## 重要规则

1. **绝对不要在回复中输出代码** — 所有文件操作必须通过工具调用
2. **直接对用户说话** — 不要用第三人称或自言自语
3. **简洁直接** — 像专业开发者对同事说话
4. **自主解决问题** — 遇到问题先 search_skill，不要问用户
5. **写前确认** — write_file/create_file/delete_file 前告诉用户计划
6. **先搜再写** — 写代码前 search_skill 获取参考

## 当前项目文件
{ws_files}

## 可用技能文档
{skill_list if skill_list else '（暂无技能文档）'}
"""
        return prompt

    # ── 流式对话 ───────────────────────────────────────────────

    async def chat(
        self,
        user_msg: str,
        llm_client: Any,
        emit_callback: Callable[[dict], Any] | None = None,
        temperature: float = 0.5,
        max_tokens: int = 32768,
    ) -> str:
        """
        发送消息到 LLM 并流式返回响应。

        完整流程:
        1. 构建系统提示（含项目上下文）
        2. 将用户消息追加到对话历史
        3. 流式调用 LLM（支持 emit_callback 实时推送）
        4. 收集完整响应并追加到对话历史
        5. 更新项目时间戳

        Parameters
        ----------
        user_msg : str
            用户消息
        llm_client : Any
            LLM 客户端实例（需支持 stream_chat 方法）
        emit_callback : Callable[[dict], Any] | None
            SSE 事件回调函数
        temperature : float
            生成温度（默认 0.5）
        max_tokens : int
            最大 token 数（默认 4096）

        Returns
        -------
        str
            LLM 的完整响应文本
        """
        if not user_msg or not user_msg.strip():
            logger.warning("chat: 用户消息为空")
            return ""

        # 追加用户消息到 PM 对话历史
        self.project.add_pm_message("user", user_msg)

        # 构建消息列表（使用 PM 专属对话历史，自动压缩）
        messages = self._compress_history(list(self.project.pm_conversation))

        # 构建系统提示
        system_prompt = self.build_agent_system_prompt()

        # 流式调用 LLM
        full_response = ""
        token_count = 0

        try:
            # 适配不同的 LLM 客户端接口
            if hasattr(llm_client, "stream_chat"):
                # cx2118 Script Weaver 内置 LLMClient（返回 dict 事件流）
                async for ev in llm_client.stream_chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    skip_reasoning=False,
                ):
                    if ev.get("type") == "token":
                        token = ev.get("content", "")
                        if token:
                            full_response += token
                            token_count += 1
                            # 推送 token 事件
                            if emit_callback:
                                try:
                                    await emit_callback({
                                        "type": "agent_token",
                                        "agent": self.agent_name,
                                        "token": token,
                                        "token_count": token_count,
                                    })
                                except Exception:
                                    pass
                    elif ev.get("type") == "reasoning":
                        # 推送思考事件（前端折叠显示）
                        if emit_callback:
                            try:
                                await emit_callback({
                                    "type": "agent_reasoning",
                                    "agent": self.agent_name,
                                    "content": ev.get("content", ""),
                                })
                            except Exception:
                                pass

            elif hasattr(llm_client, "chat") and hasattr(llm_client, "_client"):
                # 独立 llm_client.py 的 LLMClient（返回 AsyncGenerator[str, None]）
                async for token in llm_client.stream_chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if token:
                        full_response += token
                        token_count += 1
                        if emit_callback:
                            try:
                                await emit_callback({
                                    "type": "agent_token",
                                    "agent": self.agent_name,
                                    "token": token,
                                    "token_count": token_count,
                                })
                            except Exception:
                                pass

            else:
                # 回退：尝试非流式调用
                logger.warning("chat: 未知的 LLM 客户端类型，尝试非流式调用")
                result = await llm_client.chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                full_response = result if isinstance(result, str) else str(result)

        except asyncio.CancelledError:
            logger.info("chat: 对话被取消")
            raise

        except Exception as exc:
            logger.error("chat: LLM 调用失败: %s", exc, exc_info=True)
            error_msg = f"Agent 调用失败: {exc}"
            if emit_callback:
                try:
                    await emit_callback({
                        "type": "agent_error",
                        "agent": self.agent_name,
                        "error": error_msg,
                    })
                except Exception:
                    pass
            return error_msg

        # ── 工具调用循环 ──
        max_tool_rounds = 20
        _tool_history = {}  # 记录工具调用次数，同一工具最多16轮
        _TOOL_CALL_LIMIT = 16
        _loop_start = time.time()
        _MAX_LOOP_TIME = 120  # 2分钟超时
        for _round in range(max_tool_rounds):
            if time.time() - _loop_start > _MAX_LOOP_TIME:
                logger.warning("chat: 工具调用超时 (%ds)", _MAX_LOOP_TIME)
                if emit_callback:
                    try: await emit_callback({"type": "agent_error", "agent": self.agent_name, "error": "工具调用超时，请直接回复用户"})
                    except Exception: pass
                break
            # DeepSeek returns literal \n (backslash+n) instead of real newlines
            full_response = full_response.replace('\\n', '\n').replace('\\t', '\t')
            tool_calls = self._parse_tool_calls(full_response)
            if not tool_calls:
                break

            # 防死循环：检测重复调用，同一工具+参数最多12次
            blocked = False
            for tc in tool_calls:
                call_key = f"{tc['name']}:{json.dumps(tc['args'], sort_keys=True, default=str)}"
                count = _tool_history.get(call_key, 0)
                if count >= _TOOL_CALL_LIMIT:
                    blocked = True
                    if emit_callback:
                        try:
                            await emit_callback({
                                "type": "agent_tool_call",
                                "agent": self.agent_name,
                                "tool": tc["name"],
                                "args": tc["args"],
                                "skipped": True,
                            })
                        except Exception:
                            pass
                    break
                _tool_history[call_key] = count + 1

            if not tool_calls or blocked:
                # 被防死循环拦截，告诉LLM不要重复
                self.project.add_pm_message("user", "你刚刚重复调用了相同的工具。请不要再调用工具，直接用文字回复用户。")
                messages = self._compress_history(list(self.project.pm_conversation))
                full_response = ""
                token_count = 0
                try:
                    if hasattr(llm_client, "stream_chat"):
                        async for ev in llm_client.stream_chat(
                            messages=messages,
                            system_prompt=system_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            skip_reasoning=False,
                        ):
                            if ev.get("type") == "token":
                                token = ev.get("content", "")
                                if token:
                                    full_response += token
                                    token_count += 1
                                    if emit_callback:
                                        try: await emit_callback({"type": "agent_token", "agent": self.agent_name, "token": token})
                                        except Exception: pass
                            elif ev.get("type") == "reasoning":
                                if emit_callback:
                                    try: await emit_callback({"type": "agent_reasoning", "agent": self.agent_name, "content": ev.get("content", "")})
                                    except Exception: pass
                except Exception:
                    pass
                break

            # 执行工具并收集结果
            tool_results = []
            for tc in tool_calls:
                # Save tool call to history
                self.project.add_pm_message("tool_call", json.dumps({"tool": tc["name"], "args": tc["args"]}, ensure_ascii=False))
                if emit_callback:
                    try:
                        await emit_callback({
                            "type": "agent_tool_call",
                            "agent": self.agent_name,
                            "tool": tc["name"],
                            "args": tc["args"],
                        })
                    except Exception:
                        pass
                result = await self.execute_tool(tc["name"], **tc["args"])
                tool_results.append({"tool": tc["name"], "result": result})
                # Save tool result to history — 保留完整内容，不做截断
                self.project.add_pm_message("tool_result", json.dumps({"tool": tc["name"], "result": result}, ensure_ascii=False, default=str))
                # 写入文件时同步写到磁盘
                if tc["name"] == "write_file":
                    fname = tc["args"].get("filename", "")
                    fcontent = tc["args"].get("content", "")
                    if fname and fcontent:
                        try:
                            ws = _WORKSPACE_DIR
                            os.makedirs(ws, exist_ok=True)
                            fpath = os.path.join(ws, fname)
                            os.makedirs(os.path.dirname(fpath) if os.path.dirname(fpath) != ws else ws, exist_ok=True)
                            with open(fpath, "w", encoding="utf-8") as f:
                                f.write(fcontent)
                        except Exception as e:
                            tool_results[-1]["result"]["disk_error"] = str(e)
                if emit_callback:
                    # SSE 只发摘要，完整内容留在后端给 LLM
                    summary_result = {}
                    if isinstance(result, dict):
                        for k, v in result.items():
                            if k == "content" and isinstance(v, str) and len(v) > 200:
                                lines = v.split("\n")
                                summary_result[k] = "\n".join(lines[:5]) + f"\n... ({len(lines)} lines total)"
                            else:
                                summary_result[k] = v
                    else:
                        summary_result = result
                    try:
                        await emit_callback({
                            "type": "agent_tool_result",
                            "agent": self.agent_name,
                            "tool": tc["name"],
                            "result": summary_result,
                        })
                    except Exception:
                        pass

            # 追加工具结果到对话历史 — 全量保留，不做截断
            tool_result_text = "\n".join(
                f"[{r['tool']}] 结果: {json.dumps(r['result'], ensure_ascii=False, default=str)}"
                for r in tool_results
            )
            self.project.add_pm_message("user", f"工具执行结果:\n{tool_result_text}\n\n请根据工具结果继续。")

            # 超过6轮工具调用后，压缩历史中旧的全量read_file结果
            conv = list(self.project.pm_conversation)
            _compress_read_history(conv)
            self.project.pm_conversation = conv

            # 再次调用 LLM（压缩上下文）
            messages = self._compress_history(list(self.project.pm_conversation))
            full_response = ""
            token_count = 0
            try:
                if hasattr(llm_client, "stream_chat"):
                    async for ev in llm_client.stream_chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        skip_reasoning=False,
                    ):
                        if ev.get("type") == "token":
                            token = ev.get("content", "")
                            if token:
                                full_response += token
                                token_count += 1
                                if emit_callback:
                                    try:
                                        await emit_callback({
                                            "type": "agent_token",
                                            "agent": self.agent_name,
                                            "token": token,
                                        })
                                    except Exception:
                                        pass
            except Exception:
                break

        # 追加响应到对话历史
        if full_response:
            full_response = full_response.replace('\\n', '\n').replace('\\t', '\t')
            self.project.add_pm_message("assistant", full_response)

        # 推送完成事件
        if emit_callback:
            try:
                await emit_callback({
                    "type": "agent_response",
                    "agent": self.agent_name,
                    "project_id": self.project.id,
                    "message": full_response,
                    "token_count": token_count,
                })
            except Exception:
                pass

        logger.info(
            "chat: 完成 agent=%s tokens=%d chars=%d",
            self.agent_name, token_count, len(full_response),
        )
        return full_response

    # ── 工具执行 ───────────────────────────────────────────────

    def _strip_think(self, text: str) -> str:
        """Remove <think>/</think> and other thinking tags from text."""
        import re as _re
        text = _re.sub(r'<think>[\s\S]*?<\/think>', '', text)
        text = _re.sub(r'<reasoning>[\s\S]*?<\/reasoning>', '', text)
        text = _re.sub(r'<thinking>[\s\S]*?<\/thinking>', '', text)
        text = _re.sub(r'\[thinking\][\s\S]*?\[/thinking\]', '', text)
        return text.strip()

    async def execute_tool(self, tool_name: str, **kwargs) -> Any:
        """
        执行指定的工具。

        Parameters
        ----------
        tool_name : str
            工具名称
        **kwargs
            工具参数

        Returns
        -------
        Any
            工具执行结果
        """
        if tool_name == "read_file":
            filename = kwargs.get("filename", "")
            offset = int(kwargs.get("offset", 0))
            limit = int(kwargs.get("limit", 0))
            if not filename:
                return {"error": "缺少 filename 参数"}
            # Always read from shared workspace disk
            content = ""
            try:
                _ws_dir = _WORKSPACE_DIR
                fpath = os.path.join(_ws_dir, filename)
                if os.path.isfile(fpath):
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    self.project.update_file(filename, content)
            except Exception:
                pass
            if not content:
                content = self.project.get_file(filename)
            if not content:
                return {"error": f"文件 '{filename}' 不存在"}
            lines = content.split("\n")
            total = len(lines)
            # 带行号输出
            start = max(0, offset - 1) if offset > 0 else 0
            end = min(total, start + (limit if limit > 0 else total))
            selected = lines[start:end]
            numbered = "\n".join(f"{i+1}: {line}" for i, line in enumerate(selected, start))
            if end < total:
                summary = f"(Showing lines {start+1}-{end} of {total}. Read with offset={end+1} to continue.)"
            else:
                summary = f"(End of file - total {total} lines)"
            return {"content": numbered + "\n\n" + summary, "filename": filename, "total_lines": total, "truncated": end < total}

        elif tool_name == "write_file":
            filename = kwargs.get("filename", "")
            content = kwargs.get("content", "")
            if not filename:
                return {"error": "缺少 filename 参数"}
            if not content:
                return {"error": "缺少 content 参数"}
            # Strip think tags from content
            content = self._strip_think(content)
            # File size limits
            line_count = len(content.split("\n"))
            if line_count > 2000:
                return {"error": f"文件行数 {line_count} 超过限制 (最大 2000 行)，请拆分文件"}
            if len(content.encode("utf-8")) > 500_000:
                return {"error": f"文件大小超过限制 (最大 500KB)，请精简内容"}
            self.project.update_file(filename, content)
            # 写入磁盘
            try:
                ws = _WORKSPACE_DIR
                os.makedirs(ws, exist_ok=True)
                fpath = os.path.join(ws, filename)
                os.makedirs(os.path.dirname(fpath) if os.path.dirname(fpath) != ws else ws, exist_ok=True)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                return {"error": f"写入磁盘失败: {e}"}
            return {
                "success": True,
                "filename": filename,
                "size": len(content),
                "lines": len(content.split("\n")),
            }

        elif tool_name == "create_file":
            filename = kwargs.get("filename", "")
            content = kwargs.get("content", "")
            if not filename:
                return {"error": "缺少 filename 参数"}
            if not content:
                return {"error": "缺少 content 参数"}
            content = self._strip_think(content)
            line_count = len(content.split("\n"))
            if line_count > 2000:
                return {"error": f"文件行数 {line_count} 超过限制 (最大 2000 行)，请拆分文件"}
            if len(content.encode("utf-8")) > 500_000:
                return {"error": f"文件大小超过限制 (最大 500KB)，请精简内容"}
            # Check if file already exists on disk
            try:
                fpath = os.path.join(_WORKSPACE_DIR, filename)
                if os.path.isfile(fpath):
                    return {"error": f"文件已存在: {filename}，请用 write_file 覆盖或先 delete_file 删除"}
            except Exception:
                pass
            # Also check project in-memory
            if self.project.get_file(filename):
                return {"error": f"文件已存在: {filename}，请用 write_file 覆盖或先 delete_file 删除"}
            self.project.update_file(filename, content)
            try:
                os.makedirs(_WORKSPACE_DIR, exist_ok=True)
                fpath = os.path.join(_WORKSPACE_DIR, filename)
                os.makedirs(os.path.dirname(fpath) if os.path.dirname(fpath) != _WORKSPACE_DIR else _WORKSPACE_DIR, exist_ok=True)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                return {"error": f"创建文件失败: {e}"}
            return {"success": True, "filename": filename, "size": len(content), "lines": len(content.split("\n"))}

        elif tool_name == "rename_file":
            old_name = kwargs.get("old_name", "")
            new_name = kwargs.get("new_name", "")
            if not old_name or not new_name:
                return {"error": "缺少 old_name 或 new_name 参数"}
            try:
                old_path = os.path.join(_WORKSPACE_DIR, old_name)
                new_path = os.path.join(_WORKSPACE_DIR, new_name)
                if not os.path.exists(old_path):
                    return {"error": f"文件不存在: {old_name}"}
                if os.path.exists(new_path):
                    return {"error": f"目标文件已存在: {new_name}"}
                os.rename(old_path, new_path)
            except Exception as e:
                return {"error": f"重命名失败: {e}"}
            # Update project in-memory files
            if old_name in self.project.files:
                content = self.project.files.pop(old_name)
                self.project.update_file(new_name, content)
            return {"success": True, "old_name": old_name, "new_name": new_name}

        elif tool_name == "search_web":
            query = kwargs.get("query", "")
            if not query:
                return {"error": "缺少 query 参数"}
            # Web 搜索需要外部实现，这里返回占位
            return {
                "warning": "Web 搜索功能需要外部集成",
                "query": query,
                "results": [],
            }

        elif tool_name == "call_llm":
            prompt = kwargs.get("prompt", "")
            if not prompt:
                return {"error": "缺少 prompt 参数"}
            # LLM 调用需要外部 client，这里返回占位
            return {
                "warning": "LLM 调用需要外部 client 注入",
                "prompt": prompt,
            }

        elif tool_name == "list_directory":
            files = self.project.list_files()
            return {"files": files}

        elif tool_name == "list_workspace":
            ws = _WORKSPACE_DIR
            result = []
            for root, dirs, files in os.walk(ws):
                for f in files:
                    if f.startswith("."):
                        continue
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, ws)
                    sz = os.path.getsize(fp)
                    result.append({"name": rel, "size": sz})
            return {"files": result, "path": ws}

        elif tool_name == "get_cwd":
            return {"cwd": os.path.dirname(os.path.abspath(_WORKSPACE_DIR)), "workspace": _WORKSPACE_DIR}

        elif tool_name == "read_file_lines":
            filename = kwargs.get("filename", "")
            start = int(kwargs.get("start", 1))
            end = int(kwargs.get("end", 0))
            content = ""
            try:
                _ws_dir = _WORKSPACE_DIR
                fpath = os.path.join(_ws_dir, filename)
                if os.path.isfile(fpath):
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    self.project.update_file(filename, content)
            except Exception:
                pass
            if not content:
                content = self.project.get_file(filename)
            if not content:
                return {"error": f"文件 '{filename}' 不存在"}
            lines = content.split("\n")
            if end <= 0: end = len(lines)
            selected = lines[start-1:end]
            return {"content": "\n".join(selected), "total_lines": len(lines), "showing": f"{start}-{end}"}

        elif tool_name == "patch_file":
            filename = kwargs.get("filename", "")
            find_text = kwargs.get("find", "")
            replace_text = kwargs.get("replace", "")
            if not filename or not find_text:
                return {"error": "缺少 filename 或 find 参数"}
            content = ""
            try:
                _ws_dir = _WORKSPACE_DIR
                fpath = os.path.join(_ws_dir, filename)
                if os.path.isfile(fpath):
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
            except Exception:
                pass
            if not content:
                content = self.project.get_file(filename)
            if not content:
                return {"error": f"文件 '{filename}' 不存在"}
            if find_text not in content:
                return {"error": "未找到要替换的文本", "find": find_text[:100]}
            new_content = content.replace(find_text, replace_text, 1)
            self.project.update_file(filename, new_content)
            try:
                _ws_dir = _WORKSPACE_DIR
                fpath = os.path.join(_ws_dir, filename)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception:
                pass
            return {"success": True, "filename": filename, "changes": len(content) - len(new_content)}

        elif tool_name == "append_file":
            filename = kwargs.get("filename", "")
            content = kwargs.get("content", "")
            existing = self.project.get_file(filename) or ""
            self.project.update_file(filename, existing + content)
            # Also append to disk
            try:
                fpath = os.path.join(_WORKSPACE_DIR, filename)
                with open(fpath, "a", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            return {"success": True, "filename": filename, "appended": len(content)}

        elif tool_name == "delete_file":
            filename = kwargs.get("filename", "")
            if filename in self.project.files:
                del self.project.files[filename]
            try:
                fpath = os.path.join(_WORKSPACE_DIR, filename)
                if os.path.exists(fpath): os.remove(fpath)
            except Exception:
                pass
            return {"success": True, "filename": filename}

        elif tool_name == "run_code":
            code = kwargs.get("code", "")
            if not code:
                return {"error": "缺少 code 参数"}
            if not self.sandbox:
                return {"error": "沙箱未初始化，无法运行代码"}
            try:
                from sandbox import SandboxStatus
                if self.sandbox.status != SandboxStatus.READY:
                    await self.sandbox.create()
                # 自动安装代码中的依赖
                await self.sandbox.install_from_code(code)
                # 在沙箱中运行
                result = await self.sandbox.run_code(code, timeout=15)
                return {
                    "stdout": result.stdout[:3000] if result.stdout else "",
                    "stderr": result.stderr[:1000] if result.stderr else "",
                    "returncode": result.exit_code,
                    "success": result.success,
                    "duration": round(result.duration, 2),
                }
            except Exception as e:
                return {"error": str(e), "success": False}
            except Exception as e:
                return {"error": str(e), "success": False}

        elif tool_name == "git_init":
            try:
                import subprocess
                ws = _WORKSPACE_DIR
                subprocess.run(["git", "init"], cwd=ws, capture_output=True, timeout=5)
                subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True, timeout=5)
                subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True, timeout=5)
                return {"success": True, "message": "Git 仓库已初始化"}
            except Exception as e:
                return {"error": str(e)}

        elif tool_name == "git_commit":
            msg = kwargs.get("message", "update")
            try:
                import subprocess
                ws = _WORKSPACE_DIR
                subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True, timeout=5)
                r = subprocess.run(["git", "commit", "-m", msg], cwd=ws, capture_output=True, text=True, timeout=5)
                return {"success": True, "message": r.stdout.strip() or r.stderr.strip()[:200]}
            except Exception as e:
                return {"error": str(e)}

        elif tool_name == "git_log":
            try:
                import subprocess
                ws = _WORKSPACE_DIR
                r = subprocess.run(["git", "log", "--oneline", "-10"], cwd=ws, capture_output=True, text=True, timeout=5)
                return {"commits": r.stdout.strip().split("\n") if r.stdout.strip() else []}
            except Exception as e:
                return {"error": str(e)}

        elif tool_name == "git_rollback":
            commit = kwargs.get("commit", "HEAD~1")
            try:
                import subprocess
                ws = _WORKSPACE_DIR
                r = subprocess.run(["git", "checkout", commit, "--", "."], cwd=ws, capture_output=True, text=True, timeout=5)
                # 重新读取文件到项目
                for f in os.listdir(ws):
                    fp = os.path.join(ws, f)
                    if os.path.isfile(fp) and not f.startswith("."):
                        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                            self.project.update_file(f, fh.read())
                return {"success": True, "message": f"已回滚到 {commit}", "output": r.stdout.strip()[:200]}
            except Exception as e:
                return {"error": str(e)}

        elif tool_name == "done":
            return {"summary": kwargs.get("summary", ""), "status": "completed"}

        elif tool_name == "search_skill":
            import re as _re
            query = kwargs.get("query", "")
            if not query:
                return {"error": "缺少 query 参数"}
            skills_dir = os.path.normpath(os.path.join(os.path.dirname(_WORKSPACE_DIR), "workspace", "skills"))
            if not os.path.exists(skills_dir):
                return {"results": [], "message": "skills 文件夹不存在"}
            results = []
            query_lower = query.lower()
            query_words = set(_re.findall(r"[\w\u4e00-\u9fff]+", query_lower))
            for fname in os.listdir(skills_dir):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(skills_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except Exception:
                    continue
                # Parse YAML front matter for keywords
                keywords = []
                title = fname[:-3]
                if text.startswith("---"):
                    end = text.find("---", 3)
                    if end > 0:
                        fm = text[3:end].strip()
                        body = text[end+3:].strip()
                        for line in fm.split("\n"):
                            if line.startswith("keywords:"):
                                keywords = [k.strip().lower() for k in line.split(":", 1)[1].split(",")]
                            elif line.startswith("title:"):
                                title = line.split(":", 1)[1].strip()
                else:
                    body = text
                # Score: title match + keyword match + content match
                score = 0
                for w in query_words:
                    if w in title.lower(): score += 3
                    for kw in keywords:
                        if w in kw or kw in w: score += 2
                    if w in body.lower()[:2000]: score += 1
                if score > 0:
                    results.append({"file": fname, "title": title, "keywords": keywords, "score": score, "content": body})
            results.sort(key=lambda x: -x["score"])
            return {"results": results[:5], "query": query}

        elif tool_name == "write_skill":
            filename = kwargs.get("filename", "")
            title = kwargs.get("title", "")
            keywords = kwargs.get("keywords", "")
            content = kwargs.get("content", "")
            if not filename or not content:
                return {"error": "缺少 filename 或 content 参数"}
            if not filename.endswith(".md"):
                filename += ".md"
            skills_dir = os.path.normpath(os.path.join(os.path.dirname(_WORKSPACE_DIR), "workspace", "skills"))
            os.makedirs(skills_dir, exist_ok=True)
            fpath = os.path.join(skills_dir, filename)
            # Build YAML front matter
            fm = f"---\ntitle: {title}\nkeywords: {keywords}\n---\n\n"
            full_content = fm + content
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(full_content)
            except Exception as e:
                return {"error": f"写入失败: {e}"}
            return {"success": True, "filename": filename, "message": f"技能文件已写入 {filename}，需要人工预览通过后生效"}

        elif tool_name == "import_zip":
            url = kwargs.get("url", "")
            if not url:
                return {"error": "缺少 url 参数"}
            try:
                import subprocess, io, zipfile, urllib.request
                ws = _WORKSPACE_DIR
                os.makedirs(ws, exist_ok=True)
                # Download zip
                data = urllib.request.urlopen(url, timeout=30).read()
                zf = zipfile.ZipFile(io.BytesIO(data))
                # Extract, skip top-level dir if all files are in one
                names = zf.namelist()
                prefix = ""
                if names:
                    first_parts = names[0].split("/")
                    if len(first_parts) > 1 and all(n.startswith(first_parts[0]+"/") for n in names if n):
                        prefix = first_parts[0] + "/"
                count = 0
                for name in names:
                    if name.endswith("/") or not name.startswith(prefix):
                        continue
                    rel = name[len(prefix):]
                    if not rel or rel.startswith("."):
                        continue
                    target = os.path.join(ws, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "wb") as f:
                        f.write(zf.read(name))
                    count += 1
                    # Load into project
                    try:
                        with open(target, "r", encoding="utf-8", errors="ignore") as f:
                            self.project.update_file(rel, f.read())
                    except Exception:
                        pass
                return {"success": True, "imported": count, "url": url}
            except Exception as e:
                return {"error": str(e)}

        elif tool_name == "scan_design_specs":
            pattern = kwargs.get("pattern", "")
            max_files = int(kwargs.get("max_files", 5))
            ws = _WORKSPACE_DIR
            spec_extensions = {".md", ".txt", ".json", ".html", ".htm", ".css"}
            specs = []
            try:
                for root, dirs, files in os.walk(ws):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d != "skills" and d != ".git"]
                    for fname in files:
                        if len(specs) >= max_files:
                            break
                        ext = os.path.splitext(fname)[1].lower()
                        if ext not in spec_extensions:
                            continue
                        if fname.startswith("."):
                            continue
                        lower_name = fname.lower()
                        if pattern and pattern.lower() not in lower_name:
                            continue
                        fpath = os.path.join(root, fname)
                        rel = os.path.relpath(fpath, ws)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()
                        except Exception:
                            continue
                        keywords = []
                        title = fname
                        body = content
                        if content.startswith("---"):
                            end = content.find("---", 3)
                            if end > 0:
                                fm = content[3:end].strip()
                                body = content[end + 3:].strip()
                                for line in fm.split("\n"):
                                    if line.startswith("keywords:"):
                                        keywords = [k.strip().lower() for k in line.split(":", 1)[1].split(",")]
                                    elif line.startswith("title:"):
                                        title = line.split(":", 1)[1].strip()
                        words = set(re.findall(r"[\w\u4e00-\u9fff]+", body.lower()[:3000]))
                        word_count = len(body.split())
                        preview = body[:500]
                        specs.append({
                            "file": rel,
                            "title": title,
                            "keywords": keywords,
                            "word_count": word_count,
                            "unique_words": len(words),
                            "preview": preview,
                        })
                    if len(specs) >= max_files:
                        break
            except Exception as e:
                return {"error": str(e)}
            specs.sort(key=lambda x: -x["word_count"])
            return {"specs": specs, "count": len(specs), "scanned_dir": ws}

        elif tool_name == "generate_skill_from_spec":
            spec_file = kwargs.get("spec_file", "")
            skill_name = kwargs.get("skill_name", "")
            if not spec_file:
                return {"error": "缺少 spec_file 参数"}
            ws = _WORKSPACE_DIR
            spec_path = os.path.join(ws, spec_file)
            if not os.path.isfile(spec_path):
                return {"error": f"规范文件不存在: {spec_file}"}
            try:
                with open(spec_path, "r", encoding="utf-8", errors="ignore") as f:
                    spec_content = f.read()
            except Exception as e:
                return {"error": f"读取失败: {e}"}
            keywords = set()
            title_words = []
            for line in spec_content.split("\n")[:50]:
                line_stripped = line.strip()
                if line_stripped and not line_stripped.startswith("#") and not line_stripped.startswith("---"):
                    words = re.findall(r"[\w\u4e00-\u9fff]+", line_stripped.lower())
                    keywords.update(words[:5])
            if not skill_name:
                base = os.path.splitext(os.path.basename(spec_file))[0]
                safe = re.sub(r"[^\w]", "_", base)[:30].strip("_").lower()
                skill_name = f"spec_{safe}.md"
            if not skill_name.endswith(".md"):
                skill_name += ".md"
            pending_dir = os.path.normpath(os.path.join(os.path.dirname(_WORKSPACE_DIR), "workspace", "skills", "pending"))
            os.makedirs(pending_dir, exist_ok=True)
            skill_path = os.path.join(pending_dir, skill_name)
            fm_lines = [
                "---",
                f"title: {os.path.splitext(os.path.basename(spec_file))[0]} 规范",
                f"keywords: {', '.join(list(keywords)[:15])}",
                f"source: {spec_file}",
                f"generated_at: {int(time.time())}",
                f"status: pending_review",
                "---",
                "",
            ]
            full_content = "\n".join(fm_lines) + spec_content
            try:
                with open(skill_path, "w", encoding="utf-8") as f:
                    f.write(full_content)
            except Exception as e:
                return {"error": f"写入失败: {e}"}
            return {
                "success": True,
                "skill_name": skill_name,
                "pending_path": skill_path,
                "keywords": list(keywords)[:15],
                "message": f"技能文件已生成到 pending 目录: skills/pending/{skill_name}，等待人工审核后移入 skills/ 目录生效",
            }

    def _parse_tool_calls(self, text: str) -> list[dict]:
        """从 LLM 输出中解析 <tool_call> 标记。处理LLM将工具名、参数名连写的情况。"""
        import re
        calls = []
        _KNOWN_TOOLS = {'write_file','create_file','read_file','read_file_lines','delete_file',
                        'rename_file','patch_file','append_file','list_workspace','get_cwd',
                        'run_code','search_skill','write_skill','git_init','git_commit',
                        'git_log','git_rollback','import_zip','done'}
        _KNOWN_PARAMS = {
            'write_file': {'filename', 'content', 'encoding', 'append'},
            'create_file': {'filename', 'content', 'encoding'},
            'patch_file': {'filename', 'find', 'replace', 'patch'},
            'append_file': {'filename', 'content'},
            'read_file': {'filename'},
            'read_file_lines': {'filename', 'start', 'end'},
            'delete_file': {'filename'},
            'rename_file': {'old_name', 'new_name'},
            'run_code': {'code', 'language'},
            'search_skill': {'query'},
            'write_skill': {'filename', 'title', 'keywords', 'content'},
            'git_commit': {'message'},
            'git_rollback': {'commit'},
            'import_zip': {'url'},
            'done': {'summary'},
        }
        pattern = r"<tool_call>\s*(\w+)\s*\n?(.*?)</tool_call>"
        for m in re.finditer(pattern, text, re.DOTALL):
            tool_raw = m.group(1)
            raw_args = m.group(2).strip()
            if not raw_args:
                calls.append({"name": tool_raw, "args": {}})
                continue
            # Fix LLM writing tool+first_param without newline: "write_filefilename=x"
            tool_name = tool_raw
            if tool_raw not in _KNOWN_TOOLS:
                for known in _KNOWN_TOOLS:
                    if tool_raw.startswith(known) and len(tool_raw) > len(known):
                        rest = tool_raw[len(known):]
                        tool_name = known
                        if raw_args.startswith('=') and not rest.endswith('='):
                            raw_args = rest + raw_args
                        elif not raw_args.startswith('='):
                            raw_args = rest + "\n" + raw_args
                        else:
                            raw_args = rest + raw_args
                        break
            # Split all-in-one-line args: "filename=test.pycontent=import math" 
            # Insert \n before known param names found inside values
            known = _KNOWN_PARAMS.get(tool_name, set())
            if known:
                split_re = re.compile(r'(?=(' + '|'.join(re.escape(p) for p in known) + r')=)')
                raw_args = split_re.sub('\n', raw_args)
                # Remove leading \n if split inserted one at start
                raw_args = raw_args.lstrip('\n')
            args = {}
            lines = raw_args.split("\n")
            current_key = None
            current_val_parts = []
            for line in lines:
                km = re.match(r'^(\w+)=(.*)', line)
                if km:
                    k = km.group(1)
                    if k in known:
                        if current_key:
                            val = "\n".join(current_val_parts).strip()
                            val = val.replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')
                            args[current_key] = val
                        current_key = k
                        current_val_parts = [km.group(2).strip()]
                    elif current_key:
                        current_val_parts.append(line)
                    else:
                        current_key = None
                        current_val_parts = []
                elif current_key:
                    current_val_parts.append(line)
                else:
                    current_key = None
                    current_val_parts = []
            if current_key:
                val = "\n".join(current_val_parts).strip()
                val = val.replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')
                args[current_key] = val
            calls.append({"name": tool_name, "args": args})
        return calls


    def _compress_history(self, messages: list[dict], max_chars: int = 8000) -> list[dict]:
        """上下文压缩：当对话历史过长时，保留最近几轮 + 压缩早期内容为摘要。"""
        # Filter out invalid roles (tool_call, tool_result are internal only)
        valid_roles = {"system", "user", "assistant"}
        messages = [m for m in messages if m.get("role") in valid_roles]
        total = sum(len(m.get("content", "")) for m in messages)
        if total <= max_chars:
            return messages
        # 保留最近4轮(8条消息)，其余压缩为摘要
        keep = min(8, len(messages))
        recent = messages[-keep:]
        old = messages[:-keep]
        if not old:
            return recent
        summary_parts = []
        for m in old:
            role = "用户" if m.get("role") == "user" else "AI"
            content = m.get("content", "")[:150]
            summary_parts.append(f"[{role}] {content}...")
        summary = "【早期对话摘要】\n" + "\n".join(summary_parts)
        return [{"role": "user", "content": summary}] + recent


# ═══════════════════════════════════════════════════════════════════════
# Prompt 注入系统 — 节点级全量上下文
# ═══════════════════════════════════════════════════════════════════════

def build_node_prompt(
    project: PMProject,
    node_name: str,
    node_type: str,
    connections: list[dict],
    extra_context: str = "",
) -> str:
    """
    构建节点级的全量 Prompt — 为工作流中的编码 AI 提供完整上下文。

    这是一个综合性的 omni-prompt，让 AI 完全了解自己在工作流中的位置：
    - 所属项目信息
    - 当前节点身份（名称、类型）
    - 上游输入来源（哪些节点、哪些端口连接过来）
    - 下游输出目标（哪些节点会接收本节点的输出）
    - 任务描述和项目上下文

    Parameters
    ----------
    project : PMProject
        所属项目实例
    node_name : str
        节点名称
    node_type : str
        节点类型（如 'llm', 'code', 'prompt' 等）
    connections : list[dict]
        连接列表，每个连接包含:
        - source_node: str  — 源节点名称
        - source_port: str — 源端口名称
        - target_node: str  — 目标节点名称
        - target_port: str — 目标端口名称
    extra_context : str
        额外上下文（如上游节点输出数据、运行时变量等）

    Returns
    -------
    str
        完整的节点级 Prompt

    Example
    -------
    >>> project = PMProject(name="我的项目", requirements="创建一个 web 应用")
    >>> connections = [
    ...     {"source_node": "需求分析", "source_port": "text", "target_node": "编码节点", "target_port": "input"},
    ... ]
    >>> prompt = build_node_prompt(project, "编码节点", "code", connections)
    >>> print(prompt)  # 完整的节点指令 Prompt
    """

    # ── 解析连接关系 ──
    incoming: list[dict] = []   # 输入连接（其他节点 → 本节点）
    outgoing: list[dict] = []   # 输出连接（本节点 → 其他节点）

    for conn in connections:
        if not isinstance(conn, dict):
            continue
        target = conn.get("target_node", "")
        source = conn.get("source_node", "")
        if target == node_name:
            incoming.append({
                "source_node": source,
                "source_port": conn.get("source_port", "output"),
                "target_port": conn.get("target_port", "input"),
                "connection_type": conn.get("connection_type", "any"),
            })
        if source == node_name:
            outgoing.append({
                "target_node": target,
                "target_port": conn.get("target_port", "input"),
                "source_port": conn.get("source_port", "output"),
                "connection_type": conn.get("connection_type", "any"),
            })

    # ── 获取项目上下文 ──
    project_context = project.get_context(max_chars=5000)

    # ── 构建 Prompt ──
    prompt_parts: list[str] = []

    # 标题和角色定义
    prompt_parts.append(
        f"# 🤖 节点工作指令\n\n"
        f"你是节点 **「{node_name}」**，类型为 **`{node_type}`**，"
        f"属于项目 **「{project.name or '未命名'}」**（ID: `{project.id}`）。\n"
    )

    # 项目概览
    prompt_parts.append(
        f"## 📊 项目概览\n\n"
        f"- **项目名称**: {project.name or '未命名'}\n"
        f"- **项目状态**: {project.status.value}\n"
        f"- **项目描述**: {project.description or '无'}\n"
    )

    # 需求文档（如果有）
    if project.requirements:
        req_preview = _truncate(project.requirements, 2000)
        prompt_parts.append(f"## 📋 项目需求\n\n{req_preview}\n")

    # 项目结构（如果有）
    if project.structure:
        struct_text = _safe_json_dumps(project.structure)
        prompt_parts.append(
            f"## 📐 项目结构\n\n```json\n{_truncate(struct_text, 1000)}\n```\n"
        )

    # ── 节点身份和连接关系 ──
    prompt_parts.append("## 🔗 节点连接关系\n")

    # 输入连接
    if incoming:
        prompt_parts.append("### 输入来源（你的数据从哪里来）\n")
        for i, inp in enumerate(incoming, 1):
            prompt_parts.append(
                f"  {i}. 来自节点 **「{inp['source_node']}」** 的端口 "
                f"**`{inp['source_port']}`** → 你的端口 **`{inp['target_port']}`** "
                f"(类型: `{inp['connection_type']}`)"
            )
        prompt_parts.append(
            "\n你需要处理这些输入数据，并根据输入内容生成对应的输出。\n"
        )
    else:
        prompt_parts.append(
            "### 输入来源\n"
            "  你没有上游输入连接，你是工作流的起始节点。"
            "请根据项目需求自行生成初始内容。\n"
        )

    # 输出连接
    if outgoing:
        prompt_parts.append("### 输出目标（你的结果会送到哪里）\n")
        for i, out in enumerate(outgoing, 1):
            prompt_parts.append(
                f"  {i}. 你的端口 **`{out['source_port']}`** → "
                f"节点 **「{out['target_node']}」** 的端口 **`{out['target_port']}`** "
                f"(类型: `{out['connection_type']}`)"
            )
        prompt_parts.append(
            "\n请确保你的输出格式和内容与下游节点的期望一致。\n"
        )
    else:
        prompt_parts.append(
            "### 输出目标\n"
            "  你没有下游输出连接，你是工作流的终端节点。"
            "请输出最终的完整结果。\n"
        )

    # ── 任务描述 ──
    task_description = _get_task_description(node_name, node_type, project)
    prompt_parts.append(
        f"## 📝 你的任务\n\n{task_description}\n"
    )

    # ── 已有代码/文件 ──
    if project.code:
        prompt_parts.append("## 🖥️ 已有代码产出\n")
        for ctype, ccontent in project.code.items():
            if ccontent:
                preview = _truncate(ccontent, 600)
                prompt_parts.append(
                    f"### {ctype}\n```{ctype if ctype in ('python', 'html', 'css', 'javascript') else ''}\n{preview}\n```\n"
                )

    if project.files:
        prompt_parts.append("## 📁 项目文件\n")
        for fname in list(project.files.keys())[:15]:
            prompt_parts.append(f"  - `{fname}`")

    # ── 额外上下文（运行时注入） ──
    if extra_context:
        prompt_parts.append(
            f"## 📥 当前输入数据\n\n{extra_context}\n"
        )

    # ── 项目完整上下文 ──
    if project_context:
        prompt_parts.append(
            f"## 📚 完整项目上下文\n\n{project_context}\n"
        )

    # ── 通用工作指引 ──
    prompt_parts.append(
        "## ⚙️ 工作指引\n\n"
        "### 通用原则\n"
        "1. **你是 `{node_name}` 节点** — 专注于你的任务，不要越权操作其他节点的工作\n"
        "2. **理解上下文** — 仔细阅读项目需求、结构、已有代码，确保你的产出与项目一致\n"
        "3. **输入驱动** — 如果有上游输入，基于输入数据生成输出；如果没有，基于需求生成\n"
        "4. **输出完整** — 你的输出应该直接可用，下游节点不需要额外处理\n"
        "5. **保持一致性** — 代码风格、命名规范、文件格式要与项目其他部分保持一致\n"
        "\n### 代码输出规范\n"
        "- 直接输出代码，不要用 markdown 代码块包装（除非有明确要求）\n"
        "- 包含所有必要的 import 语句\n"
        "- 添加关键注释（中文）\n"
        "- 错误处理要完善\n"
        "\n### 文本输出规范\n"
        "- 如果是分析/规划任务，使用 Markdown 格式\n"
        "- 结构清晰，层次分明\n"
        "- 关键结论用粗体标注\n"
    )

    # ── 尾部身份确认 ──
    prompt_parts.append(
        f"\n---\n\n"
        f"**身份确认**: 你是节点 `{node_name}`（类型: `{node_type}`），"
        f"项目 `{project.name or '未命名'}`。"
        f"你的输入来自: {_format_input_sources(incoming)}。"
        f"你的输出送往: {_format_output_targets(outgoing)}。"
        f"请开始你的工作。\n"
    )

    return "\n".join(prompt_parts)


def _get_task_description(node_name: str, node_type: str, project: PMProject) -> str:
    """
    根据节点类型和项目上下文生成任务描述。

    Parameters
    ----------
    node_name : str
        节点名称
    node_type : str
        节点类型
    project : PMProject
        所属项目

    Returns
    -------
    str
        任务描述文本
    """
    # 从项目结构中查找节点配置
    node_config: dict = {}
    if project.structure:
        nodes = project.structure.get("nodes", {})
        if node_name in nodes:
            node_config = nodes[node_name]
        elif isinstance(nodes, list):
            for n in nodes:
                if isinstance(n, dict) and n.get("name") == node_name:
                    node_config = n
                    break

    # 如果节点配置中有 task 字段，直接使用
    if node_config.get("task"):
        return node_config["task"]

    if node_config.get("description"):
        return node_config["description"]

    # 根据节点类型生成默认任务描述
    type_tasks: dict[str, str] = {
        "llm": (
            f"作为 LLM 节点「{node_name}」，你需要根据输入的文本提示，"
            f"使用大语言模型生成相应的回复或分析结果。"
        ),
        "code": (
            f"作为编码节点「{node_name}」，你需要根据项目需求和输入数据，"
            f"编写完整、可运行的代码。代码应该包含所有必要的依赖、错误处理和注释。"
        ),
        "prompt": (
            f"作为提示节点「{node_name}」，你需要构建或处理文本提示，"
            f"为下游节点提供结构化的输入。"
        ),
        "file_input": (
            f"作为文件输入节点「{node_name}」，你需要读取和处理文件内容，"
            f"将其转换为下游节点可以使用的格式。"
        ),
        "text_transform": (
            f"作为文本处理节点「{node_name}」，你需要对输入文本进行转换、"
            f"格式化、拆分或合并等操作。"
        ),
        "condition": (
            f"作为条件分支节点「{node_name}」，你需要根据输入数据评估条件，"
            f"决定后续的工作流走向。"
        ),
        "merge": (
            f"作为合并节点「{node_name}」，你需要将多个输入合并为统一的输出格式。"
        ),
        "output": (
            f"作为输出节点「{node_name}」，你需要收集所有输入，"
            f"格式化为最终的交付结果。"
        ),
        "http": (
            f"作为 HTTP 请求节点「{node_name}」，你需要发起网络请求并处理响应。"
        ),
        "image_input": (
            f"作为图片输入节点「{node_name}」，你需要处理图片数据"
            f"（可能是 base64 编码或文件引用）。"
        ),
        "image_output": (
            f"作为图片输出节点「{node_name}」，你需要生成或处理图片输出。"
        ),
    }

    return type_tasks.get(
        node_type,
        f"作为节点「{node_name}」（类型: {node_type}），请根据输入数据和项目需求完成你的任务。",
    )


def _format_input_sources(incoming: list[dict]) -> str:
    """格式化输入来源列表"""
    if not incoming:
        return "无（起始节点）"
    return ", ".join(
        f"{inp['source_node']}.{inp['source_port']}"
        for inp in incoming
    )


def _format_output_targets(outgoing: list[dict]) -> str:
    """格式化输出目标列表"""
    if not outgoing:
        return "无（终端节点）"
    return ", ".join(
        f"{out['target_node']}.{out['target_port']}"
        for out in outgoing
    )


# ═══════════════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════════════

def create_pm_manager() -> PMAgentManager:
    """
    创建一个新的 PMAgentManager 实例。

    Returns
    -------
    PMAgentManager
        新的管理器实例
    """
    return PMAgentManager()


def create_agent_mode(
    project: PMProject,
    agent_name: str = "pm_agent",
    sandbox=None,
) -> AgentMode:
    """
    为指定项目创建 AgentMode 实例。

    Parameters
    ----------
    project : PMProject
        目标项目
    agent_name : str
        Agent 名称
    sandbox : CodeSandbox, optional
        沙箱实例，用于 run_code 工具

    Returns
    -------
    AgentMode
        Agent 模式实例
    """
    return AgentMode(project=project, agent_name=agent_name, sandbox=sandbox)


# ═══════════════════════════════════════════════════════════════════════
# 模块自检
# ═══════════════════════════════════════════════════════════════════════

def _self_test() -> bool:
    """模块自检：验证核心功能是否正常"""
    try:
        # 1. 创建项目管理器
        mgr = PMAgentManager()
        assert mgr.project_count() == 0

        # 2. 创建项目
        proj = mgr.create_project("测试项目", "这是一个测试")
        assert proj.id
        assert proj.name == "测试项目"
        assert mgr.project_count() == 1
        assert mgr.active_project_id == proj.id

        # 3. 对话管理
        proj.add_message("user", "你好")
        proj.add_message("assistant", "你好！有什么可以帮你的？")
        assert len(proj.conversation_history) == 2

        # 4. 文件管理
        proj.update_file("main.py", "print('hello')")
        assert proj.get_file("main.py") == "print('hello')"
        assert len(proj.list_files()) == 1

        # 5. 上下文构建
        context = proj.get_context(max_chars=1000)
        assert "测试项目" in context

        # 6. 节点 Prompt 注入
        connections = [
            {
                "source_node": "需求分析",
                "source_port": "text",
                "target_node": "编码节点",
                "target_port": "input",
            },
        ]
        node_prompt = build_node_prompt(proj, "编码节点", "code", connections)
        assert "编码节点" in node_prompt
        assert "需求分析" in node_prompt

        # 7. AgentMode
        agent = AgentMode(proj, "test_agent")
        sys_prompt = agent.build_agent_system_prompt()
        assert "test_agent" in sys_prompt

        # 8. 序列化/反序列化
        data = proj.to_dict()
        restored = PMProject.from_dict(data)
        assert restored.id == proj.id
        assert restored.name == proj.name

        # 9. 列出/删除项目
        projects = mgr.list_projects()
        assert len(projects) == 1
        mgr.delete_project(proj.id)
        assert mgr.project_count() == 0
        assert mgr.active_project_id is None

        logger.info("✅ 模块自检通过")
        return True

    except Exception as exc:
        logger.error("❌ 模块自检失败: %s", exc, exc_info=True)
        return False


# 模块加载时执行自检
if __name__ == "__main__":
    _self_test()
