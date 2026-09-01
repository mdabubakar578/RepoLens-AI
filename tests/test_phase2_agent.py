from services.investigator import RepositoryInvestigator
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import RAGService
from services.repository_indexer import select_index_files

SAMPLE_CODE = """
from flask import Blueprint
from services.users import find_user

bp = Blueprint("auth", __name__)

@bp.post("/login")
def login():
    return find_user()
"""


def test_graph_extracts_symbols_routes_imports_and_calls():
    graph = KnowledgeGraph()
    stats = graph.build({"pages/auth.py": SAMPLE_CODE})

    assert stats["kinds"]["function"] == 1
    assert stats["kinds"]["route"] == 1
    assert any(node.get("path") == "/login" for node in graph.nodes.values())
    assert any(edge["relation"] == "imports" for edge in graph.edges)
    assert any(edge["relation"] == "calls" for edge in graph.edges)


def test_graph_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    graph = KnowledgeGraph()
    graph.build({"pages/auth.py": SAMPLE_CODE})
    graph.save("42")

    restored = KnowledgeGraph()
    assert restored.load("42")
    assert restored.stats() == graph.stats()


def test_graph_finds_symbols_and_neighbors():
    graph = KnowledgeGraph()
    graph.build({"pages/auth.py": SAMPLE_CODE})

    matches = graph.find_symbols("login")
    assert matches[0]["name"] == "login"
    assert graph.neighbors(matches[0]["id"])


def test_source_selection_fills_with_ordinary_source_files():
    tree = [
        {"type": "blob", "path": "unusual/engine.py"},
        {"type": "blob", "path": "frontend/widget.tsx"},
        {"type": "blob", "path": "assets/logo.png"},
    ]

    selected = select_index_files(tree)

    assert "unusual/engine.py" in selected
    assert "frontend/widget.tsx" in selected
    assert "assets/logo.png" not in selected


def test_investigator_runs_bounded_impact_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rag = RAGService()
    rag._use_local = False
    rag.index_repository("7", {"pages/auth.py": SAMPLE_CODE})
    graph = KnowledgeGraph()
    graph.build({"pages/auth.py": SAMPLE_CODE})
    graph.save("7")

    investigator = RepositoryInvestigator("7")
    investigator.rag._use_local = False
    result = investigator.investigate("What is affected if I change login?")

    assert result.intent == "impact"
    assert len(result.trace) <= 5
    assert any(step["tool"] == "search_code" for step in result.trace)
    assert any(step["tool"] == "find_symbol" for step in result.trace)
    assert result.sources
    assert 0 <= result.confidence <= 100
