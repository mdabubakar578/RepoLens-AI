"""Regression tests for the background analysis workflow."""

from services import analysis_task


def test_pipeline_failure_stores_safe_user_message(monkeypatch):
    stored_errors = []

    def fail_parse(_input):
        raise RuntimeError("private-token-value")

    monkeypatch.setattr(analysis_task, "parse_from_text", fail_parse)
    monkeypatch.setattr(analysis_task.database, "update_progress", lambda *args: None)
    monkeypatch.setattr(
        analysis_task.database,
        "set_error",
        lambda analysis_id, message: stored_errors.append((analysis_id, message)),
    )

    analysis_task._run_analysis(42, "paste", "invalid", "release")

    assert stored_errors == [
        (
            42,
            "Repository analysis could not be completed. Please retry.",
        )
    ]
    assert "private-token-value" not in stored_errors[0][1]


def _silence_persistence(monkeypatch, saved):
    """Stub every persistence call so the pipeline can run without a database."""
    monkeypatch.setattr(analysis_task.database, "update_progress", lambda *args: None)
    monkeypatch.setattr(analysis_task.database, "set_error", lambda *args: None)
    monkeypatch.setattr(
        analysis_task.database, "get_analysis_by_id", lambda _id: {"repo_name": "owner/repo"}
    )
    monkeypatch.setattr(
        analysis_task.database,
        "save_extended_data",
        lambda analysis_id, data: saved.update(data),
    )
    monkeypatch.setattr(
        analysis_task.database,
        "update_narratives",
        lambda analysis_id, narratives: saved.update({"narratives": narratives}),
    )

    class _Conn:
        def execute(self, *args, **kwargs):
            return None

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(analysis_task.database, "get_db", lambda: _Ctx())


def test_missing_commits_reports_actionable_message(monkeypatch):
    errors = []
    monkeypatch.setattr(analysis_task, "parse_from_text", lambda _data: [])
    monkeypatch.setattr(analysis_task.database, "update_progress", lambda *args: None)
    monkeypatch.setattr(
        analysis_task.database, "set_error", lambda _id, message: errors.append(message)
    )

    analysis_task._run_analysis(1, "paste", "nothing", "release")

    assert errors == ["No commits found. Please check your input."]


def test_archive_fallback_supplies_sources_when_tree_is_unavailable(monkeypatch):
    """A rate-limited tree response must not leave the analysis without sources."""
    saved = {}
    _silence_persistence(monkeypatch, saved)
    commits = [{"message": "feat: add login", "author": "A", "date": None, "tags": []}]

    monkeypatch.setattr(analysis_task, "parse_from_url", lambda _url: commits)
    monkeypatch.setattr(analysis_task, "get_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis_task, "set_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        analysis_task, "fetch_repo_metadata", lambda o, r: type("M", (), {
            "description": "", "stars": 0, "forks": 0, "language": "Python",
            "languages": {}, "topics": [], "default_branch": "main", "license": "",
            "size_kb": 1, "open_issues": 0,
        })()
    )
    monkeypatch.setattr(analysis_task, "fetch_file_tree", lambda *args: [])

    archive_calls = []

    def archive(owner, repo, branch, selector=None):
        archive_calls.append((owner, repo, branch))
        return (
            [{"path": "app.py", "type": "blob", "size": 40}],
            {"app.py": "def create_app():\n    return 1\n"},
        )

    monkeypatch.setattr(analysis_task, "fetch_repository_archive", archive)
    monkeypatch.setattr(
        analysis_task, "build_repository_intelligence",
        lambda analysis_id, tree, fetch: (
            {path["path"]: fetch(path["path"]) for path in tree},
            {"index_coverage": {"indexed_files": len(tree)}, "knowledge_graph": {}},
        ),
    )
    monkeypatch.setattr(analysis_task.gemini, "generate_all", lambda text, name: {"release": "ok"})

    analysis_task._run_analysis(7, "url", "https://github.com/owner/repo", "release")

    assert archive_calls == [("owner", "repo", "main")]
    assert saved["technologies"]["source_mode"] == "github-archive"
    assert saved["narratives"] == {"release": "ok"}


def test_narrative_failure_is_reported_without_leaking_detail(monkeypatch):
    saved = {}
    errors = []
    _silence_persistence(monkeypatch, saved)
    monkeypatch.setattr(analysis_task.database, "set_error", lambda _id, m: errors.append(m))
    monkeypatch.setattr(
        analysis_task, "parse_from_text",
        lambda _d: [{"message": "chore: init", "author": "A", "date": None, "tags": []}],
    )

    def boom(*args, **kwargs):
        raise RuntimeError("secret-api-detail")

    monkeypatch.setattr(analysis_task.gemini, "generate_all", boom)

    analysis_task._run_analysis(9, "paste", "abc def", "release")

    assert errors == ["Repository summaries could not be completed. Please retry."]
    assert all("secret-api-detail" not in message for message in errors)
