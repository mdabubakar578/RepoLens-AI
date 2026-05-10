"""
services/repo_analyzer.py
===========================
Technology detection, dependency analysis, commit quality scoring,
hotspot detection, TODO/FIXME aggregation, and directory summarization.
"""
from __future__ import annotations
import logging, re, os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger("repolens.analyzer")

# ── Technology Detection Rules ─────────────────────────────────────────────────
TECH_SIGNATURES: dict[str, dict] = {
    # Frontend
    "React": {"files": ["package.json"], "content": ['"react"'], "extensions": [".jsx", ".tsx"]},
    "Next.js": {"files": ["next.config.js", "next.config.ts", "next.config.mjs"], "content": ['"next"']},
    "Vue.js": {"files": ["vue.config.js"], "content": ['"vue"'], "extensions": [".vue"]},
    "Angular": {"files": ["angular.json"], "content": ['"@angular/core"']},
    "Svelte": {"files": ["svelte.config.js"], "extensions": [".svelte"]},
    "TailwindCSS": {"files": ["tailwind.config.js", "tailwind.config.ts"], "content": ['"tailwindcss"']},
    # Backend
    "FastAPI": {"content": ['"fastapi"', "from fastapi", "import fastapi"]},
    "Django": {"files": ["manage.py"], "content": ['"django"', "from django", "DJANGO_SETTINGS"]},
    "Flask": {"content": ['"flask"', "from flask", "import flask"]},
    "Express": {"content": ['"express"', "require('express')", "require(\"express\")"]},
    "Spring Boot": {"files": ["pom.xml", "build.gradle"], "content": ["spring-boot"]},
    "Rails": {"files": ["Gemfile", "Rakefile"], "content": ['"rails"', "'rails'"]},
    "Node.js": {"files": ["package.json"]},
    # Database
    "PostgreSQL": {"content": ["postgresql", "psycopg", "pg_", "postgres"]},
    "MongoDB": {"content": ["mongodb", "mongoose", "pymongo"]},
    "MySQL": {"content": ["mysql", "mysqlclient"]},
    "Redis": {"content": ["redis", "ioredis"]},
    "SQLite": {"content": ["sqlite3", "sqlite"]},
    # DevOps
    "Docker": {"files": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]},
    "Kubernetes": {"files": ["k8s/", "kubernetes/"], "content": ["apiVersion:", "kind: Deployment"]},
    "GitHub Actions": {"files": [".github/workflows/"]},
    "Terraform": {"extensions": [".tf"]},
    # Languages (detected from extensions)
    "Python": {"extensions": [".py"]},
    "TypeScript": {"extensions": [".ts", ".tsx"]},
    "JavaScript": {"extensions": [".js", ".jsx"]},
    "Java": {"extensions": [".java"]},
    "Go": {"extensions": [".go"], "files": ["go.mod"]},
    "Rust": {"extensions": [".rs"], "files": ["Cargo.toml"]},
    "C#": {"extensions": [".cs"], "files": [".csproj"]},
    "Ruby": {"extensions": [".rb"], "files": ["Gemfile"]},
    "PHP": {"extensions": [".php"], "files": ["composer.json"]},
}

@dataclass
class TechDetection:
    name: str
    confidence: float = 0.0  # 0-1
    category: str = ""  # frontend, backend, database, devops, language
    evidence: list[str] = field(default_factory=list)

@dataclass
class RepoAnalysis:
    technologies: list[TechDetection] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    language_stats: dict[str, float] = field(default_factory=dict)
    file_count: int = 0
    directory_summary: str = ""
    todos: list[dict] = field(default_factory=list)
    hotspots: list[dict] = field(default_factory=list)
    commit_quality: dict = field(default_factory=dict)
    risk_items: list[dict] = field(default_factory=list)

def analyze_repository(file_tree: list[dict], file_contents: dict[str, str],
                       commits: list[dict] | None = None,
                       github_languages: dict[str, int] | None = None) -> RepoAnalysis:
    """Run full repository analysis from file tree and contents."""
    analysis = RepoAnalysis()
    analysis.file_count = len(file_tree)
    analysis.technologies = detect_technologies(file_tree, file_contents)
    analysis.dependencies = parse_dependencies(file_contents)
    analysis.language_stats = compute_language_stats(file_tree, github_languages)
    analysis.directory_summary = summarize_directory_structure(file_tree)
    analysis.todos = find_todos(file_contents)
    if commits:
        analysis.hotspots = detect_hotspots(commits)
        analysis.commit_quality = score_commit_quality(commits)
        analysis.risk_items = detect_risks(commits, file_tree, analysis.todos)
    return analysis

