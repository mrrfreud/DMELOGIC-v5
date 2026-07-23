"""
Nova — DMELogic Intelligent Agent  v3.0
========================================
Persistent memory, session logging, proactive awareness, smart context.

New in v3.0:
  - Persistent memory (nova_memory.db) — survives restarts
  - Session log — every conversation saved and searchable
  - "Remember that" — saves facts, preferences, patient notes
  - "What do you know about X" — recalls stored knowledge
  - Proactive startup check — flags urgent items at launch
  - Auto-summarizes long conversations to stay within context
  - Smart context injection — relevant memories loaded per turn

Usage:
    python nova_agent.py              # interactive
    python nova_agent.py --voice      # voice output
    python nova_agent.py --run "morning summary"

.env:
    ANTHROPIC_API_KEY=sk-ant-...
    DMELOGIC_API_KEY=your-key
    DMELOGIC_API_URL=http://127.0.0.1:8400
    ELEVENLABS_API_KEY=          # optional
    ELEVENLABS_VOICE_ID=         # optional
"""

from __future__ import annotations
import os, sys, json, logging, argparse, sqlite3, re, csv
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_or_default(name: str, default: str) -> str:
    value = str(os.getenv(name, "") or "").strip()
    placeholders = {
        "your-key",
        "your-key-here",
        "your-strong-key",
        "your-strong-key-here",
        "changeme",
        "change-me",
    }
    if not value or value.lower() in placeholders:
        return default
    return value

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
DMELOGIC_API_KEY   = _env_or_default("DMELOGIC_API_KEY", "dev-key-change-me")
DMELOGIC_API_URL   = os.getenv("DMELOGIC_API_URL", "http://127.0.0.1:8400")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE   = os.getenv("ELEVENLABS_VOICE_ID", "")
CLAUDE_MODEL        = os.getenv("CLAUDE_MODEL",        "claude-haiku-4-5-20251001")
CLAUDE_VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-sonnet-4-5-20250929")
CLAUDE_MODEL_FALLBACKS = os.getenv("CLAUDE_MODEL_FALLBACKS", "").strip()
NOVA_MAX_TOOL_RESULT_CHARS = int(os.getenv("NOVA_MAX_TOOL_RESULT_CHARS", "12000"))
NOVA_TOOL_RESULT_PREVIEW_CHARS = int(os.getenv("NOVA_TOOL_RESULT_PREVIEW_CHARS", "3000"))
NOVA_MAX_HISTORY_CHARS = int(os.getenv("NOVA_MAX_HISTORY_CHARS", "160000"))

# Memory database lives alongside nova_agent.py
NOVA_DB_PATH = Path(__file__).parent / "nova_memory.db"
REPORT_EXPORT_DIR = Path(__file__).parent / "report_exports"

log = logging.getLogger("nova")

try:
    import anthropic
except ImportError:
    sys.exit("Run: pip install anthropic")
try:
    import requests
except ImportError:
    sys.exit("Run: pip install requests")

try:
    import dmelogic.nova_ringcentral as rc_tools
except Exception:
    rc_tools = None


# ══════════════════════════════════════════════════════════════════════════
#  PERSISTENT MEMORY  (nova_memory.db)
# ══════════════════════════════════════════════════════════════════════════
class NovaMemory:
    """
    SQLite-backed persistent memory for Nova.

    Tables:
      memories    — facts, preferences, patient notes, reminders
      sessions    — conversation logs per session
      session_msgs— individual messages within sessions
    """

    def __init__(self, db_path: Path = NOVA_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL DEFAULT 'general',
                subject     TEXT,
                content     TEXT NOT NULL,
                source      TEXT DEFAULT 'user',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mem_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_mem_subject  ON memories(subject);

            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT NOT NULL,
                ended_at    TEXT,
                summary     TEXT,
                msg_count   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS session_msgs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                ts          TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT NOT NULL,
                tag         TEXT NOT NULL DEFAULT 'general',
                status      TEXT NOT NULL DEFAULT 'active',
                created_at  TEXT NOT NULL,
                done_at     TEXT,
                due_at      TEXT,
                follow_up_notes TEXT,
                follow_up_at TEXT,
                remind_every_minutes INTEGER NOT NULL DEFAULT 30,
                last_notified_at TEXT,
                notification_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_rem_tag    ON reminders(tag);
            CREATE INDEX IF NOT EXISTS idx_rem_status ON reminders(status);

            -- Structured session summaries (auto-generated on disconnect)
            CREATE TABLE IF NOT EXISTS session_insights (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER NOT NULL,
                patients     TEXT,    -- JSON list of {id, name, context}
                orders       TEXT,    -- JSON list of {id, action_taken}
                decisions    TEXT,    -- JSON list of decisions made
                unresolved   TEXT,    -- JSON list of items left unfinished
                learned      TEXT,    -- JSON list of facts learned casually
                summary      TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            -- Entity context: per-patient interaction log
            CREATE TABLE IF NOT EXISTS entity_context (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type  TEXT NOT NULL,       -- 'patient', 'prescriber', 'order'
                entity_id    TEXT NOT NULL,        -- patient_id, order_id, etc.
                entity_name  TEXT,                 -- human-readable name
                context      TEXT NOT NULL,         -- what happened
                session_id   INTEGER,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entity ON entity_context(entity_type, entity_id);

            -- Escalated rules: patterns corrected 3+ times get promoted
            CREATE TABLE IF NOT EXISTS learned_rules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                rule         TEXT NOT NULL,
                source       TEXT,                 -- what triggered the escalation
                times_corrected INTEGER DEFAULT 1,
                active       INTEGER DEFAULT 1,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );
        """)

        # Backward-compatible schema migration for existing installs.
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(reminders)").fetchall()
            if len(r) > 1
        }
        if "due_at" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN due_at TEXT")
        if "follow_up_notes" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN follow_up_notes TEXT")
        if "follow_up_at" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN follow_up_at TEXT")
        if "remind_every_minutes" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN remind_every_minutes INTEGER NOT NULL DEFAULT 30")
        if "last_notified_at" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN last_notified_at TEXT")
        if "notification_count" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN notification_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_at)")

        conn.commit()
        conn.close()

    # ── Memory CRUD ───────────────────────────────────────────────────────

    def remember(self, content: str, category: str = "general",
                 subject: str = None, source: str = "user") -> int:
        """Store a new memory. Returns the memory ID."""
        now = datetime.now().isoformat()
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO memories (category, subject, content, source, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (category, subject, content, source, now, now)
        )
        conn.commit()
        mid = cur.lastrowid
        conn.close()
        log.info(f"Memory saved [{category}]: {content[:80]}")
        return mid

    def recall(self, query: str = None, category: str = None,
               subject: str = None, limit: int = 10) -> List[Dict]:
        """Retrieve memories by category, subject, or keyword search."""
        conn = self._conn()
        conditions, params = [], []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if subject:
            conditions.append("LOWER(subject) LIKE LOWER(?)")
            params.append(f"%{subject}%")
        if query:
            conditions.append("LOWER(content) LIKE LOWER(?)")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def recall_all(self, limit: int = 50) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def forget(self, memory_id: int) -> bool:
        conn = self._conn()
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        conn.close()
        return True

    def format_for_prompt(self) -> str:
        """Return memories as a concise block for injection into system prompt."""
        mems = self.recall_all(limit=30)
        if not mems:
            return ""
        lines = ["NOVA'S MEMORY (facts learned from previous sessions):"]
        for m in mems:
            subj = f"[{m['subject']}] " if m['subject'] else ""
            lines.append(f"- {subj}{m['content']}")
        return "\n".join(lines)

    # ── Reminders ─────────────────────────────────────────────────────────

    def add_reminder(
        self,
        content: str,
        tag: str = "general",
        due_at: str | None = None,
        remind_every_minutes: int = 30,
        follow_up_notes: str | None = None,
        follow_up_at: str | None = None,
    ) -> int:
        """Add a new active reminder. Returns reminder ID."""
        now = datetime.now().isoformat()
        due = str(due_at or "").strip() or None
        follow_up_notes_val = str(follow_up_notes or "").strip() or None
        follow_up_at_val = str(follow_up_at or "").strip() or None
        try:
            cadence = int(remind_every_minutes)
        except Exception:
            cadence = 30
        cadence = max(1, min(cadence, 24 * 60))

        conn = self._conn()
        cur = conn.execute(
            """
            INSERT INTO reminders (
                content, tag, status, created_at, due_at, follow_up_notes, follow_up_at,
                remind_every_minutes, last_notified_at, notification_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                content,
                tag.lower(),
                "active",
                now,
                due,
                follow_up_notes_val,
                follow_up_at_val,
                cadence,
                None,
                0,
            )
        )
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        log.info(f"Reminder added [{tag}] due={due or 'none'} every={cadence}m: {content[:80]}")
        return rid

    def update_reminder(
        self,
        reminder_id: int,
        content: str,
        tag: str = "general",
        due_at: str | None = None,
        follow_up_notes: str | None = None,
        follow_up_at: str | None = None,
        remind_every_minutes: int = 30,
    ) -> bool:
        """Update an active reminder in place."""
        now = datetime.now().isoformat()
        due = str(due_at or "").strip() or None
        follow_up_notes_val = str(follow_up_notes or "").strip() or None
        follow_up_at_val = str(follow_up_at or "").strip() or None
        try:
            cadence = int(remind_every_minutes)
        except Exception:
            cadence = 30
        cadence = max(1, min(cadence, 24 * 60))

        conn = self._conn()
        conn.execute(
            """
            UPDATE reminders
            SET content = ?,
                tag = ?,
                due_at = ?,
                follow_up_notes = ?,
                follow_up_at = ?,
                remind_every_minutes = ?,
                notification_count = CASE
                    WHEN COALESCE(due_at, '') <> COALESCE(?, '') THEN 0
                    ELSE notification_count
                END,
                last_notified_at = CASE
                    WHEN COALESCE(due_at, '') <> COALESCE(?, '') THEN NULL
                    ELSE last_notified_at
                END
            WHERE id = ? AND status = 'active'
            """,
            (
                content,
                tag.lower(),
                due,
                follow_up_notes_val,
                follow_up_at_val,
                cadence,
                due,
                due,
                reminder_id,
            ),
        )
        conn.commit()
        conn.close()
        log.info(f"Reminder updated #{reminder_id} [{tag}] due={due or 'none'} every={cadence}m")
        return True

    def get_reminders(self, tag: str = None, status: str = "active") -> List[Dict]:
        """Get reminders filtered by tag and/or status."""
        conn = self._conn()
        conditions = ["status = ?"]
        params = [status]
        if tag and tag != "all":
            conditions.append("LOWER(tag) LIKE LOWER(?)")
            params.append(f"%{tag}%")
        where = "WHERE " + " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT * FROM reminders {where} ORDER BY COALESCE(due_at, created_at) ASC",
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def complete_reminder(self, reminder_id: int) -> bool:
        """Mark a reminder as done."""
        conn = self._conn()
        conn.execute(
            "UPDATE reminders SET status='done', done_at=? WHERE id=?",
            (datetime.now().isoformat(), reminder_id)
        )
        conn.commit()
        conn.close()
        return True

    def complete_reminder_by_content(self, keyword: str) -> List[int]:
        """Mark reminders as done by keyword match. Returns list of completed IDs."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id FROM reminders WHERE status='active' AND LOWER(content) LIKE LOWER(?)",
            (f"%{keyword}%",)
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE reminders SET status='done', done_at=? WHERE id IN ({placeholders})",
                [datetime.now().isoformat()] + ids,
            )
            conn.commit()
        conn.close()
        return ids

    def delete_reminder(self, reminder_id: int) -> bool:
        """Permanently delete a reminder."""
        conn = self._conn()
        conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        conn.commit()
        conn.close()
        return True

    def format_reminders_for_prompt(self) -> str:
        """Return active reminders for system prompt injection."""
        reminders = self.get_reminders(status="active")
        if not reminders:
            return ""
        lines = ["ACTIVE REMINDERS:"]
        for r in reminders:
            due = str(r.get("due_at") or "").strip()
            cadence = int(r.get("remind_every_minutes") or 30)
            if due:
                lines.append(
                    f"  #{r['id']} [{r['tag']}] {r['content']} (due {due}; repeat every {cadence}m until done)"
                )
            else:
                lines.append(f"  #{r['id']} [{r['tag']}] {r['content']} (added {r['created_at'][:10]})")
            if str(r.get("follow_up_notes") or "").strip():
                lines.append(f"      follow-up: {str(r.get('follow_up_notes') or '').strip()}")
            if str(r.get("follow_up_at") or "").strip():
                lines.append(f"      follow-up at: {str(r.get('follow_up_at') or '').strip()}")
        return chr(10).join(lines)

    # ── Session logging ───────────────────────────────────────────────────

    def start_session(self) -> int:
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO sessions (started_at) VALUES (?)",
            (datetime.now().isoformat(),)
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
        return sid

    def log_message(self, session_id: int, role: str, content: str):
        conn = self._conn()
        conn.execute(
            "INSERT INTO session_msgs (session_id, role, content, ts) VALUES (?,?,?,?)",
            (session_id, role, content if isinstance(content, str) else json.dumps(content, default=str),
             datetime.now().isoformat())
        )
        conn.execute(
            "UPDATE sessions SET msg_count = msg_count + 1 WHERE id = ?",
            (session_id,)
        )
        conn.commit()
        conn.close()

    def end_session(self, session_id: int, summary: str = ""):
        conn = self._conn()
        conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (datetime.now().isoformat(), summary, session_id)
        )
        conn.commit()
        conn.close()

    def get_recent_sessions(self, limit: int = 5) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def search_sessions(self, query: str, limit: int = 5) -> List[Dict]:
        conn = self._conn()
        rows = conn.execute(
            (
                "SELECT s.*, m.content as matched_message "
                "FROM sessions s JOIN session_msgs m ON m.session_id = s.id "
                "WHERE LOWER(m.content) LIKE LOWER(?) "
                "GROUP BY s.id ORDER BY s.started_at DESC LIMIT ?"
            ),
            (f"%{query}%", limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Session Insights (auto-summary) ──────────────────────────────────

    def save_session_insight(self, session_id: int, insight: Dict) -> int:
        """Save a structured session summary generated by Claude."""
        now = datetime.now().isoformat()
        conn = self._conn()
        cur = conn.execute(
            """INSERT INTO session_insights
               (session_id, patients, orders, decisions, unresolved, learned, summary, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id,
             json.dumps(insight.get("patients", []), default=str),
             json.dumps(insight.get("orders", []), default=str),
             json.dumps(insight.get("decisions", []), default=str),
             json.dumps(insight.get("unresolved", []), default=str),
             json.dumps(insight.get("learned", []), default=str),
             insight.get("summary", ""),
             now)
        )
        conn.commit()
        iid = cur.lastrowid
        conn.close()
        log.info(f"Session insight saved for session {session_id}")
        return iid

    def get_recent_insights(self, limit: int = 3) -> List[Dict]:
        """Get the most recent session insights for context injection."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM session_insights ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            for field in ("patients", "orders", "decisions", "unresolved", "learned"):
                try:
                    d[field] = json.loads(d[field]) if d[field] else []
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            results.append(d)
        return results

    def get_unresolved_items(self, limit: int = 10) -> List[str]:
        """Get unresolved items from recent sessions."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT unresolved FROM session_insights WHERE unresolved IS NOT NULL "
            "AND unresolved != '[]' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            try:
                parsed = json.loads(r["unresolved"])
                if isinstance(parsed, list):
                    items.extend(parsed)
            except Exception:
                pass
        return items

    # ── Entity Context ───────────────────────────────────────────────────

    def log_entity_interaction(self, entity_type: str, entity_id: str,
                                context: str, entity_name: str = None,
                                session_id: int = None):
        """Log an interaction with a patient, prescriber, or order."""
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute(
            """INSERT INTO entity_context
               (entity_type, entity_id, entity_name, context, session_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (entity_type, str(entity_id), entity_name, context, session_id, now)
        )
        conn.commit()
        conn.close()

    def get_entity_context(self, entity_type: str, entity_id: str,
                            limit: int = 5) -> List[Dict]:
        """Get recent interaction history for an entity."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM entity_context WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (entity_type, str(entity_id), limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_entity_context_by_name(self, name: str, limit: int = 5) -> List[Dict]:
        """Search entity context by name (fuzzy)."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM entity_context WHERE LOWER(entity_name) LIKE LOWER(?) "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{name}%", limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Learned Rules ────────────────────────────────────────────────────

    def get_learned_rules(self) -> List[Dict]:
        """Get all active learned rules for system prompt injection."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM learned_rules WHERE active = 1 ORDER BY times_corrected DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_learned_rule(self, rule: str, source: str = "") -> int:
        """Add or increment a learned rule."""
        now = datetime.now().isoformat()
        conn = self._conn()
        # Check if similar rule exists
        existing = conn.execute(
            "SELECT id, times_corrected FROM learned_rules "
            "WHERE active = 1 AND LOWER(rule) LIKE LOWER(?)",
            (f"%{rule[:50]}%",)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE learned_rules SET times_corrected = times_corrected + 1, "
                "updated_at = ? WHERE id = ?",
                (now, existing["id"])
            )
            conn.commit()
            rid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO learned_rules (rule, source, times_corrected, active, created_at, updated_at) "
                "VALUES (?,?,1,1,?,?)",
                (rule, source, now, now)
            )
            conn.commit()
            rid = cur.lastrowid
        conn.close()
        return rid

    # ── Context builders for system prompt ───────────────────────────────

    def format_continuity_context(self) -> str:
        """Build a continuity block from recent sessions for system prompt injection."""
        insights = self.get_recent_insights(limit=3)
        if not insights:
            return ""

        lines = ["RECENT SESSION CONTEXT (auto-generated — what happened recently):"]
        for ins in insights:
            date = ins.get("created_at", "")[:16]
            summary = ins.get("summary", "")
            if summary:
                lines.append(f"\n[{date}] {summary}")

            # Highlight unresolved items
            unresolved = ins.get("unresolved", [])
            if unresolved:
                lines.append("  UNRESOLVED:")
                for item in unresolved:
                    lines.append(f"    - {item}")

            # Recent patient interactions
            patients = ins.get("patients", [])
            if patients:
                names = [p.get("name", p.get("id", "?")) if isinstance(p, dict) else str(p)
                         for p in patients[:5]]
                lines.append(f"  Patients discussed: {', '.join(names)}")

        return "\n".join(lines)

    def format_learned_rules(self) -> str:
        """Format learned rules for system prompt injection."""
        rules = self.get_learned_rules()
        if not rules:
            return ""
        lines = ["LEARNED RULES (patterns corrected multiple times — follow strictly):"]
        for r in rules:
            lines.append(f"  - {r['rule']} (corrected {r['times_corrected']}x)")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  DMELogic API CLIENT
