"""Tests for the read-only deployment load smoke utility."""

import sys

import pytest

from benchmarks import load_smoke


def test_percentile_interpolates_values():
    assert load_smoke._percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    assert load_smoke._percentile([], 0.95) == 0.0


def test_load_smoke_aggregates_success(monkeypatch):
    def fake_fetch(base_url, path, timeout):
        assert base_url == "http://localhost:5000"
        assert timeout == 1.0
        return load_smoke.RequestResult(path=path, status=200, latency_ms=10.0)

    monkeypatch.setattr(load_smoke, "_fetch", fake_fetch)
    result = load_smoke.run_load_smoke(
        "http://localhost:5000",
        request_count=8,
        concurrency=2,
        paths=("/", "/health"),
        timeout=1.0,
    )

    assert result["successful_requests"] == 8
    assert result["success_rate"] == 1.0
    assert result["status_counts"] == {"200": 8}
    assert result["median_latency_ms"] == 10.0
    assert len(result["results"]) == 8


@pytest.mark.parametrize(
    ("base_url", "request_count", "concurrency", "paths"),
    [
        ("file:///tmp/app", 1, 1, ("/",)),
        ("http://localhost:5000", 0, 1, ("/",)),
        ("http://localhost:5000", 1, 0, ("/",)),
        ("http://localhost:5000", 1, 65, ("/",)),
        ("http://localhost:5000", 1, 1, ("health",)),
    ],
)
def test_load_smoke_rejects_invalid_configuration(
    base_url,
    request_count,
    concurrency,
    paths,
):
    with pytest.raises(ValueError):
        load_smoke.run_load_smoke(
            base_url,
            request_count=request_count,
            concurrency=concurrency,
            paths=paths,
        )


def test_fetch_success(monkeypatch):
    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def read():
            return b""

    monkeypatch.setattr(
        load_smoke.urllib.request,
        "urlopen",
        lambda request, timeout: Response(),
    )
    result = load_smoke._fetch("http://localhost:5000", "/health", 1.0)

    assert result.status == 204
    assert result.error == ""
    assert result.path == "/health"


def test_fetch_network_error(monkeypatch):
    def fail(request, timeout):
        raise load_smoke.urllib.error.URLError("offline")

    monkeypatch.setattr(load_smoke.urllib.request, "urlopen", fail)
    result = load_smoke._fetch("http://localhost:5000", "/health", 1.0)

    assert result.status == 0
    assert result.error == "offline"


def test_main_prints_summary_and_enforces_gate(monkeypatch, capsys):
    def successful_run(**kwargs):
        return {"success_rate": 1.0, "results": [{"status": 200}]}

    monkeypatch.setattr(load_smoke, "run_load_smoke", successful_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["load-smoke", "--requests", "2", "--summary-only"],
    )
    assert load_smoke.main() == 0
    output = capsys.readouterr().out
    assert '"success_rate": 1.0' in output
    assert '"results"' not in output

    monkeypatch.setattr(
        load_smoke,
        "run_load_smoke",
        lambda **kwargs: {"success_rate": 0.5, "results": []},
    )
    monkeypatch.setattr(sys, "argv", ["load-smoke"])
    assert load_smoke.main() == 1
    capsys.readouterr()