def detect_technologies(file_tree: list[dict], file_contents: dict[str, str]) -> list[TechDetection]:
    """Detect technologies from file names and contents."""
    file_paths = {item["path"] for item in file_tree}
    file_names = {os.path.basename(p) for p in file_paths}
    extensions = Counter(os.path.splitext(p)[1].lower() for p in file_paths if "." in p)
    all_content = "\n".join(file_contents.values()).lower()
    detected: list[TechDetection] = []
    categories = {
        "React": "frontend", "Next.js": "frontend", "Vue.js": "frontend", "Angular": "frontend",
        "Svelte": "frontend", "TailwindCSS": "frontend",
        "FastAPI": "backend", "Django": "backend", "Flask": "backend", "Express": "backend",
        "Spring Boot": "backend", "Rails": "backend", "Node.js": "runtime",
        "PostgreSQL": "database", "MongoDB": "database", "MySQL": "database",
        "Redis": "database", "SQLite": "database",
        "Docker": "devops", "Kubernetes": "devops", "GitHub Actions": "devops", "Terraform": "devops",
        "Python": "language", "TypeScript": "language", "JavaScript": "language",
        "Java": "language", "Go": "language", "Rust": "language", "C#": "language",
        "Ruby": "language", "PHP": "language",
    }
    for tech, sigs in TECH_SIGNATURES.items():
        evidence, score = [], 0.0
        sig_files = sigs.get("files", [])
        for sf in sig_files:
            if sf.endswith("/"):
                if any(p.startswith(sf) for p in file_paths):
                    evidence.append(f"Directory: {sf}"); score += 0.5
            elif sf in file_names:
                evidence.append(f"File: {sf}"); score += 0.4
        for pattern in sigs.get("content", []):
            if pattern.lower() in all_content:
                evidence.append(f"Content match: {pattern}"); score += 0.3
        for ext in sigs.get("extensions", []):
            if extensions.get(ext, 0) > 0:
                evidence.append(f"Extension: {ext} ({extensions[ext]} files)"); score += 0.2
        if evidence:
            detected.append(TechDetection(
                name=tech, confidence=min(score, 1.0),
                category=categories.get(tech, "other"), evidence=evidence
            ))
    detected.sort(key=lambda t: -t.confidence)
    return detected

def parse_dependencies(file_contents: dict[str, str]) -> dict[str, list[str]]:
    """Extract dependencies from package files."""
    deps: dict[str, list[str]] = {}
    for path, content in file_contents.items():
        basename = os.path.basename(path)
        if basename == "requirements.txt":
            deps["python"] = [l.split("==")[0].split(">=")[0].split("<=")[0].strip()
                              for l in content.splitlines() if l.strip() and not l.startswith("#")]
        elif basename == "package.json":
            import json
            try:
                pkg = json.loads(content)
                npm_deps = list((pkg.get("dependencies") or {}).keys())
                npm_deps += list((pkg.get("devDependencies") or {}).keys())
                deps["npm"] = npm_deps
            except Exception: pass
        elif basename in ("Cargo.toml",):
            deps["cargo"] = re.findall(r'^(\w[\w-]*)\s*=', content, re.MULTILINE)
        elif basename in ("go.mod",):
            deps["go"] = re.findall(r'^\s+([\w./\-]+)\s', content, re.MULTILINE)
    return deps

