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


def test_unknown_narrative_format_uses_default(app, monkeypatch):
    captured = {}
    monkeypatch.setattr(database, "save_analysis", lambda **kwargs: 123)
    monkeypatch.setattr(
        "pages.home.start_background_analysis",
        lambda analysis_id, input_mode, input_data, format_pref: captured.update(
            format_pref=format_pref
        ),
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
    assert captured["format_pref"] == "release"
