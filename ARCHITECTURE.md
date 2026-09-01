# RepoLens AI Architecture

RepoLens uses a small layered Flask architecture. The layers are intentionally lightweight so the project remains easy to explain, test, and extend.

## Request flow

```text
Flask route
    |
Application service
    |
Domain analysis / investigator tools
    |
GitHub, Gemini, SQLite, and local index adapters
```

## Navigation map

### HTTP presentation — `pages/`

Flask blueprints validate HTTP-specific input, call an application service, and render Jinja templates or JSON. Business logic does not belong in this layer.

- `home.py` — ingestion form, task creation, and status polling.
- `qa.py` — thin adapter for repository questions.
- `analyze.py` — result display and narrative export.
- `architecture.py`, `risk.py`, `history.py`, `detail.py` — report views.

### Application workflows — `services/`

- `analysis_task.py` — background workflow coordinator.
- `repository_indexer.py` — source selection, fetching, RAG indexing, graph building, and coverage.
- `qa_service.py` — Q&A validation, investigation, Gemini generation, fallback, and grounding.
- `investigator.py` — bounded read-only agent plan and trace.
- `serialization.py` — shared safe persistence decoding.

### Deterministic analysis — `services/`

- `github_service.py` — GitHub API, clone fallback, and git-log parsing.
- `commit_classifier.py` — rule-based commit grouping and contribution signals.
- `repo_analyzer.py` — technologies, dependencies, hotspots, risks, and complexity.
- `architecture_analyzer.py` — explainable structural heuristics.
- `knowledge_graph.py` — AST-derived files, symbols, routes, imports, and calls.
- `rag_service.py` — AST-aware chunks and FAISS/keyword retrieval.

### AI integration

- `gemini_client.py` — Gemini transport, retries, and local narrative fallback.
- `ai_prompts.py` — provider-neutral narrative prompts.

The LLM is not responsible for repository ingestion or deterministic analysis. It receives evidence assembled by RepoLens services.

### Persistence and UI

- `database.py` — deliberately small SQLite data-access module.
- `components/` — Jinja templates and reusable fragments.
- `static/styles.css` — application styling.
- `tests/` — deterministic agent, graph, indexing, and Q&A service tests.

## Dependency rules

1. Pages may call services and persistence.
2. Application services may call deterministic analysis and external adapters.
3. Deterministic analysis must not depend on Flask.
4. Templates contain presentation behavior only.
5. Investigator tools are read-only and capped by `AGENT_MAX_STEPS`.
6. Generated databases, caches, and indexes are never committed.

## Extension points

- Add another model provider behind the existing client interface without changing routes.
- Add language parsers behind `KnowledgeGraph` without changing the investigator.
- Add retrieval strategies inside `RAGService` without changing Q&A response contracts.
