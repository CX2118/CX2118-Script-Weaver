#!/usr/bin/env python3
"""Log Aggregator Plugin — 收集日志并持久化到文件"""
import json
import time
from pathlib import Path


def info():
    return {
        "name": "log_aggregator",
        "version": "1.0.0",
        "description": "收集并持久化所有阶段日志到文件",
        "type": "notifier",
    }


def _append_log(entry: dict, log_dir: str = "logs"):
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    date_str = time.strftime("%Y%m%d")
    file_path = log_path / f"pipeline_{date_str}.jsonl"
    entry["_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def execute(hook_name, **kwargs):
    if hook_name not in ("after_coding", "after_pm_chat", "after_director_plan"):
        return {"cancelled": False, "modified": False, "data": kwargs}

    budget = kwargs.get("budget", {})
    log_entry = {
        "hook": hook_name,
        "elapsed": kwargs.get("profile", {}).get("elapsed_seconds", 0),
        "budget_total": budget.get("total", 0),
        "budget_pct": budget.get("pct", 0),
    }

    if hook_name == "after_coding":
        log_entry["python_lines"] = len(kwargs.get("python_code", "").splitlines()) if kwargs.get("python_code") else 0
        log_entry["html_lines"] = len(kwargs.get("html_code", "").splitlines()) if kwargs.get("html_code") else 0
        log_entry["findings_count"] = len(kwargs.get("findings", []))
    elif hook_name == "after_pm_chat":
        log_entry["response_preview"] = (kwargs.get("message", "") or "")[:200]
    elif hook_name == "after_director_plan":
        structure = kwargs.get("structure", {})
        log_entry["has_structure"] = bool(structure)

    try:
        _append_log(log_entry)
    except Exception:
        pass

    return {"cancelled": False, "modified": False, "data": kwargs}
