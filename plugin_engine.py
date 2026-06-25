#!/usr/bin/env python3
"""
plugin_engine.py — CX2118 Script Weaver 高度自定义插件引擎
═════════════════════════════════════════════════════════════════
支持: Hook / Agent / Validator / Transformer / Middleware / Exporter / Notifier
特性: 热插拔 · 依赖解析 · 生命周期管理 · Pipeline 阶段拦截 · 优先级调度 · 沙箱隔离
"""

import ast
import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

# ═════════════════ Plugin Type Enum ═════════════════

class PluginType(str, Enum):
    HOOK = "hook"                # Pipeline 阶段钩子
    AGENT = "agent"              # 自定义 AI Agent
    VALIDATOR = "validator"      # 代码验证器
    TRANSFORMER = "transformer"  # 代码/数据转换器
    MIDDLEWARE = "middleware"    # 中间件（拦截/修改数据流）
    EXPORTER = "exporter"        # 数据导出器
    NOTIFIER = "notifier"        # 通知推送
    SKILL_PROVIDER = "skill_provider"  # 技能提供者
    UI_PANEL = "ui_panel"        # 自定义 UI 面板


class HookPoint(str, Enum):
    """Pipeline 钩子点 — 定义在哪些阶段可以挂载插件"""
    # PM 阶段
    BEFORE_PM_CHAT = "before_pm_chat"
    AFTER_PM_CHAT = "after_pm_chat"
    BEFORE_PM_APPROVE = "before_pm_approve"
    AFTER_PM_APPROVE = "after_pm_approve"
    BEFORE_REQUIREMENTS_SAVE = "before_requirements_save"
    AFTER_REQUIREMENTS_SAVE = "after_requirements_save"

    # Director 阶段
    BEFORE_DIRECTOR_PLAN = "before_director_plan"
    AFTER_DIRECTOR_PLAN = "after_director_plan"
    BEFORE_SKILL_MATCH = "before_skill_match"
    AFTER_SKILL_MATCH = "after_skill_match"

    # Coding 阶段
    BEFORE_CODING = "before_coding"
    AFTER_CODING = "after_coding"
    BEFORE_PYTHON_CODE = "before_python_code"
    AFTER_PYTHON_CODE = "after_python_code"
    BEFORE_HTML_CODE = "before_html_code"
    AFTER_HTML_CODE = "after_html_code"
    BEFORE_CODE_SAVE = "before_code_save"
    AFTER_CODE_SAVE = "after_code_save"
    BEFORE_SYNTAX_CHECK = "before_syntax_check"
    AFTER_SYNTAX_CHECK = "after_syntax_check"

    # Review 阶段
    BEFORE_AI_REVIEW = "before_ai_review"
    AFTER_AI_REVIEW = "after_ai_review"
    BEFORE_REVIEW_FIX = "before_review_fix"
    AFTER_REVIEW_FIX = "after_review_fix"
    BEFORE_HUMAN_REVIEW = "before_human_review"
    AFTER_HUMAN_REVIEW = "after_human_review"

    # 全局
    ON_ERROR = "on_error"
    ON_PHASE_CHANGE = "on_phase_change"
    ON_BUDGET_WARNING = "on_budget_warning"
    ON_STARTUP = "on_startup"
    ON_SHUTDOWN = "on_shutdown"


class PluginState(str, Enum):
    UNLOADED = "unloaded"
    LOADED = "loaded"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


# ═════════════════ Plugin Manifest ═════════════════

