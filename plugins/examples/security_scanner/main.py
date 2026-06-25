"""
security_scanner — 安全代码扫描插件
═══════════════════════════════════
扫描代码中的安全隐患：硬编码密钥、SQL注入、XSS等。
"""

import re


PATTERNS_SECRET = [
    (r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']+["\']', "hardcoded_password", "critical"),
    (r'(?:api_key|apikey|secret_key|token)\s*=\s*["\'][^"\']+["\']', "hardcoded_secret", "critical"),
    (r'(?:private_key|ssh_key)\s*=\s*["\'][A-Za-z0-9+/=]{20,}["\']', "hardcoded_private_key", "critical"),
]

PATTERNS_SQL = [
    (r'(?:execute|exec)\s*\(\s*f["\'].*\{.*\}.*["\']', "f_string_sql", "error"),
    (r'(?:cursor\.execute|db\.execute)\s*\(\s*["\'].*%(?:s|d).*["\']', "string_format_sql", "error"),
]

PATTERNS_XSS = [
    (r'(?:innerHTML|outerHTML)\s*=\s*(?:req|request|params|query)', "direct_xss", "error"),
    (r'render_template_string.*\binclude\b', "template_injection", "warning"),
]


def on_load(config=None, engine=None):
    return config


async def on_hook(context=None, config=None, **kwargs):
    """Hook handler for security scanning."""
    if not context or not context.data:
        return None
    code = context.data.get("code", "")
    target = context.data.get("target", "python")
    findings = _scan(code, target, config or {})
    if findings:
        context.set_data("security_findings", findings)
        context.set_data("has_critical", any(f["severity"] == "critical" for f in findings))
    return {"data": context.data}


def validate(code: str, target: str = "", config=None, **kwargs):
    """Validator entry point."""
    if config is None:
        config = {}
    return _scan(code, target, config)


def _scan(code: str, target: str, config: dict) -> list:
    findings = []
    if not code:
        return findings

    if config.get("check_hardcoded_secrets", True) and target == "python":
        for pattern, title, severity in PATTERNS_SECRET:
            for i, line in enumerate(code.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "severity": severity,
                        "line": i,
                        "title": f"安全风险: {title}",
                        "description": f"在第 {i} 行发现潜在的{title}",
                    })

    if config.get("check_sql_injection", True) and target == "python":
        for pattern, title, severity in PATTERNS_SQL:
            for i, line in enumerate(code.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "severity": severity,
                        "line": i,
                        "title": f"SQL注入风险: {title}",
                        "description": f"在第 {i} 行发现潜在的SQL注入",
                    })

    if config.get("check_xss", True) and target == "html":
        for pattern, title, severity in PATTERNS_XSS:
            for i, line in enumerate(code.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "severity": severity,
                        "line": i,
                        "title": f"XSS风险: {title}",
                        "description": f"在第 {i} 行发现XSS漏洞风险",
                    })

    return findings
