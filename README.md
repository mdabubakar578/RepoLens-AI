# RepoLens AI

RepoLens AI is a Flask-based repository analysis app that helps developers understand a GitHub project from its commit history, file structure, source code, and generated documentation views.

The app accepts a GitHub repository URL, pasted git log, or uploaded log file, then produces repository summaries, release-style narratives, architecture insights, risk observations, and a source-grounded Q&A experience.

## Features

- **Repository ingestion**
  - Fetches commit history from GitHub URLs.
  - Supports pasted or uploaded git logs.
  - Stores analysis results in SQLite for history and sharing.

- **Repository Q&A**
  - Indexes selected source files for retrieval.
  - Retrieves relevant code chunks for a user question.
  - Uses Gemini to answer with repository context.
  - Returns sources with file paths, line ranges, relevance scores, and fallback warnings.

- **RAG indexing**
  - Chunks Python files with AST function/class boundaries.
  - Preserves module-level imports and setup code.
  - Uses sliding-window chunking for other file types.
  - Supports FAISS and sentence-transformer semantic search when available.
  - Falls back to keyword search when vector search is unavailable.

- **Repository analysis**
  - Detects technologies, dependencies, language distribution, and entry points.
  - Summarizes directory structure.
  - Identifies TODO/FIXME markers.
  - Scores commit quality.
  - Detects high-churn files and risk signals.

- **Generated views**
  - Release notes.
  - Standup summaries.
  - Onboarding guides.
  - Portfolio/project summaries.
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
RAG chunking and index persistence
        |
        v
Gemini narrative generation and repository Q&A
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
- `services/gemini_client.py` - Gemini client, prompt templates, retry/error/timeout handling.
- `services/repo_analyzer.py` - Technology detection, dependency parsing, risk signals, commit quality, hotspots.
- `services/architecture_analyzer.py` - Architecture pattern detection, module classification, API endpoint discovery.
- `services/github_service.py` - GitHub repository metadata, tree, and file-content access.
- `services/analysis_task.py` - End-to-end analysis pipeline.
- `services/task_recovery.py` - Marks stale interrupted analyses as failed on restart.
- `static/styles.css` - Global UI theme and component styles.

## Tech Stack

- **Backend:** Python, Flask
- **Database:** SQLite
- **LLM provider:** Google Gemini through `google-genai`
- **Retrieval:** FAISS, sentence-transformers, AST parsing, keyword fallback
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

A hosted demo link can be added here after deployment.

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

- If Gemini is not configured, narrative features use demo/fallback behavior where available.
- Repository Q&A can fall back to retrieval-only responses when generation fails.
- API calls include timeout handling to avoid long hangs.
- Stale analyses are marked as failed after restart so the UI does not show permanent processing states.
- Local runtime files such as SQLite databases and WAL/SHM files are ignored by Git.

## Technical Notes

For more implementation detail, see [docs/technical-overview.md](docs/technical-overview.md).

---

RepoLens AI is built for practical repository understanding: turning commit history, project structure, and source code into useful engineering views.
