from types import SimpleNamespace

from services.investigator import Investigation
from services.qa_service import RepositoryQAService


def _analysis():
    return {"id": 1, "repo_name": "sample/repository"}


def test_qa_service_rejects_missing_analysis(monkeypatch):
    monkeypatch.setattr("services.qa_service.database.get_analysis_by_id", lambda _: None)

    result = RepositoryQAService().answer(999, "How does login work?")

    assert result.status_code == 404
    assert result.fallback is True


def test_qa_service_rejects_oversized_question(monkeypatch):
    monkeypatch.setattr("services.qa_service.database.get_analysis_by_id", lambda _: _analysis())

    result = RepositoryQAService().answer(1, "x" * 501)

    assert result.status_code == 400
    assert result.warning == "Question too long"


def test_qa_service_returns_agent_fallback(monkeypatch):
    investigation = Investigation(
        question="Where is login?",
        intent="lookup",
        trace=[
            {
                "step": 1,
                "tool": "search_code",
                "input": "login",
                "observation": "Found 1",
            }
        ],
        sources=[
            {
                "file_path": "pages/auth.py",
                "start_line": 10,
                "end_line": 15,
                "score": 0.8,
                "relevance": "high",
            }
        ],
        context="--- pages/auth.py (lines 10-15) ---",
        confidence=72,
        confidence_label="Medium",
        sufficient_evidence=True,
    )
    investigator = SimpleNamespace(investigate=lambda _: investigation)
    monkeypatch.setattr("services.qa_service.database.get_analysis_by_id", lambda _: _analysis())
    monkeypatch.setattr("services.qa_service.database.get_extended_data", lambda _: {})
    monkeypatch.setattr(
        "services.qa_service.RepositoryInvestigator",
        lambda _: investigator,
    )
    monkeypatch.setattr("services.qa_service.gemini.is_available", lambda: False)

    result = RepositoryQAService().answer(1, "Where is login?")

    assert result.status_code == 200
    assert result.provider == "Retrieval-only fallback"
    assert result.sources[0]["file_path"] == "pages/auth.py"
    assert result.agent_trace[0]["tool"] == "search_code"


def test_response_body_hides_internal_status_code():
    result = RepositoryQAService._error("Bad request", "Invalid", 400)

    assert result.response_body()["answer"] == "Bad request"
    assert "status_code" not in result.response_body()
