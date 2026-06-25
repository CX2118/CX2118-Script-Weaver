"""
security_auditor_agent — 安全审计员 AI Agent 插件
═══════════════════════════════════════════════════
演示如何通过插件系统注入自定义 AI Agent 到 Pipeline 中。
此 Agent 使用 LLM 进行安全审计，检查 OWASP Top 10 等安全问题。
"""


def on_load(config=None, engine=None):
    if engine:
        engine.log("security_auditor_agent loaded")
    return config


def get_agent_info():
    """
    返回 Agent 配置信息，供 Plugin Engine 注册到全局 Agent 系统。

    返回格式兼容 config.json 的 agents 结构。
    """
    return {
        "name": "安全审计员",
        "enabled": True,
        "provider": "siliconflow",
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        "token_limit": 4096,
        "system_prompt": (
            "你是安全审计专家。审查代码中的安全风险：\n"
            "1. SQL 注入 / XSS / CSRF\n"
            "2. 硬编码密钥/密码\n"
            "3. 不安全的反序列化\n"
            "4. 权限校验缺失\n"
            "5. 敏感数据泄露\n\n"
            '输出 JSON: {"findings": [{"severity":"error|warning|info","title":"...","description":"...","line":N}]}'
        ),
    }


async def on_hook(context=None, config=None, hook_point="", **kwargs):
    """
    Hook 处理 — 在关键阶段触发安全审计。

    注意: 实际 LLM 调用需要通过 engine 访问 Config.get_client_for()。
    此处演示静态规则审计作为降级方案。
    """
    if not context or not context.data:
        return None

    if config is None:
        config = {}

    code = context.data.get("code", "")
    if not code:
        return None

    # 静态安全规则（快速检查）
    findings = _static_security_audit(code, config)

    if findings:
        existing = context.get_data("security_findings", [])
        existing.extend(findings)
        context.set_data("security_findings", existing)

        # 如果配置了严重问题阻断
        critical = [f for f in findings if f["severity"] == "error"]
        if critical and config.get("block_on_critical", False):
            context.cancel()
            context.set_data("block_reason", f"安全审计发现 {len(critical)} 个严重问题")

    return {"data": {"security_findings": findings}}


def _static_security_audit(code: str, config: dict) -> list[dict]:
    """静态安全审计规则"""
    import re
    findings = []

    # OWASP A01: 权限控制失败
    auth_patterns = [
        (r'@(app|router)\.(get|post|put|delete)\s*\([^)]*(?<!auth)(?<!login)(?<!public)',
         "warning", "A01", "可能缺少权限控制"),
    ]

    # OWASP A03: 注入
    if config.get("check_injection", True):
        injection_patterns = [
            (rf'(?:execute|cursor\.execute)\s*\(\s*f["\']', "error", "A03", "SQL 注入: f-string 在 SQL 中使用"),
            (rf'(?:execute|cursor\.execute)\s*\(\s*[^)]*\+\s*(?:request|input|params)', "error", "A03", "SQL 注入: 字符串拼接"),
            (r'eval\s*\(\s*(?:request|input|params)', "error", "A03", "代码注入: eval 使用用户输入"),
            (r'innerHTML\s*=', "warning", "A03", "XSS: 直接设置 innerHTML"),
        ]
        auth_patterns.extend(injection_patterns)

    # OWASP A02: 加密失败
    crypto_patterns = [
        (r'(?:password|secret|api_key|private_key)\s*=\s*["\'][^"\']{8,}["\']',
         "error", "A02", "硬编码敏感信息"),
        (r'(?:md5|sha1)\s*\(', "warning", "A02", "弱哈希算法"),
    ]
    auth_patterns.extend(crypto_patterns)

    # OWASP A05: 安全配置错误
    config_patterns = [
        (r'DEBUG\s*=\s*True', "warning", "A05", "调试模式开启"),
        (r'ALLOWED_HOSTS\s*=\s*\["\*"\]', "warning", "A05", "ALLOWED_HOSTS 通配符"),
        (r'CORS.*allow_origins.*\*', "warning", "A05", "CORS 允许所有来源"),
    ]
    auth_patterns.extend(config_patterns)

    for pattern, severity, owasp_id, title in auth_patterns:
        for i, line in enumerate(code.splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "severity": severity,
                    "line": i,
                    "title": f"[{owasp_id}] {title}",
                    "description": f"OWASP {owasp_id}: 第 {i} 行存在安全风险",
                    "owasp_id": owasp_id,
                })

    return findings
