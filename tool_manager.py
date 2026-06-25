#!/usr/bin/env python3
"""
tool_manager.py — CX2118 Script Weaver 工具管理器（两阶段安装）
═══════════════════════════════════════════════════════════════
功能:
  - 两阶段工具拉取：第一阶段搜索名称，第二阶段获取完整参数
  - AI 自动搜索和配置工具
  - 工具注册/发现/管理
  - 工具依赖自动解析
  - 工具安装到沙箱
  - 支持多种工具源（pip、git、local）
"""

import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class ToolSource(str, Enum):
    PIP = "pip"          # PyPI 包
    GIT = "git"          # Git 仓库
    LOCAL = "local"      # 本地路径
    CUSTOM = "custom"    # 自定义安装命令


class ToolPhase(str, Enum):
    PHASE1_NAME = "phase1_name"         # 第一阶段：搜索工具名称
    PHASE2_PARAMS = "phase2_params"     # 第二阶段：获取完整参数
    PHASE3_INSTALL = "phase3_install"   # 安装阶段
    PHASE4_CONFIGURE = "phase4_configure"  # 配置阶段
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ToolSpec:
    """工具规格"""
    name: str                           # 工具名称
    display_name: str = ""              # 显示名称
    description: str = ""               # 描述
    source: ToolSource = ToolSource.PIP  # 来源
    phase: ToolPhase = ToolPhase.PHASE1_NAME
    # 第一阶段信息（搜索结果）
    candidates: list = field(default_factory=list)
    selected_candidate: dict = field(default_factory=dict)
    # 第二阶段信息（完整参数）
    install_command: str = ""           # 安装命令
    version_spec: str = ""              # 版本规格
    extra_args: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    # 安装后信息
    installed: bool = False
    install_path: str = ""
    config: dict = field(default_factory=dict)
    # 元数据
    tags: list = field(default_factory=list)
    category: str = ""
    requires_sandbox: bool = True
    created_at: float = 0.0
    error: str = ""


@dataclass
class ToolSearchResult:
    """工具搜索结果"""
    name: str
    version: str = ""
    description: str = ""
    source: ToolSource = ToolSource.PIP
    install_command: str = ""
    homepage: str = ""
    downloads: int = 0
    score: float = 0.0


