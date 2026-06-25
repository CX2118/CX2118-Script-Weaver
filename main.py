#!/usr/bin/env python3
"""
cx2118 Script Weaver v9.3.0 — Multi-Agent Pipeline
═══════════════════════════════════════════════════
PM → Director → Python Coder → HTML Coder → AI Reviewer → Human Review
行级精准编辑 · ast.parse 语法检查 · Skill 匹配 · AI 代码审查 · MD 项目文档读取
v7.0.0 新增: 沙箱引擎 · 多文件管理 · 工具管理器 · 项目调度器 · 断点恢复
v9.0.0 新增: PM Agent Manager · Web Search · 轮询端点 · 工作流增强 · 节点包导入导出 · 文件夹管理
v9.3.0 新增: DEFAULT_SESSION 回退 · PM 调试日志 · Plan 工具栏 · 工作流 LLM 模型选择 · 文件审批后端
"""

import ast
import asyncio
import copy
import difflib
import json
import os
import re
import signal
import sys
import time
import traceback
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
import httpx

from plugin_engine import (
    PluginEngine, PluginAPI, PluginType, HookPoint, HookContext,
    get_plugin_engine,
)

from sandbox import CodeSandbox, SandboxManager, SandboxConfig, SandboxStatus
from multi_file_manager import MultiFileManager, ProjectStructure
from tool_manager import ToolRegistry, ToolInstaller
from project_dispatch import ProjectDispatcher, DispatchPhase
from breakpoint_recovery import BreakpointRecovery
from storage import Storage
from workflow_engine import WorkflowEngine, NodeType, ConnectionType, CurveType, WorkflowFolder
from pm_agent import PMAgentManager, AgentMode, build_node_prompt, PMProject
from web_search import WebSearcher, WebSearchConfig, search_and_summarize, create_searcher_from_config
import uuid
import io

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"


# ═══════════════ ANSI Colors ═══════════════

class T:
    G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; B = "\033[34m"
    C = "\033[36m"; D = "\033[2m"; BD = "\033[1m"; RST = "\033[0m"
    @classmethod
    def strip(cls, s):
        return re.sub(r"\033\[[0-9;]*m", "", s)

def tprint(text):
    if not sys.stdout.isatty():
        text = T.strip(text)
    print(text, flush=True)

def ts():
    return time.strftime("%H:%M:%S")


# ═══════════════ Configuration ═══════════════

class Config:
    def __init__(self):
        self._http_clients = {}
        self.reload()
    def reload(self):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            self.data = json.load(f)
    def save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    @property
    def port(self): return self.data.get("port", 8880)
    @property
    def workspace(self): return self.data.get("workspace", "workspace")
    @property
    def skills_dir(self): return self.data.get("skills_dir", "workspace/skills")
    @property
    def files(self): return self.data.get("files", {})
    @property
    def max_retries(self): return self.data.get("max_review_retries", 3)
    @property
    def budget_limit(self): return self.data.get("budget_limit", 10_000_000)
    @property
    def plugins_dir(self): return self.data.get("plugins_dir", "plugins")
    @property
    def plugins_config(self): return self.data.get("plugins", {})
    @property
    def sandbox_dir(self): return self.data.get("sandbox", {}).get("dir", ".sandbox")
    @property
    def sandbox_auto_install(self): return self.data.get("sandbox", {}).get("auto_install", True)
    @property
    def web_search_config(self): return self.data.get("web_search", {})
    def is_plugins_enabled(self):
        return self.plugins_config.get("enabled", True)
    def is_hot_reload(self):
        return self.plugins_config.get("hot_reload", True)
    def get_agent(self, name):
        return self.data.get("agents", {}).get(name, {})
    def get_provider(self, name=""):
        name = name or "siliconflow"
        return self.data.get("llm_providers", {}).get(name, {})
    def get_client_for(self, agent_name):
        a = self.get_agent(agent_name)
        if not a.get("enabled", False):
            raise ConnectionError(f"Agent '{agent_name}' is disabled")
        pname = a.get("provider", "siliconflow")
        p = self.get_provider(pname)
        key = p.get("api_key", "").strip()
        if not key:
            raise ConnectionError(
                f"NO_API_KEY: Provider '{pname}' (used by agent '{agent_name}') has no API Key. "
                f"Go to Settings → {p.get('name', pname)} → enter your API Key."
            )
        base_url = p.get("base_url", "").strip()
        model = a.get("model", "")
        print(f"[DEBUG] Creating client: agent={agent_name} provider={pname} model={model} base_url={base_url} key={key[:8]}...", file=sys.stderr, flush=True)
        if pname not in self._http_clients:
            self._http_clients[pname] = httpx.AsyncClient(
                timeout=httpx.Timeout(120, connect=30), follow_redirects=True,
            )
        hc = self._http_clients[pname]
        c = AsyncOpenAI(
            base_url=p.get("base_url") or None,
            api_key=key, http_client=hc,
        )
        return LLMClient(c, a.get("model", ""), a.get("token_limit", 4096))

cfg = Config()


# ═══════════════ LLM Client ═══════════════

class LLMClient:
    MAX_TOKEN_LIMIT = 32768
    _TRANSIENT = (
        "500", "502", "503", "529", "Unknown error",
        "timeout", "Timeout", "Connection", "ECONNRESET",
        "ConnectError", "ConnectTimeout", "ReadTimeout",
        "RemoteProtocolError", "ReadError", "WriteError",
        "DNS", "NameResolution", "ENOTFOUND",
        "api_connection_error", "api_timeout",
        "connection_reset", "broken_pipe",
    )
    def __init__(self, client, model, max_tokens=4096):
        self._c = client
        self._m = model
        self._mt = min(max_tokens or 4096, self.MAX_TOKEN_LIMIT)
        self.usage = {"prompt": 0, "completion": 0}
    async def stream_chat(
        self, messages, system_prompt="", temperature=0.5,
        max_tokens=None, skip_reasoning=True, retries=3,
    ) -> AsyncGenerator[dict, None]:
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + list(messages)
        mt = min(max_tokens or self._mt, self._mt, self.MAX_TOKEN_LIMIT)
        for attempt in range(1, retries + 1):
            stream_gen = None
            try:
                stream = await self._c.chat.completions.create(
                    model=self._m, messages=messages,
                    temperature=temperature, max_tokens=mt, stream=True,
                )
                stream_gen = stream
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    d = chunk.choices[0].delta
                    rc = getattr(d, "reasoning_content", None)
                    if rc and not skip_reasoning:
                        yield {"type": "reasoning", "content": rc}
                    # DeepSeek models may return content in reasoning_content field
                    actual_content = d.content or (rc if skip_reasoning else None)
                    if actual_content:
                        # 去除思考过程标签残留
                        import re as _re
                        actual_content = _re.sub(r'<think>[\s\S]*?<\/think>', '', actual_content)
                        actual_content = _re.sub(r'<reasoning>[\s\S]*?<\/reasoning>', '', actual_content)
                        if actual_content:
                            yield {"type": "token", "content": actual_content}
                    if hasattr(chunk, "usage") and chunk.usage:
                        self.usage = {
                            "prompt": chunk.usage.prompt_tokens or 0,
                            "completion": chunk.usage.completion_tokens or 0,
                        }
                stream_gen = None
                return
            except (asyncio.CancelledError, GeneratorExit):
                stream_gen = None
                raise
            except Exception as e:
                stream_gen = None
                err_str = str(e)
                is_transient = any(t in err_str for t in self._TRANSIENT) or any(t in type(e).__name__ for t in self._TRANSIENT)
                if is_transient and attempt < retries:
                    wait = min(attempt * 3, 30)
                    tprint(f"  {T.Y}[{ts()}] ⚠ LLM error ({attempt}/{retries}), retry {wait}s...{T.RST}")
                    tprint(f"  {T.D}[{ts()}]   Error: {err_str[:150]}{T.RST}")
                    await asyncio.sleep(wait)
                    continue
                raise
            finally:
                # Ensure orphaned stream generator is properly closed
                if stream_gen is not None:
                    try:
                        await stream_gen.aclose()
                    except Exception:
                        pass
    async def close(self):
        pass


# ═══════════════ Skill Matcher ═══════════════

class SkillMatcher:
    def __init__(self, d):
        self.dir = Path(d)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir = self.dir / "pending"
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self._cache = None
        self._cache_ts = 0
    def scan(self) -> list[dict]:
        try:
            latest = max(f.stat().st_mtime for f in self.dir.glob("*.md"))
        except (ValueError, OSError):
            latest = 0
        try:
            pending_latest = max(f.stat().st_mtime for f in self.pending_dir.glob("*.md"))
            latest = max(latest, pending_latest)
        except (ValueError, OSError):
            pass
        if self._cache is not None and latest <= self._cache_ts:
            return self._cache
        self._cache_ts = latest
        skills = []
        for f in sorted(self.dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            meta = {"file": f.name, "title": f.stem, "keywords": [], "content": text, "status": "approved"}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    fm = text[3:end].strip()
                    for line in fm.split("\n"):
                        if line.startswith("keywords:"):
                            meta["keywords"] = [k.strip().lower() for k in line.split(":", 1)[1].split(",")]
                        elif line.startswith("title:"):
                            meta["title"] = line.split(":", 1)[1].strip()
                    meta["content"] = text[end + 3:].strip()
            skills.append(meta)
        for f in sorted(self.pending_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            meta = {"file": f"pending/{f.name}", "title": f.stem, "keywords": [], "content": text, "status": "pending"}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    fm = text[3:end].strip()
                    for line in fm.split("\n"):
                        if line.startswith("keywords:"):
                            meta["keywords"] = [k.strip().lower() for k in line.split(":", 1)[1].split(",")]
                        elif line.startswith("title:"):
                            meta["title"] = line.split(":", 1)[1].strip()
                    meta["content"] = text[end + 3:].strip()
            skills.append(meta)
        self._cache = skills
        return skills
    def search(self, query: str, top_k: int = 3) -> list[dict]:
        all_s = self.scan()
        if not all_s: return []
        ql = query.lower()
        qw = set(re.findall(r"[\w\u4e00-\u9fff]+", ql))
        scored = []
        for s in all_s:
            sc = 0
            for kw in s["keywords"]:
                if kw in ql: sc += 3
                for w in qw:
                    if w in kw or kw in w: sc += 1
            for w in qw:
                if w in s["title"].lower(): sc += 2
            if sc > 0: scored.append((sc, s))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]
    def list_all(self):
        return [{"file": s["file"], "title": s["title"], "keywords": s["keywords"], "status": s.get("status", "approved")} for s in self.scan()]
    def read(self, fn):
        p = self.dir / fn
        return p.read_text(encoding="utf-8") if p.exists() else ""
    def write(self, fn, content):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / fn).write_text(content, encoding="utf-8")

skills = SkillMatcher(cfg.skills_dir)


# ═══════════════ Project Context Reader ═══════════════

def read_project_context(max_chars: int = 6000) -> str:
    """Read project markdown + structure files to give AI long-term memory."""
    ws = BASE_DIR / cfg.workspace
    parts, total = [], 0

    # 1. Requirements
    req = fread("requirements", "")
    if req and total < max_chars:
        chunk = req[: min(2500, max_chars - total)]
        parts.append(f"## 📋 需求文档\n{chunk}")
        total += len(chunk)

    # 2. Structure
    struct = fread("structure", "{}")
    if struct and struct != "{}" and total < max_chars:
        chunk = struct[: min(2000, max_chars - total)]
        parts.append(f"## 📐 项目结构\n{chunk}")
        total += len(chunk)

    # 3. Other .md files in workspace
    if ws.exists():
        for md in sorted(ws.glob("*.md")):
            if total >= max_chars:
                break
            try:
                content = md.read_text(encoding="utf-8")
            except Exception:
                continue
            remaining = max_chars - total
            if remaining <= 0:
                break
            chunk = content[: min(2000, remaining)]
            parts.append(f"## 📄 {md.name}\n{chunk}")
            total += len(chunk)

    return "\n\n".join(parts) if parts else ""


def search_skills_for_error(error_text: str) -> str:
    matched = skills.search(error_text, top_k=2)
    if not matched: return ""
    return "\n\n".join(
        f"【{s['title']} ({s['file']})】\n{s['content'][:600]}" for s in matched
    )


# ═══════════════ Edit Engine ═══════════════

def format_code_with_lines(code: str) -> str:
    lines = code.splitlines()
    w = max(3, len(str(len(lines))))
    return "\n".join(f"[{i + 1:>{w}}] {line}" for i, line in enumerate(lines))

@dataclass
class EditOp:
    action: str; start: int; end: int; content: str = ""

def _strip_think_tags(text: str) -> str:
    """Remove <think>/</think>, <reasoning>, <thinking>, [thinking] tags from LLM output."""
    import re as _re
    text = _re.sub(r'<think>[\s\S]*?<\/think>', '', text)
    text = _re.sub(r'<reasoning>[\s\S]*?<\/reasoning>', '', text)
    text = _re.sub(r'<thinking>[\s\S]*?<\/thinking>', '', text)
    text = _re.sub(r'<thought>[\s\S]*?<\/thought>', '', text)
    text = _re.sub(r'\[thinking\][\s\S]*?\[/thinking\]', '', text)
    return text

_MAX_FILE_LINES = 2000
_MAX_FILE_BYTES = 500_000

def parse_edit_ops(text: str) -> tuple[list[EditOp], str]:
    if "<<<FULL_REWRITE>>>" in text:
        raw = text.split("<<<FULL_REWRITE>>>", 1)[1].strip()
        if raw.endswith("<<<END>>>"):
            raw = raw[:-len("<<<END>>>")].strip()
        return [], _strip_md(raw)
    ops = []
    pat = r"<<<\s*(REPLACE|INSERT|DELETE)\s+(\d+)(?:\s*-\s*(\d+))?\s*>>>(.*?)<<<\s*END\s*>>>"
    for m in re.finditer(pat, text, re.DOTALL | re.IGNORECASE):
        act = m.group(1).upper()
        s = int(m.group(2))
        e = int(m.group(3)) if m.group(3) else s
        c = m.group(4).rstrip("\n") if act != "DELETE" else ""
        ops.append(EditOp(act, s, e, c))
    if ops: return ops, ""
    if _looks_like_code(text): return [], _strip_md(text)
    if _looks_like_html(text): return [], _strip_md(text)
    return [], ""

def apply_edit_ops(code: str, ops: list[EditOp]) -> str:
    lines = code.splitlines()
    total = len(lines)
    for op in ops:
        op.start = max(1, min(op.start, total))
        op.end = max(op.start, min(op.end, total))
    for op in sorted(ops, key=lambda o: o.start, reverse=True):
        s, e = op.start - 1, op.end
        if op.action == "REPLACE": lines[s:e] = op.content.splitlines()
        elif op.action == "INSERT": lines[e:e] = op.content.splitlines()
        elif op.action == "DELETE": lines[s:e] = []
    return "\n".join(lines)

