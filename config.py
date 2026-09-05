"""
config.py — CENTRAL CONFIGURATION
===================================
All services and pages import from here.
Uses environment variables with sensible defaults.
"""

import logging
import os
import secrets

from dotenv import load_dotenv

load_dotenv()

# ─── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "data", "repolens.db")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
INDEX_CACHE_DIR = os.path.join(BASE_DIR, ".repolens", "cache")
TEMP_CLONE_DIR = os.path.join(BASE_DIR, "temp")
DB_TIMEOUT_SECONDS = int(os.environ.get("DB_TIMEOUT_SECONDS", "15"))

# ─── Google Gemini API ───────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AI_TIMEOUT_SECONDS = int(os.environ.get("AI_TIMEOUT_SECONDS", "30"))
AI_MAX_ATTEMPTS = int(os.environ.get("AI_MAX_ATTEMPTS", "2"))

# ─── GitHub API ──────────────────────────────────────────────────────────────
GITHUB_API_TOKEN = os.environ.get("GITHUB_API_TOKEN", "")
GITHUB_API_BASE_URL = os.environ.get("GITHUB_API_BASE_URL", "https://api.github.com")
GITHUB_API_TIMEOUT_SECONDS = int(os.environ.get("GITHUB_API_TIMEOUT_SECONDS", "20"))
GITHUB_API_USER_AGENT = os.environ.get("GITHUB_API_USER_AGENT", "RepoLensAI/2.0")
ALLOWED_REPO_HOSTS = ["github.com", "gitlab.com", "bitbucket.org"]

# ─── Branding ────────────────────────────────────────────────────────────────
APP_NAME = "RepoLens AI"
APP_TAGLINE = "AI-powered GitHub repository analysis and insights"
APP_VERSION = "2.0.0"

# ─── Analysis Limits ────────────────────────────────────────────────────────
MAX_COMMITS_PER_ANALYSIS = 500
MAX_PASTE_CHARS = 50_000
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_FILE_SCAN_SIZE = 100_000  # Max chars per file for analysis
MAX_REPO_FILES = 500  # Max files to scan in a repo
MAX_ARCHIVE_DOWNLOAD_BYTES = 25_000_000  # Bound public archive fallback memory
# One API request per commit, so churn enrichment is sampled, not exhaustive.
CHURN_COMMIT_SAMPLE = int(os.environ.get("CHURN_COMMIT_SAMPLE", "60"))
CLONE_DEPTH = 200
GIT_CLONE_TIMEOUT_SECONDS = int(os.environ.get("GIT_CLONE_TIMEOUT_SECONDS", "45"))
ENABLE_GIT_CLONE_FALLBACK = True

# ─── RAG Settings ────────────────────────────────────────────────────────────
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() == "true"
RAG_USE_EMBEDDINGS = os.environ.get("RAG_USE_EMBEDDINGS", "false").lower() == "true"
# Small enough to run on a modest host; 384 dimensions over a few thousand
# chunks is a millisecond-scale matrix multiply.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
RAG_CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "800"))
RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "100"))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
MAX_INDEX_FILES = int(os.environ.get("MAX_INDEX_FILES", "60"))
AGENT_MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "5"))
MAX_QUESTION_CHARS = int(os.environ.get("MAX_QUESTION_CHARS", "500"))

SKIP_DIRECTORIES = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "venv",
    ".venv",
    "__pycache__",
    ".next",
    ".nuxt",
    "vendor",
    "target",
    "coverage",
    ".tox",
    "eggs",
    ".eggs",
    "bower_components",
    ".cache",
    ".parcel-cache",
    "out",
    ".output",
}
INDEX_FETCH_WORKERS = int(os.environ.get("INDEX_FETCH_WORKERS", "8"))
ANALYSIS_WORKERS = int(os.environ.get("ANALYSIS_WORKERS", "4"))
ANALYSIS_QUEUE_SIZE = int(os.environ.get("ANALYSIS_QUEUE_SIZE", "12"))


SKIP_EXTENSIONS = {
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".sum",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".wav",
    ".sqlite",
    ".db",
    ".sqlite3",
}

# ─── Cache Settings ──────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", str(24 * 3600)))  # 24h

# ─── Narrative Formats ───────────────────────────────────────────────────────
# Two formats, not four. Measured on identical commit data, the removed
# "standup" and "portfolio" outputs overlapped release by 53 and 58 percent of
# content words and reused its sections verbatim, so they restated the same
# facts in a different voice. Each format also costs one model request per
# analysis, which matters on a quota-limited key.
NARRATIVE_FORMATS = [
    ("release", "Release Notes"),
    ("onboarding", "Onboarding Story"),
]
DEFAULT_NARRATIVE_FORMAT = "release"

# ─── Feature Flags ───────────────────────────────────────────────────────────
ENABLE_HISTORY = True
ENABLE_SHARE = True
ENABLE_ARCHITECTURE = True
ENABLE_QA = True
ENABLE_RISK = True

# ─── Flask ───────────────────────────────────────────────────────────────────
_CONFIGURED_SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
_SECRET_KEY_PLACEHOLDERS = {"", "YOUR_SECRET_KEY_HERE", "change-me"}
SECRET_KEY_IS_EPHEMERAL = _CONFIGURED_SECRET_KEY in _SECRET_KEY_PLACEHOLDERS
SECRET_KEY = (
    secrets.token_urlsafe(48) if SECRET_KEY_IS_EPHEMERAL else _CONFIGURED_SECRET_KEY
)
DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


def setup_logging() -> logging.Logger:
    """Configure structured logging for the application."""
    logger = logging.getLogger("repolens")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    return logger


logger = setup_logging()
