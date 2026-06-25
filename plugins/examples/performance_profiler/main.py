#!/usr/bin/env python3
"""Performance Profiler Plugin — 追踪各阶段执行时间和 Token 消耗"""
import time

_timings = {}


def info():
    return {
        "name": "performance_profiler",
        "version": "1.0.0",
        "description": "追踪各阶段执行时间和资源消耗",
        "type": "hook",
    }


def _elapsed(stage: str) -> float:
    key = f"_{stage}_start"
    if key in _timings:
        return round(time.time() - _timings.pop(key), 2)
    return 0.0


async def execute(hook_name, **kwargs):
    stage = hook_name.replace("before_", "").replace("after_", "")

    if hook_name.startswith("before_"):
        _timings[f"_{stage}_start"] = time.time()
        return {"cancelled": False, "modified": False, "data": kwargs}

    elapsed = _elapsed(stage)
    budget = kwargs.get("budget", {})
    profile_info = {
        "stage": stage,
        "elapsed_seconds": elapsed,
        "budget_total": budget.get("total", 0),
        "budget_pct": budget.get("pct", 0),
    }

    if elapsed > 0:
        print(f"  [Profiler] {stage}: {elapsed}s | budget: {budget.get('pct', 0)}%")

    return {
        "cancelled": False,
        "modified": False,
        "data": {**kwargs, "profile": profile_info},
    }
