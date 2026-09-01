"""
services/analysis_task.py
===========================
Background task for repository analysis.
Uses a bounded worker pool so the frontend can poll safely under load.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import config
import database
from services.architecture_analyzer import analyze_architecture
from services.cache_service import get_cached, set_cached
from services.commit_classifier import group_commits, serialize_groups_for_prompt
from services.gemini_client import gemini
from services.github_service import (
    extract_owner_repo,
    fetch_file_content,
    fetch_file_tree,
    fetch_repo_metadata,
    parse_from_file,
    parse_from_text,
    parse_from_url,
)
from services.repo_analyzer import analyze_repository
from services.repository_indexer import build_repository_intelligence

logger = logging.getLogger("repolens.task")
_ANALYSIS_POOL = ThreadPoolExecutor(
    max_workers=config.ANALYSIS_WORKERS, thread_name_prefix="analysis"
)
_ANALYSIS_SLOTS = threading.BoundedSemaphore(config.ANALYSIS_WORKERS + config.ANALYSIS_QUEUE_SIZE)


def _run_and_release(*args) -> None:
    try:
        _run_analysis(*args)
    finally:
        _ANALYSIS_SLOTS.release()


def start_background_analysis(analysis_id: int, input_mode: str, input_data: str, format_pref: str):
    """Submit analysis to a bounded worker pool.

    Bounded workers prevent a traffic spike from creating an unbounded number
    of threads and exhausting deployment memory.
    """
    if not _ANALYSIS_SLOTS.acquire(blocking=False):
        database.set_error(analysis_id, "Analysis queue is full. Please retry shortly.")
        return
    _ANALYSIS_POOL.submit(_run_and_release, analysis_id, input_mode, input_data, format_pref)


def _run_analysis(analysis_id: int, input_mode: str, input_data: str, format_pref: str):
    """The main background task logic."""
    try:
        # 1. Fetch Commits
        logger.info(f"Task {analysis_id}: [START] Beginning analysis pipeline.")
        database.update_progress(analysis_id, 5, "Reading commit history")
        logger.info(f"Task {analysis_id}: Fetching commits from {input_mode}...")

        if input_mode == "url":
            commits = parse_from_url(input_data)
        elif input_mode == "file":
            commits = parse_from_file(input_data)
        elif input_mode == "paste":
            commits = parse_from_text(input_data)
        else:
            raise ValueError(f"Unknown input mode: {input_mode}")

        if not commits:
            logger.error(f"Task {analysis_id}: No commits found.")
            database.set_error(analysis_id, "No commits found. Please check your input.")
            return

        logger.info(f"Task {analysis_id}: Fetched {len(commits)} commits.")

        # 2. Group Commits
        database.update_progress(analysis_id, 20, "Organizing commit history")
        logger.info(f"Task {analysis_id}: Grouping commits...")
        groups = group_commits(commits)
        commit_data_text = serialize_groups_for_prompt(groups)
        logger.info(f"Task {analysis_id}: Grouping complete.")

        logger.info(f"Task {analysis_id}: Saving commit data to database.")
        with database.get_db() as conn:
            conn.execute(
                "UPDATE analyses SET raw_commits_json=?, grouped_commits_json=?, commit_count=? WHERE id=?",
                (
                    json.dumps(commits, default=str),
                    json.dumps(groups, default=str),
                    len(commits),
                    analysis_id,
                ),
            )

        repo_metadata = {}
        tech_data = {}
        arch_data = {}

        if input_mode == "url" and "github.com" in input_data:
            try:
                logger.info(f"Task {analysis_id}: Starting enhanced GitHub analysis.")
                database.update_progress(analysis_id, 30, "Reading repository structure")
                owner, repo = extract_owner_repo(input_data)
                cache_key = f"{owner}/{repo}"

                # Metadata
                logger.info(f"Task {analysis_id}: Fetching repo metadata...")
                cached_meta = get_cached(cache_key, "_meta")
                if cached_meta:
                    repo_metadata = cached_meta
                    logger.info(f"Task {analysis_id}: Using cached metadata.")
                else:
                    meta = fetch_repo_metadata(owner, repo)
                    repo_metadata = {
                        "description": meta.description,
                        "stars": meta.stars,
                        "forks": meta.forks,
                        "language": meta.language,
                        "languages": meta.languages,
                        "topics": meta.topics,
                        "default_branch": meta.default_branch,
                        "license": meta.license,
                        "size_kb": meta.size_kb,
                        "open_issues": meta.open_issues,
                    }
                    set_cached(cache_key, repo_metadata, "_meta")
                    logger.info(f"Task {analysis_id}: Fetched and cached metadata.")

                # File Tree
                logger.info(f"Task {analysis_id}: Fetching file tree...")
                branch = (repo_metadata or {}).get("default_branch", "main")
                cached_tree = get_cached(cache_key, "_tree")
                if cached_tree:
                    file_tree = cached_tree
                    logger.info(f"Task {analysis_id}: Using cached file tree.")
                else:
                    file_tree = fetch_file_tree(owner, repo, branch)
                    if file_tree:
                        set_cached(cache_key, file_tree, "_tree")
                        logger.info(f"Task {analysis_id}: Fetched and cached file tree.")

                # Code Analysis
                if file_tree:
                    logger.info(f"Task {analysis_id}: Analyzing source code...")
                    cached_sources = get_cached(cache_key, "_sources")
                    database.update_progress(analysis_id, 45, "Indexing selected source files")

                    def fetch_source(path: str) -> str | None:
                        if isinstance(cached_sources, dict):
                            return cached_sources.get(path)
                        return fetch_file_content(owner, repo, path, branch)

                    file_contents, index_stats = build_repository_intelligence(
                        analysis_id, file_tree, fetch_source
                    )
                    if file_contents and not isinstance(cached_sources, dict):
                        set_cached(cache_key, file_contents, "_sources")
                    github_langs = (repo_metadata or {}).get("languages", {})
                    repo_analysis = analyze_repository(
                        file_tree, file_contents, commits, github_langs
                    )
                    logger.info(
                        "Task %s: indexed %s files",
                        analysis_id,
                        index_stats["index_coverage"]["indexed_files"],
                    )

                    tech_data = {
                        "technologies": [
                            {
                                "name": item.name,
                                "category": item.category,
                                "confidence": item.confidence,
                            }
                            for item in repo_analysis.technologies[:15]
                        ],
                        "dependencies": repo_analysis.dependencies,
                        "language_stats": repo_analysis.language_stats,
                        "todos": repo_analysis.todos[:20],
                        "hotspots": repo_analysis.hotspots[:10],
                        "commit_quality": repo_analysis.commit_quality,
                        "risk_items": repo_analysis.risk_items,
                        "directory_summary": repo_analysis.directory_summary,
                        **index_stats,
                    }

                    logger.info(f"Task {analysis_id}: Analyzing architecture...")
                    database.update_progress(analysis_id, 70, "Analyzing architecture and risks")
                    arch_report = analyze_architecture(file_tree, file_contents)
                    arch_data = {
                        "patterns": arch_report.patterns,
                        "modules": arch_report.modules,
                        "api_endpoints": arch_report.api_endpoints[:20],
                        "description": arch_report.description,
                        "insights": arch_report.insights,
                    }
                    logger.info(f"Task {analysis_id}: Enhanced analysis complete.")
            except Exception as exc:
                logger.warning(f"Task {analysis_id}: Enhanced analysis failed (non-fatal): {exc}")

        # Store extended data
        if repo_metadata or tech_data or arch_data:
            logger.info(f"Task {analysis_id}: Saving extended data to database.")
            database.save_extended_data(
                analysis_id,
                {
                    "metadata": repo_metadata or {},
                    "technologies": tech_data or {},
                    "architecture": arch_data or {},
                },
            )

        # 4. Generate AI Narratives
        database.update_progress(analysis_id, 82, "Generating repository summaries")
        logger.info(f"Task {analysis_id}: Generating AI narratives with Gemini...")
        # Get repo_name from DB
        analysis = database.get_analysis_by_id(analysis_id)
        repo_name = analysis.get("repo_name", "Repository") if analysis else "Repository"

        try:
            import time

            start_ai = time.time()
            narratives = gemini.generate_all(commit_data_text, repo_name)
            duration = time.time() - start_ai
            logger.info(
                f"Task {analysis_id}: AI narratives generated successfully in {duration:.2f} seconds."
            )

            logger.info(
                f"Task {analysis_id}: Updating database with narratives and marking status='done'."
            )
            database.update_progress(analysis_id, 96, "Saving analysis results")
            database.update_narratives(analysis_id, narratives)
            logger.info(f"Task {analysis_id}: [COMPLETE] Analysis successfully finished.")
        except Exception:
            logger.exception(
                "Task %s: narrative generation or result persistence failed",
                analysis_id,
            )
            database.set_error(
                analysis_id,
                "Repository summaries could not be completed. Please retry.",
            )

    except Exception:
        logger.exception("Task %s: analysis pipeline failed", analysis_id)
        try:
            database.set_error(
                analysis_id,
                "Repository analysis could not be completed. Please retry.",
            )
        except Exception:
            logger.exception("Task %s: failed to save error status", analysis_id)
