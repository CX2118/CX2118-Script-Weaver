#!/usr/bin/env python3
"""
project_dispatch.py — CX2118 Script Weaver 项目负责人调度器
═══════════════════════════════════════════════════════════════
功能:
  - 像AI助手一样统一调度所有模块
  - 接收用户需求 → 分析 → 分派任务
  - 管理多文件项目的编码流程
  - 协调沙箱执行和工具安装
  - 提供断点恢复能力
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Awaitable


class DispatchPhase(str, Enum):
    IDLE = "idle"
    ANALYZING = "analyzing"         # 分析需求
    PLANNING = "planning"           # 制定计划
    INSTALLING_TOOLS = "installing_tools"  # 安装工具
    CODING = "coding"               # 编码阶段
    REVIEWING = "reviewing"         # 审查阶段
    SANDBOX_TEST = "sandbox_test"   # 沙箱测试
    FIXING = "fixing"               # 修复阶段
    COMPLETE = "complete"           # 完成
    PAUSED = "paused"               # 暂停（断点）
    ERROR = "error"                 # 错误


@dataclass
class TaskItem:
    """调度任务项"""
    id: str
    type: str  # "code_file" / "install_tool" / "run_sandbox" / "review" / "fix"
    target: str  # 文件名 / 工具名 / 命令
    description: str = ""
    status: str = "pending"  # pending / running / done / error / skipped
    result: dict = field(default_factory=dict)
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    retries: int = 0
    max_retries: int = 3


@dataclass
class Breakpoint:
    """断点信息 — 用于恢复"""
    phase: DispatchPhase
    task_index: int
    snapshot: dict = field(default_factory=dict)
    timestamp: float = 0.0
    reason: str = ""
    recoverable: bool = True


class ProjectDispatcher:
    """
    项目负责人调度器

    设计理念（像 AI 助手一样工作）:
    1. 接收需求 → 分析需要做什么
    2. 制定执行计划（任务列表）
    3. 按顺序执行任务
    4. 每个任务有成功/失败/重试机制
    5. 支持暂停/恢复（断点恢复）
    6. 通过回调通知前端进度
    """

    def __init__(self, emit_callback: Optional[Callable] = None):
        self.phase = DispatchPhase.IDLE
        self.tasks: list[TaskItem] = []
        self.current_task_index: int = -1
        self.breakpoints: list[Breakpoint] = []
        self._emit = emit_callback or (lambda ev: None)
        self._stop = False
        self._history: list[dict] = []
        # 模块引用（由 main.py 注入）
        self.sandbox = None
        self.multi_file_manager = None
        self.tool_manager = None
        self.tool_installer = None
        self._task_counter = 0

    # ── 核心调度流程 ──

    async def dispatch(self, requirements: str, structure: dict = None):
        """
        主调度流程

        步骤:
        1. 分析需求
        2. 制定计划
        3. 安装工具（如需要）
        4. 编码（多文件）
        5. 沙箱测试
        6. 审查 & 修复
        """
        self._stop = False
        self.tasks = []

        # 1. 分析
        await self._set_phase(DispatchPhase.ANALYZING)
        plan = await self._analyze_requirements(requirements, structure)
        if self._stop:
            return await self._save_breakpoint("用户停止")

        # 2. 计划
        await self._set_phase(DispatchPhase.PLANNING)
        self._build_task_list(plan, structure)
        await self._emit({"type": "dispatch_plan", "tasks": self._task_summaries()})

        # 3-6. 执行任务
        for i, task in enumerate(self.tasks):
            if self._stop:
                return await self._save_breakpoint("用户停止")
            self.current_task_index = i
            await self._execute_task(task)
            # 指数退避重试
            while task.status == "error" and task.retries < task.max_retries:
                if self._stop:
                    break
                task.retries += 1
                backoff = 1.5 * task.retries
                await asyncio.sleep(backoff)
                await self._execute_task(task)

        # 完成
        self.current_task_index = len(self.tasks)
        success_count = sum(1 for t in self.tasks if t.status == "done")
        error_count = sum(1 for t in self.tasks if t.status == "error")
        await self._set_phase(DispatchPhase.COMPLETE)
        await self._emit({
            "type": "dispatch_complete",
            "success": error_count == 0,
            "total": len(self.tasks),
            "success_count": success_count,
            "error_count": error_count,
        })

    async def resume_from_breakpoint(self, bp_index: int = -1):
        """从断点恢复"""
        if not self.breakpoints:
            return
        bp = self.breakpoints[bp_index]
        if not bp.recoverable:
            await self._emit({"type": "error", "message": "断点不可恢复"})
            return

        # 恢复快照
        self.phase = bp.phase
        self.current_task_index = bp.task_index
        if bp.snapshot:
            self._restore_snapshot(bp.snapshot)

        await self._emit({
            "type": "breakpoint_resumed",
            "bp_index": bp_index,
            "phase": bp.phase.value,
            "task_index": bp.task_index,
        })

        # 从当前任务继续执行
        for i in range(self.current_task_index, len(self.tasks)):
            if self._stop:
                return await self._save_breakpoint("用户停止")
            self.current_task_index = i
            task = self.tasks[i]
            if task.status in ("done", "skipped"):
                continue
            await self._execute_task(task)

        await self._set_phase(DispatchPhase.COMPLETE)

    def stop(self):
        """停止调度"""
        self._stop = True

    # ── 需求分析 ──

    async def _analyze_requirements(self, requirements: str, structure: dict) -> dict:
        """分析需求，提取工具和文件需求"""
        plan = {
            "tools_needed": [],
            "files_needed": [],
            "has_sandbox_test": True,
            "has_review": True,
        }

        # 如果有结构信息
        if structure:
            files = structure.get("files", {})
            for fname in files:
                if isinstance(fname, str):
                    plan["files_needed"].append(fname)
            deps = structure.get("dependencies", [])
            plan["tools_needed"].extend(deps)

        # 从需求文本中提取关键词
        if requirements:
            tool_keywords = {
                "http": "requests", "web": "flask", "api": "fastapi",
                "database": "sqlalchemy", "redis": "redis",
                "data": "pandas", "plot": "matplotlib", "chart": "matplotlib",
                "ai": "openai", "scrap": "beautifulsoup4", "crawl": "scrapy",
                "test": "pytest", "cli": "typer", "log": "loguru",
            }
            for keyword, tool in tool_keywords.items():
                if keyword.lower() in requirements.lower() and tool not in plan["tools_needed"]:
                    plan["tools_needed"].append(tool)

        await self._emit({
            "type": "analysis_result",
            "tools_needed": plan["tools_needed"],
            "files_needed": plan["files_needed"],
        })
        return plan

    def _build_task_list(self, plan: dict, structure: dict):
        """根据计划构建任务列表"""
        self.tasks = []
        self._task_counter = 0

        # 安装工具任务
        for tool_name in plan["tools_needed"]:
            self.tasks.append(TaskItem(
                id=self._next_id(),
                type="install_tool",
                target=tool_name,
                description=f"安装工具: {tool_name}",
            ))

        # 多文件编码任务
        files = []
        if structure and structure.get("files"):
            for fname, finfo in structure.get("files", {}).items():
                if isinstance(fname, str):
                    files.append(fname)
        elif structure:
            files = list(structure.get("files", {}).keys())
        else:
            files = plan.get("files_needed", ["main.py"])

        for fname in files:
            self.tasks.append(TaskItem(
                id=self._next_id(),
                type="code_file",
                target=fname,
                description=f"编码文件: {fname}",
            ))

        # 沙箱测试
        if plan["has_sandbox_test"] and files:
            entry = structure.get("entry_point", "main.py") if structure else "main.py"
            self.tasks.append(TaskItem(
                id=self._next_id(),
                type="run_sandbox",
                target=entry,
                description=f"沙箱运行: {entry}",
            ))

        # 审查任务
        if plan["has_review"]:
            self.tasks.append(TaskItem(
                id=self._next_id(),
                type="review",
                target="all",
                description="代码审查",
            ))

    # ── 任务执行 ──

    async def _execute_task(self, task: TaskItem):
        """执行单个任务"""
        task.status = "running"
        task.start_time = time.time()
        await self._emit({
            "type": "task_start",
            "task_id": task.id,
            "task_type": task.type,
            "task_target": task.target,
            "index": self.current_task_index,
            "total": len(self.tasks),
        })

        try:
            if task.type == "install_tool":
                await self._exec_install_tool(task)
            elif task.type == "code_file":
                await self._exec_code_file(task)
            elif task.type == "run_sandbox":
                await self._exec_run_sandbox(task)
            elif task.type == "review":
                await self._exec_review(task)
            elif task.type == "fix":
                await self._exec_fix(task)

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            await self._emit({"type": "task_error", "task_id": task.id, "error": str(e)})

        task.end_time = time.time()
        duration = task.end_time - task.start_time
        await self._emit({
            "type": "task_done",
            "task_id": task.id,
            "status": task.status,
            "duration": round(duration, 2),
            "index": self.current_task_index,
        })
        self._record_task(task)

    async def _exec_install_tool(self, task: TaskItem):
        """执行工具安装任务"""
        if not self.tool_installer:
            task.status = "skipped"
            task.result = {"message": "工具安装器未配置"}
            return

        result = await self.tool_installer.install_tool(task.target)
        task.status = "done" if result["success"] else "error"
        task.result = result
        await self._emit({
            "type": "tool_installed",
            "tool": task.target,
            **result,
        })

    async def _exec_code_file(self, task: TaskItem):
        """执行文件编码任务（占位 — 由 main.py 的 AI coder 完成）"""
        task.status = "done"
        task.result = {"message": f"{task.target} 编码完成（由 AI Coder 处理）"}

    async def _exec_run_sandbox(self, task: TaskItem):
        """执行沙箱运行任务"""
        if not self.sandbox:
            task.status = "skipped"
            task.result = {"message": "沙箱未创建"}
            return

        if not self.multi_file_manager or not self.multi_file_manager.project:
            task.status = "skipped"
            task.result = {"message": "项目未创建"}
            return

        # 从多文件管理器获取所有代码
        files = self.multi_file_manager.project.get_all_code()
        if not files:
            task.status = "skipped"
            return

        await self._emit({"type": "sandbox_run_start", "file": task.target})
        result = await self.sandbox.run_multi_file(files, task.target)
        task.status = "done" if result.success else "error"
        task.result = {
            "success": result.success,
            "exit_code": result.exit_code,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
            "duration": round(result.duration, 2),
            "timeout": result.timeout,
        }

    async def _exec_review(self, task: TaskItem):
        """执行代码审查（占位 — 由 AI Reviewer 完成）"""
        task.status = "done"
        task.result = {"message": "审查完成"}

    async def _exec_fix(self, task: TaskItem):
        """执行修复任务"""
        task.status = "done"
        task.result = {"message": "修复完成"}

    # ── 断点管理 ──

    async def _save_breakpoint(self, reason: str):
        """保存断点"""
        bp = Breakpoint(
            phase=self.phase,
            task_index=self.current_task_index,
            snapshot=self._take_snapshot(),
            timestamp=time.time(),
            reason=reason,
        )
        self.breakpoints.append(bp)
        self.phase = DispatchPhase.PAUSED
        await self._emit({
            "type": "breakpoint_saved",
            "bp_index": len(self.breakpoints) - 1,
            "reason": reason,
            "phase": bp.phase.value,
            "task_index": bp.task_index,
        })

    def _take_snapshot(self) -> dict:
        """获取当前状态快照"""
        return {
            "phase": self.phase.value,
            "current_task_index": self.current_task_index,
            "tasks": [
                {
                    "id": t.id, "type": t.type, "target": t.target,
                    "status": t.status, "retries": t.retries,
                    "result": t.result, "error": t.error,
                }
                for t in self.tasks
            ],
        }

    def _restore_snapshot(self, snapshot: dict):
        """恢复快照"""
        tasks_data = snapshot.get("tasks", [])
        for td in tasks_data:
            for task in self.tasks:
                if task.id == td["id"]:
                    task.status = td["status"]
                    task.retries = td.get("retries", 0)
                    task.result = td.get("result", {})
                    task.error = td.get("error", "")

    def list_breakpoints(self) -> list[dict]:
        """列出所有断点"""
        return [
            {
                "index": i,
                "phase": bp.phase.value,
                "task_index": bp.task_index,
                "reason": bp.reason,
                "timestamp": bp.timestamp,
                "recoverable": bp.recoverable,
            }
            for i, bp in enumerate(self.breakpoints)
        ]

    # ── 辅助 ──

    async def _set_phase(self, phase: DispatchPhase):
        self.phase = phase
        await self._emit({"type": "dispatch_phase", "phase": phase.value})

    def _next_id(self) -> str:
        self._task_counter += 1
        return f"task_{self._task_counter:03d}"

    def _task_summaries(self) -> list[dict]:
        return [
            {
                "id": t.id,
                "type": t.type,
                "target": t.target,
                "description": t.description,
                "status": t.status,
            }
            for t in self.tasks
        ]

    def _record_task(self, task: TaskItem):
        self._history.append({
            "id": task.id,
            "type": task.type,
            "target": task.target,
            "status": task.status,
            "duration": task.end_time - task.start_time,
            "retries": task.retries,
        })
        if len(self._history) > 500:
            self._history = self._history[-200:]

    def get_status(self) -> dict:
        return {
            "phase": self.phase.value,
            "current_task_index": self.current_task_index,
            "total_tasks": len(self.tasks),
            "done_tasks": sum(1 for t in self.tasks if t.status == "done"),
            "error_tasks": sum(1 for t in self.tasks if t.status == "error"),
            "pending_tasks": sum(1 for t in self.tasks if t.status == "pending"),
            "breakpoints": len(self.breakpoints),
            "progress": round(
                (self.current_task_index / len(self.tasks) * 100) if self.tasks else 0, 1
            ),
        }
