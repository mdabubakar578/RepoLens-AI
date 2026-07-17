"""
database.py — SQLite connection, table creation, and query helpers.
All pages and services use these helpers — no direct DB access elsewhere.
"""
import sqlite3, json, os
from datetime import datetime
from config import DATABASE_PATH

def get_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                slug                 TEXT UNIQUE NOT NULL,
                repo_url             TEXT,
                repo_name            TEXT,
                input_mode           TEXT DEFAULT 'url',
                raw_commits_json     TEXT,
                grouped_commits_json TEXT,
                narrative_release    TEXT,
                narrative_standup    TEXT,
                narrative_onboarding TEXT,
                narrative_portfolio  TEXT,
                extended_data_json   TEXT,
                commit_count         INTEGER DEFAULT 0,
                status               TEXT DEFAULT 'pending',
                error_message        TEXT,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_analyses_slug ON analyses(slug);
            CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
        """)
    _migrate_extended_column()
    seed_demo_analyses_if_empty()

def _migrate_extended_column():
    """Add extended_data_json column if it doesn't exist (migration)."""
    try:
        with get_db() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(analyses)").fetchall()]
            if "extended_data_json" not in cols:
                conn.execute("ALTER TABLE analyses ADD COLUMN extended_data_json TEXT")
    except Exception:
        pass

# ─── CRUD ─────────────────────────────────────────────────────────────────────

def save_analysis(slug, repo_url, repo_name, input_mode, raw_commits, grouped_commits, commit_count):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO analyses
               (slug, repo_url, repo_name, input_mode, raw_commits_json, grouped_commits_json, commit_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (slug, repo_url, repo_name, input_mode,
             json.dumps(raw_commits, default=str), json.dumps(grouped_commits, default=str), commit_count))
        return cursor.lastrowid

def update_narratives(analysis_id, narratives: dict):
    with get_db() as conn:
        conn.execute(
            """UPDATE analyses SET narrative_release=?, narrative_standup=?,
               narrative_onboarding=?, narrative_portfolio=?, status='done' WHERE id=?""",
            (narratives.get("release",""), narratives.get("standup",""),
             narratives.get("onboarding",""), narratives.get("portfolio",""), analysis_id))

def save_extended_data(analysis_id, data: dict):
    with get_db() as conn:
        conn.execute("UPDATE analyses SET extended_data_json=? WHERE id=?",
            (json.dumps(data, default=str), analysis_id))

def set_error(analysis_id, message):
    with get_db() as conn:
        conn.execute("UPDATE analyses SET status='error', error_message=? WHERE id=?", (message, analysis_id))

def get_analysis_by_id(analysis_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        return dict(row) if row else None

def get_analysis_by_slug(slug):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

def get_all_analyses(search="", page=1, per_page=12):
    offset = (page - 1) * per_page
    with get_db() as conn:
        if search:
            pattern = f"%{search}%"
            rows = conn.execute(
                "SELECT * FROM analyses WHERE repo_name LIKE ? OR repo_url LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (pattern, pattern, per_page, offset)).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE repo_name LIKE ? OR repo_url LIKE ?",
                (pattern, pattern)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, offset)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        return [dict(r) for r in rows], total

def get_extended_data(analysis_id) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT extended_data_json FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if row and row[0]:
            try: return json.loads(row[0])
            except Exception: pass
    return {}

def seed_demo_analyses_if_empty():
    """Insert demo analyses when they are missing so hosted demos are explorable."""
    if os.environ.get("ENABLE_DEMO_DATA", "true").lower() != "true":
        return

    with get_db() as conn:
        from services.demo_data import DEMO_ANALYSES

        for demo in DEMO_ANALYSES:
            conn.execute(
                """INSERT OR IGNORE INTO analyses
                   (slug, repo_url, repo_name, input_mode, raw_commits_json, grouped_commits_json,
                    narrative_release, narrative_standup, narrative_onboarding, narrative_portfolio,
                    extended_data_json, commit_count, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    demo["slug"],
                    demo["repo_url"],
                    demo["repo_name"],
                    demo["input_mode"],
                    demo["raw_commits_json"],
                    demo["grouped_commits_json"],
                    demo["narrative_release"],
                    demo["narrative_standup"],
                    demo["narrative_onboarding"],
                    demo["narrative_portfolio"],
                    demo["extended_data_json"],
                    demo["commit_count"],
                    demo["status"],
                ),
            )

def recover_stale_analyses(minutes=10):
    """
    Finds and updates analyses that are stuck in a non-terminal state
    (not 'done' or 'error') and are older than the specified minutes.
    Returns a list of recovered tasks.
    """
    with get_db() as conn:
        # SQLite CURRENT_TIMESTAMP is in UTC
        rows = conn.execute(
            """SELECT id, repo_name, created_at, status
               FROM analyses
               WHERE status NOT IN ('done', 'error')
                 AND created_at < datetime('now', ?)""",
            (f"-{minutes} minutes",)
        ).fetchall()

        recovered = [dict(r) for r in rows]

        if recovered:
            conn.execute(
                """UPDATE analyses
                   SET status = 'error',
                       error_message = 'Task interrupted or server restarted'
                   WHERE status NOT IN ('done', 'error')
                     AND created_at < datetime('now', ?)""",
                (f"-{minutes} minutes",)
            )

        return recovered
