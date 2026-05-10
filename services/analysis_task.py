"""
services/analysis_task.py
===========================
Background task for repository analysis.
Runs in a separate thread so the frontend can poll for progress.
"""
import threading, json, logging, os
import database, config
from services.github_service import (
    parse_from_url, parse_from_file, parse_from_text,
    extract_owner_repo, fetch_repo_metadata, fetch_file_tree, fetch_file_content
)
from services.commit_classifier import group_commits, serialize_groups_for_prompt
from services.repo_analyzer import analyze_repository
from services.architecture_analyzer import analyze_architecture
from services.grok_client import grok
from services.cache_service import get_cached, set_cached

logger = logging.getLogger("repolens.task")

def start_background_analysis(analysis_id: int, input_mode: str, input_data: str, format_pref: str):
    """Spawns a background thread to run the analysis pipeline."""
    thread = threading.Thread(
        target=_run_analysis,
        args=(analysis_id, input_mode, input_data, format_pref)
    )
    thread.daemon = True
    thread.start()

def _run_analysis(analysis_id: int, input_mode: str, input_data: str, format_pref: str):
    """The main background task logic."""
    try:
        # 1. Fetch Commits
        logger.info(f"Task {analysis_id}: Fetching commits...")
        if input_mode == "url":
            commits = parse_from_url(input_data)
        elif input_mode == "file":
            commits = parse_from_file(input_data)
        elif input_mode == "paste":
            commits = parse_from_text(input_data)
        else:
            raise ValueError(f"Unknown input mode: {input_mode}")

        if not commits:
            database.set_error(analysis_id, "No commits found. Please check your input.")
            return

        # 2. Group Commits
        logger.info(f"Task {analysis_id}: Grouping {len(commits)} commits...")
        groups = group_commits(commits)
        commit_data_text = serialize_groups_for_prompt(groups)
        
        if input_mode == "url" and "github.com" in input_data:
            try:
                owner, repo = extract_owner_repo(input_data)
                cache_key = f"{owner}/{repo}"

                # Update DB with commits (so the UI has them even if AI fails)
                with database.get_db() as conn:
                    conn.execute(
                        "UPDATE analyses SET raw_commits_json=?, grouped_commits_json=?, commit_count=? WHERE id=?",
                        (json.dumps(commits, default=str), json.dumps(groups, default=str), len(commits), analysis_id)
                    )

                # Metadata
                cached_meta = get_cached(cache_key, "_meta")
                if cached_meta:
                    repo_metadata = cached_meta
                else:
                    meta = fetch_repo_metadata(owner, repo)
                    repo_metadata = {
                        "description": meta.description, "stars": meta.stars,
                        "forks": meta.forks, "language": meta.language,
                        "languages": meta.languages, "topics": meta.topics,
                        "default_branch": meta.default_branch, "license": meta.license,
                        "size_kb": meta.size_kb, "open_issues": meta.open_issues,
                    }
                    set_cached(cache_key, repo_metadata, "_meta")

                # File Tree
                branch = (repo_metadata or {}).get("default_branch", "main")
                cached_tree = get_cached(cache_key, "_tree")
                if cached_tree:
                    file_tree = cached_tree
                else:
                    file_tree = fetch_file_tree(owner, repo, branch)
                    if file_tree: set_cached(cache_key, file_tree, "_tree")

                # Code Analysis
                if file_tree:
                    key_file_paths = _select_key_files(file_tree)
                    file_contents = {}
                    for fp in key_file_paths[:15]:
                        content = fetch_file_content(owner, repo, fp, branch)
                        if content: file_contents[fp] = content

                    github_langs = (repo_metadata or {}).get("languages", {})
                    repo_analysis = analyze_repository(file_tree, file_contents, commits, github_langs)
                    tech_data = {
                        "technologies": [{"name": t.name, "category": t.category, "confidence": t.confidence} for t in repo_analysis.technologies[:15]],
                        "dependencies": repo_analysis.dependencies,
                        "language_stats": repo_analysis.language_stats,
                        "todos": repo_analysis.todos[:20],
                        "hotspots": repo_analysis.hotspots[:10],
                        "commit_quality": repo_analysis.commit_quality,
                        "risk_items": repo_analysis.risk_items,
                        "directory_summary": repo_analysis.directory_summary,
                    }

                    arch_report = analyze_architecture(file_tree, file_contents)
                    arch_data = {
                        "patterns": arch_report.patterns,
                        "modules": arch_report.modules,
                        "api_endpoints": arch_report.api_endpoints[:20],
                        "description": arch_report.description,
                        "insights": arch_report.insights,
                    }
            except Exception as exc:
                logger.warning(f"Task {analysis_id}: Enhanced analysis failed (non-fatal): {exc}")

        # Store extended data
        if repo_metadata or tech_data or arch_data:
            database.save_extended_data(analysis_id, {
                "metadata": repo_metadata or {},
                "technologies": tech_data,
                "architecture": arch_data,
            })

        # 4. Generate AI Narratives
        logger.info(f"Task {analysis_id}: Generating AI narratives...")
        # Get repo_name from DB
        analysis = database.get_analysis_by_id(analysis_id)
        repo_name = analysis.get("repo_name", "Repository") if analysis else "Repository"
        
        narratives = grok.generate_all(commit_data_text, repo_name)
        database.update_narratives(analysis_id, narratives)
        logger.info(f"Task {analysis_id}: Complete.")

    except Exception as e:
        logger.error(f"Task {analysis_id}: Failed with error: {e}")
        database.set_error(analysis_id, str(e))

def _select_key_files(file_tree: list[dict]) -> list[str]:
    priority_names = {
        "readme.md", "package.json", "requirements.txt", "cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "dockerfile", "docker-compose.yml",
        "app.py", "main.py", "index.js", "index.ts", "server.js", "server.ts",
        "manage.py", ".env.example", "setup.py", "pyproject.toml",
    }
    selected = []
    for item in file_tree:
        if item["type"] == "blob" and os.path.basename(item["path"]).lower() in priority_names:
            selected.append(item["path"])
    for item in file_tree:
        if item["type"] == "blob" and item["path"] not in selected:
            basename = os.path.basename(item["path"]).lower()
            if any(kw in basename for kw in ["config", "route", "model", "schema", "auth"]):
                selected.append(item["path"])
        if len(selected) >= 20: break
    return selected