class ToolRegistry:
    """
    工具注册表 — 管理所有可用工具

    工具来源:
    1. pip search (通过 PyPI API)
    2. 预定义常用工具库
    3. 项目历史安装记录
    """

    # 预定义常用工具
    PREDEFINED_TOOLS = {
        "requests": {
            "display_name": "Requests",
            "description": "HTTP 库，简洁优雅的 HTTP 请求",
            "source": ToolSource.PIP,
            "install_command": "pip install requests",
            "category": "network",
            "tags": ["http", "api", "web", "rest"],
        },
        "httpx": {
            "display_name": "HTTPX",
            "description": "下一代 HTTP 客户端，支持 async",
            "source": ToolSource.PIP,
            "install_command": "pip install httpx",
            "category": "network",
            "tags": ["http", "async", "api", "web"],
        },
        "flask": {
            "display_name": "Flask",
            "description": "轻量级 Web 框架",
            "source": ToolSource.PIP,
            "install_command": "pip install flask",
            "category": "web",
            "tags": ["web", "server", "api", "framework"],
        },
        "fastapi": {
            "display_name": "FastAPI",
            "description": "高性能异步 Web 框架",
            "source": ToolSource.PIP,
            "install_command": "pip install fastapi uvicorn",
            "category": "web",
            "tags": ["web", "async", "api", "framework"],
            "dependencies": ["uvicorn"],
        },
        "numpy": {
            "display_name": "NumPy",
            "description": "科学计算基础库",
            "source": ToolSource.PIP,
            "install_command": "pip install numpy",
            "category": "science",
            "tags": ["math", "array", "matrix", "science"],
        },
        "pandas": {
            "display_name": "Pandas",
            "description": "数据分析和处理库",
            "source": ToolSource.PIP,
            "install_command": "pip install pandas",
            "category": "data",
            "tags": ["data", "analysis", "csv", "excel", "dataframe"],
        },
        "matplotlib": {
            "display_name": "Matplotlib",
            "description": "数据可视化绘图库",
            "source": ToolSource.PIP,
            "install_command": "pip install matplotlib",
            "category": "visualization",
            "tags": ["plot", "chart", "graph", "visualization"],
        },
        "openai": {
            "display_name": "OpenAI",
            "description": "OpenAI API 官方 Python SDK",
            "source": ToolSource.PIP,
            "install_command": "pip install openai",
            "category": "ai",
            "tags": ["ai", "gpt", "llm", "openai"],
        },
        "beautifulsoup4": {
            "display_name": "BeautifulSoup4",
            "description": "HTML/XML 解析库",
            "source": ToolSource.PIP,
            "install_command": "pip install beautifulsoup4 lxml",
            "category": "web",
            "tags": ["html", "parser", "scraping", "xml"],
            "dependencies": ["lxml"],
        },
        "selenium": {
            "display_name": "Selenium",
            "description": "浏览器自动化测试工具",
            "source": ToolSource.PIP,
            "install_command": "pip install selenium",
            "category": "automation",
            "tags": ["browser", "testing", "automation", "webdriver"],
        },
        "scrapy": {
            "display_name": "Scrapy",
            "description": "强大的网页爬虫框架",
            "source": ToolSource.PIP,
            "install_command": "pip install scrapy",
            "category": "web",
            "tags": ["crawler", "spider", "scraping"],
        },
        "pydantic": {
            "display_name": "Pydantic",
            "description": "数据验证和设置管理",
            "source": ToolSource.PIP,
            "install_command": "pip install pydantic",
            "category": "utility",
            "tags": ["validation", "data", "schema", "settings"],
        },
        "sqlalchemy": {
            "display_name": "SQLAlchemy",
            "description": "SQL 工具包和 ORM",
            "source": ToolSource.PIP,
            "install_command": "pip install sqlalchemy",
            "category": "database",
            "tags": ["sql", "database", "orm", "mysql", "postgres"],
        },
        "redis": {
            "display_name": "Redis",
            "description": "Redis Python 客户端",
            "source": ToolSource.PIP,
            "install_command": "pip install redis",
            "category": "database",
            "tags": ["cache", "redis", "nosql", "database"],
        },
        "celery": {
            "display_name": "Celery",
            "description": "分布式任务队列",
            "source": ToolSource.PIP,
            "install_command": "pip install celery",
            "category": "task",
            "tags": ["task", "queue", "worker", "async"],
        },
        "rich": {
            "display_name": "Rich",
            "description": "终端富文本/格式化输出",
            "source": ToolSource.PIP,
            "install_command": "pip install rich",
            "category": "utility",
            "tags": ["terminal", "formatting", "cli", "color"],
        },
        "typer": {
            "display_name": "Typer",
            "description": "CLI 应用构建库",
            "source": ToolSource.PIP,
            "install_command": "pip install typer",
            "category": "utility",
            "tags": ["cli", "terminal", "command", "argument"],
        },
        "loguru": {
            "display_name": "Loguru",
            "description": "简单强大的日志库",
            "source": ToolSource.PIP,
            "install_command": "pip install loguru",
            "category": "utility",
            "tags": ["logging", "debug", "log"],
        },
        "python-dotenv": {
            "display_name": "python-dotenv",
            "description": ".env 文件管理",
            "source": ToolSource.PIP,
            "install_command": "pip install python-dotenv",
            "category": "config",
            "tags": ["env", "config", "environment", "secret"],
        },
    }

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._load_predefined()

    def _load_predefined(self):
        """加载预定义工具"""
        for name, info in self.PREDEFINED_TOOLS.items():
            ts = ToolSpec(
                name=name,
                display_name=info.get("display_name", name),
                description=info.get("description", ""),
                source=info.get("source", ToolSource.PIP),
                install_command=info.get("install_command", f"pip install {name}"),
                category=info.get("category", ""),
                tags=info.get("tags", []),
                dependencies=info.get("dependencies", []),
                phase=ToolPhase.PHASE2_PARAMS,  # 预定义工具已有完整参数
                created_at=time.time(),
            )
            self._tools[name] = ts

    def search(self, query: str, limit: int = 10) -> list[ToolSearchResult]:
        """
        第一阶段：搜索工具名称

        搜索策略:
        1. 精确匹配预定义工具
        2. 模糊匹配（名称/描述/标签）
        3. 按相关性评分排序
        """
        q = query.lower().strip()
        results = []

        for name, ts in self._tools.items():
            score = 0.0
            # 精确匹配
            if name.lower() == q:
                score = 100.0
            elif name.lower().startswith(q):
                score = 80.0
            elif q in name.lower():
                score = 60.0
            # 标签匹配
            for tag in ts.tags:
                if q in tag.lower():
                    score += 15.0
            # 描述匹配
            if q in ts.description.lower():
                score += 20.0
            # 分类匹配
            if q in ts.category.lower():
                score += 10.0

            if score > 0:
                results.append(ToolSearchResult(
                    name=ts.name,
                    version=ts.version_spec or "latest",
                    description=ts.description,
                    source=ts.source,
                    install_command=ts.install_command,
                    score=score,
                ))

        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def get_full_params(self, name: str) -> Optional[dict]:
        """
        第二阶段：获取工具完整参数

        返回完整的安装信息，包括:
        - 安装命令
        - 依赖列表
        - 额外参数
        - 配置建议
        """
        ts = self._tools.get(name)
        if not ts:
            return None
        return {
            "name": ts.name,
            "display_name": ts.display_name,
            "description": ts.description,
            "source": ts.source.value,
            "install_command": ts.install_command,
            "version_spec": ts.version_spec,
            "extra_args": ts.extra_args,
            "dependencies": ts.dependencies,
            "requires_sandbox": ts.requires_sandbox,
            "category": ts.category,
            "tags": ts.tags,
            "config_suggestion": self._get_config_suggestion(name),
        }

    def register_tool(self, spec: ToolSpec):
        """注册自定义工具"""
        self._tools[spec.name] = spec

    def unregister_tool(self, name: str):
        """取消注册工具"""
        self._tools.pop(name, None)

    def list_tools(self, category: str = "") -> list[dict]:
        """列出所有工具"""
        tools = []
        for ts in self._tools.values():
            if category and ts.category != category:
                continue
            tools.append({
                "name": ts.name,
                "display_name": ts.display_name,
                "description": ts.description,
                "source": ts.source.value,
                "phase": ts.phase.value,
                "installed": ts.installed,
                "category": ts.category,
                "tags": ts.tags,
            })
        return tools

    def list_categories(self) -> list[str]:
        """列出所有工具分类"""
        cats = set()
        for ts in self._tools.values():
            if ts.category:
                cats.add(ts.category)
        return sorted(cats)

    def mark_installed(self, name: str):
        """标记工具已安装"""
        if name in self._tools:
            self._tools[name].installed = True
            self._tools[name].phase = ToolPhase.COMPLETED

    def _get_config_suggestion(self, name: str) -> dict:
        """获取工具配置建议"""
        suggestions = {
            "openai": {
                "env": {"OPENAI_API_KEY": "sk-..."},
                "code_import": "from openai import OpenAI",
            },
            "fastapi": {
                "env": {},
                "code_import": "from fastapi import FastAPI\nfrom fastapi.responses import HTMLResponse\nimport uvicorn",
                "run_command": "uvicorn main:app --host 0.0.0.0 --port 8000",
            },
            "flask": {
                "env": {},
                "code_import": "from flask import Flask, render_template",
            },
            "sqlalchemy": {
                "env": {"DATABASE_URL": "sqlite:///app.db"},
                "code_import": "from sqlalchemy import create_engine, Column, Integer, String\nfrom sqlalchemy.ext.declarative import declarative_base",
            },
            "redis": {
                "env": {"REDIS_URL": "redis://localhost:6379/0"},
                "code_import": "import redis",
            },
        }
        return suggestions.get(name, {})