@dataclass
class PluginManifest:
    """插件清单 — 每个插件必须包含 manifest.json"""
    name: str
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    author: str = ""
    plugin_type: PluginType = PluginType.HOOK
    entry_point: str = ""  # Python 入口文件 (相对于插件目录)
    # Hook 插件配置
    hook_points: list[str] = field(default_factory=list)
    hook_priority: int = 50  # 0-100, 越小越先执行
    # 依赖
    dependencies: list[str] = field(default_factory=list)
    # Agent 插件配置
    agent_config: dict = field(default_factory=dict)
    # Validator 插件配置
    validator_targets: list[str] = field(default_factory=lambda: ["python", "html"])
    # 通用配置
    config_schema: dict = field(default_factory=dict)  # UI 配置表单 JSON Schema
    default_config: dict = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    icon: str = "🧩"

    @classmethod
    def from_dict(cls, data: dict) -> "PluginManifest":
        pt_str = data.get("plugin_type") or data.get("type", "hook")
        try:
            pt = PluginType(pt_str)
        except ValueError:
            pt = PluginType.HOOK

        return cls(
            name=data.get("name", "unnamed"),
            version=data.get("version", "1.0.0"),
            display_name=data.get("display_name", data.get("name", "")),
            description=data.get("description", ""),
            author=data.get("author", ""),
            plugin_type=pt,
            entry_point=data.get("entry_point", ""),
            hook_points=data.get("hook_points", []),
            hook_priority=data.get("hook_priority", 50),
            dependencies=data.get("dependencies", []),
            agent_config=data.get("agent_config", {}),
            validator_targets=data.get("validator_targets", ["python", "html"]),
            config_schema=data.get("config_schema", {}),
            default_config=data.get("default_config", {}),
            permissions=data.get("permissions", []),
            tags=data.get("tags", []),
            icon=data.get("icon", "🧩"),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "author": self.author,
            "plugin_type": self.plugin_type.value,
            "entry_point": self.entry_point,
            "hook_points": self.hook_points,
            "hook_priority": self.hook_priority,
            "dependencies": self.dependencies,
            "agent_config": self.agent_config,
            "validator_targets": self.validator_targets,
            "config_schema": self.config_schema,
            "default_config": self.default_config,
            "permissions": self.permissions,
            "tags": self.tags,
            "icon": self.icon,
        }


# ═════════════════ Plugin Instance ═════════════════

@dataclass
class PluginInstance:
    """已加载的插件实例"""
    manifest: PluginManifest
    state: PluginState = PluginState.UNLOADED
    module: Any = None
    config: dict = field(default_factory=dict)
    error_log: list[str] = field(default_factory=list)
    load_time: float = 0.0
    call_count: int = 0
    last_call_time: float = 0.0
    hooks: dict[str, Callable] = field(default_factory=dict)
    plugin_dir: Path = None


# ═════════════════ Hook Context ═════════════════

@dataclass
class HookContext:
    """钩子执行上下文 — 在插件钩子之间传递数据"""
    hook_point: str
    data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    cancelled: bool = False
    modified: bool = False
    error: str = ""
    plugin_results: list = field(default_factory=list)

    def cancel(self):
        """取消后续钩子和 pipeline 原始操作"""
        self.cancelled = True

    def set_data(self, key: str, value: Any):
        self.data[key] = value
        self.modified = True

    def get_data(self, key: str, default=None) -> Any:
        return self.data.get(key, default)


# ═════════════════ Plugin API (暴露给插件的接口) ═════════════════

class PluginAPI:
    """
    插件 API — 通过 engine 参数传递给每个插件

    提供插件可以使用的安全接口
    """

    def __init__(self, engine: 'PluginEngine'):
        self._engine = engine

    @property
    def engine(self) -> 'PluginEngine':
        return self._engine

    def get_config(self, key: str = "", default=None):
        """获取当前插件配置"""
        name = self._get_caller_name()
        cfg = self._engine.get_plugin_config(name)
        if key:
            return cfg.get(key, default)
        return cfg

    def update_config(self, key: str, value: Any):
        """更新当前插件配置"""
        name = self._get_caller_name()
        cfg = self._engine.get_plugin_config(name)
        cfg[key] = value
        self._engine.update_plugin_config(name, cfg)

    def get_plugin(self, name: str) -> dict:
        """获取另一个插件的信息"""
        return self._engine.get_plugin_info(name)

    def log(self, message: str):
        """插件日志输出"""
        name = self._get_caller_name()
        print(f"  [Plugin:{name}] {message}", flush=True)

    def emit_event(self, event_type: str, data: dict):
        """通过 SSE 向前端发送事件"""
        # 在 main.py 中通过 monkey-patch 注入
        if hasattr(self._engine, "_state") and self._engine._state:
            asyncio.create_task(
                self._engine._state.emit({
                    "type": f"plugin_event_{event_type}",
                    "plugin": self._get_caller_name(),
                    **data,
                })
            )

    def _get_caller_name(self) -> str:
        frame = inspect.currentframe()
        if frame:
            caller = frame.f_back
            if caller:
                module = inspect.getmodule(caller)
                if module:
                    for name, inst in self._engine._plugins.items():
                        if inst.module is module:
                            return name
        return "unknown"


# ═════════════════ Plugin Engine ═════════════════

