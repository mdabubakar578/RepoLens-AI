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
