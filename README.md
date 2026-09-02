# RepoLens AI

RepoLens AI implements the project topic “AI Agent That Analyses GitHub Repositories.” It combines commit history, source retrieval, structural analysis, and an explainable bounded investigator.

The app accepts a GitHub repository URL, pasted git log, or uploaded log file, then produces repository summaries, release-style narratives, architecture insights, risk observations, and a source-grounded Q&A experience.

## Features

- **Repository ingestion**
  - Fetches commit history from GitHub URLs.
  - Supports pasted or uploaded git logs.
  - Stores analysis results in SQLite for history and sharing.

- **Repository Q&A**
  - Indexes selected source files for retrieval.
  - Retrieves relevant code chunks for a user question.
  - Uses the custom RepoLens Investigator to gather evidence, then optionally uses Gemini to phrase the answer.
  - Returns sources with file paths, line ranges, relevance scores, and fallback warnings.

- **RAG indexing**
  - Chunks Python files with AST function/class boundaries.
  - Preserves module-level imports and setup code.
  - Uses sliding-window chunking for other file types.
  - Uses identifier-aware lexical retrieval by default and supports optional FAISS and sentence-transformer search.
  - Keeps exact code-identifier evidence in both default and hybrid modes.

- **Repository analysis**
  - Detects technologies, dependencies, language distribution, and entry points.
  - Summarizes directory structure.
  - Identifies TODO/FIXME markers.
  - Scores commit quality.
  - Detects high-churn files and risk signals.

- **RepoLens Investigator (Phase II)**
  - Plans bounded repository investigations with a maximum of five read-only tool calls.
  - Searches code, finds symbols, and traces dependency evidence through a source graph.
  - Resolves structure from Python (AST) and JavaScript/TypeScript (deterministic reader); other languages are searchable as text but contribute no structural edges.
  - Supports lookup, architecture, request-flow, and change-impact questions.
  - Shows its tool trace, evidence sources, and an application-defined confidence score.
  - Reports the share of the repository actually indexed, and says when structural evidence is unavailable, instead of claiming complete understanding.

- **Generated views**
  - Release notes.
  - Onboarding guides.
  - Architecture report.
  - Risk report.
  - Shareable story pages and cards.

## How It Works

```text
Repository URL or git log
        |
        v
Commit parsing and grouping
        |
        v
GitHub metadata, file tree, and selected source fetch
        |
        v
Technology, architecture, risk, and commit analysis
        |
        v
Code-aware chunks and resolved Python graph
        |
        v
Bounded investigation, optional grounded generation, and citations
        |
        v
Flask pages for results, architecture, risk, history, share, and Q&A
```

## Project Structure

- `app.py` - Flask application factory, blueprint registration, startup setup.
- `database.py` - SQLite connection, migrations, CRUD helpers, stale task recovery.
- `pages/` - Flask route handlers for home, analysis, history, detail/share, architecture, risk, and Q&A.
- `components/` - Jinja templates and reusable UI fragments.
- `services/rag_service.py` - Code chunking, FAISS/keyword retrieval, RAG context generation.
- `services/gemini_client.py` - Gemini transport, retry/error/timeout handling.
- `services/repo_analyzer.py` - Technology detection, dependency parsing, risk signals, commit quality, hotspots.
- `services/architecture_analyzer.py` - Architecture pattern detection, module classification, API endpoint discovery.
- `services/github_service.py` - GitHub repository metadata, tree, and file-content access.
- `services/analysis_task.py` - End-to-end analysis pipeline.
- `services/repository_indexer.py` - Source selection, retrieval indexing, graph building, and coverage.
- `services/qa_service.py` - Agentic Q&A workflow, fallbacks, and grounding validation.
- `services/investigator.py` - Bounded repository investigation and auditable tool trace.
- `services/knowledge_graph.py` - AST-derived symbols, routes, imports, and calls.
- `services/task_recovery.py` - Marks stale interrupted analyses as failed on restart.
- `static/styles.css` - Global UI theme and component styles.

## Tech Stack

- **Backend:** Python, Flask
- **Database:** SQLite
- **LLM provider:** Google Gemini through `google-genai`
- **Retrieval:** identifier-aware lexical search with IDF weighting, Python AST and JavaScript/TypeScript source graph, optional FAISS and sentence-transformers
- **Frontend:** Jinja templates, HTML, CSS, JavaScript
- **Runtime:** Gunicorn-compatible Flask app

## Setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create local environment config:

```bash
cp .env.example .env
```

4. Add a Gemini API key:

```bash
GEMINI_API_KEY=your_api_key_here
```

5. Run the app:

```bash
flask --app app run --debug
```

## Live Demo

Hosted URL: https://repolens-ai-9fwr.onrender.com/

## CLI Usage

Run analysis from the terminal:

```bash
python cli.py .
```

Generate a compatible git log file:

```bash
git log --pretty=format:"%H|%s|%an|%ae|%ad|%D" --date=iso > my-log.txt
```

## Reliability Notes

- If Gemini is not configured, narrative features use local fallback behavior where available.
- Repository Q&A can fall back to retrieval-only responses when generation fails.
- API calls include timeout handling to avoid long hangs.
- Stale analyses are marked as failed after restart so the UI does not show permanent processing states.
- Local runtime files such as SQLite databases and WAL/SHM files are ignored by Git.

## Quality Evidence

Run the deterministic quality gates:

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m coverage run --source=app,config,database,pages,services,benchmarks -m pytest -q
python -m coverage report --fail-under=70
python -m benchmarks.runner --check
```

The recorded suite has 97 passing tests, 77% whole-project coverage, and 85% combined coverage for the five core agent modules. The controlled offline benchmark records 100% File Recall@5 versus 77.8% for a naive baseline on the questions used while tuning. On a held-out question set the naive baseline also reaches 100% Recall@5, so that advantage does not reproduce out of sample; the measured gain there is in ranking quality (MRR 0.80 versus 0.65). These figures are regression evidence for the included corpus, not universal accuracy.

## Technical Notes

See [EVALUATION.md](EVALUATION.md) for the benchmark method, held-out results, and scoring ablation.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the current code navigation map and dependency rules.

---

RepoLens AI is built for practical repository understanding: turning commit history, project structure, and source code into useful engineering views.
