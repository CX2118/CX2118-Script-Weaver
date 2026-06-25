#!/usr/bin/env python3
"""Code Beautifier Plugin — 自动格式化代码"""
import re
import textwrap


def info():
    return {
        "name": "code_beautifier",
        "version": "1.0.0",
        "description": "自动格式化和规范化代码风格",
        "type": "transformer",
    }


def _beautify_python(code: str) -> str:
    """Basic Python code beautification."""
    if not code.strip():
        return code
    lines = code.splitlines()
    result = []
    prev_empty = False
    for line in lines:
        stripped = line.rstrip()
        if stripped == "":
            if not prev_empty:
                result.append("")
            prev_empty = True
        else:
            result.append(stripped)
            prev_empty = False
    result_str = "\n".join(result)
    if not result_str.endswith("\n"):
        result_str += "\n"
    return result_str


def _beautify_html(code: str) -> str:
    """Basic HTML beautification."""
    if not code.strip():
        return code
    code = re.sub(r'>\s*<', '>\n<', code)
    code = re.sub(r'\n{3,}', '\n\n', code)
    return code.strip() + "\n"


async def execute(hook_name, **kwargs):
    if hook_name != "after_coding":
        return {"cancelled": False, "modified": False, "data": kwargs}

    modified = False
    py_code = kwargs.get("python_code", "")
    html_code = kwargs.get("html_code", "")

    if py_code:
        new_py = _beautify_python(py_code)
        if new_py != py_code:
            kwargs["python_code"] = new_py
            modified = True

    if html_code:
        new_html = _beautify_html(html_code)
        if new_html != html_code:
            kwargs["html_code"] = new_html
            modified = True

    return {"cancelled": False, "modified": modified, "data": kwargs}