class ToolInstaller:
    """
    工具安装器 — 负责在沙箱中安装工具

    工作流程:
    1. 获取工具完整参数（第二阶段）
    2. 检查依赖链
    3. 在沙箱中执行安装
    4. 验证安装结果
    """

    def __init__(self, registry: ToolRegistry, sandbox=None):
        self.registry = registry
        self.sandbox = sandbox  # 可选的 CodeSandbox 实例

    async def install_tool(self, tool_name: str) -> dict:
        """
        安装工具（两阶段流程）

        Returns:
            {"success": bool, "tool": str, "message": str, "details": dict}
        """
        # 第二阶段：获取完整参数
        params = self.registry.get_full_params(tool_name)
        if not params:
            return {
                "success": False,
                "tool": tool_name,
                "message": f"未找到工具: {tool_name}",
                "details": {},
            }

        # 安装依赖链
        all_packages = [tool_name] + params.get("dependencies", [])
        results = {"installed": [], "failed": [], "output": ""}

        if self.sandbox:
            # 在沙箱中安装
            for pkg in all_packages:
                pkg_info = self.registry.get_full_params(pkg)
                if pkg_info and pkg_info.get("installed"):
                    results["installed"].append(pkg)
                    continue

                install_result = await self.sandbox.install([pkg])
                if install_result.success:
                    results["installed"].append(pkg)
                    self.registry.mark_installed(pkg)
                else:
                    results["failed"].append(pkg)
                results["output"] += install_result.output + "\n"
        else:
            # 本地安装（使用当前 pip）
            for pkg in all_packages:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "pip", "install", "--quiet", pkg,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                    if proc.returncode == 0:
                        results["installed"].append(pkg)
                        self.registry.mark_installed(pkg)
                    else:
                        results["failed"].append(pkg)
                        results["output"] += stderr.decode(errors="replace")
                except asyncio.TimeoutError:
                    results["failed"].append(pkg)
                    results["output"] += f"{pkg}: 安装超时\n"
                except Exception as e:
                    results["failed"].append(pkg)
                    results["output"] += f"{pkg}: {e}\n"

        success = len(results["failed"]) == 0
        if success:
            self.registry.mark_installed(tool_name)

        return {
            "success": success,
            "tool": tool_name,
            "message": f"安装完成: {', '.join(results['installed'])}" + (
                f" | 失败: {', '.join(results['failed'])}" if results["failed"] else ""
            ),
            "details": results,
            "config_suggestion": params.get("config_suggestion", {}),
        }

    async def batch_install(self, tool_names: list[str]) -> list[dict]:
        """批量安装工具"""
        results = []
        for name in tool_names:
            r = await self.install_tool(name)
            results.append(r)
        return results
