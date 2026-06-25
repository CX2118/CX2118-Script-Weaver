"""
notification_hub — 通知中心插件
═════════════════════════════════
在 Pipeline 关键阶段发送通知：控制台高亮、文件日志、Webhook 回调。
"""

import json
import time
from pathlib import Path


def on_load(config=None, engine=None):
    if engine:
        engine.log("notification_hub loaded")
    return config


async def notify(event: str, message: str, data: dict = None, config=None, **kwargs):
    if config is None:
        config = {}
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {"timestamp": timestamp, "event": event, "message": message, "data": data or {}}
    if config.get("console_notify", True):
        _console_notify(event, message)
    if config.get("log_to_file", False):
        _log_to_file(log_entry, config.get("log_file_path", "workspace/notifications.log"))
    webhook_url = config.get("webhook_url", "")
    if webhook_url:
        await _send_webhook(webhook_url, log_entry)


async def on_hook(context=None, config=None, hook_point="", **kwargs):
    if not context:
        return None
    if config is None:
        config = {}
    event = hook_point
    message = ""
    phase_names = {"idle": "空闲", "requirements": "需求分析", "planning": "项目规划", "coding": "编码阶段", "review": "审查阶段"}
    if hook_point == "on_phase_change":
        phase = context.data.get("phase", "unknown")
        message = f"Pipeline 阶段切换 → {phase_names.get(phase, phase)}"
    elif hook_point == "on_error":
        if not config.get("notify_on_error", True):
            return None
        message = f"⚠ 错误: {context.data.get('message', '未知错误')}"
    elif hook_point == "on_budget_warning":
        budget = context.data.get("budget", {})
        message = f"Token 预算警告: {budget.get('pct', 0)}% 已使用"
    elif hook_point == "after_coding" and config.get("notify_on_complete", True):
        message = "✅ 编码完成"
    elif hook_point == "after_ai_review":
        findings = context.data.get("findings", [])
        message = f"🔍 AI 审查完成: {len(findings)} 个问题"
    elif hook_point == "on_startup":
        message = "🚀 Plugin Engine 启动"
    elif hook_point == "on_shutdown":
        message = "👋 Plugin Engine 关闭"
    if message:
        await notify(event, message, context.data, config=config)
    return None


def _console_notify(event: str, message: str):
    colors = {
        "on_phase_change": "\033[36m", "on_error": "\033[31m", "on_budget_warning": "\033[33m",
        "after_coding": "\033[32m", "after_ai_review": "\033[34m", "on_startup": "\033[32m", "on_shutdown": "\033[33m",
    }
    color = colors.get(event, "\033[37m")
    reset = "\033[0m"
    ts = time.strftime("%H:%M:%S")
    print(f"  {color}[🔔 {ts}] {message}{reset}", flush=True)


def _log_to_file(entry: dict, file_path: str):
    try:
        log_path = Path(file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [notification_hub] Log error: {e}", flush=True)


async def _send_webhook(url: str, data: dict):
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=data)
            print(f"  [notification_hub] Webhook {resp.status_code}", flush=True)
    except Exception as e:
        print(f"  [notification_hub] Webhook error: {e}", flush=True)
