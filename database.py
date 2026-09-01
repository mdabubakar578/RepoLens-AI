"""
database.py — SQLite connection, table creation, and query helpers.
All pages and services use these helpers — no direct DB access elsewhere.
"""

import contextlib
import json
import os
import sqlite3

from config import DATABASE_PATH, DB_TIMEOUT_SECONDS, NARRATIVE_FORMATS


@contextlib.contextmanager
def get_db():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


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
                progress             INTEGER DEFAULT 0,
                stage                TEXT DEFAULT 'Queued',
                error_message        TEXT,
                created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_analyses_slug ON analyses(slug);
            CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
        """)
    _migrate_analysis_columns()
    seed_demo_analyses_if_empty()


def _migrate_analysis_columns():
    """Add columns introduced after the initial database schema."""
    additions = {
        "extended_data_json": "TEXT",
        "progress": "INTEGER DEFAULT 0",
        "stage": "TEXT DEFAULT 'Queued'",
    }
    with get_db() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(analyses)")}
        for name, definition in additions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE analyses ADD COLUMN {name} {definition}")


# ─── CRUD ─────────────────────────────────────────────────────────────────────


def save_analysis(
    slug, repo_url, repo_name, input_mode, raw_commits, grouped_commits, commit_count
):
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO analyses
               (slug, repo_url, repo_name, input_mode, raw_commits_json, grouped_commits_json, commit_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                slug,
                repo_url,
                repo_name,
                input_mode,
                json.dumps(raw_commits, default=str),
                json.dumps(grouped_commits, default=str),
                commit_count,
            ),
        )
        return cursor.lastrowid


def update_narratives(analysis_id, narratives: dict):
    """Persist the narratives for whichever formats are configured.

    Driven by NARRATIVE_FORMATS so adding or removing a format cannot leave
    this statement out of step with the rest of the application. Retired
    columns are left in place so existing rows keep their history.
    """
    keys = [key for key, _ in NARRATIVE_FORMATS if key.isidentifier()]
    assignments = ", ".join(f"narrative_{key}=?" for key in keys)
    values = [narratives.get(key, "") for key in keys]
    with get_db() as conn:
        conn.execute(
            f"UPDATE analyses SET {assignments}, status='done', progress=100, "
            "stage='Complete' WHERE id=?",
            (*values, analysis_id),
        )


def save_extended_data(analysis_id, data: dict):
    with get_db() as conn:
        conn.execute(
            "UPDATE analyses SET extended_data_json=? WHERE id=?",
            (json.dumps(data, default=str), analysis_id),
        )


def update_progress(analysis_id: int, progress: int, stage: str) -> None:
    """Persist user-visible analysis progress for polling clients."""
    bounded = max(0, min(int(progress), 100))
    with get_db() as conn:
        conn.execute(
            "UPDATE analyses SET progress=?, stage=? WHERE id=?",
            (bounded, stage[:120], analysis_id),
        )


def set_error(analysis_id, message):
    with get_db() as conn:
        conn.execute(
            "UPDATE analyses SET status='error', progress=100, stage='Failed', error_message=? WHERE id=?",
            (message, analysis_id),
        )


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
    order_clause = """
        ORDER BY
            CASE status
                WHEN 'done' THEN 0
                WHEN 'pending' THEN 1
                ELSE 2
            END,
            created_at DESC
    """
    with get_db() as conn:
        if search:
            pattern = f"%{search}%"
            rows = conn.execute(
                f"SELECT * FROM analyses WHERE repo_name LIKE ? OR repo_url LIKE ? {order_clause} LIMIT ? OFFSET ?",
                (pattern, pattern, per_page, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE repo_name LIKE ? OR repo_url LIKE ?",
                (pattern, pattern),
            ).fetchone()[0]
        else:
            rows = conn.execute(
                f"SELECT * FROM analyses {order_clause} LIMIT ? OFFSET ?",
                (per_page, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        return [dict(r) for r in rows], total


def get_extended_data(analysis_id) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT extended_data_json FROM analyses WHERE id=?", (analysis_id,)
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                pass
    return {}


def seed_demo_analyses_if_empty():
    """Insert demo analyses when they are missing so hosted demos are explorable."""
    if os.environ.get("ENABLE_DEMO_DATA", "false").lower() != "true":
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
            (f"-{minutes} minutes",),
        ).fetchall()

        recovered = [dict(r) for r in rows]

        if recovered:
            conn.execute(
                """UPDATE analyses
                   SET status = 'error',
                       progress = 100,
                       stage = 'Failed',
                       error_message = 'Analysis was interrupted or exceeded the processing window. Please retry.'
                   WHERE status NOT IN ('done', 'error')
                     AND created_at < datetime('now', ?)""",
                (f"-{minutes} minutes",),
            )

        return recovered
