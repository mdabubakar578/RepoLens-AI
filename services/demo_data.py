"""Demo analysis records used to make fresh deployments explorable."""

from __future__ import annotations

import json


def _commit(hash_, message, author, date, commit_type, noisy=False, tags=None):
    return {
        "hash": hash_,
        "message": message,
        "author": author,
        "email": "demo@example.com",
        "date": date,
        "tags": tags or [],
        "commit_type": commit_type,
        "is_noisy": noisy,
    }


REPOLENS_COMMITS = [
    _commit("a18c92f", "Implement repository Q&A with retrieved source evidence", "RepoLens Team", "2026-07-15T10:15:00", "feature"),
    _commit("b21d44a", "Add AST-based Python chunking for RAG context", "RepoLens Team", "2026-07-15T13:20:00", "feature"),
    _commit("c76b910", "Add Gemini timeout handling and retrieval fallback", "RepoLens Team", "2026-07-16T09:05:00", "bugfix"),
    _commit("d42f884", "Detect repository technologies and architecture patterns", "RepoLens Team", "2026-07-16T15:30:00", "feature"),
    _commit("e82a191", "Improve history cards and Q&A evidence drawer", "RepoLens Team", "2026-07-17T11:10:00", "refactor"),
    _commit("f31c728", "Prepare production WSGI entrypoint", "RepoLens Team", "2026-07-17T16:00:00", "devops", tags=["v2.0.0"]),
]

TASKFLOW_COMMITS = [
    _commit("9ac1021", "Create Flask task API and SQLite schema", "Demo Maintainer", "2026-06-25T09:00:00", "feature"),
    _commit("b884fc0", "Add dashboard filters for task status and owner", "Demo Maintainer", "2026-06-26T14:25:00", "feature"),
    _commit("d6f41ab", "Fix overdue task date parsing", "Demo Maintainer", "2026-06-27T10:40:00", "bugfix"),
    _commit("e1029aa", "Add onboarding notes for local development", "Demo Maintainer", "2026-06-28T12:10:00", "docs"),
    _commit("f09bc77", "Refactor service layer around task repository helpers", "Demo Maintainer", "2026-07-01T17:15:00", "refactor"),
]


REPOLENS_GROUPS = [
    {
        "week_key": "2026-W29",
        "label": "Week of Jul 13, 2026",
        "commits": REPOLENS_COMMITS,
        "commit_count": len(REPOLENS_COMMITS),
        "type_counts": {"feature": 3, "bugfix": 1, "refactor": 1, "devops": 1},
        "milestones": [{"tag": "v2.0.0", "commit": "f31c728", "message": "Prepare production WSGI entrypoint"}],
        "is_milestone_week": True,
        "date_from": "Jul 15",
        "date_to": "Jul 17, 2026",
    }
]

TASKFLOW_GROUPS = [
    {
        "week_key": "2026-W27",
        "label": "Week of Jun 29, 2026",
        "commits": TASKFLOW_COMMITS[-1:],
        "commit_count": 1,
        "type_counts": {"refactor": 1},
        "milestones": [],
        "is_milestone_week": False,
        "date_from": "Jul 01",
        "date_to": "Jul 01, 2026",
    },
    {
        "week_key": "2026-W26",
        "label": "Week of Jun 22, 2026",
        "commits": TASKFLOW_COMMITS[:-1],
        "commit_count": 4,
        "type_counts": {"feature": 2, "bugfix": 1, "docs": 1},
        "milestones": [],
        "is_milestone_week": False,
        "date_from": "Jun 25",
        "date_to": "Jun 28, 2026",
    },
]


