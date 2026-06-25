#!/usr/bin/env python3
"""
multi_file_manager.py — CX2118 Script Weaver 多文件项目管理
═════════════════════════════════════════════════════════════════
功能:
  - 管理多 .py 文件项目（类似 AI 助手的多文件协调）
  - 文件树管理（创建、读取、更新、删除文件）
  - 文件依赖分析
  - 项目负责人统一调度各文件的编码任务
  - 多文件 diff / merge / 冲突检测
"""

import ast
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class FileStatus(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"
    DELETED = "deleted"
    CONFLICT = "conflict"


@dataclass
class ProjectFile:
    """项目文件"""
    name: str
    content: str = ""
    original_content: str = ""  # 上一版本（用于 diff）
    file_type: str = "python"   # python / html / json / text
    status: FileStatus = FileStatus.NEW
    language: str = "python"
    description: str = ""
    dependencies: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    last_modified: float = 0.0
    line_count: int = 0
    char_count: int = 0

    def update_content(self, new_content: str):
        """更新内容"""
        if self.content != new_content:
            self.original_content = self.content
            self.content = new_content
            self.status = FileStatus.MODIFIED
            self.last_modified = time.time()
            self.line_count = len(new_content.splitlines())
            self.char_count = len(new_content)
            # 分析代码结构
            if self.file_type == "python":
                self._analyze_python(new_content)

    def _analyze_python(self, code: str):
        """分析 Python 代码结构"""
        self.imports = []
        self.functions = []
        self.classes = []
        self.dependencies = []
        try:
            tree = ast.parse(code)
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.imports.append(alias.name)
                        self.dependencies.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        self.imports.append(f"from {node.module}")
                        self.dependencies.append(node.module.split(".")[0])
                elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    self.functions.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    self.classes.append(node.name)
        except SyntaxError:
            pass

    def mark_unchanged(self):
        self.original_content = self.content
        self.status = FileStatus.UNCHANGED

    def get_diff_summary(self) -> dict:
        """获取变更摘要"""
        if self.status == FileStatus.UNCHANGED or not self.original_content:
            return {"status": self.status.value, "changes": 0}
        old_lines = self.original_content.splitlines()
        new_lines = self.content.splitlines()
        added = len(new_lines) - len([l for l in new_lines if l in old_lines])
        removed = len(old_lines) - len([l for l in old_lines if l in new_lines])
        return {
            "status": self.status.value,
            "changes": added + removed,
            "additions": max(0, added),
            "deletions": max(0, removed),
            "old_lines": len(old_lines),
            "new_lines": len(new_lines),
        }


@dataclass
class ProjectStructure:
    """项目结构"""
    name: str = ""
    description: str = ""
    files: dict[str, ProjectFile] = field(default_factory=dict)
    entry_point: str = "main.py"
    created_at: float = 0.0
    updated_at: float = 0.0

    def add_file(self, name: str, content: str = "", file_type: str = "python"):
        """添加文件"""
        pf = ProjectFile(
            name=name,
            content=content,
            original_content="",
            file_type=file_type,
            status=FileStatus.NEW,
        )
        if content:
            pf.update_content(content)
        self.files[name] = pf
        self.updated_at = time.time()

    def remove_file(self, name: str):
        """删除文件"""
        if name in self.files:
            del self.files[name]
            self.updated_at = time.time()

    def get_file(self, name: str) -> Optional[ProjectFile]:
        return self.files.get(name)

    def update_file(self, name: str, content: str):
        """更新文件内容"""
        if name in self.files:
            self.files[name].update_content(content)
            self.updated_at = time.time()
        else:
            self.add_file(name, content)

    def get_entry_file(self) -> Optional[ProjectFile]:
        return self.files.get(self.entry_point)

    def list_files(self) -> list[dict]:
        """列出所有文件信息"""
        return [
            {
                "name": f.name,
                "type": f.file_type,
                "status": f.status.value,
                "line_count": f.line_count,
                "char_count": f.char_count,
                "functions": f.functions,
                "classes": f.classes,
                "imports": f.imports[:10],
                "description": f.description,
                "is_entry": f.name == self.entry_point,
                "last_modified": f.last_modified,
            }
            for f in self.files.values()
        ]

    def get_dependency_graph(self) -> dict:
        """获取依赖图"""
        graph = {}
        for name, f in self.files.items():
            deps = []
            for dep in f.dependencies:
                # 检查是否是项目内文件
                dep_module = dep.replace("_", "").replace("-", "").lower()
                for other_name in self.files:
                    if other_name != name:
                        other_stem = Path(other_name).stem.replace("_", "").replace("-", "").lower()
                        if dep_module == other_stem or dep_module.startswith(other_stem):
                            deps.append(other_name)
                            break
            graph[name] = {
                "dependencies": deps,
                "imports": f.imports,
                "functions": f.functions,
            }
        return graph

    def get_all_code(self) -> dict[str, str]:
        """获取所有文件代码"""
        return {name: f.content for name, f in self.files.items()}

    def get_summary(self) -> dict:
        """项目摘要"""
        total_lines = sum(f.line_count for f in self.files.values())
        total_chars = sum(f.char_count for f in self.files.values())
        modified = sum(1 for f in self.files.values() if f.status != FileStatus.UNCHANGED)
        return {
            "name": self.name,
            "description": self.description,
            "file_count": len(self.files),
            "total_lines": total_lines,
            "total_chars": total_chars,
            "modified_count": modified,
            "entry_point": self.entry_point,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MultiFileManager:
    """
    多文件项目管理器

    核心职责:
    1. 维护项目文件树
    2. 跟踪文件变更状态
    3. 分析文件间依赖关系
    4. 为项目负责人（AI调度器）提供文件信息
    5. 支持从 Director 规划自动创建文件结构
    """

    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.project: Optional[ProjectStructure] = None
        self._history: list[dict] = []

    def create_project(self, name: str, description: str = "", entry: str = "main.py"):
        """创建新项目"""
        self.project = ProjectStructure(
            name=name,
            description=description,
            entry_point=entry,
            created_at=time.time(),
            updated_at=time.time(),
        )
        # 初始化默认文件
        self.project.add_file(entry, self._default_main_py())
        self._record("create_project", {"name": name})
        return self.project

    def load_from_structure(self, structure: dict):
        """
        从 Director 的结构规划创建项目

        structure 示例:
        {
            "project_name": "MyApp",
            "files": {
                "main.py": {"description": "入口文件"},
                "utils.py": {"description": "工具函数"},
                "config.py": {"description": "配置管理"}
            },
            "dependencies": ["requests", "numpy"]
        }
        """
        name = structure.get("project_name", "Untitled")
        files = structure.get("files", {})
        entry = structure.get("entry_point", "main.py")

        # 检查第一个 .py 文件作为默认入口
        if not entry or entry not in files:
            for k in files:
                if k.endswith(".py"):
                    entry = k
                    break

        self.project = ProjectStructure(
            name=name,
            description=structure.get("description", ""),
            entry_point=entry,
            created_at=time.time(),
            updated_at=time.time(),
        )

        # 创建文件
        for fname, finfo in files.items():
            if isinstance(finfo, dict):
                desc = finfo.get("description", "")
                content = self._generate_file_stub(fname, desc)
            else:
                content = str(finfo)
                desc = ""
            self.project.add_file(fname, content)

        self._record("load_structure", {"name": name, "files": list(files.keys())})
        return self.project

    def save_to_workspace(self):
        """保存所有文件到 workspace 目录"""
        if not self.project:
            return
        for name, f in self.project.files.items():
            fpath = self.workspace_dir / name
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(f.content, encoding="utf-8")
        self._record("save_to_workspace", {"file_count": len(self.project.files)})

    def load_from_workspace(self):
        """从 workspace 目录加载所有 .py 文件"""
        py_files = list(self.workspace_dir.glob("**/*.py"))
        html_files = list(self.workspace_dir.glob("**/*.html"))
        all_files = py_files + html_files

        if not all_files:
            return None

        # 确定入口文件
        entry = "main.py"
        if not (self.workspace_dir / "main.py").exists():
            if py_files:
                entry = py_files[0].relative_to(self.workspace_dir).as_posix()

        name = self.workspace_dir.name
        self.project = ProjectStructure(
            name=name,
            entry_point=entry,
            created_at=time.time(),
            updated_at=time.time(),
        )

        for fpath in all_files:
            rel_path = fpath.relative_to(self.workspace_dir).as_posix()
            content = fpath.read_text(encoding="utf-8")
            ft = "python" if fpath.suffix == ".py" else "html"
            self.project.add_file(rel_path, content, file_type=ft)
            self.project.files[rel_path].mark_unchanged()

        self._record("load_from_workspace", {"file_count": len(self.project.files)})
        return self.project

    def update_file(self, name: str, content: str):
        """更新指定文件"""
        if not self.project:
            raise ValueError("项目未创建")
        self.project.update_file(name, content)
        self._record("update_file", {"name": name})

    def rename_file(self, old_name: str, new_name: str):
        """重命名文件"""
        if not self.project or old_name not in self.project.files:
            return False
        f = self.project.files.pop(old_name)
        f.name = new_name
        self.project.files[new_name] = f
        if self.project.entry_point == old_name:
            self.project.entry_point = new_name
        self._record("rename_file", {"old": old_name, "new": new_name})
        return True

    def delete_file(self, name: str):
        """删除文件"""
        if not self.project:
            return False
        self.project.remove_file(name)
        self._record("delete_file", {"name": name})
        return True

    def get_compile_order(self) -> list[str]:
        """获取编译/编码顺序（拓扑排序，考虑依赖）"""
        if not self.project:
            return []
        graph = self.project.get_dependency_graph()
        visited = set()
        order = []

        def visit(name):
            if name in visited or name not in graph:
                return
            visited.add(name)
            for dep in graph[name].get("dependencies", []):
                visit(dep)
            order.append(name)

        for name in self.project.files:
            visit(name)

        return order

    # ── 辅助方法 ──

    def _default_main_py(self) -> str:
        return (
            "#!/usr/bin/env python3\n"
            '"""\n'
            "Main Script\n"
            '"""\n\n\n'
            "def main():\n"
            "    pass\n\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    def _generate_file_stub(self, filename: str, description: str = "") -> str:
        """根据文件名生成代码骨架"""
        stem = Path(filename).stem
        desc_line = f"# {description}\n" if description else ""
        if stem == "main":
            return self._default_main_py()
        elif "util" in stem or "helper" in stem:
            return (
                f'"""{description or stem} - 工具函数"""\n\n'
                f"{desc_line}\n"
                "import os\nimport sys\nfrom pathlib import Path\n"
                "from typing import Any, Optional\n\n\n"
                f"def {stem}_helper(data: Any) -> Any:\n"
                '    """TODO: 实现"""\n'
                "    return data\n"
            )
        elif "config" in stem or "setting" in stem:
            return (
                f'"""{description or stem} - 配置管理"""\n\n'
                f"{desc_line}\n"
                "import json\nimport os\nfrom pathlib import Path\n\n\n"
                "CONFIG = {\n"
                '    "debug": False,\n'
                '    "version": "1.0.0",\n'
                "}\n\n\n"
                "def load_config(path: str = \"config.json\") -> dict:\n"
                '    """TODO: 加载配置"""\n'
                "    return CONFIG\n"
            )
        elif "model" in stem or "data" in stem or "schema" in stem:
            return (
                f'"""{description or stem} - 数据模型"""\n\n'
                f"{desc_line}\n"
                "from dataclasses import dataclass, field\n"
                "from typing import Optional, List\n\n\n"
                "@dataclass\n"
                "class BaseModel:\n"
                '    """TODO: 定义数据模型"""\n'
                "    id: int = 0\n"
                "    name: str = \"\"\n"
            )
        elif "test" in stem:
            return (
                f'"""{description or stem} - 测试"""\n\n'
                f"{desc_line}\n"
                "import unittest\n\n\n"
                f"class Test{stem.title().replace('_', '')}(unittest.TestCase):\n"
                "    def test_placeholder(self):\n"
                "        self.assertTrue(True)\n\n\n"
                'if __name__ == "__main__":\n'
                "    unittest.main()\n"
            )
        elif "api" in stem or "route" in stem or "handler" in stem:
            return (
                f'"""{description or stem} - API 路由"""\n\n'
                f"{desc_line}\n"
                "from fastapi import APIRouter\n"
                "from pydantic import BaseModel\n\n\n"
                "router = APIRouter()\n\n\n"
                "@router.get(\"/\")\n"
                "async def index():\n"
                '    return {"status": "ok"}\n'
            )
        else:
            return (
                f'"""{description or stem}"""\n\n'
                f"{desc_line}\n"
                "\n\n"
            )

    def _record(self, action: str, data: dict = None):
        self._history.append({
            "action": action,
            "data": data or {},
            "time": time.time(),
        })
        if len(self._history) > 200:
            self._history = self._history[-100:]

    def get_history(self) -> list[dict]:
        return list(self._history)

    def get_project_info(self) -> dict:
        if not self.project:
            return {"exists": False}
        return {
            "exists": True,
            **self.project.get_summary(),
            "files": self.project.list_files(),
            "dependency_graph": self.project.get_dependency_graph(),
            "compile_order": self.get_compile_order(),
        }