def compute_language_stats(file_tree: list[dict], github_languages: dict[str, int] | None = None) -> dict[str, float]:
    """Compute language distribution by file count or GitHub bytes."""
    if github_languages:
        total = sum(github_languages.values()) or 1
        return {lang: round(bytes_count / total * 100, 1) for lang, bytes_count in github_languages.items()}
    ext_map = {"py": "Python", "js": "JavaScript", "ts": "TypeScript", "jsx": "React JSX",
               "tsx": "React TSX", "java": "Java", "go": "Go", "rs": "Rust", "rb": "Ruby",
               "php": "PHP", "cs": "C#", "html": "HTML", "css": "CSS", "vue": "Vue",
               "svelte": "Svelte", "swift": "Swift", "kt": "Kotlin", "dart": "Dart"}
    counter = Counter()
    for item in file_tree:
        if item["type"] == "blob":
            ext = item["path"].rsplit(".", 1)[-1].lower() if "." in item["path"] else ""
            if ext in ext_map: counter[ext_map[ext]] += 1
    total = sum(counter.values()) or 1
    return {lang: round(count / total * 100, 1) for lang, count in counter.most_common(10)}

def summarize_directory_structure(file_tree: list[dict]) -> str:
    """Generate a human-readable directory summary."""
    dirs = set()
    for item in file_tree:
        parts = item["path"].split("/")
        if len(parts) > 1: dirs.add(parts[0])
    top_dirs = sorted(dirs)[:20]
    lines = [f"📁 {d}/" for d in top_dirs]
    root_files = [item["path"] for item in file_tree if "/" not in item["path"] and item["type"] == "blob"][:10]
    for f in sorted(root_files): lines.append(f"📄 {f}")
    return "\n".join(lines) if lines else "No directory structure available"

def find_todos(file_contents: dict[str, str]) -> list[dict]:
    """Find TODO, FIXME, HACK, XXX comments across files."""
    pattern = re.compile(r'(?:#|//|/\*|\*)\s*(TODO|FIXME|HACK|XXX)\s*[:\-]?\s*(.*)', re.IGNORECASE)
    todos = []
    for path, content in file_contents.items():
        for i, line in enumerate(content.splitlines()[:500], 1):
            m = pattern.search(line)
            if m:
                todos.append({"file": path, "line": i, "type": m.group(1).upper(),
                    "text": m.group(2).strip()[:200], "priority": "high" if m.group(1).upper() in ("FIXME","HACK") else "normal"})
    return todos[:100]

def detect_hotspots(commits: list[dict]) -> list[dict]:
    """Find files with the most commit churn from commit messages."""
    file_mentions = Counter()
    for c in commits:
        msg = c.get("message", "")
        files = re.findall(r'[\w/]+\.[\w]+', msg)
        for f in files: file_mentions[f] += 1
    return [{"file": f, "mentions": count, "risk": "high" if count > 10 else "medium" if count > 5 else "low"}
            for f, count in file_mentions.most_common(15) if count >= 2]

def score_commit_quality(commits: list[dict]) -> dict:
    """Score overall commit quality based on message patterns."""
    total = len(commits) or 1
    noisy = sum(1 for c in commits if c.get("is_noisy"))
    short_msg = sum(1 for c in commits if len(c.get("message", "")) < 10)
    has_type = sum(1 for c in commits if re.match(r'^(feat|fix|docs|style|refactor|test|chore|ci|perf)\b', c.get("message",""), re.IGNORECASE))
    quality_score = max(0, min(100, 100 - (noisy / total * 30) - (short_msg / total * 30) + (has_type / total * 20)))
    return {"score": round(quality_score), "total": total, "noisy": noisy,
            "short_messages": short_msg, "conventional_commits": has_type,
            "grade": "A" if quality_score >= 80 else "B" if quality_score >= 60 else "C" if quality_score >= 40 else "D"}

def detect_risks(commits: list[dict], file_tree: list[dict], todos: list[dict]) -> list[dict]:
    """Identify potential risk areas in the repository."""
    risks = []
    if len(todos) > 20:
        risks.append({"type": "tech_debt", "severity": "medium",
            "title": f"{len(todos)} TODO/FIXME items found", "description": "High number of unresolved tasks may indicate tech debt"})
    large_files = [f for f in file_tree if f.get("size", 0) > 50000 and f["type"] == "blob"]
    if large_files:
        risks.append({"type": "code_smell", "severity": "low",
            "title": f"{len(large_files)} large files detected", "description": "Files over 50KB may need refactoring"})
    if commits:
        quality = score_commit_quality(commits)
        if quality["score"] < 40:
            risks.append({"type": "process", "severity": "medium",
                "title": f"Low commit quality score ({quality['score']}/100)",
                "description": f"{quality['noisy']} noisy commits, {quality['short_messages']} short messages"})
    return risks
