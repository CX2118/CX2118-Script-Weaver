#!/usr/bin/env python3
"""
storage.py — Real-time Persistent Storage for cx2118 Script Weaver v9.2
═══════════════════════════════════════════════════════════════════════
SQLite-backed storage with WAL mode for concurrent reads.
All state changes are immediately persisted — no manual save needed.

Tables:
  sessions          — session metadata (id, name, timestamps, mode)
  pipeline_state    — current pipeline phase + serialized data
  messages          — conversation messages (role, content, timestamp)
  code_files        — generated code (python, html, etc.)
  requirements      — requirements documents
  plans             — project plans (structure JSON + raw text)
  reviews           — review findings (JSON array)
  workflows         — workflow definitions (JSON)
  workflow_results  — per-node workflow execution results (JSON)
  files_meta        — uploaded file metadata (binary on disk, meta in DB)
  settings          — global key-value settings (not session-scoped)
  snapshots         — crash-recovery snapshots (full session dump)
  pm_projects       — PM project management (name, description, status, data)
  workflow_folders  — hierarchical workflow folders (name, parent_id)
  search_history    — search query history (query, provider, results)
  node_packages     — imported/exported workflow node bundles

Binary files are stored on disk at:  storage/files/{session_id}/{filename}
Database file is stored at:          storage/project.db

Usage:
    from storage import Storage

    store = Storage("/path/to/storage")
    sid = store.new_session()
    store.save_message(sid, "user", "Hello")
    store.save_code(sid, "python", "print('hi')")
    store.save_settings("provider.name", "siliconflow")
    store.save_snapshot(sid)
    store.close()
"""

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

__version__ = "9.3.0"
__all__ = ["Storage"]

# ═══════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════

logger = logging.getLogger("cx2118.storage")

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ═══════════════════════════════════════════════════════════════════════
# DDL — Table Creation
# ═══════════════════════════════════════════════════════════════════════

_DDL = """
-- Session metadata
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    mode          TEXT NOT NULL DEFAULT 'idle'
);

-- Current pipeline phase and associated data
CREATE TABLE IF NOT EXISTS pipeline_state (
    session_id    TEXT PRIMARY KEY,
    phase         TEXT NOT NULL DEFAULT 'idle',
    data          TEXT NOT NULL DEFAULT '{}',
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Conversation messages
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    timestamp     REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Generated code files (python, html, etc.)
CREATE TABLE IF NOT EXISTS code_files (
    session_id    TEXT NOT NULL,
    file_type     TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL,
    PRIMARY KEY (session_id, file_type),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Requirements documents
CREATE TABLE IF NOT EXISTS requirements (
    session_id    TEXT PRIMARY KEY,
    content       TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Project plans (structure as JSON + raw text)
CREATE TABLE IF NOT EXISTS plans (
    session_id    TEXT PRIMARY KEY,
    structure     TEXT NOT NULL DEFAULT '{}',
    raw           TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Review findings (stored as JSON array)
CREATE TABLE IF NOT EXISTS reviews (
    session_id    TEXT PRIMARY KEY,
    findings      TEXT NOT NULL DEFAULT '[]',
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Workflow definitions (JSON)
CREATE TABLE IF NOT EXISTS workflows (
    session_id    TEXT NOT NULL,
    workflow_id   TEXT NOT NULL,
    data          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (session_id, workflow_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Workflow execution results (per-node, stored as JSON)
CREATE TABLE IF NOT EXISTS workflow_results (
    session_id    TEXT NOT NULL,
    workflow_id   TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    result        TEXT NOT NULL DEFAULT '{}',
    updated_at    REAL NOT NULL,
    PRIMARY KEY (session_id, workflow_id, node_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Uploaded file metadata (binary data on disk under files/{session_id}/)
CREATE TABLE IF NOT EXISTS files_meta (
    session_id    TEXT NOT NULL,
    filename      TEXT NOT NULL,
    mime_type     TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    PRIMARY KEY (session_id, filename),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Global key-value settings (not session-scoped)
CREATE TABLE IF NOT EXISTS settings (
    key           TEXT PRIMARY KEY,
    value         TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL
);

-- Crash-recovery snapshots (full session state as JSON)
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    data          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- PM 项目管理
CREATE TABLE IF NOT EXISTS pm_projects (
    project_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    data          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- 工作流文件夹（层级结构）
CREATE TABLE IF NOT EXISTS workflow_folders (
    folder_id     TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    parent_id     TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- 搜索历史
CREATE TABLE IF NOT EXISTS search_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    query         TEXT NOT NULL,
    provider      TEXT NOT NULL DEFAULT '',
    results       TEXT NOT NULL DEFAULT '[]',
    timestamp     REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- 节点包（导入/导出的工作流节点束）
CREATE TABLE IF NOT EXISTS node_packages (
    package_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    data          TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts       ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_workflows_session ON workflows(session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_results   ON workflow_results(session_id, workflow_id);
CREATE INDEX IF NOT EXISTS idx_files_meta_session ON files_meta(session_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_session ON snapshots(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated  ON sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_pm_projects_session ON pm_projects(session_id);
CREATE INDEX IF NOT EXISTS idx_pm_projects_status ON pm_projects(status);
CREATE INDEX IF NOT EXISTS idx_wf_folders_session ON workflow_folders(session_id);
CREATE INDEX IF NOT EXISTS idx_wf_folders_parent ON workflow_folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_search_history_session ON search_history(session_id);
CREATE INDEX IF NOT EXISTS idx_node_packages_session ON node_packages(session_id);
"""


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _now() -> float:
    """Return current UTC timestamp as a float."""
    return time.time()