def _strip_md(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        ls = t.split("\n")
        if ls[0].startswith("```"): ls = ls[1:]
        if ls and ls[-1].strip() == "```": ls = ls[:-1]
        return "\n".join(ls)
    # Handle inline wrappers: ```lang ... ``` or ''' ... '''
    for wrapper in ["```", "'''"]:
        start = t.find(wrapper)
        if start >= 0:
            end = t.rfind(wrapper, start + len(wrapper))
            if end > start:
                inner = t[start+len(wrapper):end].strip()
                # Remove language tag from first line
                if inner.startswith(("python", "html", "javascript", "js", "css", "json")):
                    inner = inner.split("\n", 1)[-1].strip()
                return inner
    return t

def _looks_like_code(t: str) -> bool:
    return sum(1 for k in ["def ", "class ", "import ", "return ", "if __name__", "    "] if k in t) >= 2

def _looks_like_html(t: str) -> bool:
    tl = t.lower()
    return "<!doctype" in tl or "<html" in tl or "<head" in tl or "<body" in tl

def _extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM output."""
    text = _strip_md(text).strip()
    try: return json.loads(text)
    except json.JSONDecodeError: pass
    start = text.find("{")
    if start >= 0:
        for end in range(len(text) - 1, start, -1):
            if text[end] == "}":
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return {}


# ═══════════════ Diff Engine ═══════════════

def compute_diff_events(old: str, new: str) -> list[dict]:
    ol, nl = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, ol, nl)
    events = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        if tag == "delete":
            for i in range(i1, i2):
                events.append({"type": "diff_del", "row": i + 1, "content": ol[i]})
        elif tag == "insert":
            for j in range(j1, j2):
                events.append({"type": "diff_add", "row": j + 1, "content": nl[j]})
        elif tag == "replace":
            a, b = ol[i1:i2], nl[j1:j2]
            mn = min(len(a), len(b))
            for k in range(mn):
                events.append({"type": "diff_mod", "old_row": i1+k+1, "new_row": j1+k+1, "old_content": a[k], "new_content": b[k]})
            for k in range(mn, len(a)):
                events.append({"type": "diff_del", "row": i1+k+1, "content": a[k]})
            for k in range(mn, len(b)):
                events.append({"type": "diff_add", "row": j1+k+1, "content": b[k]})
    return events

def diff_summary(events):
    a = sum(1 for e in events if e["type"] == "diff_add")
    d = sum(1 for e in events if e["type"] == "diff_del")
    m = sum(1 for e in events if e["type"] == "diff_mod")
    return {"additions": a, "deletions": d, "changes": m, "total": a + d + m}


# ═══════════════ Syntax Check ═══════════════

@dataclass
class CheckResult:
    passed: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    @property
    def summary(self):
        parts = [f"ERROR: {e}" for e in self.errors] + [f"WARN: {w}" for w in self.warnings]
        return "\n".join(parts) if parts else "OK"

def static_check(code: str) -> CheckResult:
    if not code.strip(): return CheckResult(False, ["代码为空"], [])
    if code.strip().startswith("```"): return CheckResult(False, ["输出包含 markdown 包装"], [])
    try: ast.parse(code)
    except SyntaxError as e: return CheckResult(False, [f"第{e.lineno}行: {e.msg}"], [])
    return CheckResult(True)


# ═══════════════ Token Budget ═══════════════

class BudgetExceeded(Exception): pass

class TokenBudget:
    def __init__(self, limit):
        self.limit = limit; self.total = 0
    def add(self, n):
        self.total += n
        if self.total >= self.limit: raise BudgetExceeded(f"{self.total:,} / {self.limit:,}")
    def summary(self):
        return {"total": self.total, "limit": self.limit,
                "pct": round(self.total / self.limit * 100, 1) if self.limit else 0}


# ═══════════════ Pipeline State ═══════════════

class Phase(str, Enum):
    IDLE = "idle"; REQUIREMENTS = "requirements"; PLANNING = "planning"
    CODING = "coding"; REVIEW = "review"

class State:
    def __init__(self):
        self.sse: list[asyncio.Queue] = []
        self.phase = Phase.IDLE
        self.conversation: list[dict] = []
        self.requirements: str = ""
        self.structure: dict = {}
        self.matched_skills: list[dict] = []
        self.code: dict = {"python": "", "html": ""}
        self.budget = TokenBudget(cfg.budget_limit)
        self.stop = False
        self._ev_seq: int = 0  # 事件递增序号
        self.logs: list[dict] = []
        self.retries = 0
        # ── AI Review state ──
        self.review_findings: list[dict] = []
        self.auto_review: bool = False
        # ── Sandbox & Multi-file state ──
        self.sandbox_status: str = "not_created"
        self.project_files: dict = {}  # {filename: content}
        self.project_entry: str = "main.py"

    @property
    def session_id(self):
        return getattr(self, '_session_id', None)

    @session_id.setter
    def session_id(self, sid):
        self._session_id = sid

    async def emit(self, ev: dict):
        ev.setdefault("ts", time.time())
        self._ev_seq += 1
        ev["_seq"] = self._ev_seq
        self.logs.append(ev)
        if len(self.logs) > 800: self.logs = self.logs[-500:]
        dead = []
        for q in list(self.sse):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    dead.append(q)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                self.sse.remove(q)
            except ValueError:
                pass
        # Auto-save pipeline state to storage
        if storage and self._session_id:
            try:
                pipeline_data = {
                    "phase": self.phase,
                    "requirements": self.requirements,
                    "structure": self.structure,
                    "conversation": getattr(self, '_conversation', []),
                }
                await asyncio.to_thread(storage.save_pipeline_state, self._session_id, pipeline_data)
            except Exception:
                pass

    async def stream(self) -> AsyncGenerator[str, None]:
        q: asyncio.Queue = asyncio.Queue(maxsize=8192)
        self.sse.append(q)

        def _safe_json(obj):
            try:
                s = json.dumps(obj, ensure_ascii=False, default=str)
                # 如果单个事件超过100KB，截断 content 字段
                if len(s) > 100000:
                    if isinstance(obj, dict) and "result" in obj and isinstance(obj["result"], dict):
                        r = obj["result"]
                        if "content" in r and len(r["content"]) > 50000:
                            r["content"] = r["content"][:50000] + "\n... [内容过长已截断]"
                            s = json.dumps(obj, ensure_ascii=False, default=str)
                return s
            except Exception:
                return json.dumps({"type": "unserializable_event"})

        try:
            snap = {"type": "snapshot", "phase": self.phase.value,
                    "conversation": self.conversation[-50:],
                    "requirements": self.requirements,
                    "structure": self.structure,
                    "matched_skills": self.matched_skills,
                    "budget": self.budget.summary(),
                    "review_findings": self.review_findings,
                    "logs": self.logs[-60:],
                    "sandbox_status": self.sandbox_status,
                    "project_files": list(self.project_files.keys()),
                    "session_id": self.session_id or ''}
            yield f"data: {_safe_json(snap)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=60)
                    yield f"data: {_safe_json(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ka\n\n"
                except (GeneratorExit, asyncio.CancelledError):
                    raise
                except Exception as e:
                    yield f"data: {_safe_json({'type': 'stream_error', 'message': str(e)[:200]})}\n\n"
                    try:
                        await asyncio.sleep(1)
                    except (GeneratorExit, asyncio.CancelledError):
                        raise
        except (GeneratorExit, asyncio.CancelledError):
            raise
        finally:
            if q in self.sse:
                try: self.sse.remove(q)
                except ValueError: pass

state = State()
DEFAULT_SESSION = "default"

# ── Initialize Plugin Engine (AFTER state is defined) ──
plugin_engine: PluginEngine = get_plugin_engine(cfg.plugins_dir)
plugin_engine._state = state  # state now exists here

# ── Sandbox ──
try:
    sandbox_manager = SandboxManager(str(BASE_DIR))
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ⚠ Sandbox init failed: {e}{T.RST}")
    sandbox_manager = None

# ── Multi-file Manager ──
try:
    multi_file_mgr = MultiFileManager(str(BASE_DIR / cfg.workspace))
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ⚠ MultiFile init failed: {e}{T.RST}")
    multi_file_mgr = None

# ── Tool Manager ──
try:
    tool_registry = ToolRegistry()
    tool_installer = ToolInstaller(tool_registry, sandbox=sandbox_manager.get_sandbox() if sandbox_manager else None)
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ⚠ ToolRegistry init failed: {e}{T.RST}")
    tool_registry = None
    tool_installer = None

# ── Breakpoint Recovery ──
try:
    breakpoint_mgr = BreakpointRecovery(str(BASE_DIR / ".breakpoints"))
    breakpoint_mgr.load_from_disk()
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ⚠ BreakpointRecovery init failed: {e}{T.RST}")
    breakpoint_mgr = None

# ── Storage system ──
try:
    storage = Storage(str(BASE_DIR / "storage"))
    tprint(f"  {T.G}[{ts()}] ✓ Storage initialized{T.RST}")
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ✗ Storage init failed: {e}{T.RST}")
    storage = None

# ── AsyncStorageWrapper — wraps synchronous Storage for WorkflowEngine ──
class AsyncStorageWrapper:
    """Wraps synchronous Storage to provide async interface for WorkflowEngine.

    The WorkflowEngine expects async storage methods, but the real Storage
    class uses synchronous sqlite3. This wrapper bridges the gap using
    asyncio.to_thread().
    """

    def __init__(self, sync_storage):
        self._sync = sync_storage

    async def save_workflow(self, session_id, workflow_id, data, folder_id=""):
        await asyncio.to_thread(self._sync.save_workflow, session_id, workflow_id, data, folder_id)

    async def load_workflow(self, session_id, workflow_id):
        return await asyncio.to_thread(self._sync.load_workflow, session_id, workflow_id)

    async def list_workflows(self, session_id):
        return await asyncio.to_thread(self._sync.list_workflows, session_id)

    async def delete_workflow(self, session_id, workflow_id):
        return await asyncio.to_thread(self._sync.delete_workflow, session_id, workflow_id)

    async def save_node_result(self, session_id, workflow_id, node_id, data):
        await asyncio.to_thread(self._sync.save_workflow_result, session_id, workflow_id, node_id, data)

    async def load_node_result(self, session_id, workflow_id, node_id):
        results = await asyncio.to_thread(self._sync.load_workflow_results, session_id, workflow_id)
        return results.get(node_id)

    async def save_folder(self, session_id, folder_id, name, parent_id=""):
        await asyncio.to_thread(self._sync.save_workflow_folder, session_id, folder_id, name, parent_id)

    async def load_folder(self, session_id, folder_id):
        # Storage has no single-folder load; return None
        return None

    async def list_folders(self, session_id, parent_id=""):
        return await asyncio.to_thread(self._sync.list_workflow_folders, session_id, parent_id)

    async def delete_folder(self, session_id, folder_id):
        return await asyncio.to_thread(self._sync.delete_workflow_folder, session_id, folder_id)


# ── Workflow engine ──
try:
    _async_storage = AsyncStorageWrapper(storage) if storage else None
    workflow_engine = WorkflowEngine(
        storage=_async_storage,
        llm_client_factory=cfg.get_client_for,
        sandbox=sandbox_manager
    )
    tprint(f"  {T.G}[{ts()}] ✓ Workflow engine initialized (async storage wrapper){T.RST}")
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ✗ Workflow engine init failed: {e}{T.RST}")
    workflow_engine = None

# ── PM Agent Manager ──
try:
    pm_manager = PMAgentManager()
    if storage:
        pm_manager.load(storage)
    tprint(f"  {T.G}[{ts()}] ✓ PM Agent Manager initialized{T.RST}")
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ✗ PM Agent Manager init failed: {e}{T.RST}")
    pm_manager = None

# ── Web Searcher ──
try:
    _ws_cfg = cfg.data.get("web_search", {})
    web_searcher = create_searcher_from_config(_ws_cfg)
    if web_searcher:
        tprint(f"  {T.G}[{ts()}] ✓ Web search ready ({_ws_cfg.get('provider', 'unknown')}){T.RST}")
    else:
        tprint(f"  {T.Y}[{ts()}] ⚠ Web search disabled{T.RST}")
except Exception as e:
    tprint(f"  {T.R}[{ts()}] ⚠ Web search init failed: {e}{T.RST}")
    web_searcher = None

# ── Project Dispatcher ──
def _emit_wrapper(ev):
    try:
        asyncio.get_running_loop().create_task(state.emit(ev))
    except RuntimeError:
        pass
dispatcher = ProjectDispatcher(emit_callback=_emit_wrapper)
if sandbox_manager:
    dispatcher.sandbox = sandbox_manager.get_sandbox()
if multi_file_mgr:
    dispatcher.multi_file_manager = multi_file_mgr
if tool_registry:
    dispatcher.tool_manager = tool_registry
if tool_installer:
    dispatcher.tool_installer = tool_installer


def _task_done(task: asyncio.Task):
    if task.cancelled(): return
    exc = task.exception()
    if exc:
        tprint(f"  {T.R}[{ts()}] ✗ Task error: {exc}{T.RST}")
        traceback.print_exc()
        try:
            asyncio.create_task(state.emit({"type": "error", "message": f"任务异常: {exc}"}))
        except RuntimeError:
            pass


# ═══════════════ File Ops ═══════════════

INITIAL_PY = '''#!/usr/bin/env python3
"""
Main Script — cx2118 Script Weaver
"""


def main():
    # Your code starts here
    pass


if __name__ == "__main__":
    main()
'''

def fpath(key: str) -> Path:
    p = Path(cfg.files.get(key, ""))
    return p if p.is_absolute() else BASE_DIR / p

def fread(key, default=""):
    p = fpath(key)
    return p.read_text(encoding="utf-8") if p.exists() else default
def fwrite(key, content):
    p = fpath(key); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

async def fread_async(key, default=""):
    p = fpath(key)
    if not p.exists(): return default
    return await asyncio.to_thread(p.read_text, encoding="utf-8")

async def fwrite_async(key, content):
    p = fpath(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(p.write_text, content, encoding="utf-8")

def fmt_error(e: Exception, context=""):
    t, m = type(e).__name__, str(e)
    if "401" in m: return "认证失败(401): API Key 无效"
    if "429" in m: return "频率限制(429): 稍后重试"
    if "400" in m:
        jm = re.search(r"'message':\s*'([^']+)'", m)
        if jm: return f"请求错误(400): {jm.group(1)[:200]}"
        return f"请求错误(400): {m[:200]}"
    if "500" in m or "Unknown error" in m: return f"服务端错误(500): {context or m[:120]}"
    if "502" in m or "503" in m: return "网关错误: 服务暂不可用"
    if "ConnectError" in t or "ConnectError" in m: return f"连接失败: 无法连接到服务器 {context}"
    if "ConnectTimeout" in t or "ConnectTimeout" in m: return f"连接超时: 服务器响应过慢 {context}"
    if "ReadTimeout" in t or "ReadTimeout" in m: return f"读取超时: 服务器处理时间过长 {context}"
    if "Connection" in t or "Connect" in m: return f"连接失败: {context or m[:120]}"
    if "Timeout" in t or "Timeout" in m: return f"请求超时: {context or m[:120]}"
    if "DNS" in m or "ENOTFOUND" in m or "NameResolution" in m: return f"DNS解析失败: 无法解析域名 {context}"
    if "RemoteProtocolError" in t or "RemoteProtocolError" in m: return f"协议错误: 远端返回异常数据 {context}"
    if "ReadError" in t or "WriteError" in t: return f"网络读写错误: {context or m[:120]}"
    if "api_connection_error" in m.lower(): return f"API连接失败: {context or m[:120]}"
    if "api_timeout" in m.lower(): return f"API请求超时: {context or m[:120]}"
    return f"[{t}] {m[:200]}"


# ═══════════════ PM Agent ═══════════════

async def pm_chat(user_msg: str):
    # ── Plugin Hook: before_pm_chat ──
    hook_ctx = await plugin_engine.execute_hook_simple(
        HookPoint.BEFORE_PM_CHAT,
        user_msg=user_msg,
        conversation=state.conversation,
    )
    if hook_ctx.cancelled:
        await state.emit({"type": "error", "message": hook_ctx.data.get("cancel_reason", "被插件取消")})
        return

    state.phase = Phase.REQUIREMENTS
    await state.emit({"type": "phase_change", "phase": "requirements"})
    await state.emit({"type": "pm_thinking"})
    state.conversation.append({"role": "user", "content": user_msg})
    agent = cfg.get_agent("project_manager")
    try: client = cfg.get_client_for("project_manager")
    except ConnectionError as e:
        await state.emit({"type": "error", "message": str(e)})
        await state.emit({"type": "pm_done"}); return
    resp = ""
    try:
        async for ev in client.stream_chat(messages=state.conversation, system_prompt=agent.get("system_prompt", ""), temperature=0.5, max_tokens=agent.get("token_limit", 4000)):
            if state.stop: break
            if ev["type"] == "token": resp += ev["content"]; await state.emit({"type": "pm_token", "token": ev["content"]})
        state.budget.add(client.usage["prompt"] + client.usage["completion"])
    except Exception as e:
        err_msg = f"PM: {fmt_error(e)}"
        if "Connection" in str(e) or "connect" in str(e).lower():
            err_msg += f" (provider={agent.get('provider','?')}, model={agent.get('model','?')}, base_url={cfg.get_provider(agent.get('provider','')).get('base_url','')})"
        await state.emit({"type": "error", "message": err_msg})
        await state.emit({"type": "pm_done"}); return
    finally: await client.close()
    state.conversation.append({"role": "assistant", "content": resp})

    # ── Plugin Hook: after_pm_chat ──
    await plugin_engine.execute_hook_simple(
        HookPoint.AFTER_PM_CHAT,
        message=resp,
        conversation=state.conversation,
    )

    await state.emit({"type": "pm_response", "message": resp})
    if storage and state.session_id:
        storage.save_message(state.session_id, "pm", resp)
    await state.emit({"type": "pm_done"})
    tprint(f"  {T.C}[{ts()}] PM: {resp[:80]}...{T.RST}")

async def pm_approve():
    state.phase = Phase.REQUIREMENTS
    await state.emit({"type": "pm_thinking"})
    agent = cfg.get_agent("project_manager")
    try: client = cfg.get_client_for("project_manager")
    except ConnectionError as e:
        await state.emit({"type": "error", "message": str(e)}); return
    doc = ""
    try:
        async for ev in client.stream_chat(
            messages=state.conversation + [{"role": "user", "content": "根据以上对话，输出最终完整需求文档。直接输出 Markdown，不要额外说明。"}],
            system_prompt=agent.get("system_prompt", ""), temperature=0.3, max_tokens=agent.get("token_limit", 4000)):
            if state.stop: break
            if ev["type"] == "token": doc += ev["content"]; await state.emit({"type": "pm_token", "token": ev["content"]})
        state.budget.add(client.usage["prompt"] + client.usage["completion"])
    except Exception as e:
        err_msg = f"PM: {fmt_error(e)}"
        if "Connection" in str(e) or "connect" in str(e).lower():
            err_msg += f" (provider={agent.get('provider','?')}, model={agent.get('model','?')}, base_url={cfg.get_provider(agent.get('provider','')).get('base_url','')})"
        await state.emit({"type": "error", "message": err_msg}); return
    finally: await client.close()

    # ── Plugin Hook: before_requirements_save ──
    hook_ctx = await plugin_engine.execute_hook_simple(
        HookPoint.BEFORE_REQUIREMENTS_SAVE,
        requirements=doc,
    )
    if hook_ctx.cancelled:
        await state.emit({"type": "error", "message": "需求保存被插件拦截"})
        return

    state.requirements = doc; fwrite("requirements", doc)

    # ── Plugin Hook: after_requirements_save ──
    await plugin_engine.execute_hook_simple(
        HookPoint.AFTER_REQUIREMENTS_SAVE,
        requirements=doc,
    )

    state.conversation.append({"role": "assistant", "content": doc})
    await state.emit({"type": "requirements_saved", "content": doc})
    await state.emit({"type": "pm_done"})
    tprint(f"  {T.G}[{ts()}] ✓ Requirements saved{ T.RST}")
    await director_plan()


# ═══════════════ Director Agent ═══════════════

async def director_plan():
    state.phase = Phase.PLANNING
    await state.emit({"type": "phase_change", "phase": "planning"})
    await state.emit({"type": "director_thinking"})
    req_text = state.requirements or fread("requirements")

    # ── Plugin Hook: before_director_plan ──
    await plugin_engine.execute_hook_simple(
        HookPoint.BEFORE_DIRECTOR_PLAN,
        requirements=req_text,
    )

    matched = skills.search(req_text)
    state.matched_skills = [{"file": s["file"], "title": s["title"], "keywords": s["keywords"]} for s in matched]
    skill_ctx = ""
    if matched:
        skill_ctx = "\n\n【相关技能参考】\n" + "".join(f"\n### {s['title']} ({s['file']})\n{s['content'][:1500]}\n" for s in matched)
        await state.emit({"type": "skills_found", "skills": state.matched_skills})
        tprint(f"  {T.B}[{ts()}] Matched {len(matched)} skill files{ T.RST}")
    prompt = f"【需求文档】\n{req_text}\n{skill_ctx}\n\n请制定项目结构，输出 JSON。"
    agent = cfg.get_agent("project_director")
    try: client = cfg.get_client_for("project_director")
    except ConnectionError as e:
        await state.emit({"type": "error", "message": str(e)}); return
    resp = ""
    try:
        async for ev in client.stream_chat(messages=[{"role": "user", "content": prompt}], system_prompt=agent.get("system_prompt", ""), temperature=0.3, max_tokens=agent.get("token_limit", 4000)):
            if state.stop: break
            if ev["type"] == "token": resp += ev["content"]; await state.emit({"type": "director_token", "token": ev["content"]})
        state.budget.add(client.usage["prompt"] + client.usage["completion"])
    except Exception as e:
        await state.emit({"type": "error", "message": f"Director: {fmt_error(e)}"}); return
    finally: await client.close()
    try:
        jm = re.search(r"\{.*\}", resp, re.DOTALL)
        state.structure = json.loads(jm.group()) if jm else {"raw": resp}
    except (json.JSONDecodeError, Exception): state.structure = {"raw": resp}
    fwrite("structure", json.dumps(state.structure, indent=2, ensure_ascii=False))
    await state.emit({"type": "director_plan", "structure": state.structure, "raw": resp})
    await state.emit({"type": "director_done"})

    # ── Plugin Hook: after_director_plan ──
    await plugin_engine.execute_hook_simple(
        HookPoint.AFTER_DIRECTOR_PLAN,
        structure=state.structure,
        raw=resp,
    )

    nh = state.structure.get("needs_html", False); np = state.structure.get("needs_python", True)
    tprint(f"  {T.G}[{ts()}] ✓ Plan | Py:{'YES' if np else 'NO'} Html:{'YES' if nh else 'NO'}{ T.RST}")


# ═══════════════ AI Code Reviewer ═══════════════

async def ai_review(coder: str, code: str) -> list[dict]:
    """AI reviewer checks code for obvious issues. Returns findings list."""
    if not code.strip() or len(code.strip()) < 20:
        return []

    # Try code_reviewer agent first, fallback to python_coder
    agent_name = "code_reviewer"
    if agent_name not in cfg.data.get("agents", {}):
        agent_name = "python_coder"
        agent = cfg.get_agent(agent_name)
    try:
        client = cfg.get_client_for(agent_name)
    except ConnectionError:
        return []

    file_type = "Python" if coder == "python" else "HTML"
    numbered = format_code_with_lines(code)

    sys_prompt = (
        f"你是 {file_type} 代码审查员。只指出明显问题，不要审查风格/命名/优化。\n\n"
        f"审查标准：\n"
        f"1. 语法错误 / 结构错误\n"
        f"2. 未定义的变量/函数\n"
        f"3. 缺少 import 语句\n"
        f"4. 明显的逻辑错误（死循环、参数不匹配等）\n\n"
        f"输出纯 JSON，无 markdown 包装：\n"
        f'{{"findings":[{{"id":1,"severity":"error","start_line":5,"end_line":8,"title":"问题标题","description":"描述","fix_edit":"<<<REPLACE 5-8>>>\\n修复代码\\n<<<END>>>"}}]}}\n'
        f'如果无问题：{{"findings":[]}}'
    )

    prompt = f"审查以下 {file_type} 代码：\n\n{numbered}"
    await state.emit({"type": "ai_review_thinking", "coder": coder})

    resp = ""
    try:
        async for ev in client.stream_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=sys_prompt, temperature=0.2, max_tokens=2048,
        ):
            if ev["type"] == "token":
                resp += ev["content"]
                await state.emit({"type": "ai_review_token", "coder": coder, "token": ev["content"]})
        state.budget.add(client.usage["prompt"] + client.usage["completion"])
    except Exception as e:
        tprint(f"  {T.Y}[{ts()}] ⚠ AI Review failed: {e}{ T.RST}")
        return []
    finally:
        await client.close()

    # Parse JSON
    data = _extract_json(resp)
    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    result = []
    for i, f in enumerate(raw_findings):
        if not isinstance(f, dict):
            continue
        # Validate fix_edit
        fix = f.get("fix_edit", "")
        if fix:
            ops, fc = parse_edit_ops(fix)
            if not ops and not fc:
                fix = ""  # Invalid edit
        result.append({
            "id": f.get("id", i + 1),
            "coder": coder,
            "severity": f.get("severity", "warning"),
            "start_line": f.get("start_line", 0),
            "end_line": f.get("end_line", f.get("start_line", 0)),
            "title": f.get("title", "未命名问题"),
            "description": f.get("description", ""),
            "fix_edit": fix,
            "status": "pending",
        })
    return result


# ═══════════════ Review Finding Actions ═══════════════

async def apply_finding(finding: dict) -> bool:
    """Apply a single review finding's fix_edit."""
    if not finding.get("fix_edit"):
        finding["status"] = "rejected"
        return False
    coder = finding["coder"]
    code = state.code.get(coder, "")
    if not code:
        finding["status"] = "rejected"
        return False
    try:
        ops, full_code = parse_edit_ops(finding["fix_edit"])
        if ops:
            new_code = apply_edit_ops(code, ops)
        elif full_code:
            new_code = full_code
        else:
            finding["status"] = "rejected"
            return False
        state.code[coder] = new_code
        fwrite(coder, new_code)
        finding["status"] = "applied"
        await state.emit({"type": "review_fix_applied", "id": finding["id"], "coder": coder})
        tprint(f"  {T.G}[{ts()}] ✓ Applied finding #{finding['id']}{ T.RST}")
        return True
    except Exception as e:
        finding["status"] = "rejected"
        tprint(f"  {T.Y}[{ts()}] ⚠ Apply failed #{finding['id']}: {e}{ T.RST}")
        return False

async def apply_all_findings():
    """Apply all pending review findings."""
    pending = [f for f in state.review_findings if f["status"] == "pending"]
    # Sort descending by line to avoid line drift
    pending.sort(key=lambda f: -f.get("start_line", 0))
    for finding in pending:
        await apply_finding(finding)
    # Send updated code state
    await _send_code_state()
    # Check if all handled
    remaining = sum(1 for f in state.review_findings if f["status"] == "pending")
    if remaining == 0:
        await state.emit({"type": "review_all_handled"})

async def accept_finding(fid: int) -> bool:
    for f in state.review_findings:
        if f["id"] == fid and f["status"] == "pending":
            ok = await apply_finding(f)
            # Re-render preview
            await _send_code_state()
            remaining = sum(1 for x in state.review_findings if x["status"] == "pending")
            if remaining == 0:
                await state.emit({"type": "review_all_handled"})
            return ok
    return False

async def reject_finding(fid: int) -> bool:
    for f in state.review_findings:
        if f["id"] == fid and f["status"] == "pending":
            f["status"] = "rejected"
            await state.emit({"type": "review_fix_rejected", "id": fid})
            remaining = sum(1 for x in state.review_findings if x["status"] == "pending")
            if remaining == 0:
                await state.emit({"type": "review_all_handled"})
            return True
    return False

async def _send_code_state():
    py = state.code.get("python") or fread("python", INITIAL_PY)
    html = state.code.get("html") or fread("html", "")
    await state.emit({
        "type": "code_state",
        "python_lines": len(py.splitlines()),
        "html_lines": len(html.splitlines()) if html else 0,
        "python_preview": py.splitlines()[:80],
    })

async def _proceed_to_review():
    await _send_code_state()
    state.phase = Phase.REVIEW
    await state.emit({"type": "phase_change", "phase": "review"})
    await state.emit({"type": "review_ready"})
    tprint(f"  {T.G}[{ts()}] 🎉 Coding complete — review phase{ T.RST}")


# ═══════════════ CODING PHASE ═══════════════

async def run_coding(extra: str = "", auto_review: bool = False):
    state.stop = False
    state.code = {"python": "", "html": ""}
    state.review_findings = []
    state.auto_review = auto_review
    state.phase = Phase.CODING
    await state.emit({"type": "phase_change", "phase": "coding"})

    # ── Plugin Hook: before_coding ──
    hook_ctx = await plugin_engine.execute_hook_simple(
        HookPoint.BEFORE_CODING,
        structure=state.structure,
        requirements=state.requirements,
    )
    if hook_ctx.cancelled:
        await state.emit({"type": "error", "message": "编码被插件取消"})
        return

    # Determine coders
    struct = state.structure
    files = struct.get("files", {})
    file_keys = list(files.keys())
    has_py = struct.get("needs_python", None)
    has_html = struct.get("needs_html", None)
    if has_py is None: has_py = any(k.endswith(".py") for k in file_keys) or len(file_keys) == 0
    if has_html is None: has_html = any(k.endswith(".html") or k.endswith(".htm") for k in file_keys)
    if not has_py and not has_html: has_py = True
    needs = []
    if has_py: needs.append("Python")
    if has_html:
        ag = cfg.get_agent("html_coder")
        if ag.get("enabled"): needs.append("HTML")
        else: has_html = False

    await state.emit({"type": "coding_plan", "needs_python": has_py, "needs_html": has_html, "coders": needs, "auto_review": auto_review})
    tprint(f"  {T.B}[{ts()}] Coders: {', '.join(needs) or 'NONE'} | Auto-review: {'ON' if auto_review else 'OFF'}{ T.RST}")

    # Run coders
    py_ok = html_ok = False
    if has_py:
        try: py_ok = await python_code(extra)
        except Exception as e:
            await state.emit({"type": "error", "message": f"Python crash: {fmt_error(e)}"})
            tprint(f"  {T.R}[{ts()}] ✗ Python crash: {e}{ T.RST}"); traceback.print_exc()
    if has_html:
        try: html_ok = await html_code(extra)
        except Exception as e:
            await state.emit({"type": "error", "message": f"HTML crash: {fmt_error(e)}"})
            tprint(f"  {T.R}[{ts()}] ✗ HTML crash: {e}{ T.RST}"); traceback.print_exc()

    if not py_ok and not html_ok:
        await state.emit({"type": "error", "message": "编程失败：未生成任何代码。请检查 API Key 和配置。"})
        tprint(f"  {T.R}[{ts()}] ✗ All coders failed{ T.RST}")
        return

    # ── AI Review ──
    all_findings = []
    if py_ok and state.code.get("python"):
        await state.emit({"type": "ai_review_start", "coder": "python"})
        tprint(f"  {T.B}[{ts()}] 🔍 AI reviewing Python...{ T.RST}")
        findings = await ai_review("python", state.code["python"])
        all_findings.extend(findings)

    if html_ok and state.code.get("html"):
        await state.emit({"type": "ai_review_start", "coder": "html"})
        tprint(f"  {T.B}[{ts()}] 🔍 AI reviewing HTML...{ T.RST}")
        findings = await ai_review("html", state.code["html"])
        all_findings.extend(findings)

    state.review_findings = all_findings

    if all_findings:
        await state.emit({"type": "review_findings", "findings": all_findings})
        err_cnt = sum(1 for f in all_findings if f["severity"] == "error")
        warn_cnt = len(all_findings) - err_cnt
        tprint(f"  {T.Y}[{ts()}] 🔍 AI Review: {err_cnt} errors, {warn_cnt} warnings{ T.RST}")

        if auto_review:
            await apply_all_findings()
            await _proceed_to_review()
        else:
            # Wait for human — don't advance
            await state.emit({"type": "awaiting_review_decision", "count": len(all_findings)})
            tprint(f"  {T.Y}[{ts()}] ⏳ Waiting for human review decisions...{ T.RST}")
            return
    else:
        await state.emit({"type": "review_no_issues"})
        tprint(f"  {T.G}[{ts()}] 🔍 AI Review: no issues{ T.RST}")

    # ── Plugin Hook: after_coding ──
    await plugin_engine.execute_hook_simple(
        HookPoint.AFTER_CODING,
        python_code=state.code.get("python", ""),
        html_code=state.code.get("html", ""),
        findings=all_findings,
        budget=state.budget.summary(),
    )

    await _proceed_to_review()


# ═══════════════ Python Coder ═══════════════

async def python_code(extra: str = "") -> bool:
    req = state.requirements or fread("requirements")
    struct = json.dumps(state.structure, ensure_ascii=False)
    current = fread("python", INITIAL_PY)
    numbered = format_code_with_lines(current)

    prompt = f"【需求】\n{req[:2000]}\n\n【结构】\n{struct[:1000]}\n\n"
    # ── inject project context ──
    proj_ctx = read_project_context()
    if proj_ctx:
        prompt += f"【项目文档】\n{proj_ctx}\n\n"
    prompt += f"【当前代码】\n{numbered}\n"
    if extra: prompt += f"\n【额外要求】\n{extra}\n"
    prompt += "\n输出编辑操作或完整代码。"

    agent = cfg.get_agent("python_coder")
    await state.emit({"type": "coder_start", "coder": "python"})
    feedback = ""; code = current; consecutive_errors = 0; skill_hint = ""

    for att in range(1, cfg.max_retries + 1):
        state.retries = att
        await state.emit({"type": "coder_attempt", "coder": "python", "attempt": att})
        full_prompt = prompt
        if feedback: full_prompt += f"\n【上次错误 — 必须修复】\n{feedback}\n"
        if skill_hint: full_prompt += f"\n【参考：相关技能文档】\n{skill_hint}\n"
        try: client = cfg.get_client_for("python_coder")
        except ConnectionError as e:
            await state.emit({"type": "error", "message": str(e)}); return False
        llm_out = ""
        try:
            async for ev in client.stream_chat(messages=[{"role": "user", "content": full_prompt}], system_prompt=agent.get("system_prompt", ""), temperature=0.4, max_tokens=agent.get("token_limit", 4096)):
                if state.stop: break
                if ev["type"] == "token": llm_out += ev["content"]; await state.emit({"type": "coder_token", "coder": "python", "token": ev["content"]})
            state.budget.add(client.usage["prompt"] + client.usage["completion"])
        except Exception as e:
            err_msg = fmt_error(e, agent.get("model", ""))
            await state.emit({"type": "error", "message": f"Python ({att}): {err_msg}"})
            if any(s in str(e) for s in ["500","502","503","529","Unknown error"]): continue
            return False
        finally: await client.close()

        llm_out = _strip_think_tags(llm_out)
        ops, full_code = parse_edit_ops(llm_out)
        if ops:
            new_code = apply_edit_ops(code, ops)
            await state.emit({"type": "edits_applied", "coder": "python", "count": len(ops), "line_count": len(new_code.splitlines())})
        elif full_code:
            new_code = full_code
            await state.emit({"type": "full_rewrite", "coder": "python", "line_count": len(new_code.splitlines())})
        else:
            feedback = "无法解析输出。请使用 <<<REPLACE>>>/<<<INSERT>>>/<<<DELETE>>> 或 <<<FULL_REWRITE>>> 格式。"
            consecutive_errors += 1
            await state.emit({"type": "parse_failed", "coder": "python", "attempt": att})
            if consecutive_errors >= 2 and not skill_hint:
                hint = search_skills_for_error("parse format edit operations")
                if hint: skill_hint = hint; await state.emit({"type": "skill_search", "coder": "python", "reason": "连续解析失败", "found": True})
            continue

        diff = compute_diff_events(code, new_code)
        s = diff_summary(diff)
        await state.emit({"type": "diff_header", "coder": "python", **s})
        for ev in diff: await state.emit(ev)
        chk = static_check(new_code)
        await state.emit({"type": "check_result", "coder": "python", "passed": chk.passed, "errors": chk.errors, "warnings": chk.warnings})
        if chk.passed:
            code = new_code; fwrite("python", code); state.code["python"] = code
            await state.emit({"type": "coder_complete", "coder": "python", "lines": len(code.splitlines()), "preview": code.splitlines()[:60]})
            tprint(f"  {T.G}[{ts()}] ✓ Python ({len(code.splitlines())} lines){ T.RST}")
            return True
        consecutive_errors += 1; feedback = chk.summary; code = new_code
        if consecutive_errors >= 2 and not skill_hint:
            err_text = "\n".join(chk.errors)
            hint = search_skills_for_error(err_text)
            if hint: skill_hint = hint; await state.emit({"type": "skill_search", "coder": "python", "reason": err_text[:100], "found": True})
            else: await state.emit({"type": "skill_search", "coder": "python", "reason": err_text[:100], "found": False})
        tprint(f"  {T.Y}[{ts()}] ⚡ Syntax error, retry {att}{ T.RST}")

    await state.emit({"type": "coder_failed", "coder": "python"})
    return False


# ═══════════════ HTML Coder ═══════════════

async def html_code(extra: str = "") -> bool:
    agent = cfg.get_agent("html_coder")
    if not agent.get("enabled"):
        await state.emit({"type": "coder_skipped", "coder": "html", "reason": "disabled"}); return False
    await state.emit({"type": "coder_start", "coder": "html"})
    req = state.requirements or fread("requirements")
    struct = json.dumps(state.structure, ensure_ascii=False)
    current = fread("html", "")
    if not current: current = "<!DOCTYPE html>\n<html><head><meta charset='UTF-8'><title>App</title></head>\n<body>\n</body>\n</html>"
    numbered = format_code_with_lines(current)

    prompt = f"【需求】\n{req[:2000]}\n\n【结构】\n{struct[:1000]}\n\n"
    proj_ctx = read_project_context()
    if proj_ctx: prompt += f"【项目文档】\n{proj_ctx}\n\n"
    prompt += f"【当前HTML】\n{numbered}\n"
    if extra: prompt += f"\n【额外要求】\n{extra}\n"
    prompt += "\n输出编辑操作或完整HTML。"

    # Inject HTML style setting
    html_style = cfg.data.get("html_style", {})
    style_content = ""
    if html_style.get("skill_file"):
        skill_content = skills.read(html_style["skill_file"])
        if skill_content:
            style_content += f"\n\n【设计风格参考】\n{skill_content[:3000]}"
    if html_style.get("custom_prompt"):
        style_content += f"\n\n【风格要求】\n{html_style['custom_prompt']}"
    if style_content:
        prompt += style_content

    feedback = ""; code = current; consecutive_errors = 0; skill_hint = ""

    for att in range(1, cfg.max_retries + 1):
        full_prompt = prompt
        if feedback: full_prompt += f"\n【错误】\n{feedback}\n"
        if skill_hint: full_prompt += f"\n【参考：相关技能文档】\n{skill_hint}\n"
        try: client = cfg.get_client_for("html_coder")
        except ConnectionError as e:
            await state.emit({"type": "error", "message": str(e)}); return False
        llm_out = ""
        try:
            async for ev in client.stream_chat(messages=[{"role": "user", "content": full_prompt}], system_prompt=agent.get("system_prompt", ""), temperature=0.4, max_tokens=agent.get("token_limit", 4096)):
                if state.stop: break
                if ev["type"] == "token": llm_out += ev["content"]; await state.emit({"type": "coder_token", "coder": "html", "token": ev["content"]})
            state.budget.add(client.usage["prompt"] + client.usage["completion"])
        except Exception as e:
            err_msg = fmt_error(e, agent.get("model", ""))
            await state.emit({"type": "error", "message": f"HTML ({att}): {err_msg}"})
            if any(s in str(e) for s in ["500","502","503","529","Unknown error"]): continue
            return False
        finally: await client.close()

        llm_out = _strip_think_tags(llm_out)
        ops, full_code = parse_edit_ops(llm_out)
        if ops:
            new_code = apply_edit_ops(code, ops)
            await state.emit({"type": "edits_applied", "coder": "html", "count": len(ops), "line_count": len(new_code.splitlines())})
        elif full_code:
            new_code = full_code
            await state.emit({"type": "full_rewrite", "coder": "html", "line_count": len(new_code.splitlines())})
        else:
            feedback = "无法解析输出。"; consecutive_errors += 1
            if consecutive_errors >= 2 and not skill_hint:
                hint = search_skills_for_error("html parse format")
                if hint: skill_hint = hint; await state.emit({"type": "skill_search", "coder": "html", "reason": "连续解析失败", "found": True})
            continue

        diff = compute_diff_events(code, new_code)
        s = diff_summary(diff)
        await state.emit({"type": "diff_header", "coder": "html", **s})
        for ev in diff: await state.emit(ev)
        lc = new_code.lower()
        if "<html" in lc or "<!doctype" in lc:
            code = new_code; fwrite("html", code); state.code["html"] = code
            await state.emit({"type": "coder_complete", "coder": "html", "lines": len(code.splitlines()), "preview": code.splitlines()[:60]})
            tprint(f"  {T.G}[{ts()}] ✓ HTML ({len(code.splitlines())} lines){ T.RST}")
            return True
        feedback = "输出不含有效 HTML 结构"; code = new_code; consecutive_errors += 1
        if consecutive_errors >= 2 and not skill_hint:
            hint = search_skills_for_error("html structure doctype")
            if hint: skill_hint = hint; await state.emit({"type": "skill_search", "coder": "html", "reason": "HTML 结构错误", "found": True})

    await state.emit({"type": "coder_failed", "coder": "html"})
    return False


# ═══════════════ Skill Generation ═══════════════

async def generate_skill(error_info: str):
    try: client = cfg.get_client_for("project_manager")
    except ConnectionError as e:
        await state.emit({"type": "error", "message": str(e)}); return
    prompt = (f"根据以下编程错误，生成一个技能参考文件。\n\n【错误信息】\n{error_info[:1000]}\n\n"
              f"输出 Markdown 格式，包含 YAML front matter（keywords, title）和正文。")
    content = ""
    try:
        async for ev in client.stream_chat(messages=[{"role": "user", "content": prompt}], system_prompt="技术文档编写者。输出 Markdown skill 文件。", temperature=0.3, max_tokens=2000):
            if ev["type"] == "token": content += ev["content"]
        state.budget.add(client.usage["prompt"] + client.usage["completion"])
    finally: await client.close()
    safe_name = re.sub(r"[^\w]", "_", error_info[:30]).strip("_").lower()
    filename = f"skill_{safe_name}_{int(time.time())}.md"
    skills.write(filename, content)
    await state.emit({"type": "skill_generated", "filename": filename, "content": content})
    tprint(f"  {T.G}[{ts()}] ✓ Skill: {filename}{ T.RST}")


# ═══════════════ Lifespan Context Manager ═══════════════

@asynccontextmanager
async def lifespan(app):
    # ── STARTUP ──
    # ── Plugins ──
    if cfg.is_plugins_enabled():
        await plugin_engine.on_startup()
        count = len(plugin_engine.list_plugins())
        tprint(f"  {T.G}[{ts()}] 🧩 Plugins loaded: {count}{T.RST}")

    # ── Sandbox auto-create ──
    sb = sandbox_manager.get_sandbox()
    if sb.status == SandboxStatus.NOT_CREATED and (BASE_DIR / cfg.sandbox_dir).exists():
        await sb._verify_and_repair()
        state.sandbox_status = sb.status.value
        tprint(f"  {T.G}[{ts()}] 📦 Sandbox restored: {sb.status.value}{T.RST}")

    # ── Multi-file workspace auto-load ──
    result = multi_file_mgr.load_from_workspace()
    if result:
        state.project_files = result.get_all_code()
        state.project_entry = result.entry_point
        tprint(f"  {T.G}[{ts()}] 📁 Workspace loaded: {len(result.files)} files{T.RST}")

    # ── Breakpoint auto-save + heartbeat monitor ──
    def _get_bp_state():
        return {
            "phase": state.phase.value,
            "conversation": state.conversation,
            "requirements": state.requirements,
            "structure": state.structure,
            "code": state.code,
            "review_findings": state.review_findings,
            "budget": state.budget.summary(),
            "files_content": state.project_files,
        }

    async def _bp_emit(ev):
        await state.emit(ev)

    await breakpoint_mgr.start_auto_save(_get_bp_state, _bp_emit)
    snapshot_count = len(breakpoint_mgr.snapshots)
    tprint(f"  {T.G}[{ts()}] 💾 Breakpoint recovery active ({snapshot_count} snapshots){T.RST}")

    # Create default session
    if storage:
        default_sid = storage.new_session("Default Session")
        state.session_id = default_sid
        tprint(f"  {T.G}[{ts()}] ✓ Default session: {default_sid[:8]}{T.RST}")

    yield

    # ── SHUTDOWN ──
    # ── Save final snapshot ──
    breakpoint_mgr.take_snapshot(
        phase=state.phase.value,
        conversation=state.conversation,
        requirements=state.requirements,
        structure=state.structure,
        code=state.code,
        review_findings=state.review_findings,
        budget=state.budget.summary(),
        files_content=state.project_files,
    )

    # ── Stop auto-save ──
    breakpoint_mgr.stop_auto_save()

    # ── Save PM state ──
    if pm_manager and storage:
        pm_manager.save(storage)

    # ── Close web searcher ──
    if web_searcher:
        await web_searcher.close()

    # ── Close storage ──
    if storage:
        storage.close()

    # ── Plugins ──
    if cfg.is_plugins_enabled():
        await plugin_engine.on_shutdown()
        tprint(f"  {T.Y}[{ts()}] 🧩 Plugins unloaded{ T.RST}")

    tprint(f"  {T.Y}[{ts()}] 💾 Final snapshot saved{T.RST}")


# ═══════════════ FastAPI App ═══════════════

app = FastAPI(title="cx2118 Script Weaver", version="9.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ═══════════════ Session Management API ═══════════════

@app.get("/api/sessions")
async def list_sessions():
    if not storage: return {"sessions": []}
    return {"sessions": storage.list_sessions()}

@app.post("/api/sessions/new")
async def new_session(name: str = "New Session"):
    if not storage: return {"error": "Storage not available"}
    sid = storage.new_session()
    storage.rename_session(sid, name)
    return {"session_id": sid, "name": name}

@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    if not storage: return {"error": "Storage not available"}
    data = storage.load_session(sid)
    return data or {"error": "Session not found"}

@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    if not storage: return {"error": "Storage not available"}
    storage.delete_session(sid)
    return {"ok": True}

@app.post("/api/sessions/{sid}/rename")
async def rename_session(sid: str, name: str):
    if not storage: return {"error": "Storage not available"}
    storage.rename_session(sid, name)
    return {"ok": True}

@app.get("/api/stream")
async def sse():
    return StreamingResponse(state.stream(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

@app.get("/api/state")
async def get_state():
    return {"phase": state.phase.value, "budget": state.budget.summary(),
            "structure": state.structure, "requirements": state.requirements,
            "matched_skills": state.matched_skills,
            "review_findings": state.review_findings,
            "auto_review": state.auto_review,
            "sandbox_status": state.sandbox_status,
            "project_files": list(state.project_files.keys()),
            "agents": {k: {"name": v.get("name",""), "enabled": v.get("enabled",True),
                "provider": v.get("provider",""), "model": v.get("model",""),
                "token_limit": v.get("token_limit",4096), "system_prompt": v.get("system_prompt","")}
                for k, v in cfg.data.get("agents", {}).items()},
            "providers": {k: {"name": v["name"], "has_key": bool(v.get("api_key","").strip()),
                "models": v.get("models",[]), "base_url": v.get("base_url",""), "default_model": v.get("default_model","")}
                for k, v in cfg.data.get("llm_providers", {}).items()}}

@app.get("/api/project")
async def get_project():
    return {"requirements": await fread_async("requirements"), "structure": await fread_async("structure", "{}")}

@app.get("/api/file")
async def get_file():
    return {"python": await fread_async("python", INITIAL_PY), "html": await fread_async("html", "")}

@app.get("/api/skills")
async def list_skills(): return {"skills": skills.list_all()}
@app.get("/api/skills/pending")
async def list_pending_skills():
    pending = [s for s in skills.scan() if s.get("status") == "pending"]
    return {"skills": [{"file": s["file"], "title": s["title"], "keywords": s["keywords"]} for s in pending]}
@app.get("/api/skills/{filename}")
async def read_skill(filename): return {"filename": filename, "content": skills.read(filename)}
@app.post("/api/skills")
async def save_skill(request: Request):
    body = await request.json()
    filename = body.get("filename", "")
    content = body.get("content", "")
    if not filename or not content:
        return JSONResponse({"error": "filename and content required"}, 400)
    # Write directly to skills directory
    skill_path = skills.dir / filename
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(skill_path.write_text, content, "utf-8")
    skills._cache = None
    tprint(f"  {T.G}[{ts()}] ✓ Skill saved: {filename}{T.RST}")
    return {"status": "ok"}
@app.post("/api/skills/approve")
async def approve_skill(request: Request):
    body = await request.json()
    filename = body.get("filename", "")
    if not filename or not filename.startswith("pending/"):
        return JSONResponse({"error": "filename must start with pending/"}, 400)
    pending_path = skills.pending_dir / filename.replace("pending/", "")
    if not pending_path.exists():
        return JSONResponse({"error": f"pending skill not found: {filename}"}, 404)
    approved_path = skills.dir / pending_path.name
    import shutil
    shutil.move(str(pending_path), str(approved_path))
    skills._cache = None
    try: await state.emit({"type": "skill_approved", "filename": pending_path.name})
    except Exception: pass
    tprint(f"  {T.G}[{ts()}] ✓ Skill approved: {pending_path.name}{T.RST}")
    return {"status": "ok", "filename": pending_path.name}
@app.post("/api/skills/reject")
async def reject_skill(request: Request):
    body = await request.json()
    filename = body.get("filename", "")
    if not filename or not filename.startswith("pending/"):
        return JSONResponse({"error": "filename must start with pending/"}, 400)
    pending_path = skills.pending_dir / filename.replace("pending/", "")
    if not pending_path.exists():
        return JSONResponse({"error": f"pending skill not found: {filename}"}, 404)
    pending_path.unlink()
    skills._cache = None
    try: await state.emit({"type": "skill_rejected", "filename": filename})
    except Exception: pass
    tprint(f"  {T.Y}[{ts()}] ✗ Skill rejected: {filename}{T.RST}")
    return {"status": "ok"}

# ── Pipeline ──

@app.post("/api/pm/chat")
async def api_pm_chat(request: Request):
    body = await request.json(); msg = body.get("message","").strip()
    if not msg: return JSONResponse({"error": "empty"}, 400)
    # Auto-save to storage
    if storage and state.session_id:
        storage.save_message(state.session_id, "user", msg)
    task = asyncio.create_task(pm_chat(msg)); task.add_done_callback(_task_done)
    return {"status": "started"}

@app.post("/api/pm/approve")
async def api_pm_approve():
    task = asyncio.create_task(pm_approve()); task.add_done_callback(_task_done)
    return {"status": "started"}

@app.post("/api/director/plan")
async def api_director_plan():
    task = asyncio.create_task(director_plan()); task.add_done_callback(_task_done)
    return {"status": "started"}

@app.post("/api/coder/start")
async def api_coder_start(request: Request):
    body = await request.json()
    extra = body.get("extra", "")
    auto_review = body.get("auto_review", False)
    task = asyncio.create_task(run_coding(extra, auto_review))
    task.add_done_callback(_task_done)
    return {"status": "started"}

@app.post("/api/skill/generate")
async def api_skill_gen(request: Request):
    body = await request.json()
    task = asyncio.create_task(generate_skill(body.get("error","")))
    task.add_done_callback(_task_done)
    return {"status": "started"}

# ── Review Finding Actions ──

@app.post("/api/review/accept")
async def api_review_accept(request: Request):
    body = await request.json()
    fid = body.get("id")
    if fid is None: return JSONResponse({"error": "id required"}, 400)
    ok = await accept_finding(int(fid))
    return {"status": "ok" if ok else "not_found"}

@app.post("/api/review/reject")
async def api_review_reject(request: Request):
    body = await request.json()
    fid = body.get("id")
    if fid is None: return JSONResponse({"error": "id required"}, 400)
    ok = await reject_finding(int(fid))
    return {"status": "ok" if ok else "not_found"}

@app.post("/api/review/accept_all")
async def api_review_accept_all():
    task = asyncio.create_task(apply_all_findings())
    task.add_done_callback(_task_done)
    return {"status": "started"}

@app.post("/api/review/skip_all")
async def api_review_skip_all():
    """Reject all remaining and proceed."""
    for f in state.review_findings:
        if f["status"] == "pending": f["status"] = "rejected"
    await state.emit({"type": "review_all_handled"})
    await _proceed_to_review()
    return {"status": "ok"}

@app.post("/api/review/proceed")
async def api_review_proceed():
    """Proceed to review phase after human is done."""
    await _proceed_to_review()
    return {"status": "ok"}

@app.post("/api/stop")
async def stop():
    state.stop = True; return {"status": "ok"}

@app.post("/api/reset")
async def reset():
    state.phase = Phase.IDLE; state.conversation = []
    state.requirements = ""; state.structure = {}
    state.matched_skills = []; state.code = {"python": "", "html": ""}
    state.retries = 0; state.stop = False
    state.review_findings = []; state.auto_review = False
    state.sandbox_status = "not_created"
    state.project_files = {}; state.project_entry = "main.py"
    await state.emit({"type": "reset"})
    return {"status": "ok"}

# ── Config ──

@app.get("/api/config")
async def get_config():
    c = copy.deepcopy(cfg.data)
    for p in c.get("llm_providers", {}).values():
        p["has_key"] = bool(p.get("api_key","").strip()); p.pop("api_key", None)
    return c

@app.post("/api/config/provider")
async def update_provider(request: Request):
    body = await request.json(); name = body.pop("name","")
    if not name: return JSONResponse({"error": "name required"}, 400)
    if name not in cfg.data.get("llm_providers",{}):
        cfg.data.setdefault("llm_providers",{})[name] = {"name": name, "base_url": "", "api_key": "", "models": [], "default_model": ""}
    prov = cfg.data["llm_providers"][name]
    new_key = body.pop("api_key", None)
    if new_key is not None:
        new_key = new_key.strip()
        if new_key and "..." not in new_key: prov["api_key"] = new_key
        elif new_key == "": prov["api_key"] = ""
    for k, v in body.items(): prov[k] = v
    cfg.save(); has = bool(prov.get("api_key","").strip())
    await state.emit({"type": "config_updated", "provider": name, "has_key": has})
    return {"status": "ok", "has_key": has}

@app.post("/api/config/agent")
async def update_agent(request: Request):
    body = await request.json(); name = body.pop("name","")
    if not name or name not in cfg.data.get("agents",{}):
        return JSONResponse({"error": f"unknown agent: {name}"}, 400)
    ag = cfg.data["agents"][name]
    for k, v in body.items(): ag[k] = v
    cfg.save()
    await state.emit({"type": "config_updated", "agent": name})
    return {"status": "ok"}

@app.get("/api/html-style")
async def get_html_style():
    return cfg.data.get("html_style", {"skill_file": "", "custom_prompt": ""})

@app.post("/api/html-style")
async def update_html_style(request: Request):
    body = await request.json()
    cfg.data["html_style"] = {
        "skill_file": body.get("skill_file", ""),
        "custom_prompt": body.get("custom_prompt", ""),
    }
    cfg.save()
    try: await state.emit({"type": "config_updated", "html_style": True})
    except Exception: pass
    return {"status": "ok"}

@app.get("/api/config/export")
async def export_config():
    """Export prompt settings (agents + providers, no API keys)"""
    export = {
        "agents": {},
        "skills": skills.list_all(),
        "version": "9.3.0",
    }
    for name, agent in cfg.data.get("agents", {}).items():
        export["agents"][name] = {k: v for k, v in agent.items() if k != "api_key"}
    return export

@app.post("/api/config/import")
async def import_config(request: Request):
    """Import prompt settings (agents + skills)"""
    body = await request.json()
    imported_agents = 0
    imported_skills = 0
    agents_data = body.get("agents", {})
    skills_data = body.get("skills", [])
    # Merge agents (don't overwrite api_key)
    for name, agent in agents_data.items():
        if name in cfg.data.get("agents", {}):
            existing = cfg.data["agents"][name]
            for k, v in agent.items():
                if k != "api_key":
                    existing[k] = v
            imported_agents += 1
        else:
            cfg.data.setdefault("agents", {})[name] = agent
            imported_agents += 1
    # Import skills
    for s in skills_data:
        filename = s.get("file", "")
        content = s.get("content", "")
        if filename and content:
            skill_path = skills.dir / filename
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(skill_path.write_text, content, "utf-8")
            imported_skills += 1
    cfg.save()
    skills._cache = None
    tprint(f"  {T.G}[{ts()}] ✓ Imported: {imported_agents} agents, {imported_skills} skills{T.RST}")
    return {"status": "ok", "agents": imported_agents, "skills": imported_skills}

@app.post("/api/test")
async def test_connection(request: Request):
    body = await request.json(); pname = body.get("provider","")
    if pname:
        prov = cfg.get_provider(pname); key = prov.get("api_key","").strip()
        if not key: return {"status": "error", "error": f"{pname}: NO_API_KEY"}
        try:
            hc = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=True)
            c = AsyncOpenAI(base_url=prov.get("base_url") or None, api_key=key, http_client=hc)
            client = LLMClient(c, prov.get("default_model",""))
            resp = ""
            try:
                async for ev in client.stream_chat(messages=[{"role":"user","content":"Say OK"}], temperature=0, max_tokens=5):
                    if ev["type"]=="token": resp += ev["content"]
                    if len(resp)>10: break
            finally: await client.close()
            return {"status": "ok", "response": resp.strip()[:20]}
        except Exception as e: return {"status": "error", "error": fmt_error(e)}
    else:
        try:
            client = cfg.get_client_for("project_manager"); resp = ""
            try:
                async for ev in client.stream_chat(messages=[{"role":"user","content":"Say OK"}], temperature=0, max_tokens=5):
                    if ev["type"]=="token": resp += ev["content"]
                    if len(resp)>10: break
            finally: await client.close()
            return {"status": "ok", "response": resp.strip()[:20]}
        except Exception as e: return {"status": "error", "error": str(e)[:200]}


# ═══════════════ Plugin API ═══════════════

@app.get("/api/plugins")
async def api_list_plugins():
    """列出所有插件"""
    return {"plugins": plugin_engine.list_plugins()}

@app.get("/api/plugins/hooks")
async def api_hook_summary():
    """获取钩子点注册情况"""
    return {"hooks": plugin_engine.get_hook_summary()}

@app.post("/api/plugins/{name}/load")
async def api_load_plugin(name: str):
    """加载插件"""
    ok = await plugin_engine.load_plugin(name)
    if ok:
        await plugin_engine.enable_plugin(name)
    return {"success": ok, "state": plugin_engine.get_plugin_info(name).get("state")}

@app.post("/api/plugins/{name}/unload")
async def api_unload_plugin(name: str):
    """卸载插件"""
    ok = await plugin_engine.unload_plugin(name)
    return {"success": ok}

@app.post("/api/plugins/{name}/enable")
async def api_enable_plugin(name: str):
    """启用插件"""
    ok = await plugin_engine.enable_plugin(name)
    return {"success": ok}

@app.post("/api/plugins/{name}/disable")
async def api_disable_plugin(name: str):
    """禁用插件"""
    ok = await plugin_engine.disable_plugin(name)
    return {"success": ok}

@app.post("/api/plugins/{name}/reload")
async def api_reload_plugin(name: str):
    """热重载插件"""
    ok = await plugin_engine.reload_plugin(name)
    return {"success": ok}

@app.put("/api/plugins/{name}/config")
async def api_update_plugin_config(name: str, request: Request):
    """更新插件配置"""
    config = await request.json()
    plugin_engine.update_plugin_config(name, config)
    return {"success": True, "config": plugin_engine.get_plugin_config(name)}

@app.get("/api/plugins/{name}")
async def api_get_plugin_info(name: str):
    """获取插件详情"""
    return plugin_engine.get_plugin_info(name)

@app.get("/api/plugins/discover")
async def api_discover_plugins():
    """扫描发现新插件"""
    discovered = plugin_engine.discover_plugins()
    return {"discovered": discovered}

@app.post("/api/plugins/{name}/execute")
async def api_execute_plugin_hook(name: str, request: Request):
    """手动执行插件钩子"""
    body = await request.json()
    hook_point = body.get("hook_point", "")
    data = body.get("data", {})
    ctx = await plugin_engine.execute_hook_simple(hook_point, **data)
    return {"success": True, "cancelled": ctx.cancelled, "modified": ctx.modified, "data": ctx.data}


# ═══════════════ v4.0.0: Sandbox API ═══════════════

@app.get("/api/sandbox/status")
async def api_sandbox_status():
    """获取沙箱状态"""
    sb = sandbox_manager.get_sandbox()
    info = sb.get_info()
    state.sandbox_status = info["status"]
    await state.emit({"type": "sandbox_status", **info})
    return info

@app.post("/api/sandbox/create")
async def api_sandbox_create():
    """创建沙箱"""
    try:
        sb = sandbox_manager.get_sandbox()
        ok = await sb.create()
        state.sandbox_status = sb.status.value
        tprint(f"  {T.G}[{ts()}] 📦 Sandbox created: {sb.status.value}{T.RST}")
        await state.emit({"type": "sandbox_created", "status": sb.status.value, "info": sb.get_info()})
        return {"success": ok, "info": sb.get_info()}
    except Exception as e:
        state.sandbox_status = "error"
        return {"success": False, "error": str(e)}

@app.post("/api/sandbox/destroy")
async def api_sandbox_destroy():
    """销毁沙箱"""
    sandbox_manager.destroy_sandbox("default")
    state.sandbox_status = "destroyed"
    tprint(f"  {T.Y}[{ts()}] 📦 Sandbox destroyed{T.RST}")
    await state.emit({"type": "sandbox_destroyed"})
    return {"status": "ok"}

@app.post("/api/sandbox/install")
async def api_sandbox_install(request: Request):
    """安装包到沙箱"""
    body = await request.json()
    packages = body.get("packages", [])
    if not packages:
        return JSONResponse({"error": "packages required"}, 400)
    sb = sandbox_manager.get_sandbox()
    if sb.status != SandboxStatus.READY:
        await sb.create()
    result = await sb.install(packages)
    await state.emit({
        "type": "sandbox_install",
        "packages_installed": result.packages_installed,
        "packages_failed": result.packages_failed,
        "success": result.success,
    })
    tprint(f"  {T.G}[{ts()}] 📦 Install: {result.packages_installed} ok, {result.packages_failed} fail{T.RST}")
    return {
        "success": result.success,
        "installed": result.packages_installed,
        "failed": result.packages_failed,
        "output": result.output[:2000],
    }

@app.post("/api/sandbox/install-from-code")
async def api_sandbox_install_from_code(request: Request):
    """从代码自动检测并安装依赖"""
    body = await request.json()
    code = body.get("code", "")
    if not code.strip():
        return JSONResponse({"error": "code required"}, 400)
    sb = sandbox_manager.get_sandbox()
    if sb.status != SandboxStatus.READY:
        await sb.create()
    result = await sb.install_from_code(code)
    await state.emit({
        "type": "sandbox_auto_install",
        "packages_installed": result.packages_installed,
        "success": result.success,
    })
    return {
        "success": result.success,
        "installed": result.packages_installed,
        "failed": result.packages_failed,
        "output": result.output[:2000],
    }

@app.post("/api/sandbox/run")
async def api_sandbox_run(request: Request):
    """在沙箱中运行代码"""
    body = await request.json()
    code = body.get("code", "")
    filename = body.get("filename", "main.py")
    timeout = body.get("timeout", 30)
    if not code.strip():
        return JSONResponse({"error": "code required"}, 400)
    sb = sandbox_manager.get_sandbox()
    if sb.status != SandboxStatus.READY:
        await sb.create()
    # Auto-install from code if configured
    if cfg.sandbox_auto_install:
        await sb.install_from_code(code)
    result = await sb.run_code(code, filename, timeout=timeout)
    await state.emit({
        "type": "sandbox_run_result",
        "success": result.success,
        "exit_code": result.exit_code,
        "duration": round(result.duration, 2),
        "stdout": result.stdout[:2000],
        "stderr": result.stderr[:2000],
        "timeout": result.timeout,
    })
    tprint(f"  {T.G if result.success else T.R}[{ts()}] 📦 Run {'OK' if result.success else 'FAIL'} ({round(result.duration, 2)}s){T.RST}")
    return {
        "success": result.success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration": round(result.duration, 2),
        "timeout": result.timeout,
        "files_created": result.files_created,
    }

@app.post("/api/sandbox/run-file")
async def api_sandbox_run_file(request: Request):
    """在沙箱中运行 workspace 中的文件"""
    body = await request.json()
    filename = body.get("filename", "main.py")
    timeout = body.get("timeout", 30)
    ws = BASE_DIR / cfg.workspace
    file_path = ws / filename
    if not file_path.exists():
        return JSONResponse({"error": f"file not found: {filename}"}, 404)
    sb = sandbox_manager.get_sandbox()
    if sb.status != SandboxStatus.READY:
        await sb.create()
    result = await sb.run_file(str(file_path), timeout=timeout)
    await state.emit({
        "type": "sandbox_run_file_result",
        "filename": filename,
        "success": result.success,
        "exit_code": result.exit_code,
        "duration": round(result.duration, 2),
        "stdout": result.stdout[:2000],
        "stderr": result.stderr[:2000],
    })
    return {
        "success": result.success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration": round(result.duration, 2),
        "timeout": result.timeout,
    }

@app.post("/api/sandbox/ai-fix")
async def api_sandbox_ai_fix(request: Request):
    """AI 自动检查并修复代码"""
    body = await request.json()
    code = body.get("code", "")
    error_output = body.get("error", "")
    if not code.strip():
        return JSONResponse({"error": "code required"}, 400)
    sb = sandbox_manager.get_sandbox()
    if sb.status != SandboxStatus.READY:
        await sb.create()
    # Get an LLM client for AI fix
    try:
        llm_client = cfg.get_client_for("python_coder")
    except ConnectionError:
        return JSONResponse({"error": "python_coder agent not configured"}, 400)
    try:
        # Run code first to get error if not provided
        if not error_output:
            run_result = await sb.run_code(code)
            if run_result.success:
                return {"success": True, "message": "代码无错误", "code": code}
            error_output = run_result.stderr
        await state.emit({"type": "sandbox_ai_fix_start"})
        fixed_code, final_result = await sb.ai_check_and_fix(
            code, error_output, llm_client=llm_client, max_attempts=3,
        )
        await state.emit({
            "type": "sandbox_ai_fix_done",
            "success": final_result.success,
            "code": fixed_code,
        })
        tprint(f"  {T.G if final_result.success else T.R}[{ts()}] 📦 AI Fix {'OK' if final_result.success else 'FAIL'}{T.RST}")
        return {
            "success": final_result.success,
            "code": fixed_code,
            "stdout": final_result.stdout[:2000],
            "stderr": final_result.stderr[:2000],
            "attempts": 3,
        }
    finally:
        await llm_client.close()


# ═══════════════ v4.0.0: Multi-file API ═══════════════

@app.get("/api/files/list")
async def api_files_list():
    """列出项目文件"""
    if multi_file_mgr.project:
        return {"files": multi_file_mgr.project.list_files(), "entry": multi_file_mgr.project.entry_point}
    return {"files": list(state.project_files.keys()), "entry": state.project_entry}

@app.get("/api/files/read")
async def api_files_read(name: str = "main.py"):
    """读取文件内容"""
    if multi_file_mgr.project:
        f = multi_file_mgr.project.get_file(name)
        if f:
            return {"name": name, "content": f.content, "status": f.status.value, "line_count": f.line_count}
    # Fallback to state.project_files
    if name in state.project_files:
        content = state.project_files[name]
        return {"name": name, "content": content, "status": "loaded", "line_count": len(content.splitlines())}
    return JSONResponse({"error": f"file not found: {name}"}, 404)

@app.post("/api/files/write")
async def api_files_write(request: Request):
    """写入/更新文件"""
    body = await request.json()
    name = body.get("name", "")
    content = body.get("content", "")
    if not name:
        return JSONResponse({"error": "name required"}, 400)
    if multi_file_mgr.project:
        multi_file_mgr.update_file(name, content)
    state.project_files[name] = content
    await state.emit({"type": "file_updated", "name": name, "line_count": len(content.splitlines())})
    return {"status": "ok", "name": name, "line_count": len(content.splitlines())}

@app.post("/api/files/create")
async def api_files_create(request: Request):
    """创建新文件"""
    body = await request.json()
    name = body.get("name", "")
    content = body.get("content", "")
    if not name:
        return JSONResponse({"error": "name required"}, 400)
    if multi_file_mgr.project and name in multi_file_mgr.project.files:
        return JSONResponse({"error": f"file already exists: {name}"}, 400)
    if name in state.project_files:
        return JSONResponse({"error": f"file already exists: {name}"}, 400)
    if multi_file_mgr.project:
        ft = "python" if name.endswith(".py") else ("html" if name.endswith(".html") else "text")
        multi_file_mgr.project.add_file(name, content, file_type=ft)
    state.project_files[name] = content
    await state.emit({"type": "file_created", "name": name})
    tprint(f"  {T.G}[{ts()}] 📄 File created: {name}{T.RST}")
    return {"status": "ok", "name": name}

@app.post("/api/files/delete")
async def api_files_delete(request: Request):
    """删除文件"""
    body = await request.json()
    name = body.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, 400)
    if multi_file_mgr.project:
        multi_file_mgr.delete_file(name)
    state.project_files.pop(name, None)
    await state.emit({"type": "file_deleted", "name": name})
    return {"status": "ok", "name": name}

@app.post("/api/files/load-workspace")
async def api_files_load_workspace():
    """从 workspace 目录加载所有文件"""
    result = await asyncio.to_thread(multi_file_mgr.load_from_workspace)
    if result:
        state.project_files = result.get_all_code()
        state.project_entry = result.entry_point
        await state.emit({
            "type": "workspace_loaded",
            "file_count": len(result.files),
            "entry": result.entry_point,
        })
        tprint(f"  {T.G}[{ts()}] 📁 Workspace loaded: {len(result.files)} files{T.RST}")
        return {"status": "ok", "files": result.list_files(), "entry": result.entry_point}
    return {"status": "ok", "files": [], "entry": "main.py", "message": "No files found in workspace"}

@app.post("/api/files/save-workspace")
async def api_files_save_workspace():
    """保存所有文件到 workspace 目录"""
    if multi_file_mgr.project:
        await asyncio.to_thread(multi_file_mgr.save_to_workspace)
        count = len(multi_file_mgr.project.files)
        await state.emit({"type": "workspace_saved", "file_count": count})
        tprint(f"  {T.G}[{ts()}] 📁 Workspace saved: {count} files{T.RST}")
        return {"status": "ok", "file_count": count}
    return {"status": "ok", "message": "No project loaded"}

@app.get("/api/files/structure")
async def api_files_structure():
    """获取文件依赖图"""
    if multi_file_mgr.project:
        return {
            "dependency_graph": multi_file_mgr.project.get_dependency_graph(),
            "compile_order": multi_file_mgr.get_compile_order(),
            "summary": multi_file_mgr.project.get_summary(),
        }
    return {"dependency_graph": {}, "compile_order": [], "summary": {}}


# ═══════════════ v4.0.0: Tool Manager API ═══════════════

@app.get("/api/tools/search")
async def api_tools_search(q: str = ""):
    """阶段1: 搜索工具名称"""
    if not q:
        return JSONResponse({"error": "q parameter required"}, 400)
    results = tool_registry.search(q)
    return {
        "query": q,
        "results": [
            {
                "name": r.name,
                "version": r.version,
                "description": r.description,
                "source": r.source.value,
                "install_command": r.install_command,
                "score": r.score,
            }
            for r in results
        ],
        "count": len(results),
    }

@app.get("/api/tools/info")
async def api_tools_info(name: str = ""):
    """阶段2: 获取工具完整参数"""
    if not name:
        return JSONResponse({"error": "name parameter required"}, 400)
    params = tool_registry.get_full_params(name)
    if not params:
        return JSONResponse({"error": f"tool not found: {name}"}, 404)
    return params

@app.get("/api/tools/list")
async def api_tools_list(category: str = ""):
    """列出所有工具"""
    tools = tool_registry.list_tools(category)
    categories = tool_registry.list_categories()
    return {"tools": tools, "categories": categories, "count": len(tools)}

@app.post("/api/tools/install")
async def api_tools_install(request: Request):
    """安装工具"""
    body = await request.json()
    name = body.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, 400)
    result = await tool_installer.install_tool(name)
    await state.emit({"type": "tool_install_result", **result})
    tprint(f"  {T.G if result['success'] else T.R}[{ts()}] 🔧 Tool install {name}: {result['message']}{T.RST}")
    return result

@app.post("/api/tools/batch-install")
async def api_tools_batch_install(request: Request):
    """批量安装工具"""
    body = await request.json()
    names = body.get("names", [])
    if not names:
        return JSONResponse({"error": "names required"}, 400)
    results = await tool_installer.batch_install(names)
    success_count = sum(1 for r in results if r["success"])
    await state.emit({
        "type": "tool_batch_install_result",
        "total": len(names),
        "success": success_count,
        "results": results,
    })
    tprint(f"  {T.G}[{ts()}] 🔧 Batch install: {success_count}/{len(names)} ok{T.RST}")
    return {"results": results, "total": len(names), "success": success_count}


# ═══════════════ v4.0.0: Dispatch API ═══════════════

@app.get("/api/dispatch/status")
async def api_dispatch_status():
    """获取调度器状态"""
    status = dispatcher.get_status()
    status["breakpoints"] = dispatcher.list_breakpoints()
    return status

@app.post("/api/dispatch/start")
async def api_dispatch_start(request: Request):
    """启动项目调度"""
    body = await request.json()
    requirements = body.get("requirements", "")
    structure = body.get("structure", None)
    if not requirements.strip():
        return JSONResponse({"error": "requirements required"}, 400)
    task = asyncio.create_task(dispatcher.dispatch(requirements, structure))
    task.add_done_callback(_task_done)
    await state.emit({"type": "dispatch_started"})
    tprint(f"  {T.B}[{ts()}] 🚀 Dispatch started{T.RST}")
    return {"status": "started"}

@app.post("/api/dispatch/stop")
async def api_dispatch_stop():
    """停止调度"""
    dispatcher.stop()
    await state.emit({"type": "dispatch_stopped"})
    tprint(f"  {T.Y}[{ts()}] 🛑 Dispatch stopped{T.RST}")
    return {"status": "ok"}

@app.post("/api/dispatch/resume")
async def api_dispatch_resume(request: Request):
    """从断点恢复调度"""
    body = await request.json()
    bp_index = body.get("bp_index", -1)
    if not dispatcher.breakpoints:
        return JSONResponse({"error": "no breakpoints available"}, 400)
    task = asyncio.create_task(dispatcher.resume_from_breakpoint(bp_index))
    task.add_done_callback(_task_done)
    await state.emit({"type": "dispatch_resumed", "bp_index": bp_index})
    tprint(f"  {T.B}[{ts()}] ▶️ Dispatch resumed from breakpoint #{bp_index}{T.RST}")
    return {"status": "started", "bp_index": bp_index}

@app.get("/api/dispatch/breakpoints")
async def api_dispatch_breakpoints():
    """列出调度器断点"""
    return {"breakpoints": dispatcher.list_breakpoints()}


# ═══════════════ v4.0.0: Breakpoint Recovery API ═══════════════

@app.get("/api/breakpoint/list")
async def api_breakpoint_list():
    """列出所有快照"""
    return {"snapshots": breakpoint_mgr.list_snapshots(), "count": len(breakpoint_mgr.snapshots)}

@app.get("/api/breakpoint/latest")
async def api_breakpoint_latest():
    """获取最新快照"""
    snapshot = breakpoint_mgr.get_latest_snapshot()
    if not snapshot:
        return JSONResponse({"error": "no snapshots available"}, 404)
    return {
        "id": snapshot.id,
        "timestamp": snapshot.timestamp,
        "phase": snapshot.phase,
        "conversation_length": len(snapshot.conversation),
        "requirements_preview": snapshot.requirements[:200],
        "files": list(snapshot.files_content.keys()),
        "has_requirements": bool(snapshot.requirements),
        "has_structure": bool(snapshot.structure),
    }

@app.post("/api/breakpoint/recover")
async def api_breakpoint_recover(request: Request):
    """从快照恢复"""
    body = await request.json()
    snapshot_id = body.get("snapshot_id", "")
    info = await asyncio.to_thread(breakpoint_mgr.get_recovery_info, snapshot_id)
    if not info.get("recoverable"):
        return JSONResponse({"error": info.get("reason", "not recoverable")}, 400)
    snapshot = breakpoint_mgr.get_snapshot(snapshot_id)
    if not snapshot:
        # Try latest
        snapshot = breakpoint_mgr.get_latest_snapshot()
    if not snapshot:
        return JSONResponse({"error": "snapshot not found"}, 404)
    # Restore state
    state.phase = Phase(snapshot.phase) if snapshot.phase in [p.value for p in Phase] else Phase.IDLE
    state.conversation = list(snapshot.conversation)
    state.requirements = snapshot.requirements
    state.structure = snapshot.structure
    state.code = snapshot.code or {"python": "", "html": ""}
    state.review_findings = list(snapshot.review_findings)
    state.project_files = dict(snapshot.files_content)
    await state.emit({
        "type": "breakpoint_recovered",
        "snapshot_id": snapshot.id,
        "phase": state.phase.value,
    })
    tprint(f"  {T.G}[{ts()}] ♻️ Recovered from snapshot {snapshot.id}{T.RST}")
    return {
        "status": "ok",
        "snapshot_id": snapshot.id,
        "phase": state.phase.value,
        "conversation_length": len(state.conversation),
        "files": list(state.project_files.keys()),
    }

@app.post("/api/breakpoint/heartbeat")
async def api_breakpoint_heartbeat():
    """发送心跳"""
    breakpoint_mgr.heartbeat()
    return {"status": "ok", "timestamp": time.time()}

@app.get("/api/breakpoint/heartbeat-status")
async def api_breakpoint_heartbeat_status():
    """获取心跳状态"""
    return breakpoint_mgr.get_heartbeat_status()


# ═══════════════ Workflow API ═══════════════

@app.get("/api/workflows")
async def api_list_workflows(sid: str = ""):
    if not workflow_engine or not storage: return {"workflows": []}
    session_id = sid or state.session_id or DEFAULT_SESSION
    if not session_id: return {"workflows": []}
    return {"workflows": await workflow_engine.list_workflows(session_id)}

@app.post("/api/workflows/new")
async def api_create_workflow(name: str = "New Workflow", sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    if not session_id:
        session_id = storage.new_session()
        state.session_id = session_id
    wf = await workflow_engine.create_workflow(session_id, name)
    return {"workflow": wf}

@app.get("/api/workflows/{wid}")
async def api_get_workflow(wid: str, sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    wf = await workflow_engine.get_workflow(session_id, wid)
    return {"workflow": wf} if wf else {"error": "Workflow not found"}

@app.delete("/api/workflows/{wid}")
async def api_delete_workflow(wid: str, sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    await workflow_engine.delete_workflow(session_id, wid)
    return {"ok": True}

@app.post("/api/workflows/{wid}/nodes")
async def api_add_node(wid: str, req: Request):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    body = await req.json()
    session_id = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    node = await workflow_engine.add_node(
        session_id, wid,
        node_type=body.get("type", "prompt"),
        x=body.get("x", 100),
        y=body.get("y", 100),
        config=body.get("config", {})
    )
    return {"node": node}

@app.put("/api/workflows/{wid}/nodes/{nid}")
async def api_update_node(wid: str, nid: str, req: Request):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    body = await req.json()
    session_id = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    node = await workflow_engine.update_node(session_id, wid, nid, body)
    return {"node": node} if node else {"error": "Node not found"}

@app.delete("/api/workflows/{wid}/nodes/{nid}")
async def api_remove_node(wid: str, nid: str, sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    await workflow_engine.remove_node(session_id, wid, nid)
    return {"ok": True}

@app.post("/api/workflows/{wid}/connections")
async def api_add_connection(wid: str, req: Request):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    body = await req.json()
    session_id = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    conn = await workflow_engine.add_connection(
        session_id, wid,
        source_node=body["source_node"],
        source_port=body.get("source_port", "output"),
        target_node=body["target_node"],
        target_port=body.get("target_port", "input"),
        conn_type=body.get("connection_type", "any")
    )
    return {"connection": conn}

@app.delete("/api/workflows/{wid}/connections/{cid}")
async def api_remove_connection(wid: str, cid: str, sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Workflow engine not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    await workflow_engine.remove_connection(session_id, wid, cid)
    return {"ok": True}

@app.get("/api/workflow/{wid}/execute")
async def api_execute_workflow_v7(wid: str, sid: str = ""):
    if not workflow_engine or not storage:
        return EventSourceResponse({"error": "not available"})
    session_id = sid or state.session_id or DEFAULT_SESSION

    async def event_gen():
        try:
            async for ev in workflow_engine.execute(session_id, wid):
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return EventSourceResponse(event_gen())

@app.post("/api/workflow/{wid}/stop")
async def api_stop_workflow_v7(wid: str):
    if workflow_engine:
        workflow_engine.stop_workflow(wid)
    return {"ok": True}

@app.post("/api/files/upload")
async def api_upload_file(req: Request):
    if not storage: return {"error": "Storage not available"}
    session_id = state.session_id or DEFAULT_SESSION
    if not session_id:
        session_id = storage.new_session()
        state.session_id = session_id

    form = await req.form()
    files = []
    for key in form:
        if isinstance(form[key], UploadFile):
            f = form[key]
            data = await f.read()
            fname = f.filename or f"upload_{uuid.uuid4().hex[:8]}"
            storage.save_file(session_id, fname, data, f.content_type or "")
            files.append({"name": fname, "size": len(data), "type": f.content_type or ""})

    return {"files": files, "session_id": session_id}

@app.get("/api/files")
async def api_list_files(sid: str = ""):
    if not storage: return {"files": []}
    session_id = sid or state.session_id or DEFAULT_SESSION
    return {"files": storage.list_files(session_id)}

@app.get("/api/files/{fname}")
async def api_get_file(fname: str, sid: str = ""):
    if not storage: return {"error": "Storage not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    data, mime = storage.load_file(session_id, fname)
    if data is None: return {"error": "File not found"}
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

@app.delete("/api/files/{fname}")
async def api_delete_file(fname: str, sid: str = ""):
    if not storage: return {"error": "Storage not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    storage.delete_file(session_id, fname)
    return {"ok": True}

@app.get("/api/snapshots")
async def api_list_snapshots(sid: str = ""):
    if not storage: return {"snapshots": []}
    session_id = sid or state.session_id or DEFAULT_SESSION
    return {"snapshots": storage.list_snapshots(session_id)}

@app.post("/api/snapshots/save")
async def api_save_snapshot(sid: str = ""):
    if not storage: return {"error": "Storage not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    storage.save_snapshot(session_id)
    return {"ok": True}

@app.post("/api/snapshots/{sid}/restore")
async def api_restore_snapshot(sid: str):
    if not storage: return {"error": "Storage not available"}
    data = storage.load_snapshot(sid)
    if data:
        return {"snapshot": data}
    return {"error": "Snapshot not found"}


# ═══════════════ v9.0.0: Polling API ═══════════════

@app.get("/api/poll")
async def poll_state(last_ts: float = 0):
    """轮询端点：前端每隔12秒调用一次，获取自上次以来的所有新事件"""
    events = [ev for ev in state.logs if ev.get("ts", 0) > last_ts]
    if not events:
        return {"events": [], "current_ts": time.time(), "phase": state.phase.value}
    # Return last 200 events since last_ts
    events = events[-200:]
    return {
        "events": events,
        "current_ts": time.time(),
        "phase": state.phase.value,
        "conversation_len": len(state.conversation),
        "requirements_preview": state.requirements[:200] if state.requirements else "",
        "has_structure": bool(state.structure),
        "code_types": list(state.code.keys()),
    }


# ═══════════════ v9.0.0: PM Agent API ═══════════════

@app.get("/api/pm/projects")
async def api_pm_list_projects():
    if not pm_manager: return {"projects": []}
    return {"projects": pm_manager.list_projects()}

@app.post("/api/pm/projects/new")
async def api_pm_create_project(request: Request):
    if not pm_manager: return {"error": "PM not available"}
    body = await request.json()
    project = pm_manager.create_project(body.get("name", "New Project"), body.get("description", ""))
    if storage:
        pm_manager.save(storage)
    return {"project": project.to_dict()}

@app.get("/api/pm/projects/{pid}")
async def api_pm_get_project(pid: str):
    if not pm_manager: return {"error": "PM not available"}
    project = pm_manager.get_project(pid)
    if not project: return {"error": "Project not found"}
    return {"project": project.to_dict()}

@app.delete("/api/pm/projects/{pid}")
async def api_pm_delete_project(pid: str):
    if not pm_manager: return {"error": "PM not available"}
    ok = pm_manager.delete_project(pid)
    if ok and storage: pm_manager.save(storage)
    return {"ok": ok}

@app.post("/api/pm/projects/{pid}/activate")
async def api_pm_activate_project(pid: str):
    if not pm_manager: return {"error": "PM not available"}
    pm_manager.set_active(pid)
    project = pm_manager.get_project(pid)
    if not project:
        return {"error": "Project not found"}
    # Load workspace files into project (read-only snapshot for display)
    ws = str(BASE_DIR / cfg.workspace)
    if os.path.exists(ws):
        for f in os.listdir(ws):
            if f.startswith(".") or f == "skills":
                continue
            fp = os.path.join(ws, f)
            if os.path.isfile(fp):
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        project.files[f] = fh.read()
                except Exception:
                    pass
    # Do NOT overwrite workspace files — Plan and Build share the same workspace
    return {
        "ok": True,
        "project": project.summary(),
        "messages": list(project.pm_conversation),
        "workspace": os.path.basename(ws),
    }

@app.post("/api/pm/projects/{pid}/chat")
async def api_pm_project_chat(pid: str, request: Request):
    """PM Agent 模式对话 — 项目负责人与AI对接"""
    if not pm_manager: return {"error": "PM not available"}
    body = await request.json()
    msg = body.get("message", "").strip()
    model_override = body.get("model", "").strip()
    provider_override = body.get("provider", "").strip()
    if not msg: return JSONResponse({"error": "empty"}, 400)
    project = pm_manager.get_project(pid)
    if not project: return {"error": "Project not found"}
    sb = sandbox_manager.get_sandbox() if sandbox_manager else None
    agent = AgentMode(project, sandbox=sb)
    async def _emit_cb(ev):
        await state.emit(ev)
    try:
        if model_override or provider_override:
            # 自定义模型：创建临时客户端
            pname = provider_override or cfg.get_agent("project_manager").get("provider", "deepseek")
            p = cfg.get_provider(pname)
            key = p.get("api_key", "").strip()
            base_url = p.get("base_url", "").strip()
            model = model_override or p.get("default_model", "")
            import httpx
            hc = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=30), follow_redirects=True)
            from openai import AsyncOpenAI
            c = AsyncOpenAI(base_url=base_url or None, api_key=key, http_client=hc)
            client = LLMClient(c, model, a.get("token_limit", 8000) if (a := cfg.get_agent("project_manager")) else 8000)
        else:
            client = cfg.get_client_for("project_manager")
        resp = await agent.chat(msg, client, emit_callback=_emit_cb)
        await client.close()
    except Exception as e:
        err_msg = f"PM Agent: {fmt_error(e)}"
        if "Connection" in str(e) or "connect" in str(e).lower():
            _pm_agent_cfg = cfg.get_agent("project_manager")
            err_msg += f" (provider={_pm_agent_cfg.get('provider','?')}, model={_pm_agent_cfg.get('model','?')}, base_url={cfg.get_provider(_pm_agent_cfg.get('provider','')).get('base_url','')})"
        await state.emit({"type": "error", "message": err_msg})
        return {"error": str(e)}
    if storage:
        storage.save_message(state.session_id or "", "pm_agent", resp)
    # 保存项目状态（包括对话历史）
    if pm_manager and storage:
        pm_manager.save(storage)
    return {"message": resp}

@app.post("/api/pm/projects/{pid}/files/read")
async def api_pm_read_file(pid: str, request: Request):
    if not pm_manager: return {"error": "PM not available"}
    body = await request.json()
    filename = body.get("filename", "")
    project = pm_manager.get_project(pid)
    if not project: return {"error": "Project not found"}
    content = project.get_file(filename)
    if content is None: return {"error": "File not found"}
    return {"filename": filename, "content": content}

@app.post("/api/pm/projects/{pid}/files/write")
async def api_pm_write_file(pid: str, request: Request):
    if not pm_manager: return {"error": "PM not available"}
    body = await request.json()
    filename = body.get("filename", "")
    content = body.get("content", "")
    project = pm_manager.get_project(pid)
    if not project: return {"error": "Project not found"}
    project.update_file(filename, content)
    if storage: pm_manager.save(storage)
    return {"ok": True, "filename": filename}

@app.post("/api/pm/projects/{pid}/approve-file")
async def api_pm_approve_file(pid: str, request: Request):
    """Approve a pending file modification in Plan mode"""
    if not pm_manager: return {"error": "PM not available"}
    body = await request.json()
    filename = body.get("filename", "")
    content = body.get("content", "")
    action = body.get("action", "apply")  # apply / reject
    project = pm_manager.get_project(pid)
    if not project: return {"error": "Project not found"}
    if action == "apply":
        project.update_file(filename, content)
        if storage: pm_manager.save(storage)
        return {"ok": True, "filename": filename}
    return {"ok": True, "action": "rejected"}


# ═══════════════ Workspace Archive API ═══════════════

@app.get("/api/workspace/info")
async def api_workspace_info():
    """获取当前工作区信息"""
    ws = str(BASE_DIR / cfg.workspace)
    files = []
    total_size = 0
    if os.path.exists(ws):
        for f in sorted(os.listdir(ws)):
            if f.startswith("."): continue
            fp = os.path.join(ws, f)
            if os.path.isfile(fp):
                sz = os.path.getsize(fp)
                files.append({"name": f, "size": sz})
                total_size += sz
    return {"name": os.path.basename(ws), "path": ws, "files": files, "total_size": total_size, "file_count": len(files)}

@app.get("/api/workspace/archives")
async def api_list_archives():
    """列出所有归档"""
    archive_dir = str(BASE_DIR / ".workspace_backups")
    archives = []
    if os.path.exists(archive_dir):
        for name in sorted(os.listdir(archive_dir)):
            ap = os.path.join(archive_dir, name)
            if os.path.isdir(ap) and name.startswith("backup_"):
                # Get file count and total size
                file_count = 0
                total_size = 0
                for root, dirs, files in os.walk(ap):
                    for f in files:
                        if f.startswith("."): continue
                        fp = os.path.join(root, f)
                        total_size += os.path.getsize(fp)
                        file_count += 1
                # Parse timestamp from name
                ts_str = name.replace("backup_", "")
                try:
                    ts_val = int(ts_str)
                    dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_val))
                except (ValueError, OverflowError):
                    dt = name
                archives.append({
                    "name": name,
                    "display_name": dt,
                    "file_count": file_count,
                    "total_size": total_size,
                    "timestamp": ts_val if 'ts_val' in dir() else 0,
                })
    archives.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return {"archives": archives}

@app.post("/api/workspace/archive")
async def api_archive_workspace():
    """将当前工作区归档"""
    import shutil
    ws = str(BASE_DIR / cfg.workspace)
    archive_dir = str(BASE_DIR / ".workspace_backups")
    os.makedirs(archive_dir, exist_ok=True)
    backup_name = f"backup_{int(time.time())}"
    backup_path = os.path.join(archive_dir, backup_name)
    try:
        if os.path.exists(ws) and os.listdir(ws):
            shutil.copytree(ws, backup_path, dirs_exist_ok=True)
            return {"ok": True, "name": backup_name}
    except Exception as e:
        return {"error": str(e)}
    return {"error": "workspace is empty"}

@app.post("/api/workspace/new")
async def api_new_workspace():
    """创建新工作区（先归档当前的）"""
    import shutil
    ws = str(BASE_DIR / cfg.workspace)
    archive_dir = str(BASE_DIR / ".workspace_backups")
    os.makedirs(archive_dir, exist_ok=True)
    # Archive current workspace first
    backup_name = f"backup_{int(time.time())}"
    backup_path = os.path.join(archive_dir, backup_name)
    try:
        if os.path.exists(ws) and os.listdir(ws):
            shutil.copytree(ws, backup_path, dirs_exist_ok=True)
    except Exception:
        pass
    # Clear workspace
    if os.path.exists(ws):
        for f in os.listdir(ws):
            if f.startswith(".") or f == "skills":
                continue
            fp = os.path.join(ws, f)
            if os.path.isfile(fp):
                os.remove(fp)
            elif os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
    return {"ok": True, "archived": backup_name}

@app.post("/api/workspace/load/{name}")
async def api_load_archive(name: str):
    """从归档加载工作区"""
    import shutil
    ws = str(BASE_DIR / cfg.workspace)
    archive_dir = str(BASE_DIR / ".workspace_backups")
    backup_path = os.path.join(archive_dir, name)
    if not os.path.exists(backup_path):
        return {"error": f"Archive not found: {name}"}
    # Archive current workspace first
    current_backup = f"backup_{int(time.time())}"
    current_backup_path = os.path.join(archive_dir, current_backup)
    try:
        if os.path.exists(ws) and os.listdir(ws):
            shutil.copytree(ws, current_backup_path, dirs_exist_ok=True)
    except Exception:
        pass
    # Clear workspace and copy archive contents
    if os.path.exists(ws):
        for f in os.listdir(ws):
            if f.startswith(".") or f == "skills":
                continue
            fp = os.path.join(ws, f)
            if os.path.isfile(fp):
                os.remove(fp)
            elif os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
    # Copy archive files to workspace
    file_count = 0
    for root, dirs, files in os.walk(backup_path):
        for f in files:
            if f.startswith("."): continue
            src = os.path.join(root, f)
            rel = os.path.relpath(src, backup_path)
            dst = os.path.join(ws, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            file_count += 1
    # Sync workspace files into active PM project
    if pm_manager and pm_manager.get_active():
        project = pm_manager.get_active()
        if os.path.exists(ws):
            for f in os.listdir(ws):
                if f.startswith(".") or f == "skills":
                    continue
                fp = os.path.join(ws, f)
                if os.path.isfile(fp):
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                            project.files[f] = fh.read()
                    except Exception:
                        pass
    return {"ok": True, "loaded": name, "file_count": file_count, "archived_current": current_backup}

@app.delete("/api/workspace/archive/{name}")
async def api_delete_archive(name: str):
    """删除归档"""
    import shutil
    archive_dir = str(BASE_DIR / ".workspace_backups")
    backup_path = os.path.join(archive_dir, name)
    if not os.path.exists(backup_path):
        return {"error": f"Archive not found: {name}"}
    try:
        shutil.rmtree(backup_path)
        return {"ok": True, "deleted": name}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════ v9.0.0: Web Search API ═══════════════

@app.post("/api/search")
async def api_web_search(request: Request):
    if not web_searcher: return {"error": "Web search not configured"}
    body = await request.json()
    query = body.get("query", "").strip()
    if not query: return JSONResponse({"error": "query required"}, 400)
    try:
        results = await web_searcher.search(query)
        if storage and state.session_id:
            storage.save_search(state.session_id, query, getattr(web_searcher, 'provider', 'unknown'), [r.to_dict() for r in results])
        return {"query": query, "results": [r.to_dict() for r in results], "count": len(results)}
    except Exception as e:
        return {"error": str(e), "results": [], "count": 0}

@app.post("/api/search/crawl")
async def api_web_crawl(request: Request):
    if not web_searcher: return {"error": "Web search not configured"}
    body = await request.json()
    url = body.get("url", "").strip()
    if not url: return JSONResponse({"error": "url required"}, 400)
    try:
        content = await web_searcher.crawl_url(url)
        return {"url": url, "content": content[:10000], "length": len(content)}
    except Exception as e:
        return {"error": str(e), "content": "", "length": 0}

@app.get("/api/search/history")
async def api_search_history(limit: int = 20):
    if not storage: return {"history": []}
    return {"history": storage.load_search_history(state.session_id or "", limit)}


# ═══════════════ v9.0.0: Search Config API ═══════════════

@app.get("/api/search/config")
async def api_get_search_config():
    return cfg.data.get("web_search", {})

@app.post("/api/search/config")
async def api_set_search_config(request: Request):
    body = await request.json()
    cfg.data.setdefault("web_search", {}).update(body)
    cfg.save()
    return {"ok": True}


# ═══════════════ v9.0.0: Workflow Enhancements ═══════════════

@app.post("/api/workflows/{wid}/execute")
async def api_execute_workflow(wid: str, sid: str = ""):
    if not workflow_engine or not storage: return {"error": "Not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    async def _wf_emit(ev):
        await state.emit(ev)
    return StreamingResponse(
        workflow_engine.execute(session_id, wid, emit_callback=_wf_emit),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/workflows/{wid}/stop")
async def api_stop_workflow(wid: str):
    if not workflow_engine: return {"error": "Not available"}
    ex = workflow_engine._executions.get(wid)
    if ex: ex.stopped = True
    return {"ok": True}

@app.post("/api/workflows/import-package")
@app.post("/api/workflows/import")
async def api_import_workflow_package(request: Request):
    if not workflow_engine or not storage: return {"error": "Not available"}
    body = await request.json()
    sid = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    wid = body.get("workflow_id", "")
    zip_b64 = body.get("zip_data", "")
    if not zip_b64: return JSONResponse({"error": "zip_data required"}, 400)
    try:
        import base64
        zip_data = base64.b64decode(zip_b64)
        result = await workflow_engine.import_node_package(sid, wid, zip_data)
        return {"imported": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/workflows/{wid}/export-package")
async def api_export_workflow_package(wid: str, request: Request):
    if not workflow_engine or not storage: return {"error": "Not available"}
    body = await request.json()
    node_ids = body.get("node_ids", [])
    sid = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    try:
        zip_data = await workflow_engine.export_node_package(sid, wid, node_ids)
        import base64
        return {"zip_data": base64.b64encode(zip_data).decode(), "size": len(zip_data)}
    except Exception as e:
        return {"error": str(e)}

# Workflow folders
@app.get("/api/workflows/folders")
async def api_list_wf_folders(sid: str = "", parent_id: str = ""):
    if not workflow_engine: return {"folders": []}
    session_id = sid or state.session_id or DEFAULT_SESSION
    return {"folders": await workflow_engine.list_folders(session_id, parent_id)}

@app.post("/api/workflows/folders")
async def api_create_wf_folder(request: Request):
    if not workflow_engine: return {"error": "Not available"}
    body = await request.json()
    sid = body.get("session_id", "") or state.session_id or DEFAULT_SESSION
    folder = await workflow_engine.create_folder(sid, body.get("name", "New Folder"), body.get("parent_id", ""))
    return {"folder": folder}

@app.delete("/api/workflows/folders/{fid}")
async def api_delete_wf_folder(fid: str, sid: str = ""):
    if not workflow_engine: return {"error": "Not available"}
    session_id = sid or state.session_id or DEFAULT_SESSION
    await workflow_engine.delete_folder(session_id, fid)
    return {"ok": True}


# ═══════════════ Serve UI ═══════════════

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    p = BASE_DIR / "index.html"
    if not p.exists(): return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return HTMLResponse(await asyncio.to_thread(p.read_text, encoding="utf-8"))


# ═══════════════ Entry Point ═══════════════

if __name__ == "__main__":
    import uvicorn
    import subprocess
    port = cfg.port
    max_port_tries = 10

    for port_attempt in range(max_port_tries):
        try_port = port + port_attempt
        # Check if port is in use
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{try_port}"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                if port_attempt == 0:
                    tprint(f"  {T.Y}[{ts()}] ⚠ Port {try_port} in use by PID(s): {', '.join(pids)}, trying to kill...{T.RST}")
                    for pid in pids:
                        try:
                            os.kill(int(pid.strip()), signal.SIGKILL)
                            tprint(f"  {T.Y}[{ts()}]   Killed PID {pid.strip()}{T.RST}")
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    time.sleep(0.5)
                    continue
                else:
                    tprint(f"  {T.Y}[{ts()}] ⚠ Port {try_port} in use, trying next...{T.RST}")
                    continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try to bind
        try:
            import socket
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                test_sock.bind(("0.0.0.0", try_port))
            except OSError:
                test_sock.close()
                if port_attempt < max_port_tries - 1:
                    tprint(f"  {T.Y}[{ts()}] ⚠ Port {try_port} bind failed, trying next...{T.RST}")
                    continue
                else:
                    tprint(f"  {T.R}[{ts()}] ✗ No available port after {max_port_tries} attempts{ T.RST}")
                    sys.exit(1)
            finally:
                test_sock.close()
        except Exception:
            pass

        port = try_port
        break

    agents = cfg.data.get("agents", {})
    enabled = [k for k, v in agents.items() if v.get("enabled", True)]
    tprint(f"\n  {T.BD}{T.C}cx2118 Script Weaver v9.3.0 — Multi-Agent{ T.RST}")
    tprint(f"  {T.D}Port   : {port}{ T.RST}")
    tprint(f"  {T.D}Agents : {', '.join(enabled)}{ T.RST}")
    tprint(f"  {T.D}Skills : {cfg.skills_dir}{ T.RST}")
    tprint(f"  {T.D}Plugins: {cfg.plugins_dir}{ T.RST}")
    tprint(f"  {T.D}Sandbox: {cfg.sandbox_dir}{ T.RST}")
    tprint(f"  {T.D}Workspace: {cfg.workspace}{ T.RST}")
    tprint(f"  {T.D}Listen : http://0.0.0.0:{port}{ T.RST}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
