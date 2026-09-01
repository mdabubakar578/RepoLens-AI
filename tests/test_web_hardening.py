import re

import pytest

import database
from app import create_app
from services.github_service import validate_repository_url


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(database, "init_db", lambda: None)
    monkeypatch.setattr("services.task_recovery.recover_stale_tasks", lambda: [])
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.mark.parametrize(
    "url",
    [
        "ftp://github.com/owner/repo",
        "javascript://github.com/owner/repo",
        "https://user:secret@github.com/owner/repo",
        "https://example.com/owner/repo",
        "https://github.com/owner",
    ],
)
def test_repository_url_rejects_unsafe_or_incomplete_urls(url):
    with pytest.raises(ValueError):
        validate_repository_url(url)


def test_repository_url_accepts_supported_https_url():
    parsed = validate_repository_url("https://github.com/owner/repository")

    assert parsed.hostname == "github.com"


def test_invalid_url_does_not_create_analysis(app, monkeypatch):
    save_calls = []
    monkeypatch.setattr(database, "save_analysis", lambda **kwargs: save_calls.append(kwargs))

    response = app.test_client().post(
        "/analyze",
        data={"input_mode": "url", "repo_url": "javascript://github.com/owner/repo"},
    )

    assert response.status_code == 302
    assert save_calls == []


def test_invalid_upload_extension_does_not_create_analysis(app, monkeypatch):
    save_calls = []
    monkeypatch.setattr(database, "save_analysis", lambda **kwargs: save_calls.append(kwargs))

    response = app.test_client().post(
        "/analyze",
        data={"input_mode": "file", "git_log_file": (bytes_io(b"abc"), "payload.exe")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert save_calls == []


def bytes_io(value):
    from io import BytesIO

    return BytesIO(value)


def test_invalid_history_page_falls_back_to_first_page(app, monkeypatch):
    observed = {}

    def get_all_analyses(**kwargs):
        observed.update(kwargs)
        return [], 0

    monkeypatch.setattr(database, "get_all_analyses", get_all_analyses)

    response = app.test_client().get("/history?page=not-a-number")

    assert response.status_code == 200
    assert observed["page"] == 1


def test_security_headers_are_applied(app):
    response = app.test_client().get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_script_policy_allows_only_nonced_scripts(app):
    """Injected markup must not execute, so script-src carries no unsafe-inline."""
    response = app.test_client().get("/")
    policy = response.headers["Content-Security-Policy"]

    script_directive = next(
        part.strip() for part in policy.split(";") if part.strip().startswith("script-src")
    )
    assert "'nonce-" in script_directive
    assert "'unsafe-inline'" not in script_directive
    assert "'unsafe-eval'" not in script_directive
    assert "object-src 'none'" in policy


def test_each_response_uses_a_fresh_script_nonce(app):
    client = app.test_client()

    first = client.get("/").headers["Content-Security-Policy"]
    second = client.get("/").headers["Content-Security-Policy"]

    assert first != second


def test_rendered_pages_carry_no_inline_event_handlers(app):
    """Inline handlers cannot be nonced, so a strict policy would break them."""
    body = app.test_client().get("/").get_data(as_text=True)

    assert re.search(r"\son[a-z]+\s*=\s*[\"']", body) is None
    assert 'nonce="' in body


def test_health_endpoint_returns_service_identity(app):
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "service": "RepoLens AI",
        "status": "ok",
        "version": "2.0.0",
    }


def test_server_error_page_does_not_disclose_exception(app):
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    @app.get("/test-only-error")
    def test_only_error():
        raise RuntimeError("private-database-password")

    response = app.test_client().get("/test-only-error")

    assert response.status_code == 500
    assert b"private-database-password" not in response.data
    assert b"Please try again" in response.data


def test_analysis_ignores_a_submitted_format_preference(app, monkeypatch):
    """The field was removed: every format is generated, so it decided nothing."""
    captured = {}
    monkeypatch.setattr(database, "save_analysis", lambda **kwargs: 123)
    monkeypatch.setattr(
        "pages.home.start_background_analysis",
        lambda *args: captured.update(args=args),
    )

    response = app.test_client().post(
        "/analyze",
        data={
            "input_mode": "url",
            "repo_url": "https://github.com/owner/repository",
            "format_pref": "not-a-format",
        },
    )

    assert response.status_code == 302
    assert captured["args"] == (123, "url", "https://github.com/owner/repository")
    assert "fmt=" not in response.headers["Location"]


def test_result_view_falls_back_to_the_default_format(app, monkeypatch):
    """The results view still validates ?fmt= because links carry it."""
    monkeypatch.setattr(
        database,
        "get_analysis_by_id",
        lambda _id: {"id": 1, "slug": "s", "repo_name": "owner/repo", "status": "done"},
    )
    monkeypatch.setattr(database, "get_extended_data", lambda _id: {})

    ok = app.test_client().get("/result/1?fmt=not-a-format")

    assert ok.status_code == 200