def _new_id() -> str:
    """Generate a short UUID (first 8 hex chars + 4 hex chars)."""
    raw = uuid.uuid4().hex
    return f"{raw[:8]}{raw[20:24]}"


def _safe_json_dumps(obj: Any) -> str:
    """Serialize an object to JSON, handling non-serializable types gracefully."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        logger.warning("JSON serialize failed: %s", exc)
        return "{}"


def _safe_json_loads(text: str, default: Any = None) -> Any:
    """Deserialize JSON, returning *default* on failure."""
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("JSON parse failed: %s", exc)
        return default if default is not None else {}


def _safe_json_loads_list(text: str) -> list:
    """Deserialize JSON expecting a list, returning [] on failure."""
    if not text:
        return []
    try:
        val = json.loads(text)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ═══════════════════════════════════════════════════════════════════════
# Storage
# ═══════════════════════════════════════════════════════════════════════

class Storage:
    """
    SQLite-backed persistent storage for cx2118 Script Weaver.

    Every mutation method writes immediately inside a transaction.
    The database uses WAL (Write-Ahead Logging) mode so readers never
    block writers and vice-versa.

    Parameters
    ----------
    base_dir : str
        Root directory for all storage.  A ``storage/`` sub-folder will be
        created inside it, holding ``project.db`` and ``files/``.
    """

    # ─── Construction & Lifecycle ───────────────────────────────────

    def __init__(self, base_dir: str):
        self._base_dir = Path(base_dir)
        self._storage_dir = self._base_dir / "storage"
        self._db_path = self._storage_dir / "project.db"
        self._files_dir = self._storage_dir / "files"
        self._lock = threading.Lock()
        self._closed = False

        # Ensure directories exist
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._files_dir.mkdir(parents=True, exist_ok=True)

        # Open (or create) the database
        self._conn = self._open_db()
        self._init_tables()

        logger.info(
            "Storage initialized  db=%s  version=%s",
            self._db_path, __version__,
        )

    def _open_db(self) -> sqlite3.Connection:
        """Open an SQLite connection with WAL mode and pragmas."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """Run the DDL to create all tables and indexes."""
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    def close(self) -> None:
        """Gracefully close the database connection."""
        if self._closed:
            return
        self._closed = True
        try:
            # Try checkpoint WAL before closing for clean shutdown
            with self._lock:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
        except Exception as exc:
            logger.warning("Error during close: %s", exc)
        logger.info("Storage closed")

    # ─── Internal helpers ────────────────────────────────────────────

    def _ensure_session(self, session_id: str) -> bool:
        """Return True if the session exists, creating it if needed (no-op if already exists)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                return True
            now = _now()
            self._conn.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at, mode) VALUES (?, '', ?, ?, 'idle')",
                (session_id, now, now),
            )
            self._conn.commit()
            return False

    def _files_path(self, session_id: str) -> Path:
        """Return the on-disk directory for a session's binary files."""
        p = self._files_dir / session_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ════════════════════════════════════════════════════════════════
    # Session Management
    # ════════════════════════════════════════════════════════════════

    def new_session(self, name: str = "") -> str:
        """
        Create a new session and return its ID.

        Parameters
        ----------
        name : str
            Optional human-readable label.

        Returns
        -------
        str
            The newly created session ID.
        """
        session_id = _new_id()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, name, created_at, updated_at, mode) VALUES (?, ?, ?, ?, 'idle')",
                (session_id, name, now, now),
            )
            self._conn.commit()
        logger.debug("Session created: %s  name=%s", session_id, name)
        return session_id

    def load_session(self, session_id: str) -> dict:
        """
        Load a session by ID.

        Returns a dict with keys: ``id``, ``name``, ``created_at``,
        ``updated_at``, ``mode``.  Returns an empty dict if not found.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, created_at, updated_at, mode FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "mode": row["mode"],
        }

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """
        List sessions ordered by most-recently-updated first.

        Parameters
        ----------
        limit : int
            Maximum number of sessions to return.
        offset : int
            Number of sessions to skip (for pagination).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, created_at, updated_at, mode FROM sessions "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "mode": r["mode"],
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and all associated data.

        Returns True if the session existed and was deleted.
        """
        # Remove binary files directory
        fp = self._files_dir / session_id
        if fp.exists():
            try:
                shutil.rmtree(fp)
                logger.debug("Removed files dir for session %s", session_id)
            except OSError as exc:
                logger.warning("Could not remove files dir for %s: %s", session_id, exc)

        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            self._conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("Session deleted: %s", session_id)
        return deleted

    def rename_session(self, session_id: str, name: str) -> bool:
        """Rename a session. Returns True on success."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET name = ?, updated_at = ? WHERE id = ?",
                (name, _now(), session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Pipeline State
    # ════════════════════════════════════════════════════════════════

    def save_pipeline_state(self, session_id: str, state: dict) -> None:
        """
        Save (or replace) the pipeline state for a session.

        Parameters
        ----------
        state : dict
            Must contain at least ``phase`` (str).  All other keys are
            serialized into the ``data`` JSON column.
        """
        self._ensure_session(session_id)
        now = _now()
        phase = state.get("phase", "idle")
        data_json = _safe_json_dumps(state)
        with self._lock:
            self._conn.execute(
                "INSERT INTO pipeline_state (session_id, phase, data, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET phase=excluded.phase, data=excluded.data, updated_at=excluded.updated_at",
                (session_id, phase, data_json, now),
            )
            # Also update the mode on the sessions table
            self._conn.execute(
                "UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?",
                (phase, now, session_id),
            )
            self._conn.commit()

    def load_pipeline_state(self, session_id: str) -> dict:
        """Load the pipeline state.  Returns ``{}`` if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT phase, data FROM pipeline_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        result = _safe_json_loads(row["data"], default={})
        # Always include the phase explicitly
        result["phase"] = row["phase"]
        return result

    # ════════════════════════════════════════════════════════════════
    # Conversation / Messages
    # ════════════════════════════════════════════════════════════════

    def save_message(self, session_id: str, role: str, content: str) -> None:
        """Append a single message to the conversation history."""
        self._ensure_session(session_id)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def save_messages_bulk(self, session_id: str, messages: list[dict]) -> None:
        """
        Append multiple messages in a single transaction for efficiency.

        Each message dict must have ``role`` and ``content`` keys.
        """
        if not messages:
            return
        self._ensure_session(session_id)
        now = _now()
        rows = [(session_id, m["role"], m["content"], now) for m in messages]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def load_conversation(self, session_id: str, limit: int = 0) -> list[dict]:
        """
        Load all messages for a session, ordered by timestamp ascending.

        Parameters
        ----------
        limit : int
            If > 0, only return the most recent N messages.
        """
        with self._lock:
            if limit > 0:
                rows = self._conn.execute(
                    "SELECT role, content, timestamp FROM messages "
                    "WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT role, content, timestamp FROM messages "
                    "WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,),
                ).fetchall()
        return [
            {"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]}
            for r in rows
        ]

    def clear_conversation(self, session_id: str) -> int:
        """
        Delete all messages for a session.

        Returns the number of deleted rows.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
            return cursor.rowcount

    # ════════════════════════════════════════════════════════════════
    # Code Files
    # ════════════════════════════════════════════════════════════════

    def save_code(self, session_id: str, file_type: str, content: str) -> None:
        """
        Save (or replace) a code file for a session.

        Parameters
        ----------
        file_type : str
            Logical type such as ``"python"``, ``"html"``, ``"css"``, etc.
        """
        self._ensure_session(session_id)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO code_files (session_id, file_type, content, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, file_type) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
                (session_id, file_type, content, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def load_code(self, session_id: str, file_type: str) -> str:
        """
        Load a code file by type.

        Returns an empty string if not found.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM code_files WHERE session_id = ? AND file_type = ?",
                (session_id, file_type),
            ).fetchone()
        return row["content"] if row else ""

    def load_all_code(self, session_id: str) -> dict[str, str]:
        """Load all code files for a session as ``{file_type: content}``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT file_type, content FROM code_files WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return {r["file_type"]: r["content"] for r in rows}

    def delete_code(self, session_id: str, file_type: str) -> bool:
        """Delete a code file.  Returns True if it existed."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM code_files WHERE session_id = ? AND file_type = ?",
                (session_id, file_type),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Requirements & Plan
    # ════════════════════════════════════════════════════════════════

    def save_requirements(self, session_id: str, content: str) -> None:
        """Save (or replace) the requirements document."""
        self._ensure_session(session_id)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO requirements (session_id, content, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
                (session_id, content, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def load_requirements(self, session_id: str) -> str:
        """Load the requirements document.  Returns ``""`` if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM requirements WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row["content"] if row else ""

    def save_plan(self, session_id: str, structure: dict, raw: str = "") -> None:
        """
        Save (or replace) the project plan.

        Parameters
        ----------
        structure : dict
            The parsed project structure (will be JSON-serialized).
        raw : str
            The raw text output from the planning agent.
        """
        self._ensure_session(session_id)
        now = _now()
        struct_json = _safe_json_dumps(structure)
        with self._lock:
            self._conn.execute(
                "INSERT INTO plans (session_id, structure, raw, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET structure=excluded.structure, raw=excluded.raw, updated_at=excluded.updated_at",
                (session_id, struct_json, raw, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def load_plan(self, session_id: str) -> dict:
        """
        Load the project plan.

        Returns ``{"structure": {...}, "raw": "..."}``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT structure, raw FROM plans WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return {"structure": {}, "raw": ""}
        return {
            "structure": _safe_json_loads(row["structure"], default={}),
            "raw": row["raw"],
        }

    # ════════════════════════════════════════════════════════════════
    # Review
    # ════════════════════════════════════════════════════════════════

    def save_review(self, session_id: str, findings: list) -> None:
        """
        Save (or replace) the review findings for a session.

        Parameters
        ----------
        findings : list
            A list of finding dicts (will be JSON-serialized).
        """
        self._ensure_session(session_id)
        now = _now()
        findings_json = _safe_json_dumps(findings)
        with self._lock:
            self._conn.execute(
                "INSERT INTO reviews (session_id, findings, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET findings=excluded.findings, updated_at=excluded.updated_at",
                (session_id, findings_json, now),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            self._conn.commit()

    def load_review(self, session_id: str) -> list:
        """
        Load review findings.

        Returns ``[]`` if not found or on parse error.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT findings FROM reviews WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return []
        return _safe_json_loads_list(row["findings"])

    # ════════════════════════════════════════════════════════════════
    # Workflows
    # ════════════════════════════════════════════════════════════════

    def save_workflow(self, session_id: str, workflow_id: str, data: dict,
                      folder_id: str = "") -> None:
        """
        Save (or replace) a workflow definition.

        Parameters
        ----------
        workflow_id : str
            Unique identifier for the workflow within the session.
        data : dict
            The complete workflow definition (will be JSON-serialized).
        folder_id : str
            Optional folder ID to associate the workflow with a folder.
        """
        self._ensure_session(session_id)
        now = _now()
        # 将 folder_id 注入到 data JSON 中以便持久化
        if folder_id:
            data = {**data, "_folder_id": folder_id}
        else:
            data = {k: v for k, v in data.items() if k != "_folder_id"}
        data_json = _safe_json_dumps(data)
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflows (session_id, workflow_id, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, workflow_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (session_id, workflow_id, data_json, now, now),
            )
            self._conn.commit()

    def load_workflow(self, session_id: str, workflow_id: str) -> dict:
        """
        Load a workflow definition.

        Returns ``{}`` if not found.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM workflows WHERE session_id = ? AND workflow_id = ?",
                (session_id, workflow_id),
            ).fetchone()
        if not row:
            return {}
        return _safe_json_loads(row["data"], default={})

    def list_workflows(self, session_id: str) -> list[dict]:
        """List all workflow summaries for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT workflow_id, data, created_at, updated_at FROM workflows "
                "WHERE session_id = ? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
        results = []
        for r in rows:
            info = {
                "id": r["workflow_id"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            try:
                data = _safe_json_loads(r["data"], default={})
                info["name"] = data.get("name", r["workflow_id"])
                info["folder_id"] = data.get("folder_id", "")
            except Exception:
                info["name"] = r["workflow_id"]
            results.append(info)
        return results

    def delete_workflow(self, session_id: str, workflow_id: str) -> bool:
        """
        Delete a workflow and all its execution results.

        Returns True if the workflow existed.
        """
        with self._lock:
            # Delete results first (they reference the workflow_id)
            self._conn.execute(
                "DELETE FROM workflow_results WHERE session_id = ? AND workflow_id = ?",
                (session_id, workflow_id),
            )
            cursor = self._conn.execute(
                "DELETE FROM workflows WHERE session_id = ? AND workflow_id = ?",
                (session_id, workflow_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def save_workflow_result(
        self, session_id: str, workflow_id: str, node_id: str, result: dict
    ) -> None:
        """
        Save (or replace) the execution result for a single workflow node.

        Parameters
        ----------
        node_id : str
            The identifier of the node within the workflow.
        result : dict
            Execution output/error/status (will be JSON-serialized).
        """
        self._ensure_session(session_id)
        now = _now()
        result_json = _safe_json_dumps(result)
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflow_results (session_id, workflow_id, node_id, result, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, workflow_id, node_id) DO UPDATE SET result=excluded.result, updated_at=excluded.updated_at",
                (session_id, workflow_id, node_id, result_json, now),
            )
            self._conn.commit()

    def load_workflow_results(self, session_id: str, workflow_id: str) -> dict:
        """
        Load all node results for a workflow.

        Returns ``{node_id: result_dict, ...}``.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, result FROM workflow_results "
                "WHERE session_id = ? AND workflow_id = ?",
                (session_id, workflow_id),
            ).fetchall()
        return {r["node_id"]: _safe_json_loads(r["result"], default={}) for r in rows}

    # ════════════════════════════════════════════════════════════════
    # File Uploads (binary on disk, metadata in SQLite)
    # ════════════════════════════════════════════════════════════════

    def save_file(
        self,
        session_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "",
    ) -> None:
        """
        Save a binary file to disk and record its metadata.

        The file is stored at ``storage/files/{session_id}/{filename}``.

        Parameters
        ----------
        filename : str
            The original filename (e.g. ``"logo.png"``).
        data : bytes
            Raw binary content.
        mime_type : str
            Optional MIME type (e.g. ``"image/png"``).
        """
        self._ensure_session(session_id)
        now = _now()
        dir_path = self._files_path(session_id)
        file_path = dir_path / filename
        file_path.write_bytes(data)

        size = len(data)
        with self._lock:
            self._conn.execute(
                "INSERT INTO files_meta (session_id, filename, mime_type, size_bytes, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, filename) DO UPDATE SET mime_type=excluded.mime_type, size_bytes=excluded.size_bytes",
                (session_id, filename, mime_type, size, now),
            )
            self._conn.commit()

    def load_file(self, session_id: str, filename: str) -> tuple[bytes, str]:
        """
        Load a binary file.

        Returns ``(data_bytes, mime_type)``.  Raises ``FileNotFoundError``
        if the file does not exist on disk.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT mime_type FROM files_meta WHERE session_id = ? AND filename = ?",
                (session_id, filename),
            ).fetchone()
        if not row:
            raise FileNotFoundError(
                f"File metadata not found: {session_id}/{filename}"
            )
        file_path = self._files_path(session_id) / filename
        if not file_path.exists():
            raise FileNotFoundError(
                f"File data not found on disk: {session_id}/{filename}"
            )
        return file_path.read_bytes(), row["mime_type"]

    def list_files(self, session_id: str) -> list[dict]:
        """
        List all uploaded files for a session.

        Returns a list of dicts with ``filename``, ``mime_type``,
        ``size_bytes``, ``created_at``.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT filename, mime_type, size_bytes, created_at FROM files_meta "
                "WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [
            {
                "filename": r["filename"],
                "mime_type": r["mime_type"],
                "size_bytes": r["size_bytes"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_file(self, session_id: str, filename: str) -> bool:
        """
        Delete an uploaded file from both disk and metadata.

        Returns True if the file existed and was deleted.
        """
        file_path = self._files_path(session_id) / filename
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as exc:
                logger.warning(
                    "Could not delete file on disk %s/%s: %s",
                    session_id, filename, exc,
                )

        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM files_meta WHERE session_id = ? AND filename = ?",
                (session_id, filename),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def file_exists(self, session_id: str, filename: str) -> bool:
        """Check if a file exists (checks both metadata and disk)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM files_meta WHERE session_id = ? AND filename = ?",
                (session_id, filename),
            ).fetchone()
        if not row:
            return False
        return (self._files_path(session_id) / filename).exists()

    # ════════════════════════════════════════════════════════════════
    # Settings (global, not session-scoped)
    # ════════════════════════════════════════════════════════════════

    def save_settings(self, key: str, value: Any) -> None:
        """
        Save a global setting.

        The value is JSON-serialized before storage.
        """
        now = _now()
        val_json = _safe_json_dumps(value)
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, val_json, now),
            )
            self._conn.commit()

    def load_settings(self, key: str, default: Any = None) -> Any:
        """
        Load a global setting.

        Returns *default* if the key does not exist or parsing fails.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return default
        return _safe_json_loads(row["value"], default=default)

    def load_all_settings(self) -> dict:
        """
        Load all global settings as a flat dict.

        Values are deserialized from JSON.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM settings ORDER BY key"
            ).fetchall()
        result = {}
        for r in rows:
            result[r["key"]] = _safe_json_loads(r["value"], default=r["value"])
        return result

    def delete_settings(self, key: str) -> bool:
        """Delete a global setting. Returns True if it existed."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM settings WHERE key = ?", (key,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Snapshot (crash recovery)
    # ════════════════════════════════════════════════════════════════

    def save_snapshot(self, session_id: str) -> None:
        """
        Create a full crash-recovery snapshot of a session.

        The snapshot includes: pipeline state, conversation (last 100
        messages), code files, requirements, plan, review findings,
        and a list of uploaded files.
        """
        self._ensure_session(session_id)
        now = _now()

        snapshot = {
            "session_id": session_id,
            "timestamp": now,
            "pipeline_state": self.load_pipeline_state(session_id),
            "conversation": self.load_conversation(session_id, limit=100),
            "code_files": self.load_all_code(session_id),
            "requirements": self.load_requirements(session_id),
            "plan": self.load_plan(session_id),
            "review_findings": self.load_review(session_id),
            "files": self.list_files(session_id),
            "workflows": [],
        }

        # Include workflow IDs and their results
        for wf in self.list_workflows(session_id):
            wf_id = wf["workflow_id"]
            snapshot["workflows"].append({
                "workflow_id": wf_id,
                "definition": self.load_workflow(session_id, wf_id),
                "results": self.load_workflow_results(session_id, wf_id),
            })

        snapshot_json = _safe_json_dumps(snapshot)
        with self._lock:
            self._conn.execute(
                "INSERT INTO snapshots (session_id, data, created_at) VALUES (?, ?, ?)",
                (session_id, snapshot_json, now),
            )
            self._conn.commit()
        logger.info("Snapshot saved for session %s", session_id)

    def load_snapshot(self, session_id: str) -> dict:
        """
        Load the most recent snapshot for a session.

        Returns ``{}`` if no snapshot exists.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM snapshots "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        return _safe_json_loads(row["data"], default={})

    def list_snapshots(self, session_id: str, limit: int = 20) -> list[dict]:
        """List snapshots for a session, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at FROM snapshots "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            {"snapshot_id": r["id"], "created_at": r["created_at"]}
            for r in rows
        ]

    def delete_snapshot(self, snapshot_id: int) -> bool:
        """Delete a specific snapshot by its row ID. Returns True if deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM snapshots WHERE id = ?", (snapshot_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def prune_snapshots(self, session_id: str, keep: int = 10) -> int:
        """
        Delete old snapshots, keeping only the N most recent.

        Returns the number of deleted snapshots.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM snapshots "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                (session_id, keep),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" * len(ids))
            cursor = self._conn.execute(
                f"DELETE FROM snapshots WHERE id IN ({placeholders})", ids
            )
            self._conn.commit()
            return cursor.rowcount

    # ════════════════════════════════════════════════════════════════
    # PM Projects (项目管理)
    # ════════════════════════════════════════════════════════════════

    def save_pm_project(self, session_id: str, project_id: str, data: dict) -> None:
        """
        保存（或更新）PM 项目。

        Parameters
        ----------
        project_id : str
            项目唯一标识。
        data : dict
            项目数据，至少包含 ``name`` 和 ``description``。
            可选 ``status``（默认 ``"active"``）。
        """
        self._ensure_session(session_id)
        now = _now()
        name = data.get("name", "")
        description = data.get("description", "")
        status = data.get("status", "active")
        data_json = _safe_json_dumps(data)
        with self._lock:
            self._conn.execute(
                "INSERT INTO pm_projects (project_id, session_id, name, description, status, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id) DO UPDATE SET name=excluded.name, description=excluded.description, "
                "status=excluded.status, data=excluded.data, updated_at=excluded.updated_at",
                (project_id, session_id, name, description, status, data_json, now, now),
            )
            self._conn.commit()

    def load_pm_project(self, session_id: str, project_id: str) -> dict:
        """
        加载 PM 项目数据。返回 ``{}`` 如果不存在。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name, description, status, data, created_at, updated_at "
                "FROM pm_projects WHERE project_id = ? AND session_id = ?",
                (project_id, session_id),
            ).fetchone()
        if not row:
            return {}
        result = _safe_json_loads(row["data"], default={})
        result["project_id"] = project_id
        result["name"] = row["name"]
        result["description"] = row["description"]
        result["status"] = row["status"]
        result["created_at"] = row["created_at"]
        result["updated_at"] = row["updated_at"]
        return result

    def list_pm_projects(self, session_id: str, status: str = "active") -> list[dict]:
        """
        列出指定状态的 PM 项目。

        Parameters
        ----------
        status : str
            过滤状态，默认 ``"active"``。传入 ``""`` 返回全部。
        """
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT project_id, name, description, status, created_at, updated_at "
                    "FROM pm_projects WHERE session_id = ? AND status = ? ORDER BY updated_at DESC",
                    (session_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT project_id, name, description, status, created_at, updated_at "
                    "FROM pm_projects WHERE session_id = ? ORDER BY updated_at DESC",
                    (session_id,),
                ).fetchall()
        return [
            {
                "project_id": r["project_id"],
                "name": r["name"],
                "description": r["description"],
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def delete_pm_project(self, session_id: str, project_id: str) -> bool:
        """
        删除 PM 项目。返回 True 如果存在并已删除。
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM pm_projects WHERE project_id = ? AND session_id = ?",
                (project_id, session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Workflow Folders (工作流文件夹)
    # ════════════════════════════════════════════════════════════════

    def save_workflow_folder(self, session_id: str, folder_id: str,
                              name: str, parent_id: str = "") -> None:
        """
        保存（或更新）工作流文件夹。

        Parameters
        ----------
        folder_id : str
            文件夹唯一标识。
        name : str
            文件夹名称。
        parent_id : str
            父文件夹 ID，为空表示根目录。
        """
        self._ensure_session(session_id)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO workflow_folders (folder_id, session_id, name, parent_id, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(folder_id) DO UPDATE SET name=excluded.name, parent_id=excluded.parent_id",
                (folder_id, session_id, name, parent_id, now),
            )
            self._conn.commit()

    def list_workflow_folders(self, session_id: str,
                               parent_id: str = "") -> list[dict]:
        """
        列出指定父文件夹下的子文件夹。

        Parameters
        ----------
        parent_id : str
            父文件夹 ID，为空表示列出根文件夹。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT folder_id, name, parent_id, created_at "
                "FROM workflow_folders WHERE session_id = ? AND parent_id = ? ORDER BY name",
                (session_id, parent_id),
            ).fetchall()
        return [
            {
                "folder_id": r["folder_id"],
                "name": r["name"],
                "parent_id": r["parent_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_workflow_folder(self, session_id: str, folder_id: str) -> bool:
        """
        删除工作流文件夹。返回 True 如果存在并已删除。
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM workflow_folders WHERE folder_id = ? AND session_id = ?",
                (folder_id, session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def update_workflow_folder(self, session_id: str, folder_id: str,
                                name: Optional[str] = None,
                                parent_id: Optional[str] = None) -> bool:
        """
        更新工作流文件夹的名称或父文件夹。

        Parameters
        ----------
        name : str or None
            新名称，为 None 则不更新。
        parent_id : str or None
            新父文件夹 ID，为 None 则不更新。
        """
        if name is None and parent_id is None:
            return False
        sets = []
        params = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if parent_id is not None:
            sets.append("parent_id = ?")
            params.append(parent_id)
        params.extend([folder_id, session_id])
        sql = (
            "UPDATE workflow_folders SET " + ", ".join(sets)
            + " WHERE folder_id = ? AND session_id = ?"
        )
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Search History (搜索历史)
    # ════════════════════════════════════════════════════════════════

    def save_search(self, session_id: str, query: str, provider: str,
                     results: list) -> None:
        """
        保存一条搜索记录。

        Parameters
        ----------
        query : str
            搜索关键词。
        provider : str
            搜索提供方名称。
        results : list
            搜索结果列表。
        """
        self._ensure_session(session_id)
        now = _now()
        results_json = _safe_json_dumps(results)
        with self._lock:
            self._conn.execute(
                "INSERT INTO search_history (session_id, query, provider, results, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, query, provider, results_json, now),
            )
            self._conn.commit()

    def load_search_history(self, session_id: str, limit: int = 50) -> list[dict]:
        """
        加载搜索历史，按时间倒序。

        Parameters
        ----------
        limit : int
            最多返回条数，默认 50。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, query, provider, results, timestamp "
                "FROM search_history WHERE session_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "query": r["query"],
                "provider": r["provider"],
                "results": _safe_json_loads_list(r["results"]),
                "timestamp": r["timestamp"],
            }
            for r in rows
        ]

    # ════════════════════════════════════════════════════════════════
    # Node Packages (节点包)
    # ════════════════════════════════════════════════════════════════

    def save_node_package(self, session_id: str, package_id: str,
                           name: str, description: str, data: dict) -> None:
        """
        保存（或更新）节点包。

        Parameters
        ----------
        package_id : str
            节点包唯一标识。
        name : str
            包名称。
        description : str
            包描述。
        data : dict
            节点包数据（JSON 序列化）。
        """
        self._ensure_session(session_id)
        now = _now()
        data_json = _safe_json_dumps(data)
        with self._lock:
            self._conn.execute(
                "INSERT INTO node_packages (package_id, session_id, name, description, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(package_id) DO UPDATE SET name=excluded.name, "
                "description=excluded.description, data=excluded.data",
                (package_id, session_id, name, description, data_json, now),
            )
            self._conn.commit()

    def load_node_package(self, session_id: str, package_id: str) -> dict:
        """
        加载节点包数据。返回 ``{}`` 如果不存在。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name, description, data, created_at "
                "FROM node_packages WHERE package_id = ? AND session_id = ?",
                (package_id, session_id),
            ).fetchone()
        if not row:
            return {}
        result = _safe_json_loads(row["data"], default={})
        result["package_id"] = package_id
        result["name"] = row["name"]
        result["description"] = row["description"]
        result["created_at"] = row["created_at"]
        return result

    def list_node_packages(self, session_id: str) -> list[dict]:
        """
        列出会话下所有节点包。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT package_id, name, description, created_at "
                "FROM node_packages WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [
            {
                "package_id": r["package_id"],
                "name": r["name"],
                "description": r["description"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_node_package(self, session_id: str, package_id: str) -> bool:
        """
        删除节点包。返回 True 如果存在并已删除。
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM node_packages WHERE package_id = ? AND session_id = ?",
                (package_id, session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ════════════════════════════════════════════════════════════════
    # Maintenance
    # ════════════════════════════════════════════════════════════════

    def vacuum(self) -> None:
        """Run VACUUM to reclaim disk space.  Call sparingly."""
        with self._lock:
            self._conn.execute("VACUUM")
        logger.info("VACUUM complete")

    def integrity_check(self) -> bool:
        """Run ``PRAGMA integrity_check``.  Returns True if OK."""
        with self._lock:
            result = self._conn.execute("PRAGMA integrity_check").fetchone()
        ok = result[0] == "ok"
        if not ok:
            logger.error("Integrity check failed: %s", result[0])
        return ok

    def stats(self) -> dict:
        """
        Return storage statistics for diagnostics.

        Includes table row counts, database file size, etc.
        """
        tables = [
            "sessions", "pipeline_state", "messages", "code_files",
            "requirements", "plans", "reviews", "workflows",
            "workflow_results", "files_meta", "settings", "snapshots",
            "pm_projects", "workflow_folders", "search_history", "node_packages",
        ]
        counts = {}
        with self._lock:
            for t in tables:
                row = self._conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()
                counts[t] = row["c"]

        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        wal_size = (self._db_path.parent / (self._db_path.name + "-wal")).stat().st_size \
            if (self._db_path.parent / (self._db_path.name + "-wal")).exists() else 0

        # Count files on disk
        file_count = sum(
            1 for _ in self._files_dir.rglob("*") if _.is_file()
        ) if self._files_dir.exists() else 0

        return {
            "tables": counts,
            "db_size_bytes": db_size,
            "wal_size_bytes": wal_size,
            "total_files_on_disk": file_count,
            "db_path": str(self._db_path),
            "storage_dir": str(self._storage_dir),
        }


# ═══════════════════════════════════════════════════════════════════════
# __main__ — Self-test
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.DEBUG)

    print("=" * 60)
    print("cx2118 Script Weaver — Storage Self-Test  v8")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="cx2118-test-") as tmpdir:
        store = Storage(tmpdir)
        sid = store.new_session("Test Project")
        print(f"\n✓ Created session: {sid}")

        # --- Pipeline State ---
        store.save_pipeline_state(sid, {"phase": "planning", "steps": ["a", "b"]})
        ps = store.load_pipeline_state(sid)
        assert ps["phase"] == "planning", f"Expected 'planning', got {ps['phase']}"
        print("✓ Pipeline state: save/load OK")

        # --- Conversation ---
        store.save_message(sid, "user", "I want a web app")
        store.save_message(sid, "assistant", "Sure, let me plan it")
        msgs = store.load_conversation(sid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        print("✓ Conversation: 2 messages saved/loaded")

        # Bulk messages
        store.save_messages_bulk(sid, [
            {"role": "user", "content": "bulk 1"},
            {"role": "assistant", "content": "bulk 2"},
        ])
        assert len(store.load_conversation(sid)) == 4
        print("✓ Bulk messages OK")

        # Clear
        cleared = store.clear_conversation(sid)
        assert cleared == 4
        assert len(store.load_conversation(sid)) == 0
        print("✓ Clear conversation OK")

        # --- Code Files ---
        store.save_code(sid, "python", 'print("hello")')
        store.save_code(sid, "html", "<h1>Hello</h1>")
        assert store.load_code(sid, "python") == 'print("hello")'
        assert store.load_code(sid, "html") == "<h1>Hello</h1>"
        all_code = store.load_all_code(sid)
        assert set(all_code.keys()) == {"python", "html"}
        print("✓ Code files: save/load OK")

        # --- Requirements ---
        store.save_requirements(sid, "# Requirements\n\nBuild a web app")
        req = store.load_requirements(sid)
        assert "Build a web app" in req
        print("✓ Requirements: save/load OK")

        # --- Plan ---
        store.save_plan(sid, {"files": {"main.py": {}}}, "raw output from director")
        plan = store.load_plan(sid)
        assert "files" in plan["structure"]
        assert plan["raw"] == "raw output from director"
        print("✓ Plan: save/load OK")

        # --- Review ---
        findings = [
            {"id": 1, "severity": "error", "title": "Missing import"},
            {"id": 2, "severity": "warning", "title": "Unused var"},
        ]
        store.save_review(sid, findings)
        loaded = store.load_review(sid)
        assert len(loaded) == 2
        assert loaded[0]["severity"] == "error"
        print("✓ Review findings: save/load OK")

        # --- Workflows ---
        store.save_workflow(sid, "wf-1", {"nodes": ["a", "b"], "edges": []})
        wf = store.load_workflow(sid, "wf-1")
        assert "nodes" in wf
        wf_list = store.list_workflows(sid)
        assert len(wf_list) == 1
        print("✓ Workflows: save/load/list OK")

        # Workflow with folder_id
        store.save_workflow(sid, "wf-foldered", {"nodes": ["x"]}, folder_id="fold-1")
        wf_f = store.load_workflow(sid, "wf-foldered")
        assert wf_f.get("_folder_id") == "fold-1"
        print("✓ Workflow with folder_id OK")

        # Workflow results
        store.save_workflow_result(sid, "wf-1", "node-a", {"status": "success", "output": "42"})
        store.save_workflow_result(sid, "wf-1", "node-b", {"status": "running"})
        results = store.load_workflow_results(sid, "wf-1")
        assert results["node-a"]["status"] == "success"
        assert results["node-b"]["status"] == "running"
        print("✓ Workflow results: save/load OK")

        # Delete workflow
        assert store.delete_workflow(sid, "wf-1")
        assert store.load_workflow(sid, "wf-1") == {}
        print("✓ Delete workflow OK")

        # --- File Uploads ---
        store.save_file(sid, "logo.png", b"\x89PNG\r\n\x1a\nfake", "image/png")
        data, mime = store.load_file(sid, "logo.png")
        assert data == b"\x89PNG\r\n\x1a\nfake"
        assert mime == "image/png"
        files = store.list_files(sid)
        assert len(files) == 1
        assert files[0]["filename"] == "logo.png"
        assert store.file_exists(sid, "logo.png")
        print("✓ File uploads: save/load/list/exists OK")

        # Delete file
        assert store.delete_file(sid, "logo.png")
        assert not store.file_exists(sid, "logo.png")
        print("✓ Delete file OK")

        # --- Settings (global) ---
        store.save_settings("provider.name", "siliconflow")
        store.save_settings("provider.api_key", "sk-test123")
        store.save_settings("agent.pm.model", "deepseek-v3")
        assert store.load_settings("provider.name") == "siliconflow"
        assert store.load_settings("nonexistent", "default") == "default"
        all_settings = store.load_all_settings()
        assert len(all_settings) >= 3
        print("✓ Settings: save/load/all OK")

        # Delete setting
        assert store.delete_settings("provider.api_key")
        assert store.load_settings("provider.api_key") is None
        print("✓ Delete setting OK")

        # --- PM Projects ---
        store.save_pm_project(sid, "pm-1", {
            "name": "E-commerce App",
            "description": "Online shop project",
            "status": "active",
            "tasks": ["design", "implement"],
        })
        pm = store.load_pm_project(sid, "pm-1")
        assert pm["name"] == "E-commerce App"
        assert pm["status"] == "active"
        assert pm["tasks"] == ["design", "implement"]
        pm_list = store.list_pm_projects(sid, "active")
        assert len(pm_list) == 1
        assert store.delete_pm_project(sid, "pm-1")
        assert store.load_pm_project(sid, "pm-1") == {}
        print("✓ PM Projects: save/load/list/delete OK")

        # --- Workflow Folders ---
        store.save_workflow_folder(sid, "fold-root", "My Workflows")
        store.save_workflow_folder(sid, "fold-child", "Sub Folder", parent_id="fold-root")
        folders = store.list_workflow_folders(sid)
        assert len(folders) == 1
        assert folders[0]["folder_id"] == "fold-root"
        sub_folders = store.list_workflow_folders(sid, parent_id="fold-root")
        assert len(sub_folders) == 1
        assert sub_folders[0]["folder_id"] == "fold-child"
        # Update folder
        assert store.update_workflow_folder(sid, "fold-child", name="Renamed Sub")
        updated = store.list_workflow_folders(sid, parent_id="fold-root")
        assert updated[0]["name"] == "Renamed Sub"
        assert store.delete_workflow_folder(sid, "fold-child")
        print("✓ Workflow Folders: save/list/update/delete OK")

        # --- Search History ---
        store.save_search(sid, "python async", "web", [
            {"title": "Async in Python", "url": "https://example.com/1"},
        ])
        store.save_search(sid, "flask tutorial", "web", [
            {"title": "Flask Guide", "url": "https://example.com/2"},
        ])
        history = store.load_search_history(sid, limit=10)
        assert len(history) == 2
        assert history[0]["query"] == "flask tutorial"  # newest first
        assert len(history[0]["results"]) == 1
        print("✓ Search History: save/load OK")

        # --- Node Packages ---
        store.save_node_package(sid, "pkg-1", "HTTP Utils", "HTTP helper nodes", {
            "nodes": [{"type": "http_request", "config": {}}],
        })
        pkg = store.load_node_package(sid, "pkg-1")
        assert pkg["name"] == "HTTP Utils"
        assert len(pkg["nodes"]) == 1
        pkg_list = store.list_node_packages(sid)
        assert len(pkg_list) == 1
        assert store.delete_node_package(sid, "pkg-1")
        assert store.load_node_package(sid, "pkg-1") == {}
        print("✓ Node Packages: save/load/list/delete OK")

        # --- Snapshot ---
        store.save_message(sid, "user", "Before snapshot")
        store.save_snapshot(sid)
        snap = store.load_snapshot(sid)
        assert snap["session_id"] == sid
        assert "pipeline_state" in snap
        assert "conversation" in snap
        print("✓ Snapshot: save/load OK")

        # Prune
        store.save_snapshot(sid)
        store.save_snapshot(sid)
        deleted = store.prune_snapshots(sid, keep=1)
        assert deleted == 2
        print("✓ Prune snapshots OK")

        # --- Session List ---
        sid2 = store.new_session("Second Project")
        sessions = store.list_sessions()
        assert len(sessions) >= 2
        print("✓ List sessions OK")

        # Delete session
        assert store.delete_session(sid2)
        assert store.load_session(sid2) == {}
        print("✓ Delete session OK")

        # --- Stats ---
        stats = store.stats()
        assert stats["db_size_bytes"] > 0
        assert "tables" in stats
        # v8 新表应出现在统计中
        assert "pm_projects" in stats["tables"]
        assert "workflow_folders" in stats["tables"]
        assert "search_history" in stats["tables"]
        assert "node_packages" in stats["tables"]
        print(f"✓ Stats: {stats['tables']}")

        # --- Integrity ---
        assert store.integrity_check()
        print("✓ Integrity check passed")

        # Cleanup
        store.close()
        print("\n✓ All tests passed!")
        print("=" * 60)