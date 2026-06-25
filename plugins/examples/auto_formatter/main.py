"""
auto_formatter — 自动代码格式化插件
═════════════════════════════════
在代码保存前自动格式化，去除尾随空格、整理缩进、确保末尾换行。
"""

import re


def on_load(config=None, engine=None):
    if engine:
        engine.log("auto_formatter loaded")
    return config


def on_enable(config=None):
    print("  [auto_formatter] ✨ Enabled")


def on_disable():
    print("  [auto_formatter] Disabled")


async def on_hook(context=None, config=None, plugin_name="", hook_point="", **kwargs):
    if not context or not context.data:
        return None
    code = context.data.get("code", "")
    target = context.data.get("target", "python")
    if not code:
        return None
    if config is None:
        config = {}
    formatted = code
    if config.get("trim_trailing_whitespace", True):
        formatted = re.sub(r'[ \t]+$', '', formatted, flags=re.MULTILINE)
    if config.get("ensure_final_newline", True) and formatted and not formatted.endswith('\n'):
        formatted += '\n'
    if target == "python" and config.get("python_format", True):
        formatted = _format_python(formatted)
    if target in ("html", "htm") and config.get("html_format", True):
        formatted = _format_html(formatted)
    if formatted != code:
        context.set_data("code", formatted)
        context.set_data("formatted", True)
    return {"data": context.data}


def transform(code: str, target: str = "", config=None, **kwargs) -> str:
    if config is None:
        config = {}
    if not code:
        return code
    if config.get("trim_trailing_whitespace", True):
        code = re.sub(r'[ \t]+$', '', code, flags=re.MULTILINE)
    if config.get("ensure_final_newline", True) and code and not code.endswith('\n'):
        code += '\n'
    return code


def validate(code: str, target: str = "", config=None, **kwargs):
    if config is None:
        config = {}
    findings = []
    if config.get("trim_trailing_whitespace", True):
        for i, line in enumerate(code.splitlines(), 1):
            if line != line.rstrip():
                findings.append({"severity": "warning", "line": i, "title": "尾随空白"})
    max_len = config.get("max_line_length", 120)
    for i, line in enumerate(code.splitlines(), 1):
        if len(line) > max_len:
            findings.append({"severity": "info", "line": i, "title": f"行过长 ({len(line)})"})
    return findings


def _format_python(code: str) -> str:
    lines = code.splitlines()
    result = []
    in_docstring = False
    for line in lines:
        stripped = line.rstrip()
        count = stripped.count('"""') + stripped.count("'''")
        if count == 1:
            in_docstring = not in_docstring
        if not in_docstring and stripped == "" and result and result[-1].rstrip() == "":
            continue
        result.append(stripped)
    return "\n".join(result)


def _format_html(code: str) -> str:
    lines = code.splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped == "" and result and result[-1].strip() == "":
            continue
        result.append(stripped)
    return "\n".join(result)
