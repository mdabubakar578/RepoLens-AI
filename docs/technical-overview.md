# RepoLens AI Technical Overview

This document describes the main implementation pieces in RepoLens AI.

## Analysis Pipeline

The analysis flow is coordinated in `services/analysis_task.py`.

1. Parse commits from a GitHub URL, uploaded file, or pasted log.
2. Group commits by time period and classify work types.
3. For GitHub repositories, fetch metadata, language data, file tree, and selected source files.
4. Run repository analysis for technologies, dependencies, risks, hotspots, TODO markers, and commit quality.
5. Build a RAG index over selected source files.
6. Generate narratives with Gemini or configured fallback behavior.
7. Store analysis records and extended metadata in SQLite.

## Retrieval and Q&A

Repository Q&A is implemented across `services/rag_service.py`, `services/gemini_client.py`, and `pages/qa.py`.

- `RAGService.index_repository()` receives fetched file contents and creates source chunks.
- Python files are parsed with `ast` so functions and classes become natural chunks.
- Other files use sliding-window chunking.
- Each chunk stores content, file path, line range, chunk type, and an importance score.
- If FAISS and sentence-transformers are available, chunks are embedded and searched semantically.
- If local vector search is unavailable, keyword search is used.
- The Q&A route loads the persisted index, retrieves relevant chunks, builds context, and sends it to Gemini.
- Responses include retrieved source metadata so users can inspect the evidence.

## Gemini Client

`services/gemini_client.py` centralizes model access.

It contains prompt templates for:

- Architecture summaries.
- Onboarding guides.
- Security review.
- Repository Q&A.
- Narrative formats such as release notes and standup reports.

The client also handles:

- Missing API key fallback.
- Retry attempts.
- Quota/rate-limit errors.
- Timeout handling.
- Empty or malformed model responses.

## Repository Analysis

`services/repo_analyzer.py` extracts non-LLM signals from repository data.

It detects:

- Frameworks and languages from file names, extensions, and content signatures.
- Dependencies from package files.
- Language distribution.
- Entry points.
- TODO/FIXME/HACK/XXX markers.
- Commit hotspots.
- Commit message quality.
- Risk indicators such as large files, documentation gaps, technical debt, and high-churn areas.

## Architecture Analysis

`services/architecture_analyzer.py` maps repository structure to architectural patterns.

It can identify:

- MVC-style structure.
- Layered architecture.
- Monolithic applications.
- Component-based frontends.
- Clean architecture signals.
- API endpoint declarations from Flask-style decorators.

## Persistence

`database.py` stores analyses in SQLite.

The main table stores:

- Repository metadata.
- Raw commits.
- Grouped commits.
- Generated narratives.
- Extended analysis JSON.
- Status and error state.
- Creation time.

On startup, stale analyses that were interrupted during processing are marked as failed so the UI does not stay stuck in a processing state.

## UI Pages

The Flask app exposes:

- `/` - input form and project entry point.
- `/history` - previous analyses.
- `/result/<id>` - generated narratives and analysis summary.
- `/architecture/<id>` - architecture and technology report.
- `/risk/<id>` - risk and process quality report.
- `/qa/<id>` - repository Q&A interface.
- `/share/<slug>` - read-only share view.
- `/card/<slug>` - shareable story card.

## Failure Handling

The app is designed to remain usable when optional AI or retrieval features are unavailable.

- Missing Gemini configuration uses fallback/demo behavior.
- Failed Gemini Q&A calls return retrieval-only evidence.
- Missing RAG indexes show warnings rather than crashing.
- FAISS failures fall back to keyword search.
- Stale tasks are recovered on restart.
- Runtime SQLite files are ignored by Git.
