#!/usr/bin/env python3
"""
sandbox.py — CX2118 Script Weaver 沙箱引擎
═══════════════════════════════════════════════════
功能:
  - 自动创建隔离 venv（不碰系统环境）
  - pip install 安装依赖到沙箱内
  - 在沙箱中运行代码、捕获输出/错误
  - AI 自动配置运行检查错误
  - 支持超时控制、资源限制
  - 沙箱目录在当前项目目录下创建
"""

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SandboxStatus(str, Enum):
    NOT_CREATED = "not_created"
    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    ERROR = "error"
    DESTROYED = "destroyed"


@dataclass
class SandboxConfig:
    """沙箱配置"""
    sandbox_dir: str = ".sandbox"
    python_version: str = ""  # 空=使用当前 python
    timeout: int = 60  # 运行超时（秒）
    max_output: int = 65536  # 最大输出字节数
    auto_install: bool = True  # 自动安装依赖
    pip_index: str = "https://pypi.org/simple"
    pip_extra_args: list = field(default_factory=lambda: ["--timeout", "120"])
    keep_venv: bool = True  # 运行后保留 venv
    env_vars: dict = field(default_factory=dict)  # 额外环境变量


@dataclass
class RunResult:
    """运行结果"""
    success: bool = False
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    timeout: bool = False
    duration: float = 0.0
    files_created: list = field(default_factory=list)
    files_modified: list = field(default_factory=list)
    error_analysis: str = ""


@dataclass
class InstallResult:
    """安装结果"""
    success: bool = False
    packages_installed: list = field(default_factory=list)
    packages_failed: list = field(default_factory=list)
    output: str = ""


class SandboxError(Exception):
    """沙箱异常"""
    pass