class PluginEngine:
    """
    插件引擎 — 负责插件发现、加载、生命周期管理、钩子调度

    核心能力:
    1. 自动扫描 plugins/ 目录发现插件
    2. 解析 manifest.json 确定插件类型和能力
    3. 热加载/热卸载（运行时增删插件无需重启）
    4. 依赖解析与加载顺序
    5. Hook 优先级调度（支持取消/修改数据）
    6. 插件配置管理与持久化
    7. 插件沙箱隔离（错误不传播到主流程）
    """

    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, PluginInstance] = {}  # name -> instance
        self._hook_registry: dict[str, list[str]] = {}  # hook_point -> [plugin_names]
        self._agent_registry: dict[str, dict] = {}  # agent_name -> config
        self._validator_registry: dict[str, list[str]] = {}  # target -> [plugin_names]
        self._config_path = self.plugins_dir / "plugin_configs.json"
        self._global_configs: dict[str, dict] = {}
        self._load_global_configs()
        self._initialized = False

    # ── Discovery & Loading ──

    def discover_plugins(self) -> list[dict]:
        """扫描插件目录（递归），返回发现的插件信息"""
        discovered = []
        for d in sorted(self.plugins_dir.rglob("*")):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_dict(data)
                if manifest.name not in self._plugins:
                    discovered.append({
                        "name": manifest.name,
                        "version": manifest.version,
                        "display_name": manifest.display_name or manifest.name,
                        "description": manifest.description,
                        "plugin_type": manifest.plugin_type.value,
                        "icon": manifest.icon,
                        "state": "discovered",
                    })
            except Exception as e:
                discovered.append({
                    "name": d.name,
                    "state": "error",
                    "error": str(e),
                })
        return discovered

    async def load_all_plugins(self):
        """加载所有已发现的插件"""
        discovered = self.discover_plugins()
        loaded_count = 0
        for info in discovered:
            if info.get("state") == "error":
                continue
            try:
                ok = await self.load_plugin(info["name"])
                if ok:
                    loaded_count += 1
            except Exception:
                continue
        self._initialized = True
        return loaded_count

    async def load_plugin(self, name: str) -> bool:
        """加载单个插件"""
        if name in self._plugins and self._plugins[name].state in (
            PluginState.LOADED, PluginState.ENABLED
        ):
            return True  # 已加载

        plugin_dir = None
        for d in sorted(self.plugins_dir.rglob("*")):
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                if data.get("name") == name:
                    plugin_dir = d
                    break
            except Exception:
                continue

        if not plugin_dir:
            return False

        manifest_path = plugin_dir / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.from_dict(data)
        except Exception as e:
            return False

        # 默认 entry_point 为 main.py
        if not manifest.entry_point:
            manifest.entry_point = "main.py"

        # 检查依赖
        for dep in manifest.dependencies:
            if dep not in self._plugins or self._plugins[dep].state != PluginState.ENABLED:
                # 尝试加载依赖
                await self.load_plugin(dep)

        # 加载插件配置
        config = {**manifest.default_config, **self._global_configs.get(name, {})}

        instance = PluginInstance(
            manifest=manifest,
            config=config,
            plugin_dir=plugin_dir,
        )

        # 动态加载 Python 模块
        if manifest.entry_point:
            try:
                entry = plugin_dir / manifest.entry_point
                if entry.exists():
                    spec = importlib.util.spec_from_file_location(
                        f"plugin_{manifest.name}", str(entry)
                    )
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[f"plugin_{manifest.name}"] = module
                    spec.loader.exec_module(module)
                    instance.module = module

                    # 调用插件初始化函数
                    if hasattr(module, "on_load"):
                        api = self._plugin_api()
                        init_config = await self._safe_call(
                            module.on_load, config=config, engine=api
                        )
                        if isinstance(init_config, dict):
                            instance.config.update(init_config)
            except Exception as e:
                instance.state = PluginState.ERROR
                instance.error_log.append(f"Load error: {e}")
                self._plugins[name] = instance
                return False

        instance.state = PluginState.LOADED
        instance.load_time = time.time()
        self._plugins[name] = instance

        # 注册钩子
        for hp in manifest.hook_points:
            if hp not in self._hook_registry:
                self._hook_registry[hp] = []
            if name not in self._hook_registry[hp]:
                self._hook_registry[hp].append(name)

        # 注册 Agent
        if manifest.plugin_type == PluginType.AGENT and manifest.agent_config:
            self._agent_registry[manifest.name] = manifest.agent_config

        # 注册 Validator
        if manifest.plugin_type == PluginType.VALIDATOR:
            for target in manifest.validator_targets:
                if target not in self._validator_registry:
                    self._validator_registry[target] = []
                if name not in self._validator_registry[target]:
                    self._validator_registry[target].append(name)

        return True

    async def unload_plugin(self, name: str) -> bool:
        """卸载插件"""
        if name not in self._plugins:
            return False
        instance = self._plugins[name]

        # 调用卸载钩子
        if instance.module and hasattr(instance.module, "on_unload"):
            await self._safe_call(instance.module.on_unload)

        # 清除注册
        for hp in list(self._hook_registry.keys()):
            if name in self._hook_registry[hp]:
                self._hook_registry[hp].remove(name)
        if name in self._agent_registry:
            del self._agent_registry[name]
        for target in list(self._validator_registry.keys()):
            if name in self._validator_registry[target]:
                self._validator_registry[target].remove(name)

        # 从 sys.modules 移除
        mod_name = f"plugin_{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        instance.state = PluginState.UNLOADED
        instance.module = None
        return True

    async def enable_plugin(self, name: str) -> bool:
        """启用插件"""
        if name not in self._plugins:
            if not await self.load_plugin(name):
                return False
        instance = self._plugins[name]
        if instance.state == PluginState.UNLOADED:
            if not await self.load_plugin(name):
                return False
            instance = self._plugins[name]
        instance.state = PluginState.ENABLED
        if instance.module and hasattr(instance.module, "on_enable"):
            await self._safe_call(instance.module.on_enable, config=instance.config)
        return True

    async def disable_plugin(self, name: str) -> bool:
        """禁用插件"""
        if name not in self._plugins:
            return False
        instance = self._plugins[name]
        if instance.module and hasattr(instance.module, "on_disable"):
            await self._safe_call(instance.module.on_disable)
        instance.state = PluginState.DISABLED
        return True

    async def reload_plugin(self, name: str) -> bool:
        """热重载插件"""
        if name in self._plugins:
            await self.unload_plugin(name)
        return await self.load_plugin(name)

    # ── Hook Execution ──

    async def execute_hook(self, hook_point: str, context: HookContext = None) -> HookContext:
        """
        执行指定钩子点的所有注册插件

        Args:
            hook_point: 钩子点标识
            context: 钩子上下文（可选）

        Returns:
            HookContext（可能被插件修改）
        """
        if context is None:
            context = HookContext(hook_point=hook_point)

        registered = self._hook_registry.get(hook_point, [])
        if not registered:
            return context

        # 按优先级排序
        sorted_plugins = sorted(
            [self._plugins[n] for n in registered if n in self._plugins and self._plugins[n].state == PluginState.ENABLED],
            key=lambda p: p.manifest.hook_priority,
        )

        for plugin in sorted_plugins:
            if context.cancelled:
                break
            if not plugin.module:
                continue

            # 查找钩子处理函数
            handler_name = f"on_{hook_point}"
            handler = getattr(plugin.module, handler_name, None)
            if not handler:
                # 也检查通用钩子
                handler = getattr(plugin.module, "on_hook", None)
                if not handler:
                    continue

            result = await self._safe_call(
                handler,
                context=context,
                config=plugin.config,
                plugin_name=plugin.manifest.name,
                hook_point=hook_point,
            )

            if result and isinstance(result, dict):
                context.plugin_results.append(result)
                if result.get("cancel", False):
                    context.cancel()
                if result.get("data"):
                    context.data.update(result["data"])
                    context.modified = True

            plugin.call_count += 1
            plugin.last_call_time = time.time()

        return context

    async def execute_hook_simple(self, hook_point: str, **kwargs) -> HookContext:
        """简化版钩子执行 — 自动创建上下文"""
        context = HookContext(hook_point=hook_point, data=kwargs)
        return await self.execute_hook(hook_point, context)

    # ── Agent Plugin Support ──

    def get_registered_agents(self) -> dict[str, dict]:
        """获取所有插件注册的自定义 Agent"""
        return dict(self._agent_registry)

    def get_agent_config(self, agent_name: str) -> dict:
        return self._agent_registry.get(agent_name, {})

    # ── Validator Plugin Support ──

    async def run_validators(self, target: str, code: str) -> list[dict]:
        """运行目标（python/html）上的所有验证器插件"""
        results = []
        validators = self._validator_registry.get(target, [])
        for name in validators:
            if name not in self._plugins:
                continue
            instance = self._plugins[name]
            if instance.state != PluginState.ENABLED or not instance.module:
                continue
            validate_fn = getattr(instance.module, "validate", None)
            if not validate_fn:
                continue
            result = await self._safe_call(
                validate_fn, code=code, target=target, config=instance.config
            )
            if result:
                if isinstance(result, list):
                    results.extend(result)
                else:
                    results.append(result)
        return results

    # ── Transformer Plugin Support ──

    async def run_transformers(self, target: str, code: str) -> str:
        """运行所有转换器插件"""
        for name, instance in self._plugins.items():
            if instance.state != PluginState.ENABLED or not instance.module:
                continue
            if instance.manifest.plugin_type != PluginType.TRANSFORMER:
                continue
            transform_fn = getattr(instance.module, "transform", None)
            if not transform_fn:
                continue
            result = await self._safe_call(
                transform_fn, code=code, target=target, config=instance.config
            )
            if result and isinstance(result, str):
                code = result
        return code

    # ── Exporter Plugin Support ──

    async def run_exporters(self, data: dict) -> list[dict]:
        """运行所有导出器插件"""
        results = []
        for name, instance in self._plugins.items():
            if instance.state != PluginState.ENABLED or not instance.module:
                continue
            if instance.manifest.plugin_type != PluginType.EXPORTER:
                continue
            export_fn = getattr(instance.module, "export", None)
            if not export_fn:
                continue
            result = await self._safe_call(
                export_fn, data=data, config=instance.config
            )
            if result:
                results.append(result)
        return results

    # ── Notifier Plugin Support ──

    async def notify(self, event: str, message: str, data: dict = None):
        """发送通知到所有通知插件"""
        for name, instance in self._plugins.items():
            if instance.state != PluginState.ENABLED or not instance.module:
                continue
            if instance.manifest.plugin_type != PluginType.NOTIFIER:
                continue
            notify_fn = getattr(instance.module, "notify", None)
            if not notify_fn:
                continue
            await self._safe_call(
                notify_fn, event=event, message=message, data=data or {}, config=instance.config
            )

    # ── Config Management ──

    def get_plugin_config(self, name: str) -> dict:
        if name in self._plugins:
            return self._plugins[name].config
        return {}

    def update_plugin_config(self, name: str, config: dict):
        if name in self._plugins:
            self._plugins[name].config.update(config)
        self._global_configs[name] = config
        self._save_global_configs()

    def _load_global_configs(self):
        if self._config_path.exists():
            try:
                self._global_configs = json.loads(
                    self._config_path.read_text(encoding="utf-8")
                )
            except Exception:
                self._global_configs = {}

    def _save_global_configs(self):
        self._config_path.write_text(
            json.dumps(self._global_configs, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Status & Info ──

    def list_plugins(self) -> list[dict]:
        """列出所有插件状态"""
        result = []
        for name, instance in self._plugins.items():
            m = instance.manifest
            result.append({
                "name": m.name,
                "version": m.version,
                "display_name": m.display_name or m.name,
                "description": m.description,
                "author": m.author,
                "plugin_type": m.plugin_type.value,
                "state": instance.state.value,
                "hook_points": m.hook_points,
                "hook_priority": m.hook_priority,
                "dependencies": m.dependencies,
                "tags": m.tags,
                "icon": m.icon,
                "call_count": instance.call_count,
                "last_call_time": instance.last_call_time,
                "error_count": len(instance.error_log),
                "config_schema": m.config_schema,
                "current_config": instance.config,
                "permissions": m.permissions,
            })
        return result

    def get_plugin_info(self, name: str) -> dict:
        plugins = self.list_plugins()
        for p in plugins:
            if p["name"] == name:
                return p
        return {}

    def get_hook_summary(self) -> dict:
        """获取所有钩子点注册情况"""
        summary = {}
        for hp, names in self._hook_registry.items():
            summary[hp] = [
                {"name": n, "state": self._plugins[n].state.value}
                for n in names if n in self._plugins
            ]
        return summary

    # ── Utilities ──

    def _plugin_api(self) -> 'PluginAPI':
        """创建一个 PluginAPI 实例传递给插件"""
        return PluginAPI(self)

    async def _safe_call(self, func, **kwargs):
        """安全调用插件函数 — 异常隔离，不传播到主流程"""
        try:
            result = func(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            traceback.print_exc()
            return None

    async def on_startup(self):
        """引擎启动"""
        await self.load_all_plugins()
        await self.execute_hook_simple(HookPoint.ON_STARTUP)

    async def on_shutdown(self):
        """引擎关闭"""
        await self.execute_hook_simple(HookPoint.ON_SHUTDOWN)
        for name in list(self._plugins.keys()):
            await self.unload_plugin(name)




# ═════════════════ Singleton ═════════════════

_engine: PluginEngine = None

def get_plugin_engine(plugins_dir: str = "plugins") -> PluginEngine:
    global _engine
    if _engine is None:
        _engine = PluginEngine(plugins_dir)
    return _engine
