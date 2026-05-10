"""
config.py — CENTRAL CONFIGURATION
===================================
All services and pages import from here.
Uses environment variables with sensible defaults.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "data", "repolens.db")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
TEMP_CLONE_DIR = os.path.join(BASE_DIR, "temp")

# ─── xAI Grok API ───────────────────────────────────────────────────────────
XAI_API_KEY = os.environ.get("XAI_API_KEY", "YOUR_XAI_API_KEY_HERE")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-3-mini-fast")
GROK_BASE_URL = "https://api.x.ai/v1"
GROK_TIMEOUT_SECONDS = int(os.environ.get("GROK_TIMEOUT_SECONDS", "60"))
GROK_MAX_RETRIES = int(os.environ.get("GROK_MAX_RETRIES", "3"))
GROK_MAX_TOKENS = int(os.environ.get("GROK_MAX_TOKENS", "4096"))

# ─── Google Gemini API ───────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

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
MAX_FILE_SCAN_SIZE = 100_000           # Max chars per file for analysis
MAX_REPO_FILES = 500                   # Max files to scan in a repo
CLONE_DEPTH = 200
ENABLE_GIT_CLONE_FALLBACK = True

# ─── RAG Settings ────────────────────────────────────────────────────────────
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() == "true"
RAG_CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "800"))
RAG_CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "100"))
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "5"))

SKIP_DIRECTORIES = {
    "node_modules", ".git", "dist", "build", "venv", ".venv",
    "__pycache__", ".next", ".nuxt", "vendor", "target",
    "coverage", ".tox", "eggs", ".eggs", "bower_components",
    ".cache", ".parcel-cache", "out", ".output",
}

SKIP_EXTENSIONS = {
    ".min.js", ".min.css", ".map", ".lock", ".sum",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".sqlite", ".db", ".sqlite3",
}

# ─── Cache Settings ──────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", str(24 * 3600)))  # 24h

# ─── Narrative Formats ───────────────────────────────────────────────────────
NARRATIVE_FORMATS = [
    ("release", "Release Notes"),
    ("standup", "Standup Summary"),
    ("onboarding", "Onboarding Story"),
    ("portfolio", "Portfolio README"),
]
DEFAULT_NARRATIVE_FORMAT = "release"

# ─── Feature Flags ───────────────────────────────────────────────────────────
ENABLE_HISTORY = True
ENABLE_SHARE = True
ENABLE_ARCHITECTURE = True
ENABLE_QA = True
ENABLE_RISK = True

# ─── Flask ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "repolens-dev-secret-change-in-prod")
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