class CodeSandbox:
    """
    代码沙箱 — 隔离执行环境

    核心设计:
    1. 在项目目录下创建 .sandbox/ 隔离环境
    2. 使用 venv 创建独立的 Python 环境
    3. 所有 pip install 只影响沙箱
    4. 运行代码时设置 PYTHONPATH 指向沙箱
    5. 超时保护 + 输出截断
    """

    def __init__(self, project_dir: str = ".", config: Optional[SandboxConfig] = None):
        self.project_dir = Path(project_dir).resolve()
        self.config = config or SandboxConfig()
        self.sandbox_dir = self.project_dir / self.config.sandbox_dir
        self.venv_dir = self.sandbox_dir / "venv"
        self.site_packages_dir = self.venv_dir / "lib"
        self.output_dir = self.sandbox_dir / "output"
        self.temp_dir = self.sandbox_dir / "temp"
        self.status = SandboxStatus.NOT_CREATED
        self._installed_packages: set = set()
        self._run_history: list = []

    # ── 沙箱生命周期 ──

    async def create(self) -> bool:
        """创建沙箱环境"""
        if self.status == SandboxStatus.READY:
            return True
        if self.venv_dir.exists():
            return await self._verify_and_repair()

        self.status = SandboxStatus.CREATING
        try:
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.temp_dir.mkdir(parents=True, exist_ok=True)

            python = self.config.python_version or sys.executable
            proc = await asyncio.create_subprocess_exec(
                python, "-m", "venv", str(self.venv_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                raise SandboxError(f"venv 创建失败: {stderr.decode(errors='replace')}")

            # 找到 site-packages 路径
            self._detect_site_packages()

            # 记录沙箱元数据
            self._save_metadata({
                "created_at": time.time(),
                "python": python,
                "python_version": platform.python_version(),
                "os": platform.system(),
            })

            # 安装 pip 基础包
            pip_path = self._get_pip_path()
            await self._run_pip_install(["pip", "--upgrade"], pip_path)

            self.status = SandboxStatus.READY
            return True

        except asyncio.TimeoutError:
            self.status = SandboxStatus.ERROR
            raise SandboxError("创建沙箱超时")
        except Exception as e:
            self.status = SandboxStatus.ERROR
            raise SandboxError(f"创建沙箱失败: {e}")

    def _detect_site_packages(self):
        """检测 site-packages 目录"""
        if platform.system() == "Windows":
            candidate = self.venv_dir / "Lib" / "site-packages"
        else:
            python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            candidate = self.venv_dir / "lib" / python_version / "site-packages"
        if candidate.exists():
            self.site_packages_dir = candidate

    async def _verify_and_repair(self) -> bool:
        """验证并修复已有沙箱"""
        python_path = self._get_python_path()
        if not python_path.exists():
            shutil.rmtree(self.venv_dir, ignore_errors=True)
            return await self.create()
        self._detect_site_packages()
        self.status = SandboxStatus.READY
        return True

    def destroy(self):
        """销毁沙箱"""
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        self.status = SandboxStatus.DESTROYED
        self._installed_packages.clear()

    def _get_python_path(self) -> Path:
        if platform.system() == "Windows":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"

    def _get_pip_path(self) -> Path:
        if platform.system() == "Windows":
            return self.venv_dir / "Scripts" / "pip.exe"
        return self.venv_dir / "bin" / "pip"

    # ── 依赖管理 ──

    async def install(self, packages: list[str]) -> InstallResult:
        """
        安装 Python 包到沙箱

        Args:
            packages: 包列表，如 ["requests", "flask>=2.0"]

        Returns:
            InstallResult
        """
        if self.status != SandboxStatus.READY:
            await self.create()

        pip_path = self._get_pip_path()
        if not pip_path.exists():
            return InstallResult(success=False, output="pip 不存在", packages_failed=list(packages))

        result = InstallResult()
        all_args = list(packages)
        if self.config.pip_extra_args:
            all_args.extend(self.config.pip_extra_args)

        proc_result = await self._run_pip_install(all_args, pip_path)
        result.output = proc_result

        # 解析安装结果
        for pkg in packages:
            pkg_name = re.split(r"[><=!~]", pkg)[0].strip()
            if self._is_package_installed(pkg_name):
                result.packages_installed.append(pkg_name)
                self._installed_packages.add(pkg_name)
            else:
                result.packages_failed.append(pkg_name)

        result.success = len(result.packages_failed) == 0
        return result

    async def install_from_code(self, code: str) -> InstallResult:
        """
        从代码中自动提取 import 并安装依赖

        扫描代码中的 import 语句，映射到 pip 包名，然后安装
        """
        packages = self._extract_imports(code)
        if not packages:
            return InstallResult(success=True, output="无需安装依赖")
        return await self.install(packages)

    async def install_from_requirements(self, requirements_path: str) -> InstallResult:
        """从 requirements.txt 安装"""
        if not os.path.exists(requirements_path):
            return InstallResult(success=False, output=f"文件不存在: {requirements_path}")

        pip_path = self._get_pip_path()
        args = ["-r", requirements_path] + self.config.pip_extra_args
        proc_result = await self._run_pip_install(args, pip_path)

        result = InstallResult(output=proc_result)
        # 简单解析
        try:
            with open(requirements_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = re.split(r"[><=!~\[]", line)[0].strip()
                        if pkg and self._is_package_installed(pkg):
                            result.packages_installed.append(pkg)
        except Exception:
            pass
        result.success = len(result.packages_failed) == 0
        return result

    async def _run_pip_install(self, args: list, pip_path: Path) -> str:
        cmd = [str(pip_path), "install", "--no-cache-dir",
               "-i", self.config.pip_index] + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            return stdout.decode(errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            return "pip install 超时"

    def _extract_imports(self, code: str) -> list[str]:
        """从代码中提取第三方 import 并映射到 pip 包名"""
        import_map = {
            "requests": "requests", "flask": "flask", "fastapi": "fastapi",
            "uvicorn": "uvicorn", "httpx": "httpx", "aiohttp": "aiohttp",
            "numpy": "numpy", "pandas": "pandas", "matplotlib": "matplotlib",
            "scipy": "scipy", "sklearn": "scikit-learn", "cv2": "opencv-python",
            "PIL": "Pillow", "bs4": "beautifulsoup4", "lxml": "lxml",
            "openai": "openai", "tiktoken": "tiktoken", "rich": "rich",
            "typer": "typer", "click": "click", "pydantic": "pydantic",
            "sqlalchemy": "sqlalchemy", "alembic": "alembic",
            "redis": "redis", "celery": "celery", "aioredis": "aioredis",
            "websockets": "websockets", "socketio": "python-socketio",
            "jinja2": "Jinja2", "yaml": "PyYAML", "toml": "toml",
            "dotenv": "python-dotenv", "loguru": "loguru",
            "pytz": "pytz", "arrow": "arrow", "pendulum": "pendulum",
            "cryptography": "cryptography", "paramiko": "paramiko",
            "boto3": "boto3", "google.cloud": "google-cloud-storage",
            "selenium": "selenium", "playwright": "playwright",
            "stripe": "stripe", "jwt": "PyJWT",
        }
        stdlib = {
            "os", "sys", "json", "re", "time", "datetime", "math", "random",
            "collections", "itertools", "functools", "pathlib", "io",
            "asyncio", "threading", "multiprocessing", "subprocess",
            "typing", "dataclasses", "enum", "abc", "copy", "hashlib",
            "inspect", "traceback", "unittest", "logging", "argparse",
            "configparser", "tempfile", "shutil", "glob", "fnmatch",
            "struct", "array", "decimal", "fractions", "statistics",
            "textwrap", "string", "unicodedata", "difflib",
        }
        # 收集本地文件名（不含扩展名），排除第三方误判
        local_modules = set()
        if self.temp_dir.exists():
            for f in self.temp_dir.iterdir():
                if f.is_file() and f.suffix == ".py":
                    local_modules.add(f.stem)
        found = set()
        for match in re.finditer(r"^(?:from|import)\s+([\w.]+)", code, re.MULTILINE):
            mod = match.group(1).split(".")[0]
            if mod in stdlib or mod in self._installed_packages or mod in local_modules:
                continue
            if mod in import_map:
                pkg = import_map[mod]
                if pkg not in self._installed_packages:
                    found.add(pkg)
        return list(found)

    def _is_package_installed(self, package_name: str) -> bool:
        """检查包是否已安装"""
        if self.site_packages_dir.exists():
            for d in self.site_packages_dir.iterdir():
                if package_name.lower().replace("-", "_") in d.name.lower().replace("-", "_"):
                    return True
        return False

    # ── 代码运行 ──

    async def run_code(
        self,
        code: str,
        filename: str = "main.py",
        args: list = None,
        timeout: int = None,
        stdin: str = "",
        env: dict = None,
    ) -> RunResult:
        """
        在沙箱中运行 Python 代码

        Args:
            code: Python 源代码
            filename: 文件名
            args: 命令行参数
            timeout: 超时秒数
            stdin: 标准输入
            env: 额外环境变量

        Returns:
            RunResult
        """
        if self.status != SandboxStatus.READY:
            await self.create()

        timeout = timeout or self.config.timeout
        script_path = self.temp_dir / filename
        script_path.write_text(code, encoding="utf-8")

        python = self._get_python_path()
        if not python.exists():
            return RunResult(success=False, stderr="沙箱 Python 不存在", exit_code=-1)

        # 构建环境变量
        run_env = os.environ.copy()
        run_env["PYTHONIOENCODING"] = "utf-8"
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        run_env["PYTHONUNBUFFERED"] = "1"
        # 注入 site-packages
        if self.site_packages_dir.exists():
            pp = run_env.get("PYTHONPATH", "")
            run_env["PYTHONPATH"] = str(self.site_packages_dir) + ((":" + pp) if pp else "")
        # 沙箱特有变量
        run_env["SANDBOX"] = str(self.sandbox_dir)
        run_env["SANDBOX_OUTPUT"] = str(self.output_dir)
        if env:
            run_env.update(env)
        if self.config.env_vars:
            run_env.update(self.config.env_vars)

        cmd = [str(python), str(script_path)] + (args or [])

        self.status = SandboxStatus.RUNNING
        start = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=run_env,
                cwd=str(self.temp_dir),
            )
            try:
                stdin_bytes = stdin.encode("utf-8") if stdin else None
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_bytes),
                    timeout=timeout + 5,
                )
                elapsed = time.time() - start
                stdout_str = stdout.decode("utf-8", errors="replace")[:self.config.max_output]
                stderr_str = stderr.decode("utf-8", errors="replace")[:self.config.max_output]
                success = proc.returncode == 0

                result = RunResult(
                    success=success,
                    exit_code=proc.returncode,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    duration=elapsed,
                )

                # 检查 output 目录新增文件
                result.files_created = self._detect_new_files()

                self._run_history.append({
                    "time": time.time(),
                    "filename": filename,
                    "success": success,
                    "duration": elapsed,
                    "exit_code": proc.returncode,
                })

                return result

            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                elapsed = time.time() - start
                return RunResult(
                    success=False, exit_code=-1,
                    stdout="", stderr=f"运行超时 ({timeout}s)",
                    timeout=True, duration=elapsed,
                )

        except Exception as e:
            elapsed = time.time() - start
            return RunResult(
                success=False, exit_code=-1,
                stderr=f"执行异常: {e}", duration=elapsed,
            )
        finally:
            self.status = SandboxStatus.READY

    async def run_file(
        self,
        file_path: str,
        args: list = None,
        timeout: int = None,
    ) -> RunResult:
        """在沙箱中运行已有的 Python 文件"""
        path = Path(file_path)
        if not path.exists():
            return RunResult(success=False, stderr=f"文件不存在: {file_path}")
        code = path.read_text(encoding="utf-8")
        return await self.run_code(code, path.name, args, timeout)

    async def run_multi_file(
        self,
        files: dict[str, str],
        main_file: str = "main.py",
        args: list = None,
        timeout: int = None,
    ) -> RunResult:
        """
        在沙箱中运行多文件项目

        Args:
            files: {filename: code_content} 字典
            main_file: 入口文件名
            args: 命令行参数
        """
        if self.status != SandboxStatus.READY:
            await self.create()

        # 先写入所有文件
        for fname, content in files.items():
            fpath = self.temp_dir / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")

        # 收集所有文件的依赖，统一安装一次
        all_packages = set()
        for fname, fcode in files.items():
            pkgs = self._extract_imports(fcode)
            all_packages.update(pkgs)
        if all_packages:
            await self.install(list(all_packages))

        # 运行主文件
        return await self.run_code(main_code, main_file, args, timeout)

    # ── AI 错误检查 ──

    async def ai_check_and_fix(
        self,
        code: str,
        error_output: str,
        llm_client=None,
        max_attempts: int = 3,
    ) -> tuple[str, RunResult]:
        """
        AI 自动检查错误并修复代码

        流程:
        1. 运行代码，收集错误
        2. 如果有错误，让 AI 分析并修复
        3. 重新运行，重复直到无错误或达到最大尝试次数

        Args:
            code: 原始代码
            error_output: 初始错误输出
            llm_client: LLM 客户端（需有 stream_chat 方法）
            max_attempts: 最大修复尝试次数

        Returns:
            (final_code, final_result)
        """
        if not llm_client:
            return code, RunResult(success=False, stderr="无 LLM 客户端")

        current_code = code
        current_error = error_output

        for attempt in range(1, max_attempts + 1):
            # 让 AI 修复代码
            fix_prompt = (
                f"以下 Python 代码运行出错，请修复:\n\n"
                f"=== 错误输出 ===\n{current_error}\n\n"
                f"=== 代码 ===\n{current_code}\n\n"
                f"只输出修复后的完整代码，不要解释。"
            )
            try:
                messages = [{"role": "user", "content": fix_prompt}]
                resp = ""
                async for ev in llm_client.stream_chat(
                    messages=messages,
                    system_prompt="你是 Python 调试专家。修复代码中的错误，只输出修复后的完整代码。",
                    temperature=0.3,
                    max_tokens=8192,
                ):
                    if ev.get("type") == "token":
                        resp += ev["content"]
            except Exception as e:
                break

            # 清理 AI 输出
            fixed_code = self._clean_llm_code_output(resp)
            if not fixed_code:
                break

            # 重新运行
            result = await self.run_code(fixed_code)
            if result.success:
                return fixed_code, result

            current_code = fixed_code
            current_error = result.stderr

        return current_code, RunResult(
            success=False,
            stderr=f"经 {max_attempts} 次尝试仍有错误:\n{current_error}",
        )

    def _clean_llm_code_output(self, text: str) -> str:
        """清理 LLM 输出中的 markdown 包装"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines)
        return text

    # ── 文件追踪 ──

    def _detect_new_files(self) -> list[str]:
        """检测 output 目录中的新文件"""
        result = []
        if self.output_dir.exists():
            for f in self.output_dir.iterdir():
                if f.is_file():
                    result.append(f.name)
        return sorted(result)

    # ── 状态与信息 ──

    def _save_metadata(self, meta: dict):
        meta_path = self.sandbox_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def get_info(self) -> dict:
        """获取沙箱信息"""
        return {
            "status": self.status.value,
            "sandbox_dir": str(self.sandbox_dir),
            "venv_exists": self.venv_dir.exists(),
            "python_path": str(self._get_python_path()),
            "installed_packages": sorted(self._installed_packages),
            "run_count": len(self._run_history),
            "last_run": self._run_history[-1] if self._run_history else None,
        }

    def list_installed(self) -> list[str]:
        """列出沙箱中已安装的包"""
        return sorted(self._installed_packages)

    def get_run_history(self) -> list[dict]:
        """获取运行历史"""
        return list(self._run_history)


# ═════════════════ Sandbox Manager (全局管理) ═════════════════

class SandboxManager:
    """
    沙箱管理器 — 管理多个沙箱实例

    设计:
    - 每个项目可以有多个沙箱（如测试沙箱、运行沙箱）
    - 统一的创建/销毁/查询接口
    - 支持通过 API 调用
    """

    def __init__(self, default_project_dir: str = "."):
        self.default_project_dir = Path(default_project_dir).resolve()
        self._sandboxes: dict[str, CodeSandbox] = {}
        self._default_config = SandboxConfig()

    def get_sandbox(self, name: str = "default", project_dir: str = "") -> CodeSandbox:
        """获取或创建沙箱"""
        key = name
        if key not in self._sandboxes:
            pd = project_dir or str(self.default_project_dir)
            config = SandboxConfig(
                sandbox_dir=f".sandbox_{name}" if name != "default" else ".sandbox",
                **{
                    k: v for k, v in self._default_config.__dict__.items()
                    if k not in ("sandbox_dir",)
                }
            )
            self._sandboxes[key] = CodeSandbox(pd, config)
        return self._sandboxes[key]

    async def ensure_ready(self, name: str = "default") -> bool:
        """确保沙箱就绪"""
        sb = self.get_sandbox(name)
        if sb.status != SandboxStatus.READY:
            return await sb.create()
        return True

    def destroy_sandbox(self, name: str = "default"):
        """销毁沙箱"""
        if name in self._sandboxes:
            self._sandboxes[name].destroy()
            del self._sandboxes[name]

    def destroy_all(self):
        """销毁所有沙箱"""
        for name in list(self._sandboxes.keys()):
            self.destroy_sandbox(name)

    def list_sandboxes(self) -> list[dict]:
        """列出所有沙箱"""
        return [{"name": k, **v.get_info()} for k, v in self._sandboxes.items()]

    def get_status(self) -> dict:
        """获取管理器状态"""
        return {
            "sandboxes": self.list_sandboxes(),
            "total_count": len(self._sandboxes),
        }
