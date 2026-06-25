"""
code_analyzer — 代码深度分析插件
═══════════════════════════════
提供静态代码分析：圈复杂度、函数长度、安全漏洞、Import 检查。
"""

import ast
import re


def on_load(config=None, engine=None):
    if engine:
        engine.log("code_analyzer loaded")
    return config


async def on_hook(context=None, config=None, hook_point="", **kwargs):
    if not context:
        return None
    code = context.data.get("code", "")
    target = context.data.get("target", "python")
    if not code:
        return None
    if config is None:
        config = {}
    findings = validate(code=code, target=target, config=config)
    if findings:
        context.set_data("analysis_findings", findings)
    return {"data": {"analysis_findings": findings}} if findings else None


def validate(code: str, target: str = "", config=None, **kwargs):
    if config is None:
        config = {}
    if target == "python":
        return _analyze_python(code, config)
    if target in ("html", "htm"):
        return _analyze_html(code, config)
    return []


def _analyze_python(code: str, config: dict) -> list[dict]:
    findings = []
    if config.get("check_complexity", True):
        try:
            tree = ast.parse(code)
            threshold = config.get("complexity_threshold", 10)
            max_lines = config.get("max_function_lines", 50)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    complexity = _compute_complexity(node)
                    if complexity > threshold:
                        findings.append({
                            "severity": "warning" if complexity <= threshold * 2 else "error",
                            "line": node.lineno,
                            "end_line": getattr(node, "end_lineno", node.lineno),
                            "title": f"高复杂度: {node.name}",
                            "description": f"函数 '{node.name}' 圈复杂度 {complexity}，超过阈值 {threshold}。建议拆分。",
                        })
                    end = getattr(node, "end_lineno", node.lineno)
                    if end - node.lineno + 1 > max_lines:
                        findings.append({
                            "severity": "warning", "line": node.lineno, "end_line": end,
                            "title": f"函数过长: {node.name}",
                            "description": f"'{node.name}' 有 {end - node.lineno + 1} 行，超过 {max_lines} 行。",
                        })
        except SyntaxError:
            pass
    if config.get("check_security", True):
        findings.extend(_security_scan(code))
    if config.get("check_imports", True):
        findings.extend(_import_check(code))
    return findings


def _analyze_html(code: str, config: dict) -> list[dict]:
    findings = []
    if config.get("check_security", True):
        for i, line in enumerate(code.splitlines(), 1):
            if re.search(r'<script[^>]*>.*?(?:eval|document\.write)\s*\(', line, re.IGNORECASE):
                findings.append({"severity": "warning", "line": i, "title": "不安全的内联脚本"})
    return findings


def _compute_complexity(node) -> int:
    complexity = 1
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += 1
        elif isinstance(child, ast.IfExp):
            complexity += 1
    return complexity


def _security_scan(code: str) -> list[dict]:
    findings = []
    patterns = [
        (r'(?:password|secret|api_key|token)\s*=\s*["\'][^"\']+["\']', "error", "硬编码敏感信息"),
        (r'eval\s*\(', "error", "使用 eval()"),
        (r'exec\s*\(', "error", "使用 exec()"),
        (r'pickle\.loads?\s*\(', "warning", "使用 pickle"),
        (r'subprocess\.\w+\s*\([^)]*shell\s*=\s*True', "warning", "shell=True"),
        (r'os\.system\s*\(', "warning", "使用 os.system()"),
    ]
    for pattern, severity, title in patterns:
        for i, line in enumerate(code.splitlines(), 1):
            if re.search(pattern, line):
                findings.append({"severity": severity, "line": i, "title": title})
    return findings


def _import_check(code: str) -> list[dict]:
    findings = []
    try:
        tree = ast.parse(code)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        for imp in imports:
            name = imp.split(".")[-1]
            if len(re.findall(rf'\b{re.escape(name)}\b', code)) <= 1:
                findings.append({"severity": "info", "line": 1, "title": f"可能未使用的 import: {imp}"})
    except SyntaxError:
        pass
    return findings
