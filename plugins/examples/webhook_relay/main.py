"""
webhook_relay — Webhook 中继插件
═════════════════════════════════
在关键 pipeline 阶段发送 Webhook 通知。
"""

import json
import time


def on_load(config=None, engine=None):
    return config


async def on_hook(context=None, config=None, **kwargs):
    """Hook handler for webhook notifications."""
    if not context or not config:
        return None

    hook_point = context.hook_point if context else ""
    enabled_events = config.get("enabled_events", [])
    webhook_url = config.get("webhook_url", "")

    if not webhook_url or hook_point not in enabled_events:
        return None

    payload = _build_payload(hook_point, context.data, config)
    await _send_webhook(webhook_url, payload)

    return None


async def notify(event: str, message: str, data: dict = None, config=None, **kwargs):
    """Notifier entry point."""
    if config is None:
        config = {}
    webhook_url = config.get("webhook_url", "")
    if not webhook_url:
        return

    payload = {
        "event": event,
        "message": message,
        "data": data or {},
        "timestamp": time.time(),
    }
    await _send_webhook(webhook_url, payload)


def _build_payload(hook_point: str, data: dict, config: dict) -> dict:
    """Build webhook payload from hook context."""
    payload = {
        "text": f"[Script Weaver] {hook_point}",
        "hook_point": hook_point,
        "timestamp": time.time(),
    }

    if hook_point == "after_coding":
        payload["text"] = "[Script Weaver] 编码阶段完成"
        py_lines = data.get("python_code", "")
        html_lines = data.get("html_code", "")
        if py_lines:
            payload["python_lines"] = len(py_lines.splitlines())
        if html_lines:
            payload["html_lines"] = len(html_lines.splitlines())
        budget = data.get("budget", {})
        payload["budget_pct"] = budget.get("pct", 0)

    elif hook_point == "after_requirements_save":
        payload["text"] = "[Script Weaver] 需求文档已保存"
        req = data.get("requirements", "")
        if req:
            payload["requirements_preview"] = req[:200]

    elif hook_point == "after_director_plan":
        payload["text"] = "[Script Weaver] 项目规划完成"
        structure = data.get("structure", {})
        payload["needs_python"] = structure.get("needs_python", True)
        payload["needs_html"] = structure.get("needs_html", False)

    return payload


async def _send_webhook(url: str, payload: dict):
    """Send webhook payload to URL."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code < 300:
                print(f"  [webhook_relay] ✓ Sent to {url}")
            else:
                print(f"  [webhook_relay] ✗ HTTP {resp.status_code}")
    except Exception as e:
        print(f"  [webhook_relay] ✗ Failed: {e}")
