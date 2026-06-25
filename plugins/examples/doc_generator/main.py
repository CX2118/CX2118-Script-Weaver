#!/usr/bin/env python3
"""Doc Generator Plugin — 自动为代码生成 Markdown 文档"""
import re
import ast
from pathlib import Path


def info():
    return {
        "name": "doc_generator",
        "version": "1.0.0",
        "description": "自动为代码生成 Markdown 文档和 README",
        "type": "transformer",
    }


def _extract_docstring(code: str) -> str:
    """Extract module docstring from Python code."""
    try:
        tree = ast.parse(code)
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
            return str(tree.body[0].value.value).strip()
    except Exception:
        pass
    return ""


def _extract_functions(code: str) -> list:
    """Extract function names and docstrings."""
    try:
        tree = ast.parse(code)
        funcs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or "(no docstring)"
                funcs.append({"name": node.name, "doc": doc, "line": node.lineno})
        return funcs
    except Exception:
        return []


def _generate_docs(py_code: str) -> str:
    """Generate Markdown documentation from Python code."""
    if not py_code.strip():
        return ""

    lines = []
    module_doc = _extract_docstring(py_code)
    if module_doc:
        lines.append(f"# Module Documentation\n\n{module_doc}\n")

    funcs = _extract_functions(py_code)
    if funcs:
        lines.append("## Functions\n")
        for f in funcs:
            lines.append(f"### `{f['name']}` (line {f['line']})\n")
            lines.append(f"{f['doc']}\n")

    classes = []
    try:
        tree = ast.parse(py_code)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or "(no docstring)"
                classes.append({"name": node.name, "doc": doc, "line": node.lineno})
    except Exception:
        pass

    if classes:
        lines.append("## Classes\n")
        for c in classes:
            lines.append(f"### `{c['name']}` (line {c['line']})\n")
            lines.append(f"{c['doc']}\n")

    return "\n".join(lines) if lines else ""


async def execute(hook_name, **kwargs):
    if hook_name != "after_coding":
        return {"cancelled": False, "modified": False, "data": kwargs}

    py_code = kwargs.get("python_code", "")
    workspace = kwargs.get("workspace", "workspace")
    ws_path = Path(workspace)

    if py_code and py_code.strip():
        docs = _generate_docs(py_code)
        if docs:
            doc_path = ws_path / "AUTO_DOCS.md"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(docs, encoding="utf-8")

    return {"cancelled": False, "modified": False,
            "data": {**kwargs, "docs_generated": bool(py_code and py_code.strip())}}