DEMO_ANALYSES = [
    {
        "slug": "demo-repolens-ai",
        "repo_url": "https://github.com/mdabubakar578/RepoLens-AI",
        "repo_name": "RepoLens AI Demo Analysis",
        "input_mode": "url",
        "raw_commits_json": json.dumps(REPOLENS_COMMITS),
        "grouped_commits_json": json.dumps(REPOLENS_GROUPS),
        "commit_count": len(REPOLENS_COMMITS),
        "status": "done",
        "narrative_release": """# Release Notes

## Week of Jul 13, 2026

- **[Feature]** Added repository Q&A backed by retrieved source evidence.
- **[Feature]** Added AST-aware Python chunking to improve retrieval context.
- **[Feature]** Added technology and architecture detection for repository summaries.
- **[Bug Fix]** Added timeout and fallback behavior around Gemini responses.
- **[Refactor]** Improved history cards and Q&A evidence display.
- **[DevOps]** Prepared a production WSGI entrypoint.

### Summary

This demo shows how RepoLens AI turns a repository's implementation activity into readable engineering output.""",
        "narrative_standup": """# Standup Summary

The project focused on repository intelligence and Q&A. The team added source-grounded answers, AST-based chunking, architecture detection, and safer model fallback behavior. The UI was also refined so analysis history and Q&A evidence are easier to scan.""",
        "narrative_onboarding": """# Project History & Onboarding Guide

RepoLens AI is organized as a Flask application with page routes, reusable Jinja components, and service modules for repository analysis. New developers should start with `app.py`, then read `services/analysis_task.py`, `services/rag_service.py`, and `services/gemini_client.py` to understand the analysis pipeline.""",
        "narrative_portfolio": """# RepoLens AI

RepoLens AI analyzes repository history and source structure to generate release notes, onboarding summaries, architecture insights, risk observations, and repository Q&A. The project combines Flask, SQLite, Gemini, retrieval, and source-code analysis into one developer-facing workflow.""",
        "extended_data_json": json.dumps({
            "metadata": {"stars": 0, "forks": 0, "default_branch": "main", "description": "Repository analysis platform"},
            "technologies": {
                "technologies": [
                    {"name": "Python", "category": "language", "confidence": 1.0},
                    {"name": "Flask", "category": "backend", "confidence": 0.9},
                    {"name": "SQLite", "category": "database", "confidence": 0.8},
                    {"name": "Gemini", "category": "ai", "confidence": 0.8},
                ],
                "dependencies": {"python": ["flask", "google-genai", "gunicorn", "python-dotenv"]},
                "language_stats": {"Python": 58.0, "HTML": 28.0, "CSS": 14.0},
                "todos": [],
                "hotspots": [
                    {"file": "services/rag_service.py", "mentions": 6, "authors": 1, "risk": "MEDIUM", "confidence": 0.72},
                    {"file": "pages/qa.py", "mentions": 4, "authors": 1, "risk": "LOW", "confidence": 0.65},
                ],
                "commit_quality": {"score": 86, "total": 6, "noisy": 0, "short_messages": 0, "conventional_commits": 4, "grade": "A"},
                "risk_items": [
                    {"type": "runtime", "severity": "LOW", "confidence": 0.74, "title": "Hosted demo uses free-tier resources", "description": "Long-running repository analysis may be slower on a free web service."}
                ],
                "complexity_metrics": {
                    "complexity_score": 38,
                    "complexity_label": "Moderate",
                    "file_count": 42,
                    "max_directory_depth": 3,
                    "breakdown": {"volume": 12, "dependencies": 8, "churn_hotspots": 10, "tech_debt": 8},
                },
                "entry_points": ["app.py", "wsgi.py"],
                "directory_summary": "components/\npages/\nservices/\nstatic/\napp.py\nwsgi.py",
            },
            "architecture": {
                "patterns": [
                    {"name": "Layered", "confidence": 0.86, "description": "Routes, services, templates, and persistence are separated by responsibility.", "evidence": ["pages/", "services/", "components/"]},
                    {"name": "Monolith", "confidence": 0.72, "description": "A single Flask application coordinates the UI and analysis pipeline.", "evidence": ["app.py", "wsgi.py"]},
                ],
                "modules": [
                    {"name": "API Layer", "directories": ["pages"], "file_count": 7},
                    {"name": "Business Logic", "directories": ["services"], "file_count": 10},
                    {"name": "Templates", "directories": ["components"], "file_count": 12},
                ],
                "api_endpoints": ["GET /history", "GET /result/<analysis_id>", "GET /qa/<analysis_id>", "POST /qa/<analysis_id>/ask"],
                "description": "RepoLens AI uses a layered Flask architecture with route handlers, service modules, templates, and SQLite persistence.",
                "insights": ["Repository analysis is separated into service modules.", "Q&A uses retrieval before generation.", "Result pages reuse stored analysis metadata."],
            },
        }),
    },
    {
        "slug": "demo-taskflow-api",
        "repo_url": "https://github.com/example/taskflow-api",
        "repo_name": "TaskFlow API Demo Analysis",
        "input_mode": "url",
        "raw_commits_json": json.dumps(TASKFLOW_COMMITS),
        "grouped_commits_json": json.dumps(TASKFLOW_GROUPS),
        "commit_count": len(TASKFLOW_COMMITS),
        "status": "done",
        "narrative_release": """# Release Notes

## Week of Jun 29, 2026

- **[Refactor]** Reorganized task service code around repository helpers.

## Week of Jun 22, 2026

- **[Feature]** Created the Flask task API and SQLite schema.
- **[Feature]** Added dashboard filters for task status and owner.
- **[Bug Fix]** Fixed overdue task date parsing.
- **[Docs]** Added onboarding notes for local development.""",
        "narrative_standup": """# Standup Summary

The task management API moved from initial data modeling into usable dashboard workflows. Filtering, date handling, and developer onboarding notes were added, followed by a service-layer cleanup.""",
        "narrative_onboarding": """# Project History & Onboarding Guide

TaskFlow API began with a Flask API and SQLite schema, then added dashboard filtering and date-handling fixes. New contributors should review the route layer, task repository helpers, and local setup notes first.""",
        "narrative_portfolio": """# TaskFlow API

TaskFlow API is a demo repository analysis showing how RepoLens summarizes a small backend project. It highlights API setup, filtering features, bug fixes, documentation, and refactoring.""",
        "extended_data_json": json.dumps({
            "metadata": {"stars": 12, "forks": 2, "default_branch": "main", "description": "Demo task management backend"},
            "technologies": {
                "technologies": [
                    {"name": "Python", "category": "language", "confidence": 1.0},
                    {"name": "Flask", "category": "backend", "confidence": 0.88},
                    {"name": "SQLite", "category": "database", "confidence": 0.75},
                ],
                "dependencies": {"python": ["flask", "werkzeug", "python-dotenv"]},
                "language_stats": {"Python": 72.0, "HTML": 18.0, "CSS": 10.0},
                "todos": [{"file": "services/tasks.py", "line": 44, "type": "TODO", "text": "Add pagination for larger task lists"}],
                "hotspots": [{"file": "services/tasks.py", "mentions": 4, "authors": 1, "risk": "LOW", "confidence": 0.62}],
                "commit_quality": {"score": 78, "total": 5, "noisy": 0, "short_messages": 1, "conventional_commits": 3, "grade": "B"},
                "risk_items": [{"type": "docs", "severity": "LOW", "confidence": 0.68, "title": "Pagination not implemented", "description": "A TODO marker suggests larger task lists need pagination."}],
                "complexity_metrics": {
                    "complexity_score": 26,
                    "complexity_label": "Low",
                    "file_count": 18,
                    "max_directory_depth": 2,
                    "breakdown": {"volume": 8, "dependencies": 6, "churn_hotspots": 5, "tech_debt": 7},
                },
                "entry_points": ["app.py"],
                "directory_summary": "app.py\nservices/\ntemplates/\nstatic/\nREADME.md",
            },
            "architecture": {
                "patterns": [{"name": "Layered", "confidence": 0.78, "description": "Routes delegate task operations to service helpers.", "evidence": ["services/", "templates/"]}],
                "modules": [
                    {"name": "API Layer", "directories": ["app.py"], "file_count": 1},
                    {"name": "Business Logic", "directories": ["services"], "file_count": 4},
                    {"name": "Templates", "directories": ["templates"], "file_count": 5},
                ],
                "api_endpoints": ["GET /tasks", "POST /tasks", "PATCH /tasks/<id>"],
                "description": "TaskFlow API follows a small layered Flask structure with routes, services, templates, and SQLite persistence.",
                "insights": ["The service layer centralizes task operations.", "Commit history shows a move from features to cleanup."],
            },
        }),
    },
]
