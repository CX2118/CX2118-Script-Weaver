#!/usr/bin/env python3
"""Git Version Control Plugin — 自动将代码快照提交到本地 Git 仓库"""
import json
import subprocess
import os
import time
from pathlib import Path


def info():
    return {
        "name": "git_version_control",
        "version": "1.0.0",
        "description": "自动将代码快照提交到本地 Git 仓库",
        "type": "notifier",
    }


async def execute(hook_name, **kwargs):
    if hook_name not in ("after_coding", "after_requirements_save"):
        return {"cancelled": False, "modified": False, "data": kwargs}

    workspace = kwargs.get("workspace", ".")
    ws_path = Path(workspace)

    if not (ws_path / ".git").exists():
        try:
            subprocess.run(["git", "init"], cwd=str(ws_path), capture_output=True, timeout=5)
        except Exception:
            return {"cancelled": False, "modified": False, "data": kwargs}

    py_code = kwargs.get("python_code", "")
    html_code = kwargs.get("html_code", "")

    if hook_name == "after_coding" and not py_code and not html_code:
        return {"cancelled": False, "modified": False, "data": kwargs}

    try:
        subprocess.run(["git", "add", "-A"], cwd=str(ws_path), capture_output=True, timeout=10)
        ts = time.strftime("%Y%m%d_%H%M%S")
        msg = f"[ScriptWeaver] {hook_name} @ {ts}"
        result = subprocess.run(
            ["git", "commit", "-m", msg, "--allow-empty"],
            cwd=str(ws_path), capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return {"cancelled": False, "modified": False,
                    "data": {**kwargs, "git_committed": True, "git_message": msg}}
    except Exception:
        pass

    return {"cancelled": False, "modified": False, "data": kwargs}
