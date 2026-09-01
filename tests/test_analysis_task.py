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
