"""Tests for bounded repository clone fallback behavior."""

from pathlib import Path

import config
from services import github_service


def test_clone_fallback_is_bounded_and_cleans_temporary_directory(tmp_path, monkeypatch):
    observed = {}

    def clone_from(url, destination, **kwargs):
        observed.update(url=url, destination=destination, kwargs=kwargs)
        return object()

    monkeypatch.setattr(config, "TEMP_CLONE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GIT_CLONE_TIMEOUT_SECONDS", 37)
    monkeypatch.setattr(github_service.gitpython.Repo, "clone_from", clone_from)
    monkeypatch.setattr(
        github_service,
        "_extract_from_repo",
        lambda repository: [{"hash": "abc12345"}],
    )

    commits = github_service._parse_from_clone(
        "https://gitlab.com/example/repository"
    )

    assert commits == [{"hash": "abc12345"}]
    assert observed["kwargs"] == {
        "depth": config.CLONE_DEPTH,
        "single_branch": True,
        "env": {"GIT_TERMINAL_PROMPT": "0"},
        "kill_after_timeout": 37,
    }
    assert not Path(observed["destination"]).exists()


def test_changed_file_enrichment_is_skipped_without_a_token(monkeypatch):
    """Each commit costs an API request; unauthenticated runs must not spend them."""
    from services import github_service

    calls = []
    monkeypatch.setattr(github_service.config, "GITHUB_API_TOKEN", "")
    monkeypatch.setattr(
        github_service, "fetch_commit_files", lambda *a: calls.append(a) or ["x.py"]
    )
    commits = [{"full_hash": "a" * 40}]

    assert github_service.populate_changed_files("o", "r", commits) == 0
    assert calls == []
    assert "changed_files" not in commits[0]


def test_changed_file_enrichment_is_bounded_by_sample_size(monkeypatch):
    from services import github_service

    monkeypatch.setattr(github_service.config, "GITHUB_API_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setattr(github_service.config, "CHURN_COMMIT_SAMPLE", 3)
    monkeypatch.setattr(github_service, "fetch_commit_files", lambda *a: ["src/app.py"])
    commits = [{"full_hash": f"{index:040d}"} for index in range(10)]

    enriched = github_service.populate_changed_files("o", "r", commits)

    assert enriched == 3
    assert sum("changed_files" in c for c in commits) == 3
