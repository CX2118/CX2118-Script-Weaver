#!/usr/bin/env python3
"""Env Manager Plugin — 管理多环境配置"""
import json
from pathlib import Path


def info():
    return {
        "name": "env_manager",
        "version": "1.0.0",
        "description": "管理多个运行环境配置和切换",
        "type": "hook",
    }


def _load_env_config() -> dict:
    config_path = Path("env_config.json")
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"current_env": "development", "environments": {
        "development": {"description": "Development environment", "debug": True},
        "production": {"description": "Production environment", "debug": False},
    }}


def _save_env_config(config: dict):
    Path("env_config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


async def execute(hook_name, **kwargs):
    if hook_name != "before_coding":
        return {"cancelled": False, "modified": False, "data": kwargs}

    config = _load_env_config()
    current = config.get("current_env", "development")
    env_info = config.get("environments", {}).get(current, {})
    kwargs["env_name"] = current
    kwargs["env_config"] = env_info

    return {"cancelled": False, "modified": False, "data": kwargs}
