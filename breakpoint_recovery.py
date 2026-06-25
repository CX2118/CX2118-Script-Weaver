#!/usr/bin/env python3
"""
breakpoint_recovery.py — CX2118 Script Weaver 断点恢复
═══════════════════════════════════════════════════
功能:
  - 前端编码时卡死 → 自动保存状态
  - 断点持久化（保存到文件）
  - 从断点恢复执行
  - 心跳检测（检测前端是否存活）
  - 状态快照管理
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable


@dataclass
class StateSnapshot:
    """状态快照"""
    id: str
    timestamp: float
    phase: str
    conversation: list = field(default_factory=list)
    requirements: str = ""
    structure: dict = field(default_factory=dict)
    code: dict = field(default_factory=dict)
    review_findings: list = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    files_content: dict = field(default_factory=dict)  # 多文件内容
    task_list: list = field(default_factory=list)      # 调度器任务列表
    current_task: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class HeartbeatStatus:
    """心跳状态"""
    last_heartbeat: float = 0.0
    is_alive: bool = True
    missed_count: int = 0
    threshold: float = 30.0  # 30秒无心跳视为断开


class BreakpointRecovery:
    """
    断点恢复管理器

    核心功能:
    1. 定时保存状态快照
    2. 前端断开时自动保存断点
    3. 从断点恢复执行
    4. 心跳监测前端连接状态
    5. 持久化到磁盘
    """

    def __init__(self, save_dir: str = ".breakpoints"):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots: list[StateSnapshot] = []
        self._heartbeat = HeartbeatStatus()
        self._auto_save_interval = 15  # 每15秒自动保存
        self._max_snapshots = 20
        self._emit = lambda ev: None  # 回调
        self._auto_save_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ── 快照管理 ──

    def take_snapshot(
        self,
        phase: str,
        conversation: list = None,
        requirements: str = "",
        structure: dict = None,
        code: dict = None,
        review_findings: list = None,
        budget: dict = None,
        files_content: dict = None,
        task_list: list = None,
        current_task: int = 0,
        metadata: dict = None,
    ) -> StateSnapshot:
        """创建状态快照"""
        snapshot = StateSnapshot(
            id=f"bp_{int(time.time())}_{len(self.snapshots)}",
            timestamp=time.time(),
            phase=phase,
            conversation=list(conversation or []),
            requirements=requirements,
            structure=structure or {},
            code=code or {},
            review_findings=list(review_findings or []),
            budget=budget or {},
            files_content=files_content or {},
            task_list=task_list or [],
            current_task=current_task,
            metadata=metadata or {},
        )
        self.snapshots.append(snapshot)

        # 限制数量
        if len(self.snapshots) > self._max_snapshots:
            self.snapshots = self.snapshots[-self._max_snapshots:]

        # 持久化
        self._persist_snapshot(snapshot)
        return snapshot

    def get_latest_snapshot(self) -> Optional[StateSnapshot]:
        """获取最新快照"""
        return self.snapshots[-1] if self.snapshots else None

    def get_snapshot(self, snapshot_id: str) -> Optional[StateSnapshot]:
        """获取指定快照"""
        for s in self.snapshots:
            if s.id == snapshot_id:
                return s
        return None

    def list_snapshots(self) -> list[dict]:
        """列出所有快照"""
        return [
            {
                "id": s.id,
                "timestamp": s.timestamp,
                "phase": s.phase,
                "conversation_length": len(s.conversation),
                "files_count": len(s.files_content),
                "has_requirements": bool(s.requirements),
                "has_structure": bool(s.structure),
                "task_count": len(s.task_list),
                "current_task": s.current_task,
            }
            for s in reversed(self.snapshots)
        ]

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """删除快照"""
        for i, s in enumerate(self.snapshots):
            if s.id == snapshot_id:
                self.snapshots.pop(i)
                # 删除文件
                fpath = self.save_dir / f"{snapshot_id}.json"
                if fpath.exists():
                    fpath.unlink()
                return True
        return False

    def clear_snapshots(self):
        """清除所有快照"""
        self.snapshots.clear()
        for f in self.save_dir.glob("*.json"):
            f.unlink()

    # ── 持久化 ──

    def _persist_snapshot(self, snapshot: StateSnapshot):
        """持久化快照到磁盘"""
        fpath = self.save_dir / f"{snapshot.id}.json"
        data = {
            "id": snapshot.id,
            "timestamp": snapshot.timestamp,
            "phase": snapshot.phase,
            "conversation": snapshot.conversation,
            "requirements": snapshot.requirements,
            "structure": snapshot.structure,
            "code": snapshot.code,
            "review_findings": snapshot.review_findings,
            "budget": snapshot.budget,
            "files_content": snapshot.files_content,
            "task_list": snapshot.task_list,
            "current_task": snapshot.current_task,
            "metadata": snapshot.metadata,
        }
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_from_disk(self):
        """从磁盘加载所有快照"""
        self.snapshots.clear()
        for f in sorted(self.save_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = StateSnapshot(
                    id=data["id"],
                    timestamp=data["timestamp"],
                    phase=data.get("phase", ""),
                    conversation=data.get("conversation", []),
                    requirements=data.get("requirements", ""),
                    structure=data.get("structure", {}),
                    code=data.get("code", {}),
                    review_findings=data.get("review_findings", []),
                    budget=data.get("budget", {}),
                    files_content=data.get("files_content", {}),
                    task_list=data.get("task_list", []),
                    current_task=data.get("current_task", 0),
                    metadata=data.get("metadata", {}),
                )
                self.snapshots.append(s)
            except Exception:
                continue

    # ── 心跳监测 ──

    def heartbeat(self):
        """接收心跳"""
        self._heartbeat.last_heartbeat = time.time()
        self._heartbeat.is_alive = True
        self._heartbeat.missed_count = 0

    def check_alive(self) -> bool:
        """检查前端是否存活"""
        elapsed = time.time() - self._heartbeat.last_heartbeat
        if elapsed > self._heartbeat.threshold:
            self._heartbeat.is_alive = False
            self._heartbeat.missed_count += 1
            return False
        return True

    def get_heartbeat_status(self) -> dict:
        """获取心跳状态"""
        return {
            "is_alive": self._heartbeat.is_alive,
            "last_heartbeat": self._heartbeat.last_heartbeat,
            "elapsed": round(time.time() - self._heartbeat.last_heartbeat, 1),
            "missed_count": self._heartbeat.missed_count,
            "threshold": self._heartbeat.threshold,
        }

    # ── 自动保存 ──

    async def start_auto_save(
        self,
        get_state_fn: Callable,
        emit_fn: Callable = None,
    ):
        """
        启动自动保存和心跳检测

        Args:
            get_state_fn: 获取当前状态的回调函数
            emit_fn: 事件发送回调
        """
        if emit_fn:
            self._emit = emit_fn

        def _bp_emit(ev):
            """Safe emit wrapper: handles both sync and async emit callbacks."""
            if asyncio.iscoroutinefunction(self._emit):
                try:
                    asyncio.get_running_loop().create_task(self._emit(ev))
                except RuntimeError:
                    pass
            else:
                self._emit(ev)

        async def _auto_save_loop():
            while True:
                await asyncio.sleep(self._auto_save_interval)
                try:
                    # 检查心跳
                    alive = self.check_alive()
                    if not alive:
                        # 前端断开，保存断点
                        state_data = get_state_fn()
                        if state_data:
                            self.take_snapshot(**state_data)
                            _bp_emit({
                                "type": "breakpoint_auto_saved",
                                "reason": "frontend_disconnected",
                                "missed_count": self._heartbeat.missed_count,
                            })
                    else:
                        # 定时保存
                        state_data = get_state_fn()
                        if state_data:
                            self.take_snapshot(**state_data)
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        self._auto_save_task = asyncio.create_task(_auto_save_loop())

    def stop_auto_save(self):
        """停止自动保存"""
        if self._auto_save_task:
            self._auto_save_task.cancel()
            self._auto_save_task = None

    # ── 恢复 ──

    def get_recovery_info(self, snapshot_id: str = "") -> dict:
        """获取恢复信息"""
        snapshot = None
        if snapshot_id:
            snapshot = self.get_snapshot(snapshot_id)
        elif self.snapshots:
            snapshot = self.snapshots[-1]

        if not snapshot:
            return {"recoverable": False, "reason": "无可用断点"}

        return {
            "recoverable": True,
            "snapshot_id": snapshot.id,
            "phase": snapshot.phase,
            "conversation_length": len(snapshot.conversation),
            "requirements_preview": snapshot.requirements[:200],
            "files": list(snapshot.files_content.keys()),
            "task_list": snapshot.task_list,
            "current_task": snapshot.current_task,
            "timestamp": snapshot.timestamp,
            "metadata": snapshot.metadata,
        }
