"""Repository source selection and intelligence-index construction."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import RAGService

SOURCE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".ex",
    ".exs",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
}
PRIORITY_FILENAMES = {
    ".env.example",
    "app.py",
    "build.gradle",
    "cargo.toml",
    "docker-compose.yml",
    "dockerfile",
    "go.mod",
    "index.js",
    "index.ts",
    "main.py",
    "manage.py",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "readme.md",
    "requirements.txt",
    "server.js",
    "server.ts",
    "setup.py",
}
ARCHITECTURAL_KEYWORDS = {
    "auth",
    "config",
    "controller",
    "model",
    "route",
    "schema",
    "service",
}
CODE_EXTENSIONS = SOURCE_EXTENSIONS.difference({".json", ".md", ".yaml", ".yml"})
LOW_VALUE_PATH_PARTS = (
    ".changeset/",
    "/coverage/",
    "/fixtures/",
    "/snapshots/",
    "changelog",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)
TEST_PATH_PARTS = ("/test/", "/tests/", "_test.", ".test.", ".spec.")


def select_index_files(file_tree: list[dict], limit: int | None = None) -> list[str]:
    """Select the highest-value source files using explainable priority rules.

    Args:
        file_tree: GitHub tree entries containing path and type.
        limit: Maximum selected files; defaults to the configured index limit.

    Returns:
        Deterministically ranked, duplicate-free source paths.
    """
    maximum = limit or config.MAX_INDEX_FILES
    candidates: dict[str, int] = {}
    for item in file_tree:
        if item.get("type") != "blob":
            continue
        path = item["path"]
        lower_path = path.lower()
        basename = os.path.basename(lower_path)
        extension = os.path.splitext(lower_path)[1]
        normalized_path = f"/{lower_path}"

        if extension not in SOURCE_EXTENSIONS and basename not in PRIORITY_FILENAMES:
            continue
        if any(part in normalized_path for part in LOW_VALUE_PATH_PARTS):
            continue

        score = 0
        if basename in PRIORITY_FILENAMES:
            score += 100
        if extension in CODE_EXTENSIONS:
            score += 50
        if any(keyword in basename for keyword in ARCHITECTURAL_KEYWORDS):
            score += 35
        if "/src/" in normalized_path or "/app/" in normalized_path:
            score += 20
        if normalized_path.count("/") == 1:
            score += 15
        if any(part in normalized_path for part in TEST_PATH_PARTS):
            score -= 25
        candidates[path] = score

    ranked = sorted(candidates, key=lambda path: (-candidates[path], path.lower()))
    return ranked[:maximum]


def build_repository_intelligence(
    analysis_id: int,
    file_tree: list[dict],
    fetch_content: Callable[[str], str | None],
) -> tuple[dict[str, str], dict]:
    """Fetch selected sources and persist retrieval and graph indexes.

    Args:
        analysis_id: Analysis identifier used for cache filenames.
        file_tree: Eligible repository tree entries.
        fetch_content: Callback returning text for one repository path.

    Returns:
        Fetched file contents and serializable coverage statistics.
    """
    selected_paths = select_index_files(file_tree)
    file_contents: dict[str, str] = {}
    workers = min(config.INDEX_FETCH_WORKERS, len(selected_paths))
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="source-fetch") as pool:
            futures = {pool.submit(fetch_content, path): path for path in selected_paths}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    content = future.result()
                except Exception:
                    content = None
                if content:
                    file_contents[path] = content

    rag = RAGService()
    chunk_count = rag.index_repository(str(analysis_id), file_contents)
    graph = KnowledgeGraph()
    graph_stats = graph.build(file_contents)
    graph.save(str(analysis_id))

    indexed_count = len(file_contents)
    eligible_count = len(selected_paths)
    repository_count = len(file_tree)
    coverage = {
        "repository_files": repository_count,
        "eligible_files": eligible_count,
        "indexed_files": indexed_count,
        "chunks": chunk_count,
        # Share of the repository that is actually searchable. Selection is
        # capped by MAX_INDEX_FILES, so this is usually well below 100 percent
        # and must not be confused with the fetch success rate below.
        "coverage_percent": (
            round(indexed_count / repository_count * 100, 1) if repository_count else 0
        ),
        # Share of the files we chose that were successfully retrieved.
        "fetch_success_percent": (
            round(indexed_count / eligible_count * 100, 1) if eligible_count else 0
        ),
        "selection_cap": config.MAX_INDEX_FILES,
        "selection_capped": eligible_count >= config.MAX_INDEX_FILES,
    }
    return file_contents, {"index_coverage": coverage, "knowledge_graph": graph_stats}
