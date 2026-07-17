"""
services/github_service.py
============================
GitHub API interactions and git log parsing.
Refactored from git_parser.py with metadata, file tree, and rate limit awareness.
"""
from __future__ import annotations
import json, logging, re, shutil, tempfile, base64, os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
import config

logger = logging.getLogger("repolens.github")
try:
    import git as gitpython
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False

@dataclass
class RepoMetadata:
    owner: str = ""
    name: str = ""
    full_name: str = ""
    description: str = ""
    default_branch: str = "main"
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    language: str = ""
    languages: dict[str, int] = field(default_factory=dict)
    topics: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    is_private: bool = False
    size_kb: int = 0
    license: str = ""

FULL_LOG_PATTERN = re.compile(r"^([a-f0-9]{7,40})\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|(.*)$")
ONELINE_PATTERN = re.compile(r"^([a-f0-9]{6,40})\s+(.+)$")
NOISY_MESSAGE_PATTERN = re.compile(
    r"^(fix|wip|asdf|merge|update|chore|cleanup|lint|refactor|minor|small|test|checkpoint|typo|formatting|initial|docs?|hotfix)\b.*",
    re.IGNORECASE,
)

def parse_from_url(url: str) -> list[dict]:
    parsed = _validate_url(url)
    host = parsed.netloc.lower().replace("www.", "")
    if host == "github.com":
        owner, repo = _extract_github_repo(parsed.path)
        try:
            return _fetch_github_commits(owner, repo)[:config.MAX_COMMITS_PER_ANALYSIS]
        except Exception as exc:
            logger.warning("GitHub commit API failed; trying git clone fallback: %s", exc)
            if config.ENABLE_GIT_CLONE_FALLBACK and GIT_AVAILABLE:
                return _parse_from_clone(url)
            raise
    if config.ENABLE_GIT_CLONE_FALLBACK and GIT_AVAILABLE:
        return _parse_from_clone(url)
    raise RuntimeError("Only GitHub URL analysis is available without Git clone support.")

def parse_from_file(file_content: str) -> list[dict]:
    return _parse_lines(file_content.strip().splitlines())[:config.MAX_COMMITS_PER_ANALYSIS]

def parse_from_text(raw_text: str) -> list[dict]:
    return _parse_lines(raw_text.strip().splitlines())[:config.MAX_COMMITS_PER_ANALYSIS]

def extract_repo_name(url_or_text: str) -> str:
    if not url_or_text: return "Unnamed Repository"
    try:
        parts = [p for p in urlparse(url_or_text).path.rstrip("/").split("/") if p]
        if len(parts) >= 2: return f"{parts[-2]}/{parts[-1].replace('.git','')}"
        if parts: return parts[-1].replace(".git", "")
    except Exception: pass
    return "Uploaded Repository"

def extract_owner_repo(url: str) -> tuple[str, str]:
    parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
    if len(parts) >= 2: return parts[0], parts[1].replace(".git", "")
    raise ValueError("URL must include owner and repository name")

def fetch_repo_metadata(owner: str, repo: str) -> RepoMetadata:
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}")
    except Exception as exc:
        logger.warning("Failed to fetch repo metadata: %s", exc)
        return RepoMetadata(owner=owner, name=repo, full_name=f"{owner}/{repo}")
    lic = data.get("license") or {}
    meta = RepoMetadata(
        owner=owner, name=repo, full_name=data.get("full_name", f"{owner}/{repo}"),
        description=data.get("description") or "", default_branch=data.get("default_branch", "main"),
        stars=data.get("stargazers_count", 0), forks=data.get("forks_count", 0),
        open_issues=data.get("open_issues_count", 0), language=data.get("language") or "",
        topics=data.get("topics", []), created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""), is_private=data.get("private", False),
        size_kb=data.get("size", 0), license=lic.get("spdx_id", ""),
    )
    try: meta.languages = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/languages")
    except Exception: pass
    return meta

def fetch_file_tree(owner: str, repo: str, branch: str = "main") -> list[dict]:
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/git/trees/{quote(branch)}?recursive=1")
        return [{"path": i["path"], "type": i["type"], "size": i.get("size", 0)}
                for i in data.get("tree", []) if not _should_skip_path(i["path"])][:config.MAX_REPO_FILES]
    except Exception as exc:
        logger.warning("Failed to fetch file tree: %s", exc)
        return []

def fetch_file_content(owner: str, repo: str, path: str, branch: str = "main") -> str | None:
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}?ref={quote(branch)}")
        if data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")[:config.MAX_FILE_SCAN_SIZE]
    except Exception: pass
    return None