# ══════════════════════════════════════════════════════════════════════════
class DMELogicClient:
    def __init__(self):
        self.base = DMELOGIC_API_URL.rstrip("/")
        self.headers = {"Authorization": f"Bearer {DMELOGIC_API_KEY}",
                        "Content-Type": "application/json", "Accept": "application/json"}
        self.folder_path = self._resolve_folder_path()

    def _resolve_folder_path(self) -> Optional[str]:
        try:
            from dmelogic.paths import db_dir
            return str(db_dir())
        except Exception:
            return None

    def _db_conn(self, db_name: str) -> sqlite3.Connection:
        try:
            from dmelogic.db.base import get_connection
            conn = get_connection(db_name, folder_path=self.folder_path)
        except Exception:
            path = os.path.join(self.folder_path or ".", db_name)
            conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set:
        try:
            cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            return {str(r[1]) for r in cols}
        except Exception:
            return set()

    def _get(self, path: str, params: Dict = None) -> Any:
        try:
            r = requests.get(f"{self.base}{path}", headers=self.headers, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            return {"error": "Cannot connect to DMELogic API — is it running on port 8400?"}
        except requests.exceptions.HTTPError as e:
            return {"error": f"API {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def _post(self, path: str, body: Dict) -> Any:
        try:
            r = requests.post(f"{self.base}{path}", headers=self.headers, json=body, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _patch(self, path: str, body: Dict) -> Any:
        try:
            r = requests.patch(f"{self.base}{path}", headers=self.headers, json=body, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _delete(self, path: str, body: Optional[Dict] = None) -> Any:
        try:
            r = requests.delete(f"{self.base}{path}", headers=self.headers, json=body, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def dmelogic_api_call(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Generic passthrough for any DMELogic API endpoint."""
        verb = str(method or "GET").upper().strip()
        endpoint = str(path or "").strip()
        if not endpoint.startswith("/"):
            return {"error": "path must start with '/'"}
        if verb not in {"GET", "POST", "PATCH", "DELETE"}:
            return {"error": "method must be one of GET, POST, PATCH, DELETE"}
        try:
            r = requests.request(
                method=verb,
                url=f"{self.base}{endpoint}",
                headers=self.headers,
                params=params or None,
                json=body or None,
                timeout=20,
            )
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"status_code": r.status_code, "text": r.text}
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 500
            text = e.response.text if e.response is not None else str(e)
            return {"error": f"API {status_code}: {text[:400]}"}
        except Exception as e:
            return {"error": str(e)}

    def search_patients(self, q: str) -> Any:
        return self._get("/patients/search", {"q": q})
    def list_patients(self) -> Any:
        return self._get("/patients")
    def search_patients_by_phone(self, phone: str) -> Any:
        return self._get("/patients/search/phone", {"phone": phone})
    def get_patient(self, patient_id: int) -> Any:
        return self._get(f"/patients/{patient_id}")
    def create_patient(self, payload: Dict[str, Any]) -> Any:
        return self._post("/patients", payload)
    def update_patient(self, patient_id: int, payload: Dict[str, Any]) -> Any:
        return self._patch(f"/patients/{patient_id}", payload)
    def get_patient_orders(self, patient_id: int) -> Any:
        return self._get(f"/patients/{patient_id}/orders")
    def get_patient_refills_eligible(self, patient_id: int) -> Any:
        return self._get(f"/patients/{patient_id}/refills-eligible")
    def get_patient_notes(self, patient_id: int) -> Any:
        return self._get(f"/patients/{patient_id}/notes")
    def get_order(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}")
    def get_order_notes(self, order_id: int) -> Any:
        return self._get(f"/orders/{order_id}/notes")
    def update_order_status(self, order_id: int, new_status: str, notes: str = "", paid_date: str = "") -> Any:
        body = {"new_status": new_status, "updated_by": "Nova", "notes": notes}
        if paid_date:
            body["paid_date"] = paid_date
        return self._patch(f"/orders/{order_id}/status", body)
    def update_order_prescriber_contact(
        self,
        order_id: int,
        prescriber_phone: Optional[str] = None,
        prescriber_fax: Optional[str] = None,
        notes: str = "",
    ) -> Any:
        body = {
            "updated_by": "Nova",
            "notes": notes,
            "prescriber_phone": prescriber_phone,
            "prescriber_fax": prescriber_fax,
        }
        return self._patch(f"/orders/{order_id}/prescriber-contact", body)
    def update_order_patient_link(
        self,
        order_id: int,
        patient_id: int,
        notes: str = "",
    ) -> Any:
        body = {
            "updated_by": "Nova",
            "notes": notes,
            "patient_id": int(patient_id),
        }
        return self._patch(f"/orders/{order_id}/patient-link", body)
    def update_order_item_refills(
        self,
        order_id: int,
        item_id: int,
        refills: int,
        notes: str = "",
    ) -> Any:
        body = {
            "updated_by": "Nova",
            "notes": notes,
            "refills": int(refills),
        }
        return self._patch(f"/orders/{order_id}/items/{item_id}/refills", body)
    def attach_order_documents(
        self,
        order_id: int,
        attachments: List[Dict[str, Any]],
        patient_id: Optional[int] = None,
        notes: str = "",
        document_type: str = "rx",
    ) -> Any:
        body = {
            "attachments": attachments,
            "updated_by": "Nova",
            "notes": notes,
            "document_type": document_type,
        }
        if patient_id is not None:
            body["patient_id"] = int(patient_id)
        return self._post(f"/orders/{order_id}/attachments", body)
    def delete_order(
        self,
        order_id: int,
        reason: str = "",
        preserve_audit_trail: bool = True,
    ) -> Any:
        return self._delete(
            f"/orders/{order_id}",
            {
                "deleted_by": "Nova",
                "reason": reason,
                "preserve_audit_trail": preserve_audit_trail,
            },
        )
    def get_orders_by_status(self, status: str, limit: int = 50) -> Any:
        return self._get(f"/orders/status/{status}", {"limit": limit})
    def get_pending_approvals(self) -> Any:
        return self._get("/orders/pending-approvals")
    def process_approval(self, order_id: int, action: str, reason: str = "") -> Any:
        return self._post(f"/orders/{order_id}/approval",
                          {"action": action, "by": "Nova", "reason": reason})
    def get_refills_due(
        self,
        days: int = 7,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Any:
        params: Dict[str, Any] = {"days": int(days)}
        if start_date:
            params["start_date"] = str(start_date)
        if end_date:
            params["end_date"] = str(end_date)
        return self._get("/orders/refills-due", params)
    def get_deleted_orders(self) -> Any:
        return self._get("/orders/deleted")
    def check_refill_eligibility(self, last_filled: str, day_supply: int, quantity: int,
                                  insurance_type: str = "Commercial",
                                  max_quantity_per_month: int = 0) -> Any:
        return self._post("/insurance/check-refill", {
            "last_filled": last_filled, "day_supply": day_supply, "quantity": quantity,
            "insurance_type": insurance_type, "max_quantity_per_month": max_quantity_per_month
        })
    def search_prescribers(self, q: str) -> Any:
        return self._get("/prescribers/search", {"q": q})
    def get_prescriber(self, prescriber_id: int) -> Any:
        return self._get(f"/prescribers/{prescriber_id}")
    def get_prescriber_by_npi(self, npi: str) -> Any:
        return self._get(f"/prescribers/npi/{npi}")
    def list_inventory(self, needs_reorder=None, in_stock_only=None, out_of_stock=None) -> Any:
        params = {}
        if needs_reorder is not None: params["needs_reorder"] = needs_reorder
        if in_stock_only is not None: params["in_stock_only"] = in_stock_only
        if out_of_stock is not None:  params["out_of_stock"] = out_of_stock
        return self._get("/inventory", params)
    def get_inventory_item(self, item_id: int) -> Any:
        return self._get(f"/inventory/{item_id}")
    def get_inventory_by_hcpcs(self, hcpcs_code: str) -> Any:
        return self._get(f"/inventory/hcpcs/{hcpcs_code}")
    def search_inventory(self, q: str) -> Any:
        return self._get("/inventory/search", {"q": q})
    def _pick_inventory_match(self, candidates: Any, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(candidates, list) or not candidates:
            return None

        def norm(value: Any) -> str:
            return str(value or "").strip().upper()

        wanted_item_number = norm(raw.get("item_number"))
        wanted_hcpcs = norm(raw.get("hcpcs"))
        wanted_description = norm(raw.get("description"))

        if wanted_item_number:
            exact = [item for item in candidates if norm(item.get("item_number")) == wanted_item_number]
            if exact:
                return exact[0]

        if wanted_hcpcs:
            exact = [item for item in candidates if norm(item.get("hcpcs_code")) == wanted_hcpcs]
            if exact:
                return exact[0]

        if wanted_description:
            exact = [item for item in candidates if norm(item.get("description")) == wanted_description]
            if exact:
                return exact[0]

        if len(candidates) == 1:
            return candidates[0]

        return None
    def _resolve_inventory_for_order_item(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        inventory_item_id = raw.get("inventory_item_id")
        if inventory_item_id not in (None, ""):
            try:
                record = self.get_inventory_item(int(inventory_item_id))
            except Exception:
                record = None
            if isinstance(record, dict) and not record.get("error"):
                return record

        item_number = str(raw.get("item_number") or "").strip()
        if item_number:
            matches = self.search_inventory(item_number)
            match = self._pick_inventory_match(matches, raw)
            if match:
                return match

        hcpcs = str(raw.get("hcpcs") or "").strip()
        if hcpcs:
            record = self.get_inventory_by_hcpcs(hcpcs)
            if isinstance(record, dict) and not record.get("error"):
                return record

        description = str(raw.get("description") or "").strip()
        if description:
            matches = self.search_inventory(description)
            match = self._pick_inventory_match(matches, raw)
            if match:
                return match

        return None
    def get_claims_aging(self) -> Any:
        return self._get("/billing/claims/aging")
    def get_reconciliation(self, months: int = 12) -> Any:
        return self._get("/billing/reconciliation", {"months": months})
    def get_reconciliation_orders(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        insurance: str = "All",
        limit: int = 1000,
    ) -> Any:
        params: Dict[str, Any] = {"insurance": insurance, "limit": int(limit)}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._get("/billing/reconciliation/orders", params)
    def update_reconciliation_paid(self, updates: List[Dict[str, Any]], notes: str = "") -> Any:
        body = {
            "updates": updates,
            "updated_by": "Nova",
            "notes": notes,
        }
        return self._post("/billing/reconciliation/orders/paid", body)
    def open_reconciliation_report_ui(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        insurance: Optional[str] = None,
        notes: str = "",
    ) -> Any:
        body: Dict[str, Any] = {
            "requested_by": "Nova",
            "notes": notes,
        }
        if start_date:
            body["start_date"] = start_date
        if end_date:
            body["end_date"] = end_date
        if insurance:
            body["insurance"] = insurance
        return self._post("/ui/reports/reconciliation/open", body)
    def get_fee_schedule(self, hcpcs: str, rental: bool = False) -> Any:
        return self._get(f"/billing/fee-schedule/{hcpcs}", {"rental": rental})
    def get_billing_summary(self) -> Any:
        return self._get("/billing/summary")
    def get_claims(self, status: str = None, limit: int = 50) -> Any:
        params = {"limit": limit}
        if status: params["status"] = status
        return self._get("/billing/claims", params)
    def get_profit_report(self, start_date=None, end_date=None) -> Any:
        params = {}
        if start_date: params["start_date"] = start_date
        if end_date:   params["end_date"]   = end_date
        return self._get("/reports/profit", params)
    def get_inventory_value_report(self) -> Any:
        return self._get("/reports/inventory-value")
    def get_gross_margin_report(self) -> Any:
        return self._get("/reports/gross-margin")
    def get_low_stock_report(self) -> Any:
        return self._get("/reports/low-stock")
    def get_out_of_stock_report(self) -> Any:
        return self._get("/reports/out-of-stock")
    def get_reorder_by_vendor(self) -> Any:
        return self._get("/reports/reorder-by-vendor")
    def get_orders_by_status_report(self) -> Any:
        return self._get("/reports/orders-by-status")
    def get_orders_by_date(self, start_date: str, end_date: str) -> Any:
        return self._get("/reports/orders-by-date", {"start_date": start_date, "end_date": end_date})
    def get_orders_filtered(
        self,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> Any:
        params: Dict[str, Any] = {
            "limit": int(limit),
            "offset": int(offset),
        }
        if status:
            params["status"] = status
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._get("/orders/filter", params)
    def process_remittance(self, pdf_path: str) -> Any:
        return self._post("/remittance/parse", {"pdf_path": pdf_path})
    def process_refill(self, order_id: int) -> Any:
        return self._post(f"/orders/{order_id}/process-refill", {})
    def list_notes(self, search: str = None) -> Any:
        params = {}
        if search: params["search"] = search
        return self._get("/notes", params)
    def create_note(self, title: str, body: str, pinned: bool = False) -> Any:
        return self._post("/notes", {"title": title, "body": body, "pinned": pinned})

    def create_patient_tracking_note(
        self,
        patient_id: int,
        disposition: str,
        summary: str,
        prescriber: str = "",
        destination: str = "",
        callback_phone: str = "",
        created_by: str = "Nova",
        pinned: bool = True,
    ) -> Any:
        """Create a sticky note linked to a patient for RX outcomes when no order is created."""
        try:
            conn = self._db_conn("patients.db")
            patient_row = conn.execute(
                "SELECT id, first_name, last_name, notes FROM patients WHERE id = ?",
                (int(patient_id),),
            ).fetchone()
            patient = dict(patient_row) if patient_row else None
            if not patient:
                return {"error": f"Patient {patient_id} not found"}

            from dmelogic.db.sticky_notes import create_note as _create_note, set_note_links as _set_links

            disposition_value = str(disposition or "other").strip().lower()
            label_map = {
                "forwarded": "Forwarded",
                "transferred": "Transferred",
                "unable_to_fill": "Unable To Fill",
                "other": "Other",
            }
            disposition_label = label_map.get(disposition_value, "Other")

            patient_name = f"{patient.get('last_name', '')}, {patient.get('first_name', '')}".strip(", ")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines = [
                f"[RX Tracking {ts}]",
                f"Patient: {patient_name}",
                f"Disposition: {disposition_label}",
                f"Summary: {str(summary or '').strip()}",
            ]
            if str(prescriber or "").strip():
                lines.append(f"Prescriber: {str(prescriber).strip()}")
            if str(destination or "").strip():
                lines.append(f"Destination: {str(destination).strip()}")
            if str(callback_phone or "").strip():
                lines.append(f"Callback: {str(callback_phone).strip()}")
            if str(created_by or "").strip():
                lines.append(f"Recorded by: {str(created_by).strip()}")

            note_body = "\n".join(lines)
            note_title = f"RX Tracking - {disposition_label}"
            note_id = _create_note(note_title, note_body, pinned=bool(pinned), folder_path=self.folder_path)
            _set_links(int(note_id), [("patient", int(patient_id))], folder_path=self.folder_path)

            # Also append into patients.notes so tracking is visible in core patient record UI.
            try:
                existing_notes = str(patient.get("notes") or "").strip()
                update_payload = note_body if not existing_notes else (existing_notes + "\n\n" + note_body)
                conn.execute("UPDATE patients SET notes = ? WHERE id = ?", (update_payload, int(patient_id)))
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

            try:
                logger.info(
                    "tracking_note_created patient_id=%s note_id=%s disposition=%s",
                    int(patient_id),
                    int(note_id),
                    disposition_label,
                )
            except Exception:
                pass
            return {
                "success": True,
                "note_id": int(note_id),
                "patient_id": int(patient_id),
                "patient_name": patient_name,
                "disposition": disposition_label,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_patient_tracking_notes(self, patient_id: int, limit: int = 20) -> Any:
        """Return RX tracking entries stored for a patient (sticky notes + patient.notes excerpt)."""
        try:
            patient = self.get_patient(int(patient_id))
            if isinstance(patient, dict) and patient.get("error"):
                return patient

            rows = self.get_patient_notes(int(patient_id))
            notes = rows if isinstance(rows, list) else []
            tracking = []
            for n in notes:
                title = str((n or {}).get("title") or "")
                body = str((n or {}).get("body") or "")
                if title.lower().startswith("rx tracking") or "[rx tracking" in body.lower():
                    tracking.append(n)

            tracking = sorted(
                tracking,
                key=lambda x: str((x or {}).get("updated_at") or (x or {}).get("created_at") or ""),
                reverse=True,
            )[: max(1, int(limit))]

            patient_notes = ""
            if isinstance(patient, dict):
                patient_notes = str(patient.get("notes") or "")

            return {
                "patient_id": int(patient_id),
                "patient_name": (
                    f"{str(patient.get('last_name') or '').strip()}, {str(patient.get('first_name') or '').strip()}".strip(", ")
                    if isinstance(patient, dict)
                    else ""
                ),
                "tracking_notes": tracking,
                "tracking_count": len(tracking),
                "patient_notes_excerpt": patient_notes[-4000:] if patient_notes else "",
            }
        except Exception as e:
            return {"error": str(e)}

    def morning_summary(self) -> Any:
        return self._get("/agent/morning-summary")

    # ── New Nova workflow helpers ──────────────────────────────────────
    def create_order(self, payload: Dict[str, Any]) -> Any:
        """Create an order directly using the domain DB layer."""
        try:
            from dmelogic.db.models import OrderInput, OrderItemInput, BillingType, OrderStatus
            from dmelogic.db.orders import create_order as create_order_db

            patient_last_name = str(payload.get("patient_last_name") or "").strip()
            patient_first_name = str(payload.get("patient_first_name") or "").strip()
            if not patient_last_name or not patient_first_name:
                return {"error": "patient_last_name and patient_first_name are required"}

            items_in = payload.get("items") or []
            if not isinstance(items_in, list) or not items_in:
                return {"error": "items must be a non-empty list"}

            def _extract_refill_count(text: Any) -> Optional[int]:
                s = str(text or "").strip()
                if not s:
                    return None
                m = re.search(r"\brefills?\b\s*[:#=\-]?\s*(\d{1,2})\b", s, flags=re.IGNORECASE)
                if not m:
                    return None
                try:
                    return int(m.group(1))
                except Exception:
                    return None

            default_refills: Optional[int] = None
            refill_text_sources = [
                payload.get("doctor_directions"),
                payload.get("notes"),
                payload.get("sig"),
                payload.get("prescription_text"),
                payload.get("raw_text"),
                payload.get("ocr_text"),
            ]
            for text in refill_text_sources:
                default_refills = _extract_refill_count(text)
                if default_refills is not None:
                    break

            items: List[OrderItemInput] = []
            for idx, raw in enumerate(items_in, start=1):
                if not isinstance(raw, dict):
                    return {"error": f"items[{idx}] must be an object"}

                inventory_match = self._resolve_inventory_for_order_item(raw)

                hcpcs = str(raw.get("hcpcs") or "").strip()
                description = str(raw.get("description") or "").strip()
                item_number = str(raw.get("item_number") or "").strip() or None

                unit_cost_raw = raw.get("cost_ea")
                if unit_cost_raw in (None, ""):
                    unit_cost_raw = raw.get("unit_cost")

                if inventory_match:
                    hcpcs = str(inventory_match.get("hcpcs_code") or hcpcs).strip()
                    description = str(inventory_match.get("description") or description).strip()
                    item_number = str(inventory_match.get("item_number") or item_number or "").strip() or None
                    if unit_cost_raw in (None, ""):
                        retail_price = inventory_match.get("retail_price")
                        inventory_cost = inventory_match.get("cost")
                        unit_cost_raw = retail_price if retail_price not in (None, "", 0, 0.0, "0", "0.0") else inventory_cost

                cost_ea = None
                if unit_cost_raw not in (None, ""):
                    try:
                        cost_ea = Decimal(str(unit_cost_raw))
                    except (InvalidOperation, TypeError, ValueError):
                        return {"error": f"items[{idx}].cost_ea is invalid"}

                refills_raw = raw.get("refills")
                if refills_raw in (None, ""):
                    refills_raw = raw.get("refill_count")

                if refills_raw in (None, ""):
                    item_level_text = " ".join(
                        str(raw.get(k) or "")
                        for k in ("directions", "sig", "notes", "description")
                    )
                    item_refills = _extract_refill_count(item_level_text)
                    refills_value = item_refills if item_refills is not None else (default_refills if default_refills is not None else 0)
                else:
                    try:
                        refills_value = int(refills_raw)
                    except (TypeError, ValueError):
                        return {"error": f"items[{idx}].refills is invalid"}

                items.append(
                    OrderItemInput(
                        hcpcs=hcpcs,
                        description=description,
                        quantity=int(raw.get("quantity", 1) or 1),
                        refills=refills_value,
                        days_supply=int(raw.get("days_supply", 30) or 30),
                        directions=(str(raw.get("directions") or "").strip() or None),
                        item_number=item_number,
                        cost_ea=cost_ea,
                        is_placeholder=bool(raw.get("is_placeholder", False)) and not bool(inventory_match),
                    )
                )

            billing_type = str(payload.get("billing_type") or BillingType.INSURANCE.value)
            place_of_service = str(payload.get("place_of_service") or payload.get("placeOfService") or payload.get("pos") or "12").strip()
            requested_status = str(payload.get("order_status") or OrderStatus.PENDING.value)
            on_hold_requested = bool(payload.get("on_hold")) or requested_status == OrderStatus.ON_HOLD.value
            hold_until_date = str(payload.get("hold_until_date") or payload.get("active_date") or "").strip()
            hold_resume_status = str(payload.get("hold_resume_status") or payload.get("resume_status") or OrderStatus.PENDING.value).strip()
            hold_note = str(payload.get("hold_note") or payload.get("note") or "").strip()

            patient_id_value: Optional[int] = None
            raw_patient_id = payload.get("patient_id")
            if raw_patient_id not in (None, ""):
                try:
                    patient_id_value = int(raw_patient_id)
                except (TypeError, ValueError):
                    return {"error": "patient_id must be an integer"}
            else:
                try:
                    from dmelogic.db.patients import create_or_get_patient
                    patient_id_value = create_or_get_patient(
                        last_name=patient_last_name,
                        first_name=patient_first_name,
                        dob=(str(payload.get("patient_dob") or "").strip() or None),
                        phone=(str(payload.get("patient_phone") or "").strip() or None),
                        address=(str(payload.get("patient_address") or "").strip() or None),
                        primary_insurance=(str(payload.get("primary_insurance") or "").strip() or None),
                        primary_insurance_id=(str(payload.get("primary_insurance_id") or "").strip() or None),
                        folder_path=self.folder_path,
                    )
                except Exception as e:
                    log.warning(f"create_or_get_patient failed while creating order: {e}")

            if on_hold_requested and not hold_until_date:
                return {"error": "hold_until_date is required when on_hold is true"}

            order_status = OrderStatus.ON_HOLD.value if on_hold_requested else requested_status

            order_input = OrderInput(
                patient_last_name=patient_last_name,
                patient_first_name=patient_first_name,
                patient_dob=(str(payload.get("patient_dob") or "").strip() or None),
                patient_phone=(str(payload.get("patient_phone") or "").strip() or None),
                patient_address=(str(payload.get("patient_address") or "").strip() or None),
                patient_id=patient_id_value,
                prescriber_name=(str(payload.get("prescriber_name") or "").strip() or None),
                prescriber_npi=(str(payload.get("prescriber_npi") or "").strip() or None),
                rx_date=(str(payload.get("rx_date") or "").strip() or None),
                order_date=(str(payload.get("order_date") or "").strip() or None),
                delivery_date=(str(payload.get("delivery_date") or "").strip() or None),
                billing_type=billing_type,
                place_of_service=place_of_service,
                order_status=order_status,
                primary_insurance=(str(payload.get("primary_insurance") or "").strip() or None),
                primary_insurance_id=(str(payload.get("primary_insurance_id") or "").strip() or None),
                icd_code_1=(str(payload.get("icd_code_1") or "").strip() or None),
                icd_code_2=(str(payload.get("icd_code_2") or "").strip() or None),
                icd_code_3=(str(payload.get("icd_code_3") or "").strip() or None),
                icd_code_4=(str(payload.get("icd_code_4") or "").strip() or None),
                icd_code_5=(str(payload.get("icd_code_5") or "").strip() or None),
                doctor_directions=(str(payload.get("doctor_directions") or "").strip() or None),
                notes=(str(payload.get("notes") or "").strip() or None),
                items=items,
                agent_created=bool(payload.get("agent_created", False)),
                skip_icd_validation=bool(payload.get("skip_icd_validation", False)),
            )

            new_order_id = create_order_db(order_input, folder_path=self.folder_path)

            if on_hold_requested:
                from dmelogic.db.orders import set_order_hold, update_order_status

                update_order_status(new_order_id, OrderStatus.ON_HOLD.value, folder_path=self.folder_path)
                set_order_hold(
                    order_id=new_order_id,
                    hold_until_date=hold_until_date,
                    resume_status=hold_resume_status or OrderStatus.PENDING.value,
                    note=hold_note,
                    folder_path=self.folder_path,
                )

            return {
                "success": True,
                "order_id": int(new_order_id),
                "order_number": f"ORD-{int(new_order_id):03d}",
                "patient_id": patient_id_value,
                "order_status": order_status,
                "place_of_service": place_of_service,
                "on_hold": on_hold_requested,
                "hold_until_date": hold_until_date or None,
                "hold_resume_status": hold_resume_status or None,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_unbilled_orders(self, limit: int = 100) -> Any:
        # Prefer status endpoint if status values are normalized in workflow.
        data = self.get_orders_by_status("Unbilled", limit=limit)
        if isinstance(data, dict) and data.get("error"):
            return data
        rows = data if isinstance(data, list) else []
        if rows:
            return rows
        # Fallback to billed flag.
        try:
            conn = self._db_conn("orders.db")
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT id, order_date, patient_last_name, patient_first_name, order_status, "
                    "primary_insurance, billed, paid, tracking_number "
                    "FROM orders "
                    "WHERE COALESCE(billed, 0) = 0 AND COALESCE(order_status, '') != 'Cancelled' "
                    "ORDER BY id DESC "
                    "LIMIT ?"
                ),
                (int(limit),),
            )
            out = [dict(r) for r in cur.fetchall()]
            conn.close()
            return out
        except Exception as e:
            return {"error": str(e)}

    def get_orders_missing_docs(self, limit: int = 100) -> Any:
        return self.get_orders_by_status("Docs Needed", limit=limit)

    # ── Order audit helpers ────────────────────────────────────────────────

    def _audit_query_with_summary(
        self, scope_sql: str, problem_sql: str, params: tuple,
        scope_label: str, problem_key: str, limit: int
    ) -> Any:
        """Run an audit query and return both a summary and the problem rows."""
        try:
            conn = self._db_conn("orders.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM orders WHERE {scope_sql}", params)
            total = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM orders WHERE {scope_sql} AND NOT ({problem_sql})", params)
            ok_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM orders WHERE {scope_sql} AND ({problem_sql})", params)
            issue_count = cur.fetchone()[0]
            cur.execute(
                f"SELECT * FROM (SELECT id, order_date, order_status, "
                f"patient_last_name || ', ' || patient_first_name AS patient,"
                f"prescriber_name, prescriber_npi, prescriber_id,"
                f"primary_insurance, icd_code_1, attached_rx_files "
                f"FROM orders WHERE {scope_sql} AND ({problem_sql}) ORDER BY order_date DESC) LIMIT ?",
                params + (int(limit),),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return {
                "summary": {
                    "total_orders_in_scope": total,
                    "orders_passing": ok_count,
                    "orders_with_issue": issue_count,
                    "results_shown": len(rows),
                    "results_capped": issue_count > len(rows),
                    "scope": scope_label,
                },
                problem_key: rows,
            }
        except Exception as e:
            return {"error": str(e)}

    def audit_missing_prescriber(self, limit: int = 200) -> Any:
        return self._audit_query_with_summary(
            scope_sql="COALESCE(order_status,'') NOT IN ('Cancelled','Deleted')",
            problem_sql="prescriber_id IS NULL OR TRIM(COALESCE(prescriber_npi,'')) = ''",
            params=(),
            scope_label="All non-cancelled/deleted orders",
            problem_key="orders_missing_prescriber",
            limit=limit,
        )

    def audit_missing_insurance(self, limit: int = 200) -> Any:
        return self._audit_query_with_summary(
            scope_sql="COALESCE(order_status,'') NOT IN ('Cancelled','Deleted')",
            problem_sql="TRIM(COALESCE(primary_insurance,'')) = ''",
            params=(),
            scope_label="All non-cancelled/deleted orders",
            problem_key="orders_missing_insurance",
            limit=limit,
        )

    def audit_missing_diagnosis(self, limit: int = 200) -> Any:
        return self._audit_query_with_summary(
            scope_sql="COALESCE(order_status,'') NOT IN ('Cancelled','Deleted')",
            problem_sql=(
                "TRIM(COALESCE(icd_code_1,'')) = '' AND TRIM(COALESCE(icd_code_2,'')) = '' "
                "AND TRIM(COALESCE(icd_code_3,'')) = '' AND TRIM(COALESCE(icd_code_4,'')) = '' "
                "AND TRIM(COALESCE(icd_code_5,'')) = ''"
            ),
            params=(),
            scope_label="All non-cancelled/deleted orders",
            problem_key="orders_missing_diagnosis",
            limit=limit,
        )

    def audit_missing_documents(self, limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            audited_statuses = ('Active', 'Pending', 'Shipped', 'Unbilled', 'Billed')
            ph = ",".join("?" * len(audited_statuses))

            cur.execute(f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph})", audited_statuses)
            total_in_scope = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph}) AND TRIM(COALESCE(attached_rx_files,'')) != ''",
                audited_statuses,
            )
            have_docs = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph}) AND TRIM(COALESCE(attached_rx_files,'')) = ''",
                audited_statuses,
            )
            missing_count = cur.fetchone()[0]

            cur.execute(
                f"""SELECT id, order_date, order_status,
                           patient_last_name || ', ' || patient_first_name AS patient,
                           attached_rx_files, attached_signed_ticket_files
                    FROM orders
                    WHERE COALESCE(order_status,'') IN ({ph})
                      AND TRIM(COALESCE(attached_rx_files,'')) = ''
                    ORDER BY order_date DESC LIMIT ?""",
                audited_statuses + (int(limit),),
            )
            missing = [dict(r) for r in cur.fetchall()]
            conn.close()

            return {
                "summary": {
                    "audited_statuses": list(audited_statuses),
                    "total_orders_in_scope": total_in_scope,
                    "orders_with_rx_document": have_docs,
                    "orders_missing_rx_document": missing_count,
                    "results_shown": len(missing),
                    "results_capped": missing_count > len(missing),
                    "note": "Only Active/Pending/Shipped/Unbilled/Billed orders are audited. Completed, Cancelled, and other statuses are excluded.",
                },
                "orders_missing_documents": missing,
            }
        except Exception as e:
            return {"error": str(e)}

    def audit_missing_delivery_ticket(self, limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            audited_statuses = ('Shipped', 'Completed', 'Billed', 'Unbilled', 'Paid')
            ph = ",".join("?" * len(audited_statuses))

            cur.execute(f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph})", audited_statuses)
            total_in_scope = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph}) AND TRIM(COALESCE(attached_signed_ticket_files,'')) != ''",
                audited_statuses,
            )
            have_ticket = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM orders WHERE COALESCE(order_status,'') IN ({ph}) AND TRIM(COALESCE(attached_signed_ticket_files,'')) = ''",
                audited_statuses,
            )
            missing_count = cur.fetchone()[0]

            cur.execute(
                f"""SELECT id, order_date, order_status,
                           patient_last_name || ', ' || patient_first_name AS patient,
                           delivery_date, tracking_number,
                           attached_signed_ticket_files, attached_rx_files
                    FROM orders
                    WHERE COALESCE(order_status,'') IN ({ph})
                      AND TRIM(COALESCE(attached_signed_ticket_files,'')) = ''
                    ORDER BY order_date DESC LIMIT ?""",
                audited_statuses + (int(limit),),
            )
            missing = [dict(r) for r in cur.fetchall()]
            conn.close()

            return {
                "summary": {
                    "audited_statuses": list(audited_statuses),
                    "total_orders_in_scope": total_in_scope,
                    "orders_with_delivery_ticket": have_ticket,
                    "orders_missing_delivery_ticket": missing_count,
                    "results_shown": len(missing),
                    "results_capped": missing_count > len(missing),
                    "note": "Audits Shipped, Completed, Billed, Unbilled, and Paid orders for a signed delivery confirmation/ticket.",
                },
                "orders_missing_delivery_ticket": missing,
            }
        except Exception as e:
            return {"error": str(e)}

    def audit_stale_orders(self, pending_days: int = 7, active_days: int = 60,
                           hold_days: int = 14, limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """SELECT id, order_date, order_status,
                          patient_last_name || ', ' || patient_first_name AS patient,
                          CAST(julianday('now') - julianday(COALESCE(updated_date, order_date)) AS INTEGER) AS days_in_status
                   FROM orders
                   WHERE COALESCE(order_status,'') NOT IN ('Cancelled','Deleted','Completed','Paid','Closed')
                     AND (
                           (order_status = 'Pending'  AND julianday('now') - julianday(COALESCE(updated_date, order_date)) > ?)
                        OR (order_status = 'Active'   AND julianday('now') - julianday(COALESCE(updated_date, order_date)) > ?)
                        OR (order_status = 'On Hold'  AND julianday('now') - julianday(COALESCE(updated_date, order_date)) > ?)
                     )
                   ORDER BY days_in_status DESC LIMIT ?""",
                (pending_days, active_days, hold_days, int(limit)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return {"thresholds": {"Pending": pending_days, "Active": active_days, "On Hold": hold_days}, "orders": rows}
        except Exception as e:
            return {"error": str(e)}

    def audit_duplicate_hcpcs(self, limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """SELECT o.patient_id,
                          o.patient_last_name || ', ' || o.patient_first_name AS patient,
                          oi.hcpcs_code,
                          COUNT(DISTINCT o.id) AS order_count,
                          GROUP_CONCAT(o.id, ', ') AS order_ids,
                          GROUP_CONCAT(o.order_status, ', ') AS statuses
                   FROM orders o
                   JOIN order_items oi ON oi.order_id = o.id
                   WHERE COALESCE(o.order_status,'') NOT IN ('Cancelled','Deleted','Completed','Paid','Closed')
                     AND TRIM(COALESCE(oi.hcpcs_code,'')) != ''
                   GROUP BY o.patient_id, oi.hcpcs_code
                   HAVING COUNT(DISTINCT o.id) > 1
                   ORDER BY order_count DESC LIMIT ?""",
                (int(limit),),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def audit_unbilled_completed(self, limit: int = 200) -> Any:
        return self._audit_query(
            """SELECT id, order_date, order_status,
                      patient_last_name || ', ' || patient_first_name AS patient,
                      primary_insurance, billed, paid, delivery_date
               FROM orders
               WHERE COALESCE(order_status,'') IN ('Completed','Shipped','Closed')
                 AND COALESCE(billed, 0) = 0
                 AND COALESCE(paid, 0) = 0
               ORDER BY order_date ASC LIMIT ?""",
            limit=limit,
        )

    def audit_shipped_no_tracking(self, limit: int = 200) -> Any:
        return self._audit_query(
            """SELECT id, order_date, order_status,
                      patient_last_name || ', ' || patient_first_name AS patient,
                      tracking_number, delivery_date, patient_phone
               FROM orders
               WHERE order_status = 'Shipped'
                 AND TRIM(COALESCE(tracking_number,'')) = ''
                 AND TRIM(COALESCE(delivery_date,'')) = ''
               ORDER BY order_date ASC LIMIT ?""",
            limit=limit,
        )

    def get_must_go_out(self, status: str = "Pending", limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            cur = conn.cursor()
            if not self._table_exists(conn, "must_go_out_queue"):
                conn.close()
                return []
            cur.execute(
                (
                    "SELECT id, order_id, patient_name, patient_phone, notes, status, created_at, completed_at "
                    "FROM must_go_out_queue "
                    "WHERE (? = 'All' OR COALESCE(status, 'Pending') = ?) "
                    "ORDER BY created_at DESC, id DESC "
                    "LIMIT ?"
                ),
                (status, status, int(limit)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def add_must_go_out(
        self,
        patient_name: str = "",
        patient_phone: str = "",
        notes: str = "",
        order_id: int | None = None,
    ) -> Any:
        """Add an entry to the DMELogic 'Must Go Out' queue so staff follow up."""
        try:
            name = str(patient_name or "").strip()
            phone = str(patient_phone or "").strip()
            note = str(notes or "").strip()
            oid = None
            if order_id not in (None, "", 0, "0"):
                try:
                    oid = int(order_id)
                except Exception:
                    oid = None
            if oid is None and not name:
                return {"error": "Provide either an order_id or a patient_name."}
            conn = self._db_conn("orders.db")
            if not self._table_exists(conn, "must_go_out_queue"):
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS must_go_out_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER,
                        patient_name TEXT,
                        patient_phone TEXT,
                        notes TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'Pending',
                        completed_at TEXT
                    )
                    """
                )
            cur = conn.execute(
                """
                INSERT INTO must_go_out_queue (order_id, patient_name, patient_phone, notes)
                VALUES (?, ?, ?, ?)
                """,
                (oid, name or None, phone or None, note or None),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            return {"success": True, "id": int(new_id), "status": "Pending"}
        except Exception as e:
            return {"error": str(e)}

    def update_order_tracking(self, order_id: int, tracking_number: str) -> Any:
        try:
            conn = self._db_conn("orders.db")
            cur = conn.cursor()
            cur.execute(
                "UPDATE orders SET tracking_number = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                (tracking_number, int(order_id)),
            )
            conn.commit()
            changed = cur.rowcount
            conn.close()
            if changed <= 0:
                return {"error": f"Order {order_id} not found"}
            return {"success": True, "order_id": int(order_id), "tracking_number": tracking_number}
        except Exception as e:
            return {"error": str(e)}

    def schedule_delivery(self, order_id: int, delivery_date: str, driver_name: str = "") -> Any:
        try:
            conn = self._db_conn("orders.db")
            cur = conn.cursor()
            cols = self._table_columns(conn, "orders")
            note_suffix = f"Driver: {driver_name}" if driver_name else ""
            if "driver_name" in cols:
                cur.execute(
                    "UPDATE orders SET delivery_date = ?, driver_name = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                    (delivery_date, driver_name, int(order_id)),
                )
            else:
                cur.execute("SELECT COALESCE(notes,'') AS notes FROM orders WHERE id = ?", (int(order_id),))
                row = cur.fetchone()
                if not row:
                    conn.close()
                    return {"error": f"Order {order_id} not found"}
                notes = (row["notes"] or "").strip()
                if note_suffix and note_suffix.lower() not in notes.lower():
                    notes = (notes + "\n" + note_suffix).strip()
                cur.execute(
                    "UPDATE orders SET delivery_date = ?, notes = ?, updated_date = CURRENT_TIMESTAMP WHERE id = ?",
                    (delivery_date, notes, int(order_id)),
                )
            conn.commit()
            conn.close()
            return {
                "success": True,
                "order_id": int(order_id),
                "delivery_date": delivery_date,
                "driver_name": driver_name or None,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_expiring_authorizations(self, days: int = 30, limit: int = 200) -> Any:
        try:
            conn = self._db_conn("orders.db")
            cols = self._table_columns(conn, "orders")
            date_cols = [c for c in (
                "prior_auth_expiration", "prior_auth_expires", "auth_expiration_date", "authorization_expiration", "pa_expiration_date"
            ) if c in cols]
            if not date_cols:
                conn.close()
                return []
            date_col = date_cols[0]
            cur = conn.cursor()
            cur.execute(
                (
                    f"SELECT id, order_date, patient_last_name, patient_first_name, order_status, "
                    f"prescriber_name, primary_insurance, {date_col} AS auth_expiration "
                    "FROM orders "
                    f"WHERE COALESCE({date_col}, '') != '' "
                    f"ORDER BY {date_col} ASC "
                    "LIMIT ?"
                ),
                (int(limit),),
            )
            rows = []
            today = datetime.now().date()
            max_date = today + timedelta(days=max(1, int(days)))
            for r in cur.fetchall():
                d = dict(r)
                raw = str(d.get("auth_expiration") or "").strip()
                parsed = None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                    try:
                        parsed = datetime.strptime(raw, fmt).date()
                        break
                    except Exception:
                        continue
                if not parsed:
                    continue
                if today <= parsed <= max_date:
                    d["days_remaining"] = (parsed - today).days
                    rows.append(d)
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def get_claim_details(self, claim_id: int) -> Any:
        try:
            conn = self._db_conn("billing.db")
            cur = conn.cursor()
            cur.execute("SELECT * FROM claims WHERE id = ?", (int(claim_id),))
            row = cur.fetchone()
            conn.close()
            if not row:
                return {"error": f"Claim {claim_id} not found"}
            return dict(row)
        except Exception as e:
            return {"error": str(e)}

    def process_denial(self, claim_id: int) -> Any:
        claim = self.get_claim_details(claim_id)
        if isinstance(claim, dict) and claim.get("error"):
            return claim
        denial_text = str(claim.get("denial_reason") or claim.get("payer_response") or claim.get("status") or "").strip()
        code_match = re.search(r"\b([A-Z]\d{2,4}|CO-?\d{1,3}|PR-?\d{1,3})\b", denial_text.upper())
        denial_code = code_match.group(1) if code_match else "UNKNOWN"
        suggestions = {
            "CO16": "Missing/invalid required information. Verify demographics, NPI, and modifiers; re-submit corrected claim.",
            "CO18": "Duplicate claim/service. Confirm DOS and claim control numbers before resubmission.",
            "CO22": "Coordination of benefits issue. Update primary/secondary payer sequence.",
            "CO97": "Service bundled/included. Review billing modifiers and HCPCS combinations.",
            "PR1": "Deductible/co-insurance applies. Bill patient responsibility after payer adjudication.",
            "UNKNOWN": "Review ERA/EOB denial text and supporting docs; correct claim fields and submit corrected claim.",
        }
        recommendation = suggestions.get(denial_code.replace("-", ""), suggestions.get(denial_code), suggestions["UNKNOWN"])
        return {
            "claim_id": int(claim_id),
            "denial_code": denial_code,
            "denial_text": denial_text,
            "recommendation": recommendation,
            "claim": claim,
        }

    def get_denial_summary(self, days: int = 30) -> Any:
        try:
            conn = self._db_conn("billing.db")
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT COALESCE(denial_reason, payer_response, status, 'Unknown') AS reason, "
                    "COUNT(*) AS claim_count, "
                    "SUM(COALESCE(amount, billed_amount, charge_amount, 0)) AS total_amount "
                    "FROM claims "
                    "WHERE UPPER(COALESCE(status,'')) LIKE '%DENIED%' "
                    "OR COALESCE(denial_reason,'') != '' "
                    "GROUP BY COALESCE(denial_reason, payer_response, status, 'Unknown') "
                    "ORDER BY claim_count DESC"
                )
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def search_documents(self, query: str, limit: int = 25) -> Any:
        try:
            from dmelogic.db.base import resolve_db_path
            db_path = resolve_db_path("document_data.db", folder_path=self.folder_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                (
                    "SELECT filepath, rel_path, "
                    "snippet(ocr_index, 1, '[', ']', ' ... ', 18) AS snippet "
                    "FROM ocr_index "
                    "WHERE ocr_index MATCH ? "
                    "LIMIT ?"
                ),
                (str(query), int(limit)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def get_unmatched_documents(self, limit: int = 100) -> Any:
        try:
            from dmelogic.db.base import resolve_db_path
            db_path = resolve_db_path("document_data.db", folder_path=self.folder_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ocr_index'"
            ).fetchone()
            if not table_exists:
                conn.close()
                return []

            # Best-effort unmatched heuristic: files whose path/text does not contain order or patient link tags.
            cur.execute(
                (
                    "SELECT filepath, rel_path "
                    "FROM ocr_index "
                    "WHERE LOWER(COALESCE(filepath,'')) NOT LIKE '%order_%' "
                    "AND LOWER(COALESCE(filepath,'')) NOT LIKE '%patient_%' "
                    "AND LOWER(COALESCE(rel_path,'')) NOT LIKE '%order_%' "
                    "AND LOWER(COALESCE(rel_path,'')) NOT LIKE '%patient_%' "
                    "LIMIT ?"
                ),
                (int(limit),),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return {"error": str(e)}

    def create_task(self, title: str, assigned_to: str = "", priority: str = "Normal", note: str = "") -> Any:
        # Task persistence mapped to Nova reminders to keep behavior reliable across installs.
        payload = f"TASK [{priority}] {title}"
        if assigned_to:
            payload += f" | Assigned: {assigned_to}"
        if note:
            payload += f" | {note}"
        return {"task_text": payload}

    def process_refills_due_on_date(self, due_date: str, force: bool = True) -> Any:
        # Process one refill attempt per chain for the requested date.
        try:
            from dmelogic.db.refills import fetch_refills_due

            # Validate/normalize due date
            target = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    target = datetime.strptime(str(due_date).strip(), fmt).date()
                    break
                except Exception:
                    continue
            if target is None:
                return {"error": "Invalid due_date. Use YYYY-MM-DD or MM/DD/YYYY."}

            target_iso = target.strftime("%Y-%m-%d")
            today_iso = datetime.now().strftime("%Y-%m-%d")
            due_rows = fetch_refills_due(
                start_date=target_iso,
                end_date=target_iso,
                today=today_iso,
                folder_path=self.folder_path,
            )

            if not due_rows:
                return {
                    "success": True,
                    "due_date": target_iso,
                    "chains_found": 0,
                    "processed": [],
                    "skipped": [],
                    "failed": [],
                    "message": "No refill items due on that date.",
                }

            conn = self._db_conn("orders.db")
            cur = conn.cursor()

            # Build chain map from due rows: base_order_id -> representative metadata
            chains: Dict[int, Dict[str, Any]] = {}
            for row in due_rows:
                src_order_id = int(row.get("order_id") or 0)
                if not src_order_id:
                    continue

                cur.execute(
                    "SELECT id, COALESCE(parent_order_id, 0) AS parent_order_id, COALESCE(refill_number, 0) AS refill_number FROM orders WHERE id = ?",
                    (src_order_id,),
                )
                src = cur.fetchone()
                if not src:
                    continue

                base_id = int(src["parent_order_id"] or 0) or int(src["id"])
                if base_id not in chains:
                    chains[base_id] = {
                        "base_order_id": base_id,
                        "patient_name": row.get("patient_name"),
                        "patient_phone": row.get("patient_phone"),
                        "next_refill_due": row.get("next_refill_due"),
                        "source_order_ids": set(),
                    }
                chains[base_id]["source_order_ids"].add(src_order_id)

            processed = []
            skipped = []
            failed = []

            for base_id, meta in chains.items():
                # Always process the latest order in the chain (max refill_number, then max id).
                cur.execute(
                    (
                        "SELECT id, COALESCE(refill_number, 0) AS refill_number, "
                        "COALESCE(refill_completed, 0) AS refill_completed, "
                        "COALESCE(is_locked, 0) AS is_locked, "
                        "COALESCE(order_status, '') AS order_status "
                        "FROM orders "
                        "WHERE id = ? OR parent_order_id = ? "
                        "ORDER BY COALESCE(refill_number, 0) DESC, id DESC "
                        "LIMIT 1"
                    ),
                    (base_id, base_id),
                )
                latest = cur.fetchone()
                if not latest:
                    skipped.append({
                        "base_order_id": base_id,
                        "reason": "Chain not found",
                        "patient_name": meta.get("patient_name"),
                    })
                    continue

                target_order_id = int(latest["id"])

                # If latest itself is already refill_completed/locked, it's likely already processed.
                if int(latest["refill_completed"] or 0) == 1 or int(latest["is_locked"] or 0) == 1:
                    skipped.append({
                        "base_order_id": base_id,
                        "target_order_id": target_order_id,
                        "patient_name": meta.get("patient_name"),
                        "reason": "Latest chain order already marked refilled/locked",
                    })
                    continue

                result = self.process_refill(target_order_id)
                if isinstance(result, dict) and not result.get("error"):
                    processed.append({
                        "base_order_id": base_id,
                        "target_order_id": target_order_id,
                        "new_order_id": result.get("new_order_id"),
                        "patient_name": meta.get("patient_name"),
                        "next_refill_due": meta.get("next_refill_due"),
                    })
                else:
                    failed.append({
                        "base_order_id": base_id,
                        "target_order_id": target_order_id,
                        "patient_name": meta.get("patient_name"),
                        "next_refill_due": meta.get("next_refill_due"),
                        "error": (result or {}).get("error", "Unknown error"),
                    })

            conn.close()

            return {
                "success": len(failed) == 0,
                "due_date": target_iso,
                "chains_found": len(chains),
                "processed": processed,
                "skipped": skipped,
                "failed": failed,
                "summary": {
                    "processed_count": len(processed),
                    "skipped_count": len(skipped),
                    "failed_count": len(failed),
                },
            }
        except Exception as e:
            return {"error": str(e)}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", str(text).strip().lower()).strip("_") or "report"


def _normalize_report_name(report_name: str) -> str:
    raw = (report_name or "").strip().lower()
    key = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    aliases = {
        "inventory": "inventory_value",
        "inventory_report": "inventory_value",
        "margin": "gross_margin",
        "gross_margin_report": "gross_margin",
        "low_stock_items": "low_stock",
        "out_of_stock_items": "out_of_stock",
        "reorder": "reorder_by_vendor",
        "status_report": "orders_by_status",
        "orders_status": "orders_by_status",
        "orders_date": "orders_by_date",
        "aging": "claims_aging",
        "ar_aging": "claims_aging",
        "billing": "billing_summary",
        "summary": "billing_summary",
        "remittance": "remittance_processing",
        "remittance_report": "remittance_processing",
        "remittance_processing_report": "remittance_processing",
    }
    return aliases.get(key, key)


def _build_report_data(client: DMELogicClient, report_name: str, inp: Dict) -> Any:
    report = _normalize_report_name(report_name)
    if report == "profit":
        return client.get_profit_report(inp.get("start_date"), inp.get("end_date"))
    if report == "inventory_value":
        return client.get_inventory_value_report()
    if report == "gross_margin":
        return client.get_gross_margin_report()
    if report == "low_stock":
        return client.get_low_stock_report()
    if report == "out_of_stock":
        return client.get_out_of_stock_report()
    if report == "reorder_by_vendor":
        return client.get_reorder_by_vendor()
    if report == "orders_by_status":
        return client.get_orders_by_status_report()
    if report == "orders_by_date":
        start_date = inp.get("start_date")
        end_date = inp.get("end_date")
        if not start_date or not end_date:
            return {"error": "orders_by_date requires start_date and end_date in YYYY-MM-DD"}
        return client.get_orders_by_date(start_date, end_date)
    if report == "claims_aging":
        return client.get_claims_aging()
    if report == "billing_summary":
        return client.get_billing_summary()
    if report == "remittance_processing":
        pdf_path = (inp.get("pdf_path") or "").strip()
        if not pdf_path:
            return {"error": "remittance_processing requires pdf_path"}
        return client.process_remittance(pdf_path)
    return {"error": f"Unknown report_name: {report_name}"}


def _rows_from_report_data(data: Any, section: str = "") -> List[Dict[str, Any]]:
    section_key = (section or "").strip().lower()

    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []

    if section_key == "summary":
        totals = data.get("totals")
        if isinstance(totals, dict):
            summary_row = {
                "remittance_no": data.get("remittance_no", ""),
                "cycle": data.get("cycle", ""),
                "date": data.get("date", ""),
                "provider_id": data.get("provider_id", ""),
            }
            summary_row.update(totals)
            return [summary_row]
        if data and all(not isinstance(v, (dict, list)) for v in data.values()):
            return [data]

    if section_key:
        maybe_rows = data.get(section_key)
        if isinstance(maybe_rows, list):
            return [r for r in maybe_rows if isinstance(r, dict)]

    for key in ("orders", "by_status", "claims", "rows", "items", "denied_lines", "pending_lines", "unmatched"):
        value = data.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]

    # Fallback: export scalar dict as a single-row table
    if data and all(not isinstance(v, (dict, list)) for v in data.values()):
        return [data]
    return []


def _stringify_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if value is None:
        return ""
    return str(value)


def _export_report_data(report_name: str, data: Any, export_format: str = "csv", file_name: str = "", section: str = "") -> Dict[str, Any]:
    fmt = (export_format or "csv").strip().lower()
    if fmt not in {"csv", "json", "xlsx", "pdf"}:
        return {"error": "export_format must be csv, json, xlsx, or pdf"}

    REPORT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = _slugify(file_name) if file_name else f"{_slugify(report_name)}_{timestamp}"
    output_path = REPORT_EXPORT_DIR / f"{stem}.{fmt}"

    if fmt == "json":
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        row_count = len(_rows_from_report_data(data, section=section))
        return {
            "ok": True,
            "report_name": report_name,
            "format": fmt,
            "section": section or "all",
            "file_path": str(output_path.resolve()),
            "row_count": row_count,
            "exported_at": datetime.now().isoformat(),
        }

    rows = _rows_from_report_data(data, section=section)
    if not rows:
        return {"error": "No tabular rows found to export in this format. Try export_format=json or choose section=summary|denied_lines|pending_lines|unmatched."}

    headers: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                headers.append(str(key))

    if fmt == "csv":
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({h: _stringify_cell(row.get(h, "")) for h in headers})

    elif fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            return {"error": "openpyxl not installed. Install with: pip install openpyxl"}

        wb = Workbook()
        ws = wb.active
        ws.title = (_slugify(report_name)[:31] or "report")
        ws.append(headers)
        for row in rows:
            ws.append([_stringify_cell(row.get(h, "")) for h in headers])
        wb.save(output_path)

    elif fmt == "pdf":
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter, landscape
            from reportlab.lib.units import inch
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        except ImportError:
            return {"error": "reportlab not installed. Install with: pip install reportlab"}

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=landscape(letter),
            rightMargin=0.35 * inch,
            leftMargin=0.35 * inch,
            topMargin=0.35 * inch,
            bottomMargin=0.35 * inch,
        )
        styles = getSampleStyleSheet()
        title = f"Report: {report_name}"
        if section:
            title += f" ({section})"

        table_data = [headers] + [[_stringify_cell(row.get(h, "")) for h in headers] for row in rows]
        col_count = max(1, len(headers))
        page_width = landscape(letter)[0] - (0.7 * inch)
        col_width = page_width / col_count
        table = Table(table_data, repeatRows=1, colWidths=[col_width] * col_count)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eef8")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ]))
        doc.build([
            Paragraph(title, styles["Heading3"]),
            Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]),
            Spacer(1, 0.12 * inch),
            table,
        ])

    return {
        "ok": True,
        "report_name": report_name,
        "format": fmt,
        "section": section or "all",
        "file_path": str(output_path.resolve()),
        "row_count": len(rows),
        "exported_at": datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════
TOOLS = [
    {"name": "morning_summary",
     "description": "Daily digest: patient count, orders by status, pending approvals, refills due, low inventory, billing AR.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "dmelogic_api_call",
     "description": "Advanced: call any DMELogic API endpoint directly when a dedicated tool is not available.",
     "input_schema": {"type": "object", "properties": {
         "method": {"type": "string", "description": "GET | POST | PATCH | DELETE"},
         "path": {"type": "string", "description": "API path, e.g. /patients/123"},
         "params": {"type": "object"},
         "body": {"type": "object"}
     }, "required": ["method", "path"]}},
    {"name": "list_patients",
     "description": "List patients ordered by name.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "search_patients",
     "description": "Search patients by name. Handles partial, 'last first', 'last, first'.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "create_patient",
     "description": "Create a patient profile (or return existing match by name + DOB).",
     "input_schema": {"type": "object", "properties": {
         "first_name": {"type": "string"},
         "last_name": {"type": "string"},
         "dob": {"type": "string"},
         "phone": {"type": "string"},
         "address": {"type": "string"},
         "city": {"type": "string"},
         "state": {"type": "string"},
         "zip_code": {"type": "string"},
         "secondary_contact": {"type": "string"},
         "primary_insurance": {"type": "string"},
         "primary_insurance_id": {"type": "string"},
         "policy_number": {"type": "string"},
         "group_number": {"type": "string"},
         "secondary_insurance": {"type": "string"},
         "secondary_insurance_id": {"type": "string"},
         "notes": {"type": "string"}
     }, "required": ["first_name", "last_name"]}},
    {"name": "update_patient",
     "description": "Update demographics and insurance details for an existing patient.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "first_name": {"type": "string"},
         "last_name": {"type": "string"},
         "dob": {"type": "string"},
         "phone": {"type": "string"},
         "address": {"type": "string"},
         "city": {"type": "string"},
         "state": {"type": "string"},
         "zip_code": {"type": "string"},
         "secondary_contact": {"type": "string"},
         "primary_insurance": {"type": "string"},
         "primary_insurance_id": {"type": "string"},
         "policy_number": {"type": "string"},
         "group_number": {"type": "string"},
         "secondary_insurance": {"type": "string"},
         "secondary_insurance_id": {"type": "string"},
         "notes": {"type": "string"}
     }, "required": ["patient_id"]}},
    {"name": "search_patients_by_phone",
     "description": "Find patients by phone number.",
     "input_schema": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}},
    {"name": "get_patient",
     "description": "Get full patient details by ID.",
     "input_schema": {"type": "object", "properties": {"patient_id": {"type": "integer"}}, "required": ["patient_id"]}},
    {"name": "get_patient_orders",
     "description": "Get complete order history for a patient.",
     "input_schema": {"type": "object", "properties": {"patient_id": {"type": "integer"}}, "required": ["patient_id"]}},
    {"name": "get_patient_refills_eligible",
     "description": "Get orders eligible for refill for a patient.",
     "input_schema": {"type": "object", "properties": {"patient_id": {"type": "integer"}}, "required": ["patient_id"]}},
    {"name": "get_patient_notes",
     "description": "Get sticky notes linked to a patient.",
     "input_schema": {"type": "object", "properties": {"patient_id": {"type": "integer"}}, "required": ["patient_id"]}},
    {"name": "get_order",
     "description": "Get single order with all line items, tracking number, status, prescriber, insurance.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]}},
    {"name": "get_order_notes",
     "description": "Get sticky notes for an order.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]}},
    {"name": "get_orders_by_status",
     "description": "Get orders filtered by status: Pending, Active, Shipped, Completed, Cancelled, On Hold, Unbilled, Billed, Paid, Docs Needed, Ready, Submitted, Approved, Closed, Pending Approval.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["status"]}},
    {"name": "update_order_status",
     "description": "Update order status. Always confirm with user first. When setting status to Paid, include paid_date when available.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"}, "new_status": {"type": "string"}, "notes": {"type": "string"}, "paid_date": {"type": "string", "description": "Paid date as YYYY-MM-DD or MM/DD/YYYY"}},
         "required": ["order_id", "new_status"]}},
    {"name": "update_order_prescriber_contact",
     "description": "Update prescriber phone and/or fax on an existing order.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"},
         "prescriber_phone": {"type": "string"},
         "prescriber_fax": {"type": "string"},
         "notes": {"type": "string"}},
         "required": ["order_id"]}},
    {"name": "update_order_patient_link",
     "description": "Link an existing order to a patient by writing patient_id on the order.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"},
         "patient_id": {"type": "integer"},
         "notes": {"type": "string"}},
         "required": ["order_id", "patient_id"]}},
    {"name": "update_order_item_refills",
     "description": "Update the refill count for a specific order item.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"},
         "item_id": {"type": "integer"},
         "refills": {"type": "integer"},
         "notes": {"type": "string"}},
         "required": ["order_id", "item_id", "refills"]}},
    {"name": "attach_order_documents",
     "description": "Attach one or more existing document files to an order and link them to the patient profile. Use document_type='delivery_ticket' for signed delivery tickets; default is RX.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"},
         "patient_id": {"type": "integer"},
         "document_type": {"type": "string", "enum": ["rx", "delivery_ticket"], "description": "Attachment target on order. Defaults to 'rx'."},
         "attachments": {"type": "array", "items": {
             "type": "object",
             "properties": {
                 "source_path": {"type": "string"},
                 "original_name": {"type": "string"},
                 "description": {"type": "string"}
             },
             "required": ["source_path"]
         }},
         "notes": {"type": "string"}},
         "required": ["order_id", "attachments"]}},
    {"name": "delete_order",
     "description": "Delete an order. Use only after explicit user confirmation; can preserve deleted-order audit trail.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"},
         "reason": {"type": "string"},
         "preserve_audit_trail": {"type": "boolean"}},
         "required": ["order_id"]}},
    {"name": "get_pending_approvals",
     "description": "Get agent-created orders awaiting human approval.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "process_approval",
     "description": "Approve or reject an agent-created order. Confirm with user first.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer"}, "action": {"type": "string"}, "reason": {"type": "string"}},
         "required": ["order_id", "action"]}},
    {"name": "get_refills_due",
     "description": "Get orders with refills due. Supports next N days or explicit date range.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer"},
         "start_date": {"type": "string", "description": "YYYY-MM-DD or MM/DD/YYYY"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD or MM/DD/YYYY"}
     }, "required": []}},
    {"name": "check_refill_eligibility",
     "description": "Check refill eligibility. 75% rule for Medicare/Medicaid, 80% commercial.",
     "input_schema": {"type": "object", "properties": {
         "last_filled": {"type": "string"}, "day_supply": {"type": "integer"},
         "quantity": {"type": "integer"}, "insurance_type": {"type": "string"},
         "max_quantity_per_month": {"type": "integer"}}, "required": ["last_filled", "day_supply", "quantity"]}},
    {"name": "search_prescribers",
     "description": "Search prescribers by name or NPI.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "get_prescriber_by_npi",
     "description": "Look up prescriber by 10-digit NPI.",
     "input_schema": {"type": "object", "properties": {"npi": {"type": "string"}}, "required": ["npi"]}},
    {"name": "list_inventory",
     "description": "List inventory with optional filters: needs_reorder, in_stock_only, out_of_stock.",
     "input_schema": {"type": "object", "properties": {
         "needs_reorder": {"type": "boolean"}, "in_stock_only": {"type": "boolean"},
         "out_of_stock": {"type": "boolean"}}, "required": []}},
          {"name": "get_inventory_item",
            "description": "Look up one inventory item by item_id.",
            "input_schema": {"type": "object", "properties": {"item_id": {"type": "integer"}}, "required": ["item_id"]}},
          {"name": "get_inventory_by_hcpcs",
     "description": "Look up inventory item by HCPCS code.",
     "input_schema": {"type": "object", "properties": {"hcpcs_code": {"type": "string"}}, "required": ["hcpcs_code"]}},
    {"name": "search_inventory",
      "description": "Search inventory by item number, description, HCPCS, or manufacturer.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "get_claims_aging",
     "description": "AR aging report — outstanding claims by age bucket with totals.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_reconciliation",
     "description": "Monthly reconciliation: expected vs actual payments.",
     "input_schema": {"type": "object", "properties": {"months": {"type": "integer"}}, "required": []}},
    {"name": "get_reconciliation_orders",
     "description": "Get order-level reconciliation rows for billed/paid workflow with date and insurance filters.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         "insurance": {"type": "string", "description": "Insurance name or 'All'"},
         "limit": {"type": "integer"}}, "required": []}},
    {"name": "update_reconciliation_paid",
     "description": "Bulk update order paid and paid_date fields for reconciliation; this syncs with Orders tab paid fields.",
     "input_schema": {"type": "object", "properties": {
         "updates": {"type": "array", "items": {
             "type": "object",
             "properties": {
                 "order_id": {"type": "integer"},
                 "paid": {"type": "boolean"},
                 "paid_date": {"type": "string", "description": "MM/DD/YYYY optional when paid=true"}
             },
             "required": ["order_id", "paid"]
         }},
         "notes": {"type": "string"}},
         "required": ["updates"]}},
    {"name": "open_reconciliation_report_ui",
     "description": "Open the Reconciliation Report screen in the running DMELogic desktop app.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "Optional YYYY-MM-DD hint"},
         "end_date": {"type": "string", "description": "Optional YYYY-MM-DD hint"},
         "insurance": {"type": "string", "description": "Optional insurance filter hint"},
         "notes": {"type": "string"}}, "required": []}},
    {"name": "get_fee_schedule",
     "description": "Medicaid fee schedule lookup by HCPCS code.",
     "input_schema": {"type": "object", "properties": {
         "hcpcs": {"type": "string"}, "rental": {"type": "boolean"}}, "required": ["hcpcs"]}},
    {"name": "get_billing_summary",
     "description": "High-level billing totals: billed, paid, outstanding.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_claims",
     "description": "Get billing claims with optional status filter.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string"}, "limit": {"type": "integer"}}, "required": []}},
    {"name": "get_profit_report",
     "description": "Order profit report with optional date range.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string"}, "end_date": {"type": "string"}}, "required": []}},
    {"name": "get_inventory_value_report",
     "description": "Inventory value by category.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_gross_margin_report",
     "description": "Gross margin by HCPCS item.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_low_stock_report",
     "description": "Items at or below reorder point.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_out_of_stock_report",
     "description": "Items with zero inventory.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_reorder_by_vendor",
     "description": "Items needing reorder grouped by vendor.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_orders_by_status_report",
     "description": "Order counts grouped by status.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "process_refill",
     "description": "Actually process and create a refill order for an eligible order. Use ONLY after the user has confirmed they want to proceed. Call this when user says yes after refill confirmation, or says 'process refill', 'go ahead', 'create the refill'. Requires the specific order_id to refill.",
     "input_schema": {"type": "object", "properties": {
         "order_id": {"type": "integer", "description": "The order ID to create a refill for"}},
         "required": ["order_id"]}},
    {"name": "process_remittance",
     "description": "Parse a NY MMIS Title XIX remittance PDF and match claims to DMELogic orders. Use when user says 'process remittance', 'parse the remittance', 'reconcile payments', 'what got denied'. Returns paid/denied/pending with order matches and error explanations. Automatically creates billing reminders for denials.",
     "input_schema": {"type": "object", "properties": {
         "pdf_path": {"type": "string", "description": "Full path to the remittance PDF file"}},
         "required": ["pdf_path"]}},
    {"name": "get_orders_by_date",
     "description": f"Get order count and status breakdown for a specific date range. Use for 'how many orders last week', 'orders created in May', 'how many orders today', 'orders this month', 'how many orders did we create'. Calculate the date range from today before calling. Today is {datetime.now().strftime('%m-%d-%Y')}.",
     "input_schema": {"type": "object", "properties": {
         "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
         "end_date":   {"type": "string", "description": "End date YYYY-MM-DD"}},
         "required": ["start_date", "end_date"]}},
    {"name": "get_orders_filtered",
     "description": "Get full order rows with optional status and date-range filters, with pagination for large datasets.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "description": "Optional order status filter; use 'All' or omit for all statuses."},
         "start_date": {"type": "string", "description": "Optional YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "Optional YYYY-MM-DD"},
         "limit": {"type": "integer", "description": "Rows per page (default 500)"},
         "offset": {"type": "integer", "description": "Pagination offset (default 0)"}},
         "required": []}},
    {"name": "create_report",
     "description": "Create a report dataset for analytics requests. Use report_name and optional date range.",
     "input_schema": {"type": "object", "properties": {
         "report_name": {"type": "string", "description": "profit | inventory_value | gross_margin | low_stock | out_of_stock | reorder_by_vendor | orders_by_status | orders_by_date | claims_aging | billing_summary | remittance_processing"},
         "start_date": {"type": "string", "description": "YYYY-MM-DD (used by profit/orders_by_date)"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD (used by profit/orders_by_date)"},
         "pdf_path": {"type": "string", "description": "Required for remittance_processing"}},
         "required": ["report_name"]}},
    {"name": "export_report",
     "description": "Generate and export a report file to disk. Returns the full file path for download/use.",
     "input_schema": {"type": "object", "properties": {
         "report_name": {"type": "string", "description": "profit | inventory_value | gross_margin | low_stock | out_of_stock | reorder_by_vendor | orders_by_status | orders_by_date | claims_aging | billing_summary | remittance_processing"},
         "export_format": {"type": "string", "description": "csv | json | xlsx | pdf"},
         "start_date": {"type": "string", "description": "YYYY-MM-DD (used by profit/orders_by_date)"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD (used by profit/orders_by_date)"},
         "pdf_path": {"type": "string", "description": "Required for remittance_processing"},
         "section": {"type": "string", "description": "Optional tabular section: summary | denied_lines | pending_lines | unmatched"},
         "file_name": {"type": "string", "description": "Optional output file name without extension"}},
         "required": ["report_name", "export_format"]}},
    {"name": "list_notes",
     "description": "List sticky notes with optional search.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}, "required": []}},
    {"name": "create_note",
     "description": "Create a sticky note in DMELogic.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "body": {"type": "string"}, "pinned": {"type": "boolean"}},
         "required": ["body"]}},
    {"name": "create_patient_tracking_note",
     "description": "Create a patient-linked RX tracking note when no order is created (e.g., unable to fill, forwarded, transferred). Use this so staff can answer patient/prescriber callback questions later.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "disposition": {"type": "string", "description": "forwarded | transferred | unable_to_fill | other"},
         "summary": {"type": "string", "description": "What happened and why"},
         "prescriber": {"type": "string"},
         "destination": {"type": "string", "description": "Insurance plan, pharmacy, or transfer destination"},
         "callback_phone": {"type": "string"},
         "pinned": {"type": "boolean"}},
         "required": ["patient_id", "disposition", "summary"]}},
    {"name": "get_patient_tracking_notes",
     "description": "Get callback-focused RX tracking notes for a patient (no-order outcomes like transferred/forwarded/unable-to-fill).",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "limit": {"type": "integer"}},
         "required": ["patient_id"]}},
    {"name": "read_document",
     "description": "Extract all readable text from a PDF or image file on disk using OCR. Use when the user says 'read this file', 'what does this document say', 'process this fax', or gives you a file path to a PDF or image. Handles scanned/image PDFs automatically — tries native text first, falls back to Tesseract OCR.",
     "input_schema": {"type": "object", "properties": {
         "file_path": {"type": "string", "description": "Full path to the PDF or image file"}},
         "required": ["file_path"]}},

    # ── RingCentral / Communications tools ───────────────────────────────
    {"name": "get_call_log",
     "description": "Recent inbound/outbound/missed calls with caller ID, duration, and timestamp.",
     "input_schema": {"type": "object", "properties": {
         "direction": {"type": "string", "description": "All | Inbound | Outbound"},
         "limit": {"type": "integer"},
         "date_from": {"type": "string", "description": "ISO datetime, e.g. 2026-06-01T00:00:00Z"}},
         "required": []}},
    {"name": "get_missed_calls",
     "description": "Missed calls only, with urgent flags for known patient/prescriber numbers.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer"},
         "date_from": {"type": "string"},
         "flag_known": {"type": "boolean"}},
         "required": []}},
    {"name": "get_voicemails",
     "description": "List voicemail inbox messages, optionally unread only.",
     "input_schema": {"type": "object", "properties": {
         "unread_only": {"type": "boolean"},
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_voicemail_transcription",
     "description": "Get full voicemail transcription/details by voicemail message ID.",
     "input_schema": {"type": "object", "properties": {
         "voicemail_id": {"type": "string"}},
         "required": ["voicemail_id"]}},
    {"name": "initiate_call",
     "description": "Initiate a click-to-call RingOut call to a patient/prescriber.",
     "input_schema": {"type": "object", "properties": {
         "to_number": {"type": "string"},
         "from_number": {"type": "string"},
         "caller_id": {"type": "string"},
         "play_prompt": {"type": "boolean"}},
         "required": ["to_number"]}},
    {"name": "get_active_calls",
     "description": "Show active calls/telephony sessions across extensions.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer"}}, "required": []}},
    {"name": "send_sms",
     "description": "Send an SMS to a patient or prescriber.",
     "input_schema": {"type": "object", "properties": {
         "to_number": {"type": "string"},
         "message": {"type": "string"}},
         "required": ["to_number", "message"]}},
    {"name": "get_sms_inbox",
     "description": "Read incoming SMS messages.",
     "input_schema": {"type": "object", "properties": {
         "unread_only": {"type": "boolean"},
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_sms_thread",
     "description": "Get full SMS conversation history with a specific phone number.",
     "input_schema": {"type": "object", "properties": {
         "phone_number": {"type": "string"},
         "limit": {"type": "integer"}},
         "required": ["phone_number"]}},
    {"name": "get_unread_sms",
     "description": "Get unread incoming SMS across all numbers.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "send_bulk_sms",
     "description": "Send one SMS message to multiple numbers.",
     "input_schema": {"type": "object", "properties": {
         "numbers": {"type": "array", "items": {"type": "string"}},
         "message": {"type": "string"}},
         "required": ["numbers", "message"]}},
    {"name": "send_refill_reminder",
     "description": "Templated SMS: 'Your refill for [item] is due [date]. Reply YES to confirm.'",
     "input_schema": {"type": "object", "properties": {
         "to_number": {"type": "string"},
         "item": {"type": "string"},
         "due_date": {"type": "string"}},
         "required": ["to_number", "item", "due_date"]}},
    {"name": "send_delivery_notification",
     "description": "Templated SMS notifying patient that order is out for delivery.",
     "input_schema": {"type": "object", "properties": {
         "to_number": {"type": "string"},
         "driver_name": {"type": "string"},
         "order_number": {"type": "string"},
         "eta_window": {"type": "string"}},
         "required": ["to_number", "driver_name"]}},
    {"name": "send_fax",
     "description": "Send a fax to a recipient number with an attachment file.",
     "input_schema": {"type": "object", "properties": {
         "to_number": {"type": "string"},
         "file_path": {"type": "string"},
         "cover_note": {"type": "string"}},
         "required": ["to_number", "file_path"]}},
    {"name": "send_refill_fax",
     "description": "Generate the DMELogic refill request form from an order and fax it to the prescriber (no manual file path needed).",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "order_id": {"type": "integer"},
         "prescriber_fax": {"type": "string"},
         "cover_note": {"type": "string"}},
         "required": ["patient_id", "order_id"]}},
    {"name": "get_refill_request_form_path",
     "description": "Generate the DMELogic refill request form and return its file path without sending fax.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "order_id": {"type": "integer"}},
         "required": ["patient_id", "order_id"]}},
    {"name": "send_new_rx_request_fax",
     "description": "Generate the New Prescription Request form (not refill) from an order and fax it to the prescriber.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "order_id": {"type": "integer"},
         "prescriber_fax": {"type": "string"},
         "include_approved_icd10_list": {"type": "boolean"},
         "invalid_diagnosis_code": {"type": "string"},
         "cover_note": {"type": "string"}},
         "required": ["patient_id", "order_id"]}},
    {"name": "get_new_rx_request_form_path",
     "description": "Generate the New Prescription Request form and return its file path without sending fax.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "order_id": {"type": "integer"},
         "include_approved_icd10_list": {"type": "boolean"},
         "invalid_diagnosis_code": {"type": "string"}},
         "required": ["patient_id", "order_id"]}},
    {"name": "get_fax_inbox",
     "description": "List inbound faxes with sender and timestamp.",
     "input_schema": {"type": "object", "properties": {
         "unread_only": {"type": "boolean"},
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_fax_status",
     "description": "Check status of a sent fax by fax/message ID.",
     "input_schema": {"type": "object", "properties": {
         "fax_id": {"type": "string"}},
         "required": ["fax_id"]}},
    {"name": "list_sent_faxes",
     "description": "List outbound fax audit trail.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer"},
         "date_from": {"type": "string"}},
         "required": []}},
    {"name": "match_caller_to_patient",
     "description": "Match incoming phone number to known patient/prescriber records.",
     "input_schema": {"type": "object", "properties": {
         "phone_number": {"type": "string"}},
         "required": ["phone_number"]}},
    {"name": "log_call_note",
     "description": "Attach a post-call communication note to a patient/order context.",
     "input_schema": {"type": "object", "properties": {
         "note": {"type": "string"},
         "patient_id": {"type": "integer"},
         "order_id": {"type": "integer"},
         "phone_number": {"type": "string"}},
         "required": ["note"]}},
    {"name": "get_patient_communication_history",
     "description": "Unified view of calls, SMS, voicemails, and faxes for a patient.",
     "input_schema": {"type": "object", "properties": {
         "patient_id": {"type": "integer"},
         "limit": {"type": "integer"}},
         "required": ["patient_id"]}},
    {"name": "auto_remind_refills_due",
     "description": "Find refills due in N days and send templated SMS reminders automatically.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer"}},
         "required": []}},
    {"name": "get_call_analytics",
     "description": "Call analytics: volume by day, avg handle time, missed rate.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer"}},
         "required": []}},
    {"name": "get_extension_status",
     "description": "Extension availability / call / DND status.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer"}},
         "required": []}},
    {"name": "get_call_queue_stats",
     "description": "Main line call queue depth and wait-time stats.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_sms_response_rate",
     "description": "Outbound SMS response rate and response timing analytics.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer"}},
         "required": []}},
    {"name": "ringcentral_status",
     "description": "Check RingCentral configuration and live connection status for Nova.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "ringcentral_connect",
     "description": "Connect Nova to RingCentral via OAuth in your browser.",
     "input_schema": {"type": "object", "properties": {
         "timeout": {"type": "integer", "description": "OAuth wait timeout seconds (default 180)"}},
         "required": []}},
    {"name": "ringcentral_disconnect",
     "description": "Disconnect Nova from RingCentral and clear local OAuth tokens.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_communications_monitor",
     "description": "Unified incoming/outgoing monitoring for calls, SMS, and fax over a date window (default 30 days).",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "Monitoring window in days (default 30)"}},
         "required": []}},

        # ── Workflow gap-closure tools (audit) ─────────────────────────────
        {"name": "create_order",
         "description": "Create a new order from structured fields and line items. Prefer inventory-backed item selection so HCPCS, item number, and pricing come from inventory.",
         "input_schema": {"type": "object", "properties": {
                 "patient_last_name": {"type": "string"},
                 "patient_first_name": {"type": "string"},
                 "patient_id": {"type": "integer"},
                 "patient_dob": {"type": "string"},
                 "patient_phone": {"type": "string"},
                 "prescriber_name": {"type": "string"},
                 "prescriber_npi": {"type": "string"},
                 "rx_date": {"type": "string"},
                 "order_date": {"type": "string"},
                 "delivery_date": {"type": "string"},
                 "on_hold": {"type": "boolean", "description": "Create the order in On Hold status instead of Pending Approval."},
                 "hold_until_date": {"type": "string", "description": "Release date for an On Hold order, YYYY-MM-DD or MM/DD/YYYY."},
                 "hold_resume_status": {"type": "string", "description": "Status to resume to when the hold ends."},
                 "hold_note": {"type": "string", "description": "Reason or reminder for the hold."},
                 "primary_insurance": {"type": "string"},
                 "primary_insurance_id": {"type": "string"},
                 "place_of_service": {"type": "string", "description": "2-digit billing place of service code, such as 12 for Home or 31 for Skilled Nursing Facility."},
                 "icd_code_1": {"type": "string"},
                 "doctor_directions": {"type": "string"},
                 "notes": {"type": "string"},
                 "items": {
                     "type": "array",
                     "items": {
                         "type": "object",
                         "properties": {
                             "inventory_item_id": {"type": "integer", "description": "Preferred: exact inventory item_id to use for this line."},
                             "item_number": {"type": "string", "description": "Inventory item number when known."},
                             "hcpcs": {"type": "string"},
                             "description": {"type": "string"},
                             "quantity": {"type": "integer"},
                             "refills": {"type": "integer"},
                             "days_supply": {"type": "integer"},
                             "directions": {"type": "string"},
                             "cost_ea": {"type": "number", "description": "Optional override. If omitted, Nova uses inventory retail_price or cost."}
                         },
                         "required": []
                     }
                 }
             },
             "required": ["patient_last_name", "patient_first_name", "items"]}},
        {"name": "get_unbilled_orders",
         "description": "Get orders that are ready/open but not yet billed.",
         "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}},
        {"name": "process_denial",
         "description": "Analyze a denied claim and return likely denial code guidance and resubmission suggestion.",
         "input_schema": {"type": "object", "properties": {"claim_id": {"type": "integer"}}, "required": ["claim_id"]}},
        {"name": "get_must_go_out",
         "description": "Urgent must-go-out delivery queue for today/active items.",
         "input_schema": {"type": "object", "properties": {
                 "status": {"type": "string", "description": "Pending | Completed | All"},
                 "limit": {"type": "integer"}}, "required": []}},
        {"name": "add_must_go_out",
         "description": "Add an entry to the DMELogic 'Must Go Out' follow-up queue so staff know a delivery/pickup needs to be prepared or verified. Use for refill callbacks and any promise that someone will follow up with a patient.",
         "input_schema": {"type": "object", "properties": {
                 "patient_name": {"type": "string", "description": "Patient name (Last, First)"},
                 "patient_phone": {"type": "string", "description": "Callback phone number"},
                 "notes": {"type": "string", "description": "What is needed / what to follow up on"},
                 "order_id": {"type": "integer", "description": "Existing order id if known (optional)"}},
                 "required": ["patient_name"]}},
        {"name": "process_refills_due_on_date",
         "description": "Process refill chains due on a specific date. Chain-aware: targets latest order in each chain to avoid stale refill_completed blocks.",
         "input_schema": {"type": "object", "properties": {
             "due_date": {"type": "string", "description": "YYYY-MM-DD or MM/DD/YYYY"},
             "force": {"type": "boolean", "description": "Reserved for future use; default true"}},
             "required": ["due_date"]}},
        {"name": "update_order_tracking",
         "description": "Set or update a tracking number on an order.",
         "input_schema": {"type": "object", "properties": {
                 "order_id": {"type": "integer"}, "tracking_number": {"type": "string"}},
                 "required": ["order_id", "tracking_number"]}},
        {"name": "schedule_delivery",
         "description": "Assign delivery date and optional driver to an order.",
         "input_schema": {"type": "object", "properties": {
                 "order_id": {"type": "integer"},
                 "delivery_date": {"type": "string"},
                 "driver_name": {"type": "string"}},
                 "required": ["order_id", "delivery_date"]}},
        {"name": "get_expiring_authorizations",
         "description": "Orders with authorizations expiring soon.",
         "input_schema": {"type": "object", "properties": {
                 "days": {"type": "integer"}, "limit": {"type": "integer"}}, "required": []}},
        {"name": "get_orders_expiring_auth",
         "description": "Alias for get_expiring_authorizations.",
         "input_schema": {"type": "object", "properties": {
                 "days": {"type": "integer"}, "limit": {"type": "integer"}}, "required": []}},
        {"name": "search_documents",
         "description": "Full-text search across OCR-indexed documents.",
         "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
        {"name": "get_unmatched_documents",
         "description": "List OCR-indexed documents that appear unmatched/unlinked.",
         "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}},
        {"name": "create_task",
         "description": "Create a workflow task and assign staff/priority.",
         "input_schema": {"type": "object", "properties": {
                 "title": {"type": "string"},
                 "assigned_to": {"type": "string"},
                 "priority": {"type": "string"},
                 "note": {"type": "string"}}, "required": ["title"]}},
        {"name": "get_tasks",
         "description": "Get open workflow tasks (stored in Nova reminders tagged as tasks).",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "complete_task",
         "description": "Complete a task by reminder/task id.",
         "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},

    # ── Memory tools (new in v3.0) ────────────────────────────────────────
    {"name": "remember",
     "description": "Save a fact, preference, patient note, or reminder to Nova's persistent memory. Use when user says 'remember that', 'note that', 'don't forget', or provides important context to keep between sessions. Categories: preference, patient, order, clinical, reminder, general.",
     "input_schema": {"type": "object", "properties": {
         "content":  {"type": "string", "description": "What to remember"},
         "category": {"type": "string", "description": "preference | patient | order | clinical | reminder | general"},
         "subject":  {"type": "string", "description": "Optional subject tag e.g. patient name, topic"}},
         "required": ["content"]}},
    {"name": "recall",
     "description": "Search Nova's persistent memory. Use when asked 'what do you know about X', 'do you remember', 'what did I tell you about'.",
     "input_schema": {"type": "object", "properties": {
         "query":    {"type": "string", "description": "Search term"},
         "category": {"type": "string", "description": "Optional category filter"},
         "subject":  {"type": "string", "description": "Optional subject filter"}},
         "required": []}},
    {"name": "recall_all",
     "description": "List everything Nova has remembered. Use when asked 'what do you know', 'show me your memory', 'what have I told you'.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "forget",
     "description": "Delete a specific memory by its ID.",
     "input_schema": {"type": "object", "properties": {
         "memory_id": {"type": "integer"}}, "required": ["memory_id"]}},
    {"name": "search_sessions",
     "description": "Search past conversation sessions by keyword. Use when asked 'what did we discuss about X', 'do you remember when we talked about'.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},

    # ── Reminder tools ────────────────────────────────────────────────────
    {"name": "add_reminder",
        "description": "Add a reminder that persists until marked done. Use when user says 'remind me to', 'I need to', 'don't forget to', 'add to my list'. If the user gives a date/time (e.g., tomorrow at 11:00 AM), include due_at in ISO-8601 local datetime. If user says the reminder should be ongoing/perpetual, omit due_at. If timing intent is ambiguous, ask one short clarification before calling this tool. Use remind_every_minutes for repeated nudges after due time until completed.",
     "input_schema": {"type": "object", "properties": {
         "content": {"type": "string", "description": "What to remember"},
         "tag":     {"type": "string", "description": "ordering | calls | billing | follow_up | clinical | general"},
         "due_at":  {"type": "string", "description": "Optional ISO-8601 datetime, e.g. 2026-06-06T11:00:00"},
         "remind_every_minutes": {"type": "integer", "description": "Optional repeat cadence after due time (default 30)"}},
         "required": ["content"]}},

    {"name": "get_reminders",
     "description": "Get active reminders, optionally filtered by tag. Use for 'what do I need to do', 'what items to order', 'any calls to make', 'show my reminders'. If user asks about ordering/supplies use tag=ordering. If about calls use tag=calls. If about billing use tag=billing. If all reminders use no tag.",
     "input_schema": {"type": "object", "properties": {
         "tag":    {"type": "string", "description": "Optional: ordering | calls | billing | follow_up | clinical | general | all"},
         "status": {"type": "string", "description": "active (default) | done | all"}},
         "required": []}},

    {"name": "complete_reminder",
     "description": "Mark a specific reminder as done by its ID. Use when user says 'that is done', 'mark #X as done', 'completed'.",
     "input_schema": {"type": "object", "properties": {
         "reminder_id": {"type": "integer"}}, "required": ["reminder_id"]}},

    {"name": "complete_reminder_by_content",
     "description": "Mark reminders as done by keyword match — no ID needed. Use when user says 'the diapers are done', 'I ordered the briefs', 'called Dr. Jackson'. Searches content and marks matching active reminders done.",
     "input_schema": {"type": "object", "properties": {
         "keyword": {"type": "string", "description": "Keyword to match against reminder content"}},
         "required": ["keyword"]}},

    {"name": "delete_reminder",
     "description": "Permanently delete a reminder by ID.",
     "input_schema": {"type": "object", "properties": {
         "reminder_id": {"type": "integer"}}, "required": ["reminder_id"]}},

    # ── Order audit tools ─────────────────────────────────────────────────
    {"name": "audit_missing_prescriber",
     "description": "Find active/pending orders where the prescriber is not linked (no prescriber_id) or the NPI is missing. Use for compliance audits or before billing.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_missing_insurance",
     "description": "Find non-cancelled orders with no primary insurance on file. Use to catch orders that cannot be billed.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_missing_diagnosis",
     "description": "Find non-cancelled orders with no ICD diagnosis codes set. Required for billing — use before submitting claims.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_missing_documents",
     "description": "Find active orders with no RX or signed ticket documents attached. Use to catch orders that are missing required paperwork.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_stale_orders",
     "description": "Find orders stuck in a status longer than the configured thresholds: Pending > pending_days, Active > active_days, On Hold > hold_days. Returns days_in_status for each.",
     "input_schema": {"type": "object", "properties": {
         "pending_days": {"type": "integer", "description": "Flag Pending orders older than this many days (default 7)"},
         "active_days":  {"type": "integer", "description": "Flag Active orders older than this many days (default 60)"},
         "hold_days":    {"type": "integer", "description": "Flag On Hold orders older than this many days (default 14)"},
         "limit":        {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_duplicate_hcpcs",
     "description": "Find patients with two or more open orders containing the same HCPCS code. Catches duplicate billing before it happens.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_unbilled_completed",
     "description": "Find Completed or Shipped orders where billed=0 and paid=0. These are delivered but never billed — revenue at risk.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_shipped_no_tracking",
     "description": "Find orders in Shipped status with no tracking number and no delivery date recorded. Use to find deliveries that may be lost or unconfirmed.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
    {"name": "audit_missing_delivery_ticket",
     "description": "Find Shipped, Completed, Billed, Unbilled, and Paid orders that have no signed delivery confirmation/ticket attached. Essential for proof-of-delivery compliance and audit readiness.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "Max results (default 200)"}},
         "required": []}},
]


# ══════════════════════════════════════════════════════════════════════════
#  TOOL DISPATCHER
# ══════════════════════════════════════════════════════════════════════════
def dispatch_tool(name: str, inp: Dict, client: DMELogicClient,
                  memory: NovaMemory) -> str:
    try:
        # ── Memory tools ──────────────────────────────────────────────────
        if name == "remember":
            mid = memory.remember(inp["content"], inp.get("category","general"), inp.get("subject"))
            return json.dumps({"saved": True, "memory_id": mid, "content": inp["content"]})
        elif name == "recall":
            mems = memory.recall(inp.get("query"), inp.get("category"), inp.get("subject"))
            return json.dumps(mems if mems else {"result": "Nothing found matching that query."})
        elif name == "recall_all":
            mems = memory.recall_all()
            return json.dumps(mems if mems else {"result": "No memories stored yet."})
        elif name == "forget":
            memory.forget(inp["memory_id"])
            return json.dumps({"deleted": True, "memory_id": inp["memory_id"]})
        elif name == "search_sessions":
            sessions = memory.search_sessions(inp["query"])
            return json.dumps(sessions if sessions else {"result": "No past sessions found matching that."})
        elif name == "add_reminder":
            rid = memory.add_reminder(
                inp["content"],
                inp.get("tag", "general"),
                inp.get("due_at"),
                inp.get("remind_every_minutes", 30),
            )
            return json.dumps({
                "saved": True,
                "reminder_id": rid,
                "content": inp["content"],
                "tag": inp.get("tag", "general"),
                "due_at": inp.get("due_at"),
                "remind_every_minutes": inp.get("remind_every_minutes", 30),
            })
        elif name == "get_reminders":
            tag = inp.get("tag")
            status = inp.get("status","active")
            if status == "all":
                active = memory.get_reminders(tag=tag, status="active")
                done   = memory.get_reminders(tag=tag, status="done")
                return json.dumps({"active": active, "done": done})
            reminders = memory.get_reminders(tag=tag, status=status)
            return json.dumps(reminders if reminders else {"result": "No reminders found."})
        elif name == "complete_reminder":
            memory.complete_reminder(inp["reminder_id"])
            return json.dumps({"done": True, "reminder_id": inp["reminder_id"]})
        elif name == "complete_reminder_by_content":
            ids = memory.complete_reminder_by_content(inp["keyword"])
            return json.dumps({"done": True, "completed_ids": ids, "count": len(ids)})
        elif name == "delete_reminder":
            memory.delete_reminder(inp["reminder_id"])
            return json.dumps({"deleted": True, "reminder_id": inp["reminder_id"]})

        # ── RingCentral communication tools ───────────────────────────────
        elif name in {
            "get_call_log", "get_missed_calls", "get_voicemails", "get_voicemail_transcription",
            "initiate_call", "get_active_calls", "send_sms", "get_sms_inbox", "get_sms_thread",
            "get_unread_sms", "send_bulk_sms", "send_refill_reminder", "send_delivery_notification",
            "send_fax", "send_refill_fax", "get_refill_request_form_path", "send_new_rx_request_fax", "get_new_rx_request_form_path", "get_fax_inbox", "get_fax_status", "list_sent_faxes", "match_caller_to_patient",
            "get_call_analytics", "get_extension_status", "get_call_queue_stats", "get_sms_response_rate",
            "ringcentral_status", "ringcentral_connect", "ringcentral_disconnect", "get_communications_monitor",
        }:
            if rc_tools is None:
                r = {"error": "RingCentral tools module unavailable"}
            elif name == "get_call_log":
                r = rc_tools.get_call_log(inp.get("direction", "All"), inp.get("limit", 50), inp.get("date_from"))
            elif name == "get_missed_calls":
                r = rc_tools.get_missed_calls(inp.get("limit", 25), inp.get("date_from"), inp.get("flag_known", True))
            elif name == "get_voicemails":
                r = rc_tools.get_voicemails(inp.get("unread_only", True), inp.get("limit", 20))
            elif name == "get_voicemail_transcription":
                r = rc_tools.get_voicemail_transcription(inp["voicemail_id"])
            elif name == "initiate_call":
                r = rc_tools.initiate_call(
                    inp["to_number"], inp.get("from_number", ""), inp.get("caller_id", ""), inp.get("play_prompt", True)
                )
            elif name == "get_active_calls":
                r = rc_tools.get_active_calls(inp.get("limit", 50))
            elif name == "send_sms":
                r = rc_tools.send_sms(inp["to_number"], inp["message"])
            elif name == "get_sms_inbox":
                r = rc_tools.get_sms_inbox(inp.get("unread_only", False), inp.get("limit", 50))
            elif name == "get_sms_thread":
                r = rc_tools.get_sms_thread(inp["phone_number"], inp.get("limit", 100))
            elif name == "get_unread_sms":
                r = rc_tools.get_unread_sms(inp.get("limit", 100))
            elif name == "send_bulk_sms":
                r = rc_tools.send_bulk_sms(inp["numbers"], inp["message"])
            elif name == "send_refill_reminder":
                r = rc_tools.send_refill_reminder(inp["to_number"], inp["item"], inp["due_date"])
            elif name == "send_delivery_notification":
                r = rc_tools.send_delivery_notification(
                    inp["to_number"], inp["driver_name"], inp.get("order_number", ""), inp.get("eta_window", "")
                )
            elif name == "send_fax":
                r = rc_tools.send_fax(inp["to_number"], inp["file_path"], inp.get("cover_note", ""))
            elif name == "send_refill_fax":
                r = rc_tools.send_refill_fax(
                    patient_id=int(inp["patient_id"]),
                    order_id=int(inp["order_id"]),
                    prescriber_fax=inp.get("prescriber_fax"),
                    folder_path=getattr(client, "folder_path", None),
                    cover_note=inp.get("cover_note", ""),
                    send_now=True,
                )
            elif name == "get_refill_request_form_path":
                r = rc_tools.send_refill_fax(
                    patient_id=int(inp["patient_id"]),
                    order_id=int(inp["order_id"]),
                    prescriber_fax=None,
                    folder_path=getattr(client, "folder_path", None),
                    cover_note="",
                    send_now=False,
                )
            elif name == "send_new_rx_request_fax":
                r = rc_tools.send_new_rx_request_fax(
                    patient_id=int(inp["patient_id"]),
                    order_id=int(inp["order_id"]),
                    prescriber_fax=inp.get("prescriber_fax"),
                    folder_path=getattr(client, "folder_path", None),
                    cover_note=inp.get("cover_note", ""),
                    send_now=True,
                    include_approved_icd10_list=inp.get("include_approved_icd10_list", True),
                    invalid_diagnosis_code=inp.get("invalid_diagnosis_code", "R32"),
                )
            elif name == "get_new_rx_request_form_path":
                r = rc_tools.send_new_rx_request_fax(
                    patient_id=int(inp["patient_id"]),
                    order_id=int(inp["order_id"]),
                    prescriber_fax=None,
                    folder_path=getattr(client, "folder_path", None),
                    cover_note="",
                    send_now=False,
                    include_approved_icd10_list=inp.get("include_approved_icd10_list", True),
                    invalid_diagnosis_code=inp.get("invalid_diagnosis_code", "R32"),
                )
            elif name == "get_fax_inbox":
                r = rc_tools.get_fax_inbox(inp.get("unread_only", False), inp.get("limit", 50))
            elif name == "get_fax_status":
                r = rc_tools.get_fax_status(inp["fax_id"])
            elif name == "list_sent_faxes":
                r = rc_tools.list_sent_faxes(inp.get("limit", 50), inp.get("date_from"))
            elif name == "match_caller_to_patient":
                r = rc_tools.match_caller_to_patient(inp["phone_number"], getattr(client, "folder_path", None))
            elif name == "get_call_analytics":
                r = rc_tools.get_call_analytics(inp.get("days", 7))
            elif name == "get_extension_status":
                r = rc_tools.get_extension_status(inp.get("limit", 50))
            elif name == "get_call_queue_stats":
                r = rc_tools.get_call_queue_stats()
            elif name == "get_sms_response_rate":
                r = rc_tools.get_sms_response_rate(inp.get("days", 7))
            elif name == "ringcentral_status":
                r = rc_tools.ringcentral_status()
            elif name == "ringcentral_connect":
                r = rc_tools.ringcentral_connect(inp.get("timeout", 180))
            elif name == "ringcentral_disconnect":
                r = rc_tools.ringcentral_disconnect()
            elif name == "get_communications_monitor":
                r = rc_tools.get_communications_monitor(inp.get("days", 30))

        elif name == "log_call_note":
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            parts = [f"[Call Note {timestamp}]"]
            if inp.get("phone_number"):
                parts.append(f"Phone: {inp.get('phone_number')}")
            if inp.get("patient_id"):
                parts.append(f"Patient ID: {inp.get('patient_id')}")
            if inp.get("order_id"):
                parts.append(f"Order ID: {inp.get('order_id')}")
            parts.append(inp["note"])
            body = "\n".join(parts)
            r = client.create_note(
                title="Call Note",
                body=body,
                pinned=False,
            )

        elif name == "get_patient_communication_history":
            if rc_tools is None:
                r = {"error": "RingCentral tools module unavailable"}
            else:
                patient = client.get_patient(inp["patient_id"])
                patient_phone = ""
                if isinstance(patient, dict):
                    patient_phone = patient.get("phone") or patient.get("patient_phone") or ""
                limit = int(inp.get("limit", 50))

                calls = rc_tools.get_call_log(direction="All", limit=min(limit * 2, 500))
                sms = rc_tools.get_sms_thread(patient_phone, limit=limit) if patient_phone else {"records": []}
                faxes = rc_tools.get_fax_inbox(unread_only=False, limit=min(limit * 2, 500))

                phone_variants = set()
                if rc_tools and patient_phone:
                    phone_variants = set(rc_tools._phone_variants(patient_phone))

                matching_calls = []
                for item in (calls.get("records") or []):
                    item_variants = set(rc_tools._phone_variants(item.get("number"))) if rc_tools else set()
                    if not phone_variants or phone_variants.intersection(item_variants):
                        matching_calls.append(item)

                matching_faxes = []
                for item in (faxes.get("records") or []):
                    item_variants = set(rc_tools._phone_variants(item.get("number"))) if rc_tools else set()
                    if not phone_variants or phone_variants.intersection(item_variants):
                        matching_faxes.append(item)

                r = {
                    "patient_id": inp["patient_id"],
                    "patient_phone": patient_phone,
                    "calls": matching_calls[:limit],
                    "sms": (sms.get("records") or [])[:limit],
                    "faxes": matching_faxes[:limit],
                    "totals": {
                        "calls": len(matching_calls),
                        "sms": len(sms.get("records") or []),
                        "faxes": len(matching_faxes),
                    },
                }

        elif name == "auto_remind_refills_due":
            if rc_tools is None:
                r = {"error": "RingCentral tools module unavailable"}
            else:
                days = int(inp.get("days", 3))
                due = client.get_refills_due(days=days)
                rows = due if isinstance(due, list) else due.get("orders", []) if isinstance(due, dict) else []

                sent = []
                skipped = []
                failed = []
                for row in rows:
                    phone = row.get("patient_phone") or ""
                    if not phone:
                        skipped.append({"reason": "missing phone", "order_id": row.get("id")})
                        continue
                    item = row.get("description") or row.get("hcpcs_code") or "your supplies"
                    due_date = row.get("next_refill_date") or row.get("refill_due") or f"in {days} day(s)"
                    result = rc_tools.send_refill_reminder(phone, item, due_date)
                    payload = {
                        "order_id": row.get("id"),
                        "patient": f"{row.get('patient_last_name','')}, {row.get('patient_first_name','')}".strip(", "),
                        "phone": phone,
                        "result": result,
                    }
                    if result.get("success"):
                        sent.append(payload)
                    else:
                        failed.append(payload)

                r = {
                    "days": days,
                    "total_due": len(rows),
                    "sent": sent,
                    "failed": failed,
                    "skipped": skipped,
                    "summary": {
                        "sent_count": len(sent),
                        "failed_count": len(failed),
                        "skipped_count": len(skipped),
                    },
                }

        elif name == "create_order":
            r = client.create_order(inp)

        # ── Order audit tools ─────────────────────────────────────────────
        elif name == "audit_missing_prescriber":
            r = client.audit_missing_prescriber(inp.get("limit", 200))
        elif name == "audit_missing_insurance":
            r = client.audit_missing_insurance(inp.get("limit", 200))
        elif name == "audit_missing_diagnosis":
            r = client.audit_missing_diagnosis(inp.get("limit", 200))
        elif name == "audit_missing_documents":
            r = client.audit_missing_documents(inp.get("limit", 200))
        elif name == "audit_stale_orders":
            r = client.audit_stale_orders(
                inp.get("pending_days", 7),
                inp.get("active_days", 60),
                inp.get("hold_days", 14),
                inp.get("limit", 200),
            )
        elif name == "audit_duplicate_hcpcs":
            r = client.audit_duplicate_hcpcs(inp.get("limit", 200))
        elif name == "audit_unbilled_completed":
            r = client.audit_unbilled_completed(inp.get("limit", 200))
        elif name == "audit_shipped_no_tracking":
            r = client.audit_shipped_no_tracking(inp.get("limit", 200))
        elif name == "audit_missing_delivery_ticket":
            r = client.audit_missing_delivery_ticket(inp.get("limit", 200))

        elif name == "search_documents":
            r = client.search_documents(inp["query"], inp.get("limit", 25))
        elif name == "get_unmatched_documents":
            r = client.get_unmatched_documents(inp.get("limit", 100))
        elif name == "create_task":
            task_payload = client.create_task(
                inp["title"],
                inp.get("assigned_to", ""),
                inp.get("priority", "Normal"),
                inp.get("note", ""),
            )
            if task_payload.get("error"):
                r = task_payload
            else:
                rid = memory.add_reminder(task_payload.get("task_text", inp["title"]), tag="follow_up")
                r = {"success": True, "task_id": rid, "task": task_payload.get("task_text")}
        elif name == "get_tasks":
            r = memory.get_reminders(tag="follow_up", status="active")
        elif name == "complete_task":
            memory.complete_reminder(inp["task_id"])
            r = {"success": True, "task_id": inp["task_id"], "completed": True}

        # ── DMELogic tools ────────────────────────────────────────────────
        elif name == "morning_summary":             r = client.morning_summary()
        elif name == "dmelogic_api_call":          r = client.dmelogic_api_call(
            inp["method"],
            inp["path"],
            inp.get("params"),
            inp.get("body"),
        )
        elif name == "list_patients":               r = client.list_patients()
        elif name == "search_patients":             r = client.search_patients(inp["q"])
        elif name == "create_patient":              r = client.create_patient(inp)
        elif name == "update_patient":
            patient_id = int(inp["patient_id"])
            update_payload = {k: v for k, v in inp.items() if k != "patient_id"}
            r = client.update_patient(patient_id, update_payload)
        elif name == "search_patients_by_phone":    r = client.search_patients_by_phone(inp["phone"])
        elif name == "get_patient":                 r = client.get_patient(inp["patient_id"])
        elif name == "get_patient_orders":          r = client.get_patient_orders(inp["patient_id"])
        elif name == "get_patient_refills_eligible":r = client.get_patient_refills_eligible(inp["patient_id"])
        elif name == "get_patient_notes":           r = client.get_patient_notes(inp["patient_id"])
        elif name == "get_order":                   r = client.get_order(inp["order_id"])
        elif name == "get_order_notes":             r = client.get_order_notes(inp["order_id"])
        elif name == "get_orders_by_status":        r = client.get_orders_by_status(inp["status"], inp.get("limit",50))
        elif name == "update_order_status":         r = client.update_order_status(inp["order_id"], inp["new_status"], inp.get("notes",""), inp.get("paid_date",""))
        elif name == "update_order_prescriber_contact": r = client.update_order_prescriber_contact(
            inp["order_id"],
            inp.get("prescriber_phone"),
            inp.get("prescriber_fax"),
            inp.get("notes", ""),
        )
        elif name == "update_order_patient_link": r = client.update_order_patient_link(
            inp["order_id"],
            inp["patient_id"],
            inp.get("notes", ""),
        )
        elif name == "update_order_item_refills": r = client.update_order_item_refills(
            inp["order_id"],
            inp["item_id"],
            inp["refills"],
            inp.get("notes", ""),
        )
        elif name == "attach_order_documents":     r = client.attach_order_documents(
            inp["order_id"],
            inp["attachments"],
            inp.get("patient_id"),
            inp.get("notes", ""),
            inp.get("document_type", "rx"),
        )
        elif name == "delete_order":               r = client.delete_order(
            inp["order_id"],
            inp.get("reason", ""),
            inp.get("preserve_audit_trail", True),
        )
        elif name == "get_pending_approvals":       r = client.get_pending_approvals()
        elif name == "process_approval":            r = client.process_approval(inp["order_id"], inp["action"], inp.get("reason",""))
        elif name == "get_refills_due":             r = client.get_refills_due(
            inp.get("days",7),
            inp.get("start_date"),
            inp.get("end_date"),
        )
        elif name == "check_refill_eligibility":    r = client.check_refill_eligibility(
            inp["last_filled"], inp["day_supply"], inp["quantity"],
            inp.get("insurance_type","Commercial"), inp.get("max_quantity_per_month",0))
        elif name == "search_prescribers":          r = client.search_prescribers(inp["q"])
        elif name == "get_prescriber_by_npi":       r = client.get_prescriber_by_npi(inp["npi"])
        elif name == "get_prescriber":              r = client.get_prescriber(inp["prescriber_id"])
        elif name == "list_inventory":              r = client.list_inventory(inp.get("needs_reorder"), inp.get("in_stock_only"), inp.get("out_of_stock"))
        elif name == "get_inventory_item":          r = client.get_inventory_item(inp["item_id"])
        elif name == "get_inventory_by_hcpcs":      r = client.get_inventory_by_hcpcs(inp["hcpcs_code"])
        elif name == "search_inventory":            r = client.search_inventory(inp["q"])
        elif name == "get_claims_aging":            r = client.get_claims_aging()
        elif name == "get_reconciliation":          r = client.get_reconciliation(inp.get("months",12))
        elif name == "get_reconciliation_orders":   r = client.get_reconciliation_orders(
            inp.get("start_date"),
            inp.get("end_date"),
            inp.get("insurance", "All"),
            inp.get("limit", 1000),
        )
        elif name == "update_reconciliation_paid":  r = client.update_reconciliation_paid(
            inp["updates"],
            inp.get("notes", ""),
        )
        elif name == "open_reconciliation_report_ui": r = client.open_reconciliation_report_ui(
            inp.get("start_date"),
            inp.get("end_date"),
            inp.get("insurance"),
            inp.get("notes", ""),
        )
        elif name == "get_fee_schedule":            r = client.get_fee_schedule(inp["hcpcs"], inp.get("rental",False))
        elif name == "get_billing_summary":         r = client.get_billing_summary()
        elif name == "get_claims":                  r = client.get_claims(inp.get("status"), inp.get("limit",50))
        elif name == "get_profit_report":           r = client.get_profit_report(inp.get("start_date"), inp.get("end_date"))
        elif name == "get_inventory_value_report":  r = client.get_inventory_value_report()
        elif name == "get_gross_margin_report":     r = client.get_gross_margin_report()
        elif name == "get_low_stock_report":        r = client.get_low_stock_report()
        elif name == "get_out_of_stock_report":     r = client.get_out_of_stock_report()
        elif name == "get_reorder_by_vendor":       r = client.get_reorder_by_vendor()
        elif name == "get_orders_by_status_report": r = client.get_orders_by_status_report()
        elif name == "get_orders_filtered":        r = client.get_orders_filtered(
            inp.get("status"),
            inp.get("start_date"),
            inp.get("end_date"),
            inp.get("limit", 500),
            inp.get("offset", 0),
        )
        elif name == "create_report":
            data = _build_report_data(client, inp["report_name"], inp)
            r = {
                "report_name": _normalize_report_name(inp["report_name"]),
                "generated_at": datetime.now().isoformat(),
                "data": data,
            }
        elif name == "export_report":
            data = _build_report_data(client, inp["report_name"], inp)
            if isinstance(data, dict) and data.get("error"):
                r = data
            else:
                r = _export_report_data(
                    _normalize_report_name(inp["report_name"]),
                    data,
                    inp.get("export_format", "csv"),
                    inp.get("file_name", ""),
                    inp.get("section", ""),
                )
        elif name == "process_refill":                 r = client.process_refill(inp["order_id"])
        elif name == "process_remittance":
            r = client.process_remittance(inp["pdf_path"])
            # Auto-create billing reminders for denials
            try:
                result = json.loads(r) if isinstance(r, str) else r
                for denial in result.get("denied_lines", []):
                    errors = "; ".join(denial.get("error_meanings") or denial.get("errors", []))
                    content = (
                        f"DENIAL — {denial['patient']} {denial['hcpcs']} "
                        f"${denial['amount']:.2f} DOS {denial['service_date']} "
                        f"{denial.get('order_display','')} — {errors}"
                    )
                    memory.add_reminder(content, tag="billing")
            except Exception:
                pass
        elif name == "get_orders_by_date":             r = client.get_orders_by_date(inp["start_date"], inp["end_date"])
        elif name == "list_notes":                  r = client.list_notes(inp.get("search"))
        elif name == "create_note":                 r = client.create_note(inp.get("title",""), inp["body"], inp.get("pinned",False))
        elif name == "create_patient_tracking_note": r = client.create_patient_tracking_note(
            inp["patient_id"],
            inp["disposition"],
            inp["summary"],
            inp.get("prescriber", ""),
            inp.get("destination", ""),
            inp.get("callback_phone", ""),
            "Nova",
            inp.get("pinned", True),
        )
        elif name == "get_patient_tracking_notes":  r = client.get_patient_tracking_notes(
            inp["patient_id"],
            inp.get("limit", 20),
        )
        elif name == "add_must_go_out":             r = client.add_must_go_out(
            inp.get("patient_name", ""),
            inp.get("patient_phone", ""),
            inp.get("notes", ""),
            inp.get("order_id"),
        )
        elif name == "read_document":
            file_path = inp["file_path"]
            if not os.path.exists(file_path):
                return json.dumps({"error": f"File not found: {file_path}"})
            ext = Path(file_path).suffix.lower()
            try:
                if ext == ".pdf":
                    from dmelogic.ocr_tools import extract_text_from_pdf
                    text = extract_text_from_pdf(file_path)
                else:
                    # Image file — run Tesseract directly
                    try:
                        import pytesseract
                        from PIL import Image as _PIL_Image
                        img = _PIL_Image.open(file_path)
                        text = pytesseract.image_to_string(img, config="--psm 6")
                    except ImportError:
                        return json.dumps({"error": "pytesseract/Pillow not installed"})
                return json.dumps({"file": file_path, "characters": len(text), "text": text})
            except Exception as e:
                return json.dumps({"error": f"OCR failed: {e}"})
        else: r = {"error": f"Unknown tool: {name}"}
        return json.dumps(r, default=str)
    except Exception as e:
        return json.dumps({"error": f"Tool failed: {e}"})


# ══════════════════════════════════════════════════════════════════════════
#  VOICE OUTPUT
# ══════════════════════════════════════════════════════════════════════════
class VoiceOutput:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled and bool(ELEVENLABS_API_KEY)
        self._client = None
        self._voice_id = ELEVENLABS_VOICE or "EXAVITQu4vr4xnSDxMaL"
        if self.enabled:
            try:
                from elevenlabs.client import ElevenLabs
                self._client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
                log.info("ElevenLabs voice enabled")
            except ImportError:
                log.warning("elevenlabs not installed — voice disabled")
                self.enabled = False

    def speak(self, text: str) -> None:
        if not self.enabled or not self._client:
            return
        try:
            import pygame, io
            # Strip markdown for natural speech
            clean = re.sub(r'\*+', '', text)
            clean = re.sub(r'#+\s*', '', clean)
            clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean)
            clean = re.sub(r'`+', '', clean)
            clean = re.sub(r'^-\s+', '', clean, flags=re.MULTILINE)
            clean = re.sub(r'\n+', ' ', clean).strip()
            if not clean:
                return
            audio = self._client.text_to_speech.convert(
                text=clean,
                voice_id=self._voice_id,
                model_id="eleven_turbo_v2",
                output_format="mp3_44100_128",
            )
            audio_bytes = b"".join(audio)
            pygame.mixer.init()
            pygame.mixer.music.load(io.BytesIO(audio_bytes))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        except Exception as e:
            log.warning(f"Voice playback failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════
NOVA_SYSTEM_PROMPT = """You are Nova, an intelligent AI pharmacy operations assistant for Central Pharmacy Group in the Bronx, NY. You are version 3.0 — you have persistent memory across sessions.

You have full access to DMELogic through tools: patients, orders, refills, inventory, billing, reconciliation, reports, notes, and your own persistent memory.

Your personality:
- Direct and precise — say what matters, nothing extra
- Proactive — flag issues without being asked
- Persistent memory — you learn and adapt across sessions

How you work:
- Always use tools for real data — never guess
- Confirm before any write operation
- Remember patient/order IDs from earlier in the conversation
- For tracking numbers — call get_order with the order ID you already have
- When user asks to process refills due on a specific date, use process_refills_due_on_date (chain-aware) instead of iterating raw due rows.
- When user asks for refills due in a specific date range (including past dates), call get_refills_due with start_date and end_date.
- For refill request forms/faxes, use send_refill_fax or get_refill_request_form_path. Do not ask the user for a manual refill form file path.
- For new patients or first-time item requests, use send_new_rx_request_fax or get_new_rx_request_form_path (not refill format).
- If user flags R32 or invalid diagnosis coding, include approved ICD-10 guidance in the outgoing new-RX request fax.
- Automatically save important facts using the remember tool when the user shares useful context
- For report requests, use create_report. If user asks to export/download/share, use export_report and return the exact file path.
- If user asks for a full detailed list of orders by status/date, use get_orders_filtered with pagination; for very large lists, use export_report.
- When creating or editing order items, resolve each line against inventory first. Prefer get_inventory_item, get_inventory_by_hcpcs, or search_inventory so HCPCS, item number, and pricing come from inventory instead of free-typing lines.
- If patient profile is missing or incomplete, use create_patient or update_patient instead of asking for manual creation in the UI.
- If an RX cannot be filled and no order will be created, still create/find the patient and record a patient-linked tracking note with create_patient_tracking_note so callbacks are covered.
- For callback questions about unfilled/forwarded/transferred prescriptions, use get_patient_tracking_notes first, then summarize status clearly.
- If the user asks to attach a prescription or any document to an order, use attach_order_documents.
- For uploaded RX files, do not ask for manual path entry; build attachments[].source_path from the OCR root + patient last-name initial subfolder + uploaded filename, and use the resolved existing source path when provided in chat context.
- If an order is not showing in a patient's profile because patient_id is missing or incorrect, use update_order_patient_link to fix it directly. Do not claim this requires manual DB correction.
- If refill counts on order items are wrong, use update_order_item_refills for each affected item. Do not claim manual UI correction is required.
- If an order is missing prescriber phone/fax, use update_order_prescriber_contact to write those fields on the order.
- If user asks to remove an order, use delete_order only after explicit confirmation and include a reason.
- When processing prescriptions, always capture refill counts. If text says patterns like "Refills:5" or "Refill 5", set item refills accordingly instead of defaulting to 0.
- Never claim a tool is unavailable unless you have checked the current tool list in this session.
- If a specific DMELogic action is needed and no dedicated tool exists, use dmelogic_api_call.

MEMORY SYSTEM:
- You have persistent memory that survives restarts (nova_memory.db)
- When user says "remember that", "note that", "don't forget" — use the remember tool immediately
- When user asks "what do you know about X" or "do you remember" — use the recall tool
- Proactively save important context using the remember tool
- Categories: preference, patient, order, clinical, reminder, general

REMINDER SYSTEM:
- Reminders persist until marked done — they survive restarts
- When user says "remind me to", "I need to order", "don't forget to order", "add to my list" — use add_reminder immediately
- Before saving a new reminder, resolve timing intent:
    if user gave a clear date/time, save as scheduled (set due_at)
    if user clearly wants ongoing/perpetual, save without due_at
    if timing is ambiguous, ask one brief follow-up before saving: "Should I schedule this for a specific date and time, or keep it as a perpetual reminder until you mark it done?"
- Auto-detect the tag from context:
    ordering   = anything about ordering, buying, stocking supplies, inventory
    calls      = calling doctors, patients, suppliers, leaving messages
    billing    = billing tasks, claims, follow-ups with insurance
    follow_up  = following up on orders, authorizations, documents
    clinical   = clinical tasks, rx renewals, PA requests
    general    = everything else
- When user asks "what do I need to order", "what supplies are pending" → get_reminders with tag=ordering
- When user asks "any calls to make", "who do I need to call" → get_reminders with tag=calls
- When user asks "show my reminders" or "what do I have to do" → get_reminders with no tag (shows all)
- When user says "done", "that's done", "I ordered the X", "called Dr. Y" → complete_reminder_by_content with keyword
- Always show reminder ID numbers so user can reference them
- Active reminders are shown below — mention them proactively if relevant

PRESCRIPTION (Rx) PROCESSING RULES — MANDATORY, READ EVERY TIME:
- The "Refills" field on a prescription is the number of refills AUTHORIZED by the prescriber
- When creating or editing an order from an Rx, ALWAYS set each order item's refills to the Rx's authorized refill count
- Do NOT confuse "Refills: 5" (authorized by prescriber) with "0 refills processed so far" — they are completely different
- If the Rx says "Refills: 5", every item on that order gets refills = 5. No exceptions. No interpretation.
- If the Rx says "Refills: 0", every item gets refills = 0
- NEVER default refills to 0 unless the Rx explicitly states Refills: 0
- When asked to "correct refills" or "fix refills", read the Rx image FIRST, find the Refills field, then update ALL items to match that number
- The Rx refills field format is: "Days: 30  Refills: 5" — both values on the same line
- Qty on the Rx = quantity per fill (set as order item quantity)
- Days on the Rx = day supply (set as order item day_supply)
- After any refill update, state: "Rx says Refills: [N]. Set all items to [N] refills."

eRx FORMAT (electronic prescriptions):
- Patient: LAST NAME, FIRST NAME
- DOB: MM/DD/YYYY   Gender: M/F
- Address (street then city state zip)
- Phone: (xxx)xxx-xxxx   Diag: ICD-10 code
- Rx#: number
- Qty: amount   (written out)
- Days: NN   Refills: NN   ← THIS IS THE AUTHORIZED REFILL COUNT — COPY TO ORDER
- Potency UnitCd: usually Unspecified for DME
- Drug: generic description (NOT the specific HCPCS item)
- Sig: actual item specifics (size, type, brand) — THIS determines the HCPCS code

DOCUMENT & IMAGE READING:
- When given an image or document, read ALL text meticulously — every field, number, date, and name matters
- For prescriptions/faxes: extract patient name, DOB, address, prescriber name/NPI, diagnosis (ICD), items with HCPCS, quantities, and dates
- For remittance/EOBs: extract claim numbers, ICN, DOS, amounts billed/paid/denied, and denial codes
- Double-check: drug names, NDCs, quantities, and dollar amounts are high-stakes — read character by character if needed
- If pre-extracted OCR text is provided alongside an image, use it to cross-reference and correct any visual misreads
- If a file path is mentioned, use the read_document tool to extract text before answering

VOICE RESPONSE RULES:
- Default: 1 sentence. 2 max. Lead with the answer — no warm-up, no sign-off.
- No filler: never say "of course", "sure", "great", "absolutely", "certainly", or "happy to".
- No hedging: no "it seems", "it looks like", "you may want to".
- No markdown: no asterisks, hashtags, bullet dashes, or backticks.
- Write as if speaking fast and direct — every word earns its place.
- Only expand to a full breakdown when user says "with breakdown", "give me details", "explain", or "full report".
- Morning summary default: patients total, refills due today, one urgent item.

TIME GROUNDING RULES:
- You must anchor responses to the current local timestamp below.
- Do not call it "morning" unless local hour is 05 through 11.
- If local hour is 12 through 16, use "afternoon".
- If local hour is 17 through 23, use "evening".
- If local hour is 00 through 04, use "late night".
- If user points out a time mismatch, acknowledge and correct immediately in the next sentence.

{memory_block}

{reminder_block}

Today: {today}
Now (local): {now_local}
Local hour (24h): {local_hour}
"""


# ══════════════════════════════════════════════════════════════════════════
#  NOVA AGENT  v3.0
# ══════════════════════════════════════════════════════════════════════════
class NovaAgent:
    def __init__(self, voice: bool = False,
                 system_suffix: Optional[str] = None,
                 tools: Optional[List[Dict]] = None,
                 max_tokens: Optional[int] = None):
        if not ANTHROPIC_API_KEY:
            sys.exit("ANTHROPIC_API_KEY not set.")
        self.claude     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.dme        = DMELogicClient()
        self.voice      = VoiceOutput(enabled=voice)
        self.memory     = NovaMemory()
        self.history: List[Dict] = []
        self.session_id = self.memory.start_session()
        self.last_model_used = CLAUDE_MODEL
        # Per-instance overrides (defaults preserve classic behavior)
        self.system_suffix = system_suffix
        self.tools = tools if tools is not None else TOOLS
        self.max_tokens = int(max_tokens or 1024)
        self._build_system()
        log.info(f"Nova agent v3.0 initialized — session {self.session_id}")

    def _build_system(self):
        """Build system prompt with memory, reminders, continuity, and learned rules."""
        mem_block        = self.memory.format_for_prompt()
        reminder_block   = self.memory.format_reminders_for_prompt()
        continuity_block = self.memory.format_continuity_context()
        rules_block      = self.memory.format_learned_rules()
        now_local_dt     = datetime.now().astimezone()
        self.system = NOVA_SYSTEM_PROMPT.format(
            memory_block=mem_block if mem_block else "",
            reminder_block=reminder_block if reminder_block else "",
            today=now_local_dt.strftime("%A, %B %d, %Y"),
            now_local=now_local_dt.strftime("%Y-%m-%d %I:%M %p %Z"),
            local_hour=now_local_dt.strftime("%H"),
        )
        # Append continuity context and learned rules after the base prompt
        if continuity_block:
            self.system += "\n\n" + continuity_block
        if rules_block:
            self.system += "\n\n" + rules_block
        if getattr(self, "system_suffix", None):
            self.system += "\n\n" + self.system_suffix

    def _auto_trim_history(self):
        """Keep history manageable by message count and approximate char budget."""
        if len(self.history) <= 40 and self._estimate_history_chars() <= NOVA_MAX_HISTORY_CHARS:
            return
        # Find a safe cut point — only cut between complete user/assistant pairs
        # Never cut after an assistant message that has tool_use blocks
        keep = self.history[-20:]
        # Ensure we start on a user message
        while keep and keep[0].get("role") != "user":
            keep = keep[1:]
        # Ensure the first kept message is a plain user text, not a tool_result
        # block (list of tool_result dicts) — those would orphan a prior tool_use.
        # Image messages are also lists but start with type=="image", so check type.
        while keep:
            content = keep[0].get("content")
            if not isinstance(content, list):
                break
            first_block = content[0] if content else None
            first_type = None
            if isinstance(first_block, dict):
                first_type = first_block.get("type")
            elif first_block is not None:
                first_type = getattr(first_block, "type", None)
            if first_type == "tool_result":
                keep = keep[1:]
            else:
                break
        if keep:
            self.history = keep
            log.info(f"History trimmed to {len(self.history)} messages")

        # Second pass: enforce approximate character budget so one giant tool result
        # cannot blow request context even when message count is small.
        if self._estimate_history_chars() > NOVA_MAX_HISTORY_CHARS:
            compact: List[Dict[str, Any]] = []
            used = 0
            for msg in reversed(self.history):
                msg_len = self._message_char_len(msg)
                if compact and (used + msg_len) > NOVA_MAX_HISTORY_CHARS:
                    break
                compact.append(msg)
                used += msg_len
            self.history = list(reversed(compact))
            log.info(
                "History char-budget trim applied: %s msgs, ~%s chars",
                len(self.history),
                used,
            )

    def _message_char_len(self, msg: Dict[str, Any]) -> int:
        """Approximate message size for context budgeting."""
        try:
            return len(json.dumps(msg, default=str, ensure_ascii=False))
        except Exception:
            return len(str(msg))

    def _estimate_history_chars(self) -> int:
        """Approximate total history size in characters."""
        return sum(self._message_char_len(m) for m in self.history)

    def _compact_tool_result_for_history(self, tool_name: str, raw: str) -> str:
        """Cap oversized tool payloads before storing in chat history."""
        text = raw if isinstance(raw, str) else str(raw)
        if len(text) <= NOVA_MAX_TOOL_RESULT_CHARS:
            return text

        preview = text[:NOVA_TOOL_RESULT_PREVIEW_CHARS]
        tail_keep = min(1000, max(200, NOVA_MAX_TOOL_RESULT_CHARS - NOVA_TOOL_RESULT_PREVIEW_CHARS))
        tail = text[-tail_keep:] if len(text) > (NOVA_TOOL_RESULT_PREVIEW_CHARS + tail_keep) else ""

        compact_payload = {
            "truncated": True,
            "tool": tool_name,
            "original_chars": len(text),
            "kept_chars": len(preview) + len(tail),
            "preview": preview,
            "tail": tail,
            "note": "Large tool output was truncated to stay under model context limits. Request export_report for complete data.",
        }
        compact = json.dumps(compact_payload, default=str)
        log.warning(
            "Tool result truncated for history: %s (%s -> %s chars)",
            tool_name,
            len(text),
            len(compact),
        )
        return compact

    def _has_image_in_history(self) -> bool:
        """Return True if the most recent user turn contains an image block."""
        for msg in reversed(self.history):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "image":
                                return True
                        else:
                            if getattr(b, "type", None) == "image":
                                return True
                    return False
                break
        return False

    def _model_candidates(self, has_image: bool) -> List[str]:
        configured = CLAUDE_VISION_MODEL if has_image else CLAUDE_MODEL
        fallback_from_env = [m.strip() for m in CLAUDE_MODEL_FALLBACKS.split(",") if m.strip()]
        # Prefer dated model IDs because some accounts do not expose *-latest aliases.
        dated_fallbacks = [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-5-20251101",
        ]
        fallbacks = fallback_from_env + dated_fallbacks
        candidates: List[str] = []
        for name in [configured] + fallbacks:
            n = (name or "").strip()
            if n and n not in candidates:
                candidates.append(n)
        return candidates

    def _create_message_with_model_fallbacks(self, *, has_image: bool, **kwargs):
        """Call Anthropic with model fallback when a configured model is unavailable."""
        candidates = self._model_candidates(has_image)
        last_error: Optional[Exception] = None
        for model in candidates:
            try:
                response = self.claude.messages.create(model=model, **kwargs)
                self.last_model_used = model
                return response
            except Exception as e:
                err = str(e).lower()
                if ("not_found_error" in err) or ("404" in err and "model" in err):
                    last_error = e
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Anthropic model could be selected")

    def _execute_loop(self) -> str:
        """Run the Claude tool-use loop until a final text response is returned."""
        model_candidates = self._model_candidates(self._has_image_in_history())
        active_model = model_candidates[0]
        prompt_trim_retries = 0
        while True:
            response = None
            last_model_error: Optional[Exception] = None
            prompt_too_large = False
            for model in model_candidates:
                try:
                    response = self.claude.messages.create(
                        model=model,
                        max_tokens=self.max_tokens,
                        system=[{"type": "text", "text": self.system,
                                  "cache_control": {"type": "ephemeral"}}],
                        tools=self.tools,
                        messages=self.history,
                        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                    )
                    if model != active_model:
                        log.warning(f"Anthropic model fallback in use: {model}")
                        active_model = model
                    break
                except Exception as e:
                    err = str(e).lower()
                    if "prompt is too long" in err or ("invalid_request_error" in err and "tokens >" in err):
                        prompt_too_large = True
                        last_model_error = e
                        break
                    if ("not_found_error" in err) or ("404" in err and "model" in err):
                        last_model_error = e
                        continue
                    raise

            if prompt_too_large:
                prompt_trim_retries += 1
                self._auto_trim_history()
                if prompt_trim_retries > 3:
                    raise last_model_error or RuntimeError("Prompt remained too large after auto-trim")
                continue

            if response is None:
                if last_model_error:
                    raise last_model_error
                raise RuntimeError("No Anthropic model could be selected")

            text_parts = [b.text for b in response.content if b.type == "text"]
            tool_uses  = [b for b in response.content if b.type == "tool_use"]
            self.history.append({"role": "assistant", "content": response.content})

            if not tool_uses:
                final = " ".join(text_parts) if text_parts else "(no response)"
                self.memory.log_message(self.session_id, "assistant", final)
                self._build_system()  # Refresh after memory may have been updated
                self.last_model_used = active_model
                return final

            tool_results = []
            for tu in tool_uses:
                log.info(f"Tool: {tu.name}({json.dumps(tu.input, default=str)[:100]})")
                result = dispatch_tool(tu.name, tu.input, self.dme, self.memory)
                log.debug(f"Result: {result[:150]}")
                print(f"  Tool: {tu.name}...", flush=True)
                safe_result = self._compact_tool_result_for_history(tu.name, result)
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": safe_result})
            self.history.append({"role": "user", "content": tool_results})
            self._auto_trim_history()

    def _run_turn(self, user_message: str) -> str:
        self._build_system()
        self.history.append({"role": "user", "content": user_message})
        self.memory.log_message(self.session_id, "user", user_message)
        self._auto_trim_history()
        return self._execute_loop()

    def _parse_human_date(self, raw: str) -> Optional[datetime.date]:
        text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", str(raw or "").strip(), flags=re.IGNORECASE)
        text = text.replace(",", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        now = datetime.now()
        patterns = [
            ("%Y-%m-%d", True),
            ("%m/%d/%Y", True),
            ("%m-%d-%Y", True),
            ("%m/%d/%y", True),
            ("%m-%d-%y", True),
            ("%B %d %Y", True),
            ("%b %d %Y", True),
            ("%B %d", False),
            ("%b %d", False),
            ("%m/%d", False),
            ("%m-%d", False),
        ]
        for fmt, has_year in patterns:
            try:
                dt = datetime.strptime(text, fmt)
                year = dt.year if has_year else now.year
                return dt.replace(year=year).date()
            except Exception:
                continue
        return None

    def _extract_refill_range(self, message: str) -> Optional[tuple[str, str]]:
        text = str(message or "")
        lower = text.lower()
        if "refill" not in lower:
            return None

        month_pat = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
        date_tokens = []
        date_tokens.extend(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", lower))
        date_tokens.extend(re.findall(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", lower))
        date_tokens.extend(re.findall(rf"\b{month_pat}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", lower))

        parsed: List[datetime.date] = []
        for token in date_tokens:
            d = self._parse_human_date(token)
            if d:
                parsed.append(d)

        # Keep first two dates in user order for "between X and Y" style asks.
        if len(parsed) < 2:
            return None
        start = parsed[0]
        end = parsed[1]
        if end < start:
            start, end = end, start
        return (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    def _handle_refill_range_request(self, message: str) -> Optional[str]:
        lower = str(message or "").lower()
        if "refill" not in lower:
            return None
        if not any(k in lower for k in ("between", "from", "to", "list", "show", "give", "who", "which")):
            return None

        rng = self._extract_refill_range(message)
        if not rng:
            return None
        start_iso, end_iso = rng

        data = self.dme.get_refills_due(days=7, start_date=start_iso, end_date=end_iso)
        rows = data if isinstance(data, list) else data.get("orders", []) if isinstance(data, dict) else []
        if not rows:
            return (
                f"No refill orders found between {start_iso} and {end_iso} "
                "using the Orders tab refill due-date logic."
            )

        # Deduplicate to order-level for reporting clarity.
        by_date: Dict[str, Dict[int, Dict[str, Any]]] = {}
        for r in rows:
            try:
                order_id = int(r.get("order_id") or 0)
            except Exception:
                order_id = 0
            if order_id <= 0:
                continue
            due_date = str(r.get("next_refill_due") or "").strip() or "unknown"
            patient = str(r.get("patient_name") or "").strip() or "Unknown patient"
            item_label = str(r.get("description") or r.get("hcpcs_code") or "item").strip()

            date_bucket = by_date.setdefault(due_date, {})
            entry = date_bucket.setdefault(order_id, {
                "patient_name": patient,
                "items": set(),
            })
            if item_label:
                entry["items"].add(item_label)

        total_orders = 0
        lines: List[str] = [f"ORDERS DUE FOR REFILL PROCESSING ({start_iso} to {end_iso})", ""]
        for due_date in sorted(by_date.keys()):
            orders = by_date[due_date]
            total_orders += len(orders)
            lines.append(f"DUE {due_date} ({len(orders)} orders):")
            for order_id in sorted(orders.keys()):
                info = orders[order_id]
                item_list = sorted(info["items"])
                if len(item_list) > 4:
                    item_text = ", ".join(item_list[:4]) + f", +{len(item_list)-4} more"
                else:
                    item_text = ", ".join(item_list)
                lines.append(f"- ORD-{order_id} - {info['patient_name']} ({item_text})")
            lines.append("")

        lines.append(f"TOTAL: {total_orders} unique orders between {start_iso} and {end_iso}")
        return "\n".join(lines)

    def chat(self, message: str) -> str:
        direct = self._handle_refill_range_request(message)
        if direct is not None:
            self._build_system()
            self.history.append({"role": "user", "content": message})
            self.memory.log_message(self.session_id, "user", message)
            self.history.append({"role": "assistant", "content": direct})
            self.memory.log_message(self.session_id, "assistant", direct)
            self._auto_trim_history()
            return direct
        return self._run_turn(message)

    def chat_with_image(self, message: str, image_b64: str, image_type: str = "image/png") -> str:
        """Send a message with an image or PDF attachment."""
        self._build_system()
        if image_type == "application/pdf":
            media_block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": image_b64}}
            log_prefix = "[PDF]"
        else:
            media_block = {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_b64}}
            log_prefix = "[IMAGE]"
        content = [media_block, {"type": "text", "text": message}]
        self.history.append({"role": "user", "content": content})
        self.memory.log_message(self.session_id, "user", f"{log_prefix} {message}")
        self._auto_trim_history()
        return self._execute_loop()

    def _proactive_startup(self) -> Optional[str]:
        """Check for urgent items at startup and return a brief message if found."""
        try:
            summary = self.dme.morning_summary()
            alerts = []
            refills_today = summary.get("refills", {}).get("due_today", 0)
            pending = summary.get("orders", {}).get("pending_approvals", 0)
            low_stock = summary.get("inventory", {}).get("low_or_out_of_stock_count", 0)
            if refills_today > 0:
                alerts.append(f"{refills_today} refill{'s' if refills_today > 1 else ''} due today")
            if pending > 0:
                alerts.append(f"{pending} order{'s' if pending > 1 else ''} pending approval")
            if low_stock > 0:
                alerts.append(f"{low_stock} inventory item{'s' if low_stock > 1 else ''} low or out of stock")
            # Active reminders
            active_reminders = self.memory.get_reminders(status="active")
            if active_reminders:
                alerts.append(f"{len(active_reminders)} open reminder{'s' if len(active_reminders) > 1 else ''} on your list")

            # Workflow startup checks from Nova audit priorities
            try:
                docs_needed = self.dme.get_orders_missing_docs(limit=300)
                stale_docs = 0
                cutoff = datetime.now().date() - timedelta(days=3)
                for row in (docs_needed if isinstance(docs_needed, list) else []):
                    raw = str(row.get("order_date") or "").strip()
                    parsed = None
                    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                        try:
                            parsed = datetime.strptime(raw.split(" ")[0], fmt).date()
                            break
                        except Exception:
                            continue
                    if parsed and parsed <= cutoff:
                        stale_docs += 1
                if stale_docs > 0:
                    alerts.append(f"{stale_docs} order{'s' if stale_docs > 1 else ''} stuck in Docs Needed over 3 days")
            except Exception:
                pass

            try:
                expiring_auth = self.dme.get_expiring_authorizations(days=7, limit=300)
                auth_count = len(expiring_auth) if isinstance(expiring_auth, list) else 0
                if auth_count > 0:
                    alerts.append(f"{auth_count} authorization{'s' if auth_count > 1 else ''} expiring within 7 days")
            except Exception:
                pass

            try:
                denied = self.dme.get_claims(status="Denied", limit=300)
                denied_rows = denied if isinstance(denied, list) else denied.get("claims", []) if isinstance(denied, dict) else []
                recent_denied = 0
                now = datetime.now()
                for row in denied_rows:
                    raw = str(row.get("updated_at") or row.get("updated_date") or row.get("adjudicated_at") or row.get("date") or "").strip()
                    parsed = None
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                        try:
                            parsed = datetime.strptime(raw[:19], fmt)
                            break
                        except Exception:
                            continue
                    if parsed and (now - parsed).total_seconds() <= 172800:
                        recent_denied += 1
                if recent_denied > 0:
                    alerts.append(f"{recent_denied} claim denial{'s' if recent_denied > 1 else ''} in the last 48 hours")
            except Exception:
                pass

            try:
                unmatched_docs = self.dme.get_unmatched_documents(limit=300)
                unmatched_count = len(unmatched_docs) if isinstance(unmatched_docs, list) else 0
                if unmatched_count > 0:
                    alerts.append(f"{unmatched_count} unmatched document{'s' if unmatched_count > 1 else ''} in inbox")
            except Exception:
                pass

            try:
                conn = self.dme._db_conn("orders.db")
                cur = conn.cursor()
                today = datetime.now().strftime("%Y-%m-%d")
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM orders
                    WHERE COALESCE(delivery_date, '') LIKE ?
                      AND COALESCE(tracking_number, '') = ''
                      AND COALESCE(order_status, '') != 'Cancelled'
                    """,
                    (f"{today}%",),
                )
                row = cur.fetchone()
                conn.close()
                no_tracking = int((row["cnt"] if row else 0) or 0)
                if no_tracking > 0:
                    label = "deliveries" if no_tracking > 1 else "delivery"
                    alerts.append(f"{no_tracking} {label} today without tracking number")
            except Exception:
                pass

            try:
                due_soon = self.dme.get_refills_due(days=3)
                due_rows = due_soon if isinstance(due_soon, list) else due_soon.get("orders", []) if isinstance(due_soon, dict) else []
                if due_rows:
                    alerts.append(f"{len(due_rows)} patient refill{'s' if len(due_rows) > 1 else ''} due within 3 days — verify outreach")
            except Exception:
                pass

            # RingCentral proactive communication checks
            if rc_tools is not None:
                try:
                    vm = rc_tools.get_voicemails(unread_only=True, limit=25)
                    vm_total = int(vm.get("total", 0)) if isinstance(vm, dict) else 0
                    if vm_total > 0:
                        alerts.append(f"{vm_total} unread voicemail{'s' if vm_total > 1 else ''}")
                except Exception:
                    pass

                try:
                    missed = rc_tools.get_missed_calls(limit=25, flag_known=True)
                    missed_records = missed.get("records") if isinstance(missed, dict) else []
                    urgent = sum(1 for row in (missed_records or []) if row.get("urgent"))
                    if urgent > 0:
                        alerts.append(f"{urgent} missed call{'s' if urgent > 1 else ''} from known patient/prescriber numbers")
                except Exception:
                    pass

                try:
                    unread_sms = rc_tools.get_unread_sms(limit=200)
                    now = datetime.now().astimezone()
                    stale = 0
                    for msg in (unread_sms.get("records") or []):
                        created = msg.get("created_at")
                        try:
                            created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                            if created_dt.tzinfo is None:
                                created_dt = created_dt.astimezone()
                            if (now - created_dt).total_seconds() >= 7200:
                                stale += 1
                        except Exception:
                            continue
                    if stale > 0:
                        alerts.append(f"{stale} unread SMS message{'s' if stale > 1 else ''} older than 2 hours")
                except Exception:
                    pass

                try:
                    sent_faxes = rc_tools.list_sent_faxes(limit=50)
                    failed_faxes = sum(1 for row in (sent_faxes.get("records") or []) if row.get("failed"))
                    if failed_faxes > 0:
                        alerts.append(f"{failed_faxes} recent fax delivery failure{'s' if failed_faxes > 1 else ''}")
                except Exception:
                    pass

            if alerts:
                return "Heads up: " + ", ".join(alerts) + "."
        except Exception:
            pass
        return None

    def end_session(self):
        """Save session summary to memory — uses basic topic extraction."""
        if len(self.history) > 2:
            try:
                topics = set()
                keywords = {"patient": "patients", "order": "orders", "refill": "refills",
                            "billing": "billing", "inventory": "inventory"}
                for msg in self.history:
                    raw = msg.get("content", "")
                    if isinstance(raw, list):
                        parts = []
                        for block in raw:
                            if isinstance(block, dict):
                                parts.append(block.get("content", "") or block.get("text", ""))
                            else:
                                txt = getattr(block, "text", "")
                                if txt:
                                    parts.append(txt)
                        raw = " ".join(str(p) for p in parts)
                    text = str(raw).lower()
                    for kw, topic in keywords.items():
                        if kw in text:
                            topics.add(topic)
                summary = f"Discussed: {', '.join(sorted(topics)) if topics else 'general queries'}"
                self.memory.end_session(self.session_id, summary)
            except Exception:
                self.memory.end_session(self.session_id)

    def auto_summarize_session(self) -> Optional[Dict]:
        """Use Sonnet to deeply analyze the session and extract structured insights.

        Called on WebSocket disconnect. Returns the insight dict or None.
        """
        if len(self.history) < 4:
            return None  # Not enough conversation to summarize

        try:
            # Build a condensed transcript from session messages
            conn = self.memory._conn()
            rows = conn.execute(
                "SELECT role, content FROM session_msgs WHERE session_id = ? ORDER BY ts",
                (self.session_id,)
            ).fetchall()
            conn.close()

            if len(rows) < 4:
                return None

            transcript_lines = []
            for r in rows:
                role = r["role"].upper()
                content = r["content"]
                # Skip image markers and very long tool results
                if content.startswith("[IMAGE]") or content.startswith("[PDF]"):
                    content = content[:100]
                if len(content) > 300:
                    content = content[:300] + "..."
                transcript_lines.append(f"{role}: {content}")

            transcript = "\n".join(transcript_lines[-40:])  # Last 40 messages max

            summarize_prompt = f"""Analyze this pharmacy operations session transcript and extract structured insights.

TRANSCRIPT:
{transcript}

Return a JSON object with these fields:
{{
  "summary": "2-3 sentence summary of what was accomplished this session",
  "patients": [{{ "id": "patient_id_if_known", "name": "PATIENT NAME", "context": "what was discussed/done" }}],
  "orders": [{{ "id": "order_id", "action": "what was done to this order" }}],
  "decisions": ["decision 1", "decision 2"],
  "unresolved": ["item left unfinished 1", "item left unfinished 2"],
  "learned": ["important fact or preference expressed by the user"],
  "corrections": [
    {{
      "what_nova_did_wrong": "description of the mistake Nova made",
      "what_user_wanted": "the correct behavior, stated as a rule Nova should always follow",
      "severity": "high | medium | low"
    }}
  ]
}}

Rules:
- Only include fields that actually apply — empty lists for things not discussed
- For "learned": capture things the user said casually that reveal preferences, corrections, or important context
- For "unresolved": anything the user started but didn't finish, or asked about but didn't get resolved
- For "corrections": look for moments where the user corrected Nova — phrases like "no", "that's wrong", "I told you", "you don't see?", "why did you", "that's not right", "fix this", "I said X not Y". Extract the CORRECT behavior as a clear rule.
  - "high" severity = Nova did the opposite of what was asked, or made the same mistake again
  - "medium" = Nova misunderstood or got a detail wrong
  - "low" = minor preference or formatting issue
- Be specific — use patient names, order numbers, HCPCS codes when available
- Return ONLY the JSON object, no other text"""

            # Use Haiku for summarization (text-only structured extraction)
            response = self._create_message_with_model_fallbacks(
                has_image=False,
                max_tokens=1024,
                messages=[{"role": "user", "content": summarize_prompt}],
            )

            result_text = " ".join(b.text for b in response.content if b.type == "text")

            # Parse JSON from response
            import re
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                insight = json.loads(json_match.group())
            else:
                insight = {"summary": result_text.strip()[:500]}

            # Save structured insight
            self.memory.save_session_insight(self.session_id, insight)

            # Save entity interactions from the insight
            for patient in insight.get("patients", []):
                if isinstance(patient, dict) and patient.get("name"):
                    self.memory.log_entity_interaction(
                        entity_type="patient",
                        entity_id=patient.get("id", "unknown"),
                        entity_name=patient["name"],
                        context=patient.get("context", "discussed"),
                        session_id=self.session_id,
                    )

            for order in insight.get("orders", []):
                if isinstance(order, dict) and order.get("id"):
                    self.memory.log_entity_interaction(
                        entity_type="order",
                        entity_id=str(order["id"]),
                        context=order.get("action", "reviewed"),
                        session_id=self.session_id,
                    )

            # Auto-save important learned facts to persistent memory
            for fact in insight.get("learned", []):
                if fact and len(fact) > 10:
                    self.memory.remember(
                        content=fact,
                        category="auto_learned",
                        source="session_summary",
                    )

            # Process corrections → escalate to learned rules
            for correction in insight.get("corrections", []):
                if not isinstance(correction, dict):
                    continue
                rule_text = correction.get("what_user_wanted", "").strip()
                mistake = correction.get("what_nova_did_wrong", "").strip()
                severity = correction.get("severity", "medium").lower()

                if not rule_text or len(rule_text) < 10:
                    continue

                # High severity = immediate rule creation
                # Medium/low = add to rules, will escalate after repeated corrections
                source = f"Session {self.session_id}: {mistake[:100]}"
                rid = self.memory.add_learned_rule(rule_text, source=source)

                # Check if this rule has been corrected enough times to log a warning
                rules = self.memory.get_learned_rules()
                for r in rules:
                    if r["id"] == rid and r["times_corrected"] >= 3:
                        log.warning(
                            f"RULE ESCALATED (corrected {r['times_corrected']}x): {rule_text[:80]}"
                        )

                log.info(f"Correction logged [severity={severity}]: {rule_text[:80]}")

            log.info(f"Session {self.session_id} auto-summarized: {insight.get('summary', '')[:100]}")
            return insight

        except Exception as e:
            log.warning(f"Auto-summarize failed: {e}")
            return None

    def run_interactive(self) -> None:
        print("\n" + "═"*60)
        print("  Nova — DMELogic Intelligent Assistant  v3.0")
        print("  Central Pharmacy Group, Bronx NY")
        print("═"*60)
        print("  Type your message and press Enter.")
        print("  Commands: quit | clear | memory | sessions")
        if self.voice.enabled:
            print("  Voice: ON (ElevenLabs)")

        # Memory summary at startup
        mems = self.memory.recall_all(limit=5)
        if mems:
            print(f"  Memory: {len(self.memory.recall_all())} items loaded from previous sessions")
        print("═"*60 + "\n")

        # Proactive startup alert
        alert = self._proactive_startup()
        if alert:
            print(f"Nova: {alert}\n")
            if self.voice.enabled:
                self.voice.speak(alert)

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                self.end_session()
                break

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "bye"):
                farewell = "See you later."
                print(f"Nova: {farewell}")
                if self.voice.enabled:
                    self.voice.speak(farewell)
                self.end_session()
                break

            if user_input.lower() == "clear":
                self.history.clear()
                print("Nova: Conversation cleared. Memory is still intact.\n")
                continue

            if user_input.lower() in ("memory", "reminders"):
                # Show active reminders
                reminders = self.memory.get_reminders(status="active")
                if reminders:
                    print(f"Nova: {len(reminders)} active reminder(s):")
                    for r in reminders:
                        print(f"  #{r['id']} [{r['tag']}] {r['content']}")
                else:
                    print("Nova: No active reminders.")
                # Show memories
                mems = self.memory.recall_all()
                if mems:
                    print(f"       {len(mems)} memory item(s) stored:")
                    for m in mems:
                        subj = f"[{m['subject']}] " if m['subject'] else ""
                        print(f"  #{m['id']} [{m['category']}] {subj}{m['content']}")
                print()
                continue

            if user_input.lower() == "sessions":
                sessions = self.memory.get_recent_sessions(5)
                if sessions:
                    print("Nova: Recent sessions:")
                    for s in sessions:
                        print(f"  {s['started_at'][:16]} — {s['summary'] or 'no summary'} ({s['msg_count']} messages)")
                else:
                    print("Nova: No previous sessions found.")
                print()
                continue

            print("Nova: ", end="", flush=True)
            try:
                response = self.chat(user_input)
                print(response + "\n")
                if self.voice.enabled:
                    self.voice.speak(response)
            except anthropic.APIError as e:
                print(f"[API error: {e}]\n")
            except Exception as e:
                print(f"[Error: {e}]\n")
                log.exception("Agent turn error")

    def run_once(self, message: str) -> None:
        print("Nova: ", end="", flush=True)
        response = self.chat(message)
        print(response)
        if self.voice.enabled:
            self.voice.speak(response)
        self.end_session()


# ══════════════════════════════════════════════════════════════════════════
#  PHONE AGENT — answers live calls (used by nova_ui_server /call-audio)
# ══════════════════════════════════════════════════════════════════════════
_PHONE_TOOL_NAMES = {
    "search_patients", "search_patients_by_phone", "get_patient",
    "get_patient_orders", "get_patient_refills_eligible", "get_order",
    "add_reminder", "remember",
    # Refill workflow actions
    "process_refill", "send_new_rx_request_fax",
    "create_patient_tracking_note", "get_extension_status",
    "add_must_go_out",
}
PHONE_TOOLS = [t for t in TOOLS if t.get("name") in _PHONE_TOOL_NAMES]

PHONE_PERSONA = """
## LIVE PHONE CALL MODE
You are Nova answering the pharmacy's phone. You are SPEAKING OUT LOUD to a caller
on a live voice call. Everything you write is converted to speech.

Caller context:
- Caller phone number: {caller_number}
- {patient_context}
- The caller MAY be this patient, a family member, or someone else entirely.
  You must still verify identity before sharing any personal information.

Speaking style (strict):
- 1 to 3 short sentences per turn. Plain spoken language.
- NO markdown, NO lists, NO emojis, NO headings — this is speech.
- Say numbers naturally ("July twelfth", "four boxes").
- Ask ONE question at a time and wait for the answer.
- Be patient. Let the caller finish; do not rush them or talk over them.
- NEVER blame the caller, the line, or "noise/static" for not understanding.
  If something is unclear, warmly ask them to repeat it once ("Sorry, could
  you say that once more?"). Never say the connection is bad or that there is
  a lot of noise.
- If the caller speaks Spanish or asks for Spanish, switch to Spanish and
    stay in Spanish until they ask to switch back.

Identity verification (strict, ONE-WAY):
- Before sharing ANY order, prescription, or personal details, ask for the
  caller's full name AND date of birth, then silently look them up and compare.
- When you ask for the date of birth, ask for it once and let the caller say
  the whole date. If you are unsure you heard the month, day, or year, repeat
  back the date the caller JUST told you and ask them to confirm ("So that's
  March fifth, nineteen ninety — is that right?"). Repeating the caller's OWN
  spoken words back to confirm is allowed and encouraged; it prevents repeated
  do-overs. This is NOT the same as reading a date of birth from a record,
  which is never allowed.
- MANDATORY: The instant you have BOTH a name and a stated date of birth, you
  MUST call search_patients before you say anything about verification. NEVER
  say "I'm not able to verify" or ask for the DOB again until AFTER you have
  actually called search_patients and inspected the results. Refusing without
  searching is a serious error. The caller's age is irrelevant — infants and
  children have records too; always search.
- HOW to look up (do this before deciding a match):
  1. Call search_patients with the spoken name. Try the name as given; if
     nothing matches, try just the last name, and try last/first swapped
     (many records store compound last names like "OLEA DIAZ").
  2. Among the returned records, a caller is VERIFIED only when a record's
     date of birth matches the DOB the caller stated. Records store DOB as
     MM/DD/YYYY — compare by calendar date, not text (e.g. "January fifteenth
     twenty twenty" == "01/15/2020").
  3. Minor name spelling differences are OK if the DOB matches exactly. A
     wrong DOB is NEVER a match, even if the name matches.
  4. Only after a search returns NO record whose DOB matches may you conclude
     there is no match. Do NOT dead-end there: a caller with no record may be
     a brand-new patient or a prescriber's office. Follow the section
     "CALLERS WITH NO MATCHING RECORD" below to figure out who they are and
     help them appropriately.
- NEVER read names, dates of birth, addresses, or any record details TO the
  caller — not even to explain a mismatch. Verification is one-way: the caller
  states details, you compare them privately. On a mismatch say only
  "I'm not able to verify that information" — never say what the record shows
  or who a phone number belongs to.
- If verification fails and the caller asked for refill/order/status help,
    immediately offer two safe options in the SAME reply: take a callback
    message for staff OR transfer to a team member.
- If verification fails after two attempts, you must offer message or transfer
    before ending the call. Do not end with only "you're welcome"/"goodbye"
    unless the caller clearly declines further help.
- Take messages without revealing whether any record exists.

You may help with: order status, refill requests, new-patient intake, taking a
message for staff or a prescriber's office, store hours and general questions.

## CALLERS WITH NO MATCHING RECORD (new patients & prescribers)
When search_patients finds no record matching the caller's name and DOB, do
NOT just say you can't help. First find out who they are and why they called.
Ask warmly: "I'm not finding an existing record — are you calling as a new
patient, or on behalf of a doctor's office?" Then branch:

  A) EXISTING patient who likely mis-stated their info:
     - If they insist they are already a patient, let them restate their name
       and date of birth once and search again (people transpose digits or use
       nicknames). If it still does not match after two tries, treat them as a
       new patient (B) or take a message.

  B) NEW patient (never been seen here):
     - You cannot create records or orders yourself. Collect intake details so
       staff can set them up: full name, date of birth, best callback number,
       what they need (item/equipment or prescription), their insurance if they
       offer it, and their prescriber's name if they have one. Ask one item at
       a time; do not demand anything they are reluctant to share.
     - Create ONE callback task with add_reminder, tag "new_patient", whose
       content includes everything you gathered and that this is a NEW patient
       intake needing setup. (Do NOT call create_patient_tracking_note — that
       needs an existing patient id, which a new patient does not have.)
     - Tell them warmly that our team will reach out shortly to get them set up.
       Never quote prices, coverage, or eligibility.

  C) PRESCRIBER / DOCTOR'S OFFICE / caregiver calling ABOUT a patient:
     - Do NOT ask them for the patient's date of birth as identity — they are
       not the patient. Do NOT read any patient details back to them; you still
       never reveal PHI to an unverified caller.
     - Find out what they need: sending a new prescription, checking on an
       order or fax, or reaching a specific person.
       * If they want to SEND a prescription: tell them they can fax it to the
         pharmacy or send it electronically to us, and that our team will
         process it. Take a message so staff expect it.
       * If they are asking about an existing order/patient: take a message or
         offer to transfer to a team member — do not confirm or deny any
         patient details.
     - Record it with add_reminder, tag "prescriber_callback", content
       including the caller's name, practice/office, the prescriber, the
       patient they mentioned, a callback number, and exactly what they need.
     - Offer a transfer if they want to speak with someone now (see below).

For ANY no-record caller, if they ask to speak with a person, use the
"SPEAK TO A LIVE PERSON" steps below.

## REFILL REQUESTS (follow this exactly)
Only after the caller is VERIFIED (name + DOB matched via search_patients).

Step 1 — Look up internally (never read any of this out loud):
- Call get_patient_refills_eligible with the patient_id from the matched record.
- If the caller named a specific medication or item, pick the matching order.
  If more than one could apply and it is unclear, ask which prescription they
  mean. If only one eligible order exists, use it.
- For that order read two things PRIVATELY: refills_remaining and
  refill_due_date. Today's date is {today}.
  * "Has refills" = refills_remaining is greater than zero.
  * "Due" = a refill MAY be processed up to 3 days BEFORE its listed due date.
    So treat the order as DUE when refill_due_date is today, in the past, OR
    within the next 3 days (that is, refill_due_date is 3 or fewer days after
    today). Only treat it as "not yet due" when refill_due_date is MORE than
    3 days away from today.
- IMPORTANT — empty eligible list does NOT mean "nothing to do":
  get_patient_refills_eligible ONLY lists orders that still have refills left
  and are not locked. If it returns an EMPTY list (or omits the order the
  caller is asking about), the patient most likely has an order with NO refills
  remaining. When that happens you MUST call get_patient_orders with the same
  patient_id, then pick the most recent active order (or the one matching the
  item the caller named) and read its refill_status, max_refills, and
  refill_due_date PRIVATELY. If that order's refill_status is "No refills left"
  (max_refills is 0), follow path C below and fax the prescriber for that
  order_id. Never conclude "there is nothing on file" just because the eligible
  list was empty.

Step 2 — Decide and act. NEVER tell the caller how many refills remain or the
exact due date — only whether refills exist or not, in plain words.

  *** INTERNAL-ONLY INFORMATION — NEVER SPEAK THIS TO A CALLER ***
  The order's STATUS ("Billed", "Unbilled", "Submitted", "Approved",
  "Shipped", "Delivered", "Picked Up", etc.), WHETHER it has or has not been
  marked delivered/picked up, and ANY DATES (billed date, order date, processed
  date, due date) are STRICTLY INTERNAL. They exist for you to make a decision
  and to write the STAFF note ONLY. You must NEVER say any of these to the
  caller. FORBIDDEN caller phrases include (do not say anything like these):
  "it was billed on July 6th", "it's billed but not marked delivered", "it
  hasn't been delivered yet", "the status is Billed", "it was processed on
  [date]". If the caller has an approved/processed refill in the pipeline, the
  ONLY thing the caller may hear is: it LOOKS LIKE they have an APPROVED refill
  and SOMEONE WILL GET BACK TO THEM with the status and follow up. Nothing more.

  *** HOW TO CREATE A STAFF FOLLOW-UP (do all three whenever you promise the
      caller someone will get back to them) ***
  1. add_must_go_out — patient_name (Last, First), patient_phone (callback
     number), and notes describing what staff must do (e.g. "Refill request —
     order appears Billed on [date from Orders tab]; VERIFY delivery/pickup
     status and confirm with patient" or "Refill not yet due — patient
     requesting; call back"). Include order_id if you know it. This puts it on
     the DMELogic 'Must Go Out' tab so staff physically see it.
  2. add_reminder — tag "refill_callback", content with the caller's name,
     phone number, patient, and order/medication. Set due_at to RIGHT NOW
     (today's date and current time) and remind_every_minutes to 30 so the
     alert actively repeats until a staff member marks it done — do NOT leave
     due_at blank or it will sit silently.
  3. create_patient_tracking_note — patient_id, disposition "other", and a
     summary. Internal detail (billed date, status) goes HERE only.

  A) HAS refills AND it is DUE (due date is today, past, or within 3 days):
     - First check the status of the MOST RECENT order for that
       medication/supply (the last one processed). SPECIAL CASE — already
       billed (status may be stale): if that most-recent order's status is
       "Billed" (or "Unbilled", "Submitted", "Approved", "Shipped" — moving
       through billing/fulfillment) but is NOT yet "Delivered" or "Picked Up",
       then do NOT call process_refill. NOTE: staff sometimes forget to update
       an order to "Delivered"/"Picked Up" after the fact, so a "Billed" order
       may already have gone out — this must be VERIFIED by staff before the
       caller is told anything definite. Do the following:
       * Say to the caller ONLY this (in your own warm words): it looks like
         they have an APPROVED refill, and someone will get back to them with
         the status and follow up. Do NOT mention the word "billed", do NOT
         mention delivery/pickup status, and do NOT read ANY date out loud.
         Do NOT promise it is "on the way" or "being delivered" as a fact.
         Offer that they may also speak with a live person if they'd like
         (SPEAK TO A LIVE PERSON steps).
       * Create a STAFF FOLLOW-UP (all three steps above): add_must_go_out,
         add_reminder (tag "refill_callback", due_at NOW, remind_every_minutes
         30), and create_patient_tracking_note. In the must-go-out notes and
         the tracking note, state that the order appears as Billed on the date
         listed on the Orders tab and that staff should VERIFY the
         delivery/pickup status and confirm with the patient.
     - Otherwise (the most-recent order is genuinely refillable now, not
       already in the billing/fulfillment pipeline):
       * Call process_refill with that order_id. (It goes to staff for review
         and billing — you are not billing anything yourself.)
       * Tell the caller warmly: their refill is being processed and the team
         will have it ready. Do not mention counts or dates.

  B) HAS refills but NOT yet due (due date is MORE than 3 days away):
     - Before treating this as "not yet due," check the status of the MOST
       RECENT order for that medication/supply (the last one processed — look
       at the newest order in get_patient_orders / get_patient_refills_eligible
       for that item). The reason it is "not due" is that a refill was already
       processed recently.
       * SPECIAL CASE — already approved (status may be stale): if that
         most-recent processed order's status is "Billed" (or "Unbilled",
         "Submitted", "Approved", "Shipped" — i.e. it is moving through
         billing/fulfillment) but is NOT yet "Delivered" or "Picked Up", then
         do NOT tell the caller the refill is "not due." NOTE: staff sometimes
         forget to update an order to "Delivered"/"Picked Up", so this may
         already have gone out and must be VERIFIED before confirming anything
         to the caller. Say to the caller ONLY this (in your own warm words):
         it looks like they have an APPROVED refill, and someone will get back
         to them with the status and follow up. Do NOT mention the word
         "billed", do NOT mention delivery/pickup status, and do NOT read ANY
         date out loud. Do NOT state as fact that it is on the way. Offer that
         they may also speak with a live person if they'd like (use the SPEAK
         TO A LIVE PERSON steps). Do NOT call process_refill again (it is
         already in progress). Create a STAFF FOLLOW-UP (all three steps
         above): add_must_go_out, add_reminder (tag "refill_callback", due_at
         NOW, remind_every_minutes 30), and create_patient_tracking_note noting
         the order appears as Billed on the date listed on the Orders tab and
         that staff should VERIFY the delivery/pickup status and confirm with
         the patient.
       * Otherwise (the most-recent order is already "Delivered" or "Picked Up",
         so this is a genuine future refill): follow the not-yet-due handling
         below.
     - Do NOT call process_refill.
     - Tell the caller someone will contact them shortly about their refill.
     - Create a STAFF FOLLOW-UP (all three steps above):
       1. add_must_go_out with the patient name, callback phone, and notes that
          the patient requested a refill that is not yet due and wants a
          callback.
       2. add_reminder with tag "refill_callback" and content that includes the
          caller's name, phone number, the patient, the order/medication, and
          that they requested a refill that is not yet due. Set due_at to NOW
          and remind_every_minutes to 30.
       3. create_patient_tracking_note with the patient_id, disposition "other",
          and a summary of the refill request and callback needed.

  C) NO refills remaining:
     - This applies whenever the order the caller is asking about has no refills
       left — whether you found that from get_patient_refills_eligible OR from
       the get_patient_orders fallback (refill_status "No refills left").
     - ORDER OF OPERATIONS MATTERS. In the SAME turn you send the fax, your
       SPOKEN reply MUST first explain the situation to the caller. Never reply
       with only "it's done" or "we already sent the request" — the caller must
       hear WHY. Tell them CLEARLY, in your own warm spoken words, ALL THREE of
       these things:
       1. Their order has no refills left and new prescriptions are needed.
       2. They should contact their doctor and have them send over new scripts.
       3. On our end, we will ALSO fax a request for new prescriptions to their
          medical provider.
       Example phrasing (adapt naturally, keep it short and spoken): "That
       prescription has no refills left, so we'll need new scripts. Please
       contact your doctor and ask them to send over new prescriptions. On our
       end, we'll also fax a request for new prescriptions to your provider."
       Do NOT say the team will "get the refills" — there are none; new
       prescriptions must come from the doctor. Do NOT announce the fax as a
       finished fact ("we already sent it") without first giving this
       explanation in the same reply.
     - Alongside that spoken explanation you MUST actually send the request to
       the prescriber: call send_new_rx_request_fax with the patient_id and the
       order_id you identified (from either the eligible list or
       get_patient_orders). This fax step is mandatory on a no-refills order —
       never promise it without calling the tool, and never call the tool
       silently without the spoken explanation above.
     - Also create a callback task: add_reminder tagged "refill_callback"
       (caller name, number, patient, order/medication, "no refills — new Rx
       requested from prescriber") AND create_patient_tracking_note (disposition
       "forwarded", summary of the new-prescription request sent to the doctor).

## SPEAK TO A LIVE PERSON
If at any point the caller asks to speak to a person, representative, or
pharmacist:
- Call get_extension_status to see if anyone is Available (not on a call, not
  Do Not Disturb).
- If someone is Available: tell the caller you're connecting them now and append
  <<TRANSFER>> at the end of your reply.
- If NO one is available: tell the caller no one is free right now but someone
  will get back to them shortly, then create a callback task with add_reminder
  (tag "callback") including the caller's name, number, and what they wanted.
  Also add a create_patient_tracking_note ONLY if you have a verified patient
  id; for new patients or prescribers, add_reminder alone is enough. Do not
  transfer to no one.
- ONE transfer attempt only. If you already tried to connect the caller to a
  person and it did not go through (no one answered), do NOT offer or attempt a
  transfer again. Instead say a real person is not available right now, assure
  them someone will call back, take their message, and wrap up. Never bounce the
  caller between "let me transfer you" and "no one answered" more than once.

You must NEVER: quote prices, copays, or billing amounts; give medical or
dosage advice; change or cancel orders; reveal how many refills are left or a
patient's exact due dates. For anything outside the above, take a message or
transfer to staff.

Call control markers (put at the VERY END of your reply when needed):
- Append <<TRANSFER>> when the caller asks for a person/representative/
  pharmacist, is frustrated, or you cannot help after two attempts. Say you
  are transferring them first.
- Append <<HANGUP>> ONLY under the strict ending rules below.
The markers are stripped before speech — the caller never hears them.

## ENDING A CALL (applies to EVERY call — strict)
- NEVER just end the call or hang up on your own. After you have handled what
  the caller needed, ALWAYS ask if there is anything else you can help them
  with (e.g. "Is there anything else I can help you with today?").
- If they still need something, keep helping and ask again when done.
- If they say no / they're all set: thank them warmly and ask THEM to hang up
  to end the call (e.g. "Thank you for calling. You can go ahead and hang up
  whenever you're ready — have a great day."). Do NOT append <<HANGUP>>.
- Let the CALLER hang up. Do not append <<HANGUP>> before the caller does,
  UNLESS at least 2 minutes have passed since the call started and the caller
  has gone silent / is no longer responding. Only then may you give a final
  goodbye and append <<HANGUP>>.
- This rule applies to all calls, including verified callers, unverified
  callers, new patients, prescribers, and wrong numbers.
"""


class PhoneAgent(NovaAgent):
    """Nova persona for answering live phone calls: short spoken replies,
    restricted tool set, identity verification before PHI."""

    _VERIFY_FAIL_RE = re.compile(r"\b(not able to verify|can't verify|cannot verify)\b", re.IGNORECASE)
    _VERIFY_FAIL_RE_ES = re.compile(r"\b(no puedo verificar|no se puede verificar)\b", re.IGNORECASE)
    _FOLLOWUP_HINT_RE = re.compile(
        r"\b(message|call\s?back|callback|transfer|team member|representative|pharmacist|mensaje|devolver\s+la\s+llamada|transferir|miembro\s+del\s+equipo|representante|farmaceutico)\b",
        re.IGNORECASE,
    )
    _VERIFY_DETAIL_RE = re.compile(
        r"(\bi found a record\b|\bdate of birth\b.*\b(on file|in our system|record|match|mismatch)\b|\bdob\b.*\b(on file|in our system|record|match|mismatch)\b)",
        re.IGNORECASE,
    )
    _VERIFY_DETAIL_RE_ES = re.compile(
        r"(\bencontre\s+un\s+registro\b|\bfecha\s+de\s+nacimiento\b.*\b(sistema|registro|coincide|no coincide)\b)",
        re.IGNORECASE,
    )
    _SPANISH_REQUEST_RE = re.compile(
        r"\b(spanish|espanol|en espanol|habla espanol|prefiero espanol|quiero espanol)\b",
        re.IGNORECASE,
    )
    _ENGLISH_REQUEST_RE = re.compile(
        r"\b(english|in english|en ingles|prefiero ingles|quiero ingles)\b",
        re.IGNORECASE,
    )
    _SPANISH_HINT_RE = re.compile(
        r"\b(hola|gracias|por favor|buenas|necesito|quiero|mensaje|equipo|farmacia|fecha de nacimiento|llamada|resurtido|relleno)\b",
        re.IGNORECASE,
    )

    def _update_language_mode(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        if self._ENGLISH_REQUEST_RE.search(text):
            self._language_mode = "en"
            return
        if self._SPANISH_REQUEST_RE.search(text) or self._SPANISH_HINT_RE.search(text):
            self._language_mode = "es"

    def _sanitize_failed_verification_reply(self, reply: str) -> str:
        text = str(reply or "").strip()
        leaked_verify_detail = bool(
            self._VERIFY_FAIL_RE.search(text)
            or self._VERIFY_FAIL_RE_ES.search(text)
            or self._VERIFY_DETAIL_RE.search(text)
            or self._VERIFY_DETAIL_RE_ES.search(text)
        )
        if not text or not leaked_verify_detail:
            return text

        # Preserve call-control markers while normalizing the spoken text.
        transfer = "<<TRANSFER>>" in text
        hangup = "<<HANGUP>>" in text

        if self._language_mode == "es":
            if self._FOLLOWUP_HINT_RE.search(text):
                clean = (
                    "No puedo verificar esa informacion. "
                    "Quiere que tome un mensaje para que nuestro personal le devuelva la llamada, "
                    "o prefiere hablar con un miembro del equipo ahora?"
                )
            else:
                clean = (
                    "No puedo verificar esa informacion. "
                    "Puedo tomar un mensaje para que el personal le devuelva la llamada, "
                    "o transferirle con un miembro del equipo."
                )
        else:
            if self._FOLLOWUP_HINT_RE.search(text):
                clean = (
                    "I'm not able to verify that information. "
                    "Would you like me to take a message for our staff to call you back, "
                    "or would you prefer to speak with a team member now?"
                )
            else:
                clean = (
                    "I'm not able to verify that information. "
                    "I can take a callback message for staff, or transfer you to a team member."
                )

        if transfer:
            clean = f"{clean} <<TRANSFER>>"
        if hangup:
            clean = f"{clean} <<HANGUP>>"
        return clean.strip()

    def chat(self, message: str) -> str:
        msg = str(message or "")
        stripped = msg.strip()
        if stripped.lower().startswith("the call has ended."):
            raw = super().chat(message)
            return raw

        self._update_language_mode(stripped)

        model_input = stripped
        if self._language_mode == "es":
            model_input = (
                "Caller language mode is Spanish. Reply in natural spoken Spanish unless the caller asks to switch languages. "
                "Keep all privacy and verification rules unchanged. Caller says: "
                f"{stripped}"
            )

        raw = super().chat(model_input)
        return self._sanitize_failed_verification_reply(raw)

    def __init__(self, caller_number: str = "", patient_match: Optional[Dict] = None):
        self._language_mode = "en"
        patient_context = "No patient record matches this phone number."
        if patient_match and isinstance(patient_match, dict):
            p = patient_match.get("patient") or patient_match
            name = str(p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}").strip()
            pid = p.get("id") or p.get("patient_id")
            if name or pid:
                patient_context = (f"This number matches patient: {name or 'unknown name'}"
                                   f"{f' (patient id {pid})' if pid else ''}.")
        suffix = PHONE_PERSONA.format(
            caller_number=caller_number or "unknown",
            patient_context=patient_context,
            today=datetime.now().strftime("%m/%d/%Y"),
        )
        super().__init__(voice=False, system_suffix=suffix,
                         tools=PHONE_TOOLS, max_tokens=300)


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Nova — DMELogic Intelligent Agent v3.0")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--run",   type=str, default="")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
    nova = NovaAgent(voice=args.voice)
    if args.run:
        nova.run_once(args.run)
    else:
        nova.run_interactive()

if __name__ == "__main__":
    main()