def fetch_branches(owner: str, repo: str) -> list[str]:
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/branches?per_page=30")
        return [b["name"] for b in data if isinstance(b, dict)]
    except Exception: return []

def fetch_pull_requests(owner: str, repo: str, state: str = "all", limit: int = 20) -> list[dict]:
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/pulls?state={state}&per_page={limit}&sort=updated")
        return [{"number": pr.get("number"), "title": pr.get("title",""), "state": pr.get("state",""),
                 "user": (pr.get("user") or {}).get("login",""), "created_at": pr.get("created_at",""),
                 "merged_at": pr.get("merged_at")} for pr in data if isinstance(pr, dict)]
    except Exception: return []

def fetch_commit_files(owner: str, repo: str, sha: str) -> list[str]:
    """Fetch changed files for a specific commit (GitHub API)."""
    try:
        data = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(sha)}")
        return [f["filename"] for f in data.get("files", []) if "filename" in f]
    except Exception: return []

# ── GitHub API core ──
def _validate_url(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    if not parsed.scheme or not host: raise ValueError("Please enter a valid repository URL.")
    if host not in config.ALLOWED_REPO_HOSTS:
        raise ValueError(f"Host '{host}' is not allowed. Allowed: {', '.join(config.ALLOWED_REPO_HOSTS)}")
    return parsed

def _extract_github_repo(path: str) -> tuple[str, str]:
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2: raise ValueError("GitHub URLs must include owner and repo name.")
    return parts[0], parts[1].replace(".git", "")

def _fetch_github_commits(owner: str, repo: str) -> list[dict]:
    commits, tag_map = [], _fetch_github_tags(owner, repo)
    per_page = min(100, config.MAX_COMMITS_PER_ANALYSIS)
    max_pages = max(1, (config.MAX_COMMITS_PER_ANALYSIS + per_page - 1) // per_page)
    for page in range(1, max_pages + 1):
        payload = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/commits?per_page={per_page}&page={page}")
        if not isinstance(payload, list) or not payload: break
        for item in payload:
            sha = item.get("sha", "")
            ci = item.get("commit") or {}; ai = ci.get("author") or {}
            login = (item.get("author") or {}).get("login", "")
            msg = (ci.get("message") or "").strip().splitlines()[0]
            commits.append({"hash": sha[:8], "full_hash": sha, "message": msg or "No commit message",
                "author": ai.get("name") or login or "Unknown", "email": ai.get("email") or "",
                "date": _parse_date(ai.get("date","")), "date_raw": ai.get("date",""),
                "tags": tag_map.get(sha, []), "is_noisy": bool(NOISY_MESSAGE_PATTERN.match(msg or ""))})
            if len(commits) >= config.MAX_COMMITS_PER_ANALYSIS: return commits
        if len(payload) < per_page: break
    return commits

def _fetch_github_tags(owner: str, repo: str) -> dict[str, list[str]]:
    tag_map: dict[str, list[str]] = {}
    try: payload = _github_api_get(f"/repos/{quote(owner)}/{quote(repo)}/tags?per_page=100")
    except RuntimeError: return tag_map
    if not isinstance(payload, list): return tag_map
    for item in payload:
        sha = (item.get("commit") or {}).get("sha"); name = item.get("name")
        if sha and name: tag_map.setdefault(sha, []).append(name)
    return tag_map

def _github_api_get(api_path: str, use_token: bool = True):
    api_url = f"{config.GITHUB_API_BASE_URL.rstrip('/')}{api_path}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": config.GITHUB_API_USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
    token = config.GITHUB_API_TOKEN
    has_token = use_token and _has_configured_github_token(token)
    if has_token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(api_url, headers=headers)
    try:
        with urlopen(request, timeout=config.GITHUB_API_TIMEOUT_SECONDS) as response:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining and int(remaining) < 10:
                logger.warning("GitHub API rate limit low: %s remaining", remaining)
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = _read_http_error(exc)
        if exc.code == 401 and has_token:
            logger.warning("GitHub token was rejected; retrying public request without Authorization header")
            return _github_api_get(api_path, use_token=False)
        if exc.code == 403: raise RuntimeError("GitHub API rate limit reached. Add GITHUB_API_TOKEN to .env.") from exc
        if exc.code == 404: raise ValueError("Repository not found or not accessible.") from exc
        raise RuntimeError(f"GitHub API error ({exc.code}): {details}") from exc
    except URLError as exc: raise RuntimeError(f"Unable to reach GitHub API: {exc.reason}") from exc

def _has_configured_github_token(token: str) -> bool:
    if not token:
        return False
    token = token.strip()
    placeholder_values = {
        "",
        "YOUR_GITHUB_API_TOKEN_HERE",
        "your_github_token_here",
        "your-token-here",
        "github_token",
    }
    return token not in placeholder_values

def _read_http_error(exc: HTTPError) -> str:
    try: return json.loads(exc.read().decode("utf-8")).get("message", exc.reason or "Unknown")
    except Exception: return exc.reason or "Unknown error"

# ── Clone fallback ──
def _parse_from_clone(url: str) -> list[dict]:
    os.makedirs(config.TEMP_CLONE_DIR, exist_ok=True)
    clone_dir = tempfile.mkdtemp(dir=config.TEMP_CLONE_DIR)
    try:
        repo = gitpython.Repo.clone_from(url, clone_dir, depth=config.CLONE_DEPTH, no_single_branch=True)
        return _extract_from_repo(repo)[:config.MAX_COMMITS_PER_ANALYSIS]
    finally: shutil.rmtree(clone_dir, ignore_errors=True)

def _extract_from_repo(repo) -> list[dict]:
    fmt = "%H|%s|%an|%ae|%ad|%D"
    try: log_output = repo.git.log("--all", "--name-status", f"--pretty=format:COMMIT|{fmt}", "--date=iso")
    except Exception: log_output = repo.git.log("--name-status", f"--pretty=format:COMMIT|{fmt}", "--date=iso")
    
    commits = []
    current_commit = None
    for line in log_output.splitlines():
        line = line.strip()
        if not line: continue
        if line.startswith("COMMIT|"):
            if current_commit: commits.append(current_commit)
            current_commit = _parse_full_line(line[7:])
            if current_commit: current_commit["changed_files"] = []
        elif current_commit and "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 2:
                current_commit["changed_files"].append(parts[-1])
    if current_commit: commits.append(current_commit)
    return commits[:config.MAX_COMMITS_PER_ANALYSIS]

# ── Line parsing ──
def _parse_lines(lines: list[str]) -> list[dict]:
    content = "\n".join(lines)
    mc = _parse_multiline_log(content)
    if mc: return mc
    return [c for line in lines if line.strip() and (c := (_parse_full_line(line.strip()) or _parse_oneline(line.strip())))]

def _parse_multiline_log(content: str) -> list[dict]:
    pattern = re.compile(r'^commit\s+([a-f0-9]{7,40}).*?\nAuthor:\s*(.*?)\nDate:\s*(.*?)\n\n(.*?)(?=\ncommit\s+|$)', re.MULTILINE | re.DOTALL)
    commits = []
    for m in pattern.finditer(content):
        sha, author_line, date_raw, msg_block = m.groups()
        author, email = author_line.strip(), ""
        am = re.match(r"(.+?)\s*<(.+?)>", author)
        if am: author, email = am.groups()
        msg = " ".join(l.strip() for l in msg_block.splitlines() if l.strip())
        commits.append({"hash": sha[:8], "full_hash": sha, "message": msg or "No commit message",
            "author": author, "email": email, "date": _parse_date(date_raw), "date_raw": date_raw.strip(),
            "tags": [], "is_noisy": bool(NOISY_MESSAGE_PATTERN.match(msg or ""))})
    return commits

def _parse_full_line(line: str) -> dict | None:
    m = FULL_LOG_PATTERN.match(line)
    if not m: return None
    h, msg, auth, email, ds, refs = m.groups()
    return {"hash": h[:8], "full_hash": h, "message": msg.strip(), "author": auth.strip(),
        "email": email.strip(), "date": _parse_date(ds), "date_raw": ds.strip(),
        "tags": _extract_tags(refs), "is_noisy": bool(NOISY_MESSAGE_PATTERN.match(msg.strip()))}

def _parse_oneline(line: str) -> dict | None:
    m = ONELINE_PATTERN.match(line)
    if not m: return None
    h, msg = m.groups()
    return {"hash": h[:8], "full_hash": h, "message": msg.strip(), "author": "Unknown",
        "email": "", "date": None, "date_raw": "", "tags": [], "is_noisy": bool(NOISY_MESSAGE_PATTERN.match(msg.strip()))}

def _extract_tags(refs: str) -> list[str]:
    return re.findall(r"tag:\s*(v?[\d]+[\d.]+[\w.-]*)", refs, re.IGNORECASE) if refs else []

def _parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    if not date_str: return None
    try: return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError: pass
    for f in ["%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]:
        try: return datetime.strptime(date_str[:25], f)
        except ValueError: continue
    return None

def _should_skip_path(path: str) -> bool:
    for part in path.split("/"):
        if part in config.SKIP_DIRECTORIES: return True
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext in config.SKIP_EXTENSIONS
