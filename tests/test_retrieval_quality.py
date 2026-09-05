from services.investigator import RepositoryInvestigator
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import RAGService
from services.repository_indexer import select_index_files


def _index(tmp_path, monkeypatch, files, analysis_id="quality"):
    monkeypatch.chdir(tmp_path)
    rag = RAGService()
    rag._use_local = False
    rag.index_repository(analysis_id, files)
    graph = KnowledgeGraph()
    graph.build(files)
    graph.save(analysis_id)
    return rag, graph


def test_tokenizer_preserves_dotted_and_snake_case_symbols():
    terms = set(RAGService.tokenize("database.update_narratives handles Q&A"))

    assert {"database.update_narratives", "update_narratives", "update", "narratives", "qa"} <= terms


def test_exact_definition_outranks_caller(tmp_path, monkeypatch):
    rag, _ = _index(
        tmp_path,
        monkeypatch,
        {
            "service.py": "def process_payment(token):\n    return gateway.capture(token)\n",
            "route.py": "from service import process_payment\nresult = process_payment(token)\n",
        },
    )

    results = rag.search("Where is process_payment defined?", top_k=5)

    assert results[0].chunk.file_path == "service.py"
    assert results[0].term_coverage >= results[-1].term_coverage


def test_documentation_is_demoted_for_code_question(tmp_path, monkeypatch):
    rag, _ = _index(
        tmp_path,
        monkeypatch,
        {
            "README.md": "login request handler service login request handler service",
            "routes/login.py": "def login_handler(request):\n    return auth_service.login(request)\n",
        },
    )

    results = rag.search("login request handler service", top_k=5)

    assert results[0].chunk.file_path == "routes/login.py"
    assert all(result.chunk.file_path != "README.md" for result in results)


def test_documentation_remains_searchable_when_requested(tmp_path, monkeypatch):
    rag, _ = _index(
        tmp_path,
        monkeypatch,
        {"README.md": "Setup instructions: install dependencies and run the server."},
    )

    results = rag.search("Where are the setup install docs?", top_k=5)

    assert results[0].chunk.file_path == "README.md"


def test_unknown_feature_returns_no_results(tmp_path, monkeypatch):
    rag, _ = _index(
        tmp_path,
        monkeypatch,
        {"service.py": "def create_order(request):\n    return repository.save(request)\n"},
    )

    assert rag.search("quantum websocket encryption", top_k=5) == []


def test_source_selection_excludes_release_noise_and_locks():
    tree = [
        {"type": "blob", "path": ".changeset/payment.md"},
        {"type": "blob", "path": "package-lock.json"},
        {"type": "blob", "path": "src/payment_service.py"},
        {"type": "blob", "path": "README.md"},
    ]

    selected = select_index_files(tree)

    assert "src/payment_service.py" in selected
    assert ".changeset/payment.md" not in selected
    assert "package-lock.json" not in selected


def test_source_selection_is_deterministic_and_code_first():
    tree = [
        {"type": "blob", "path": "notes.md"},
        {"type": "blob", "path": "src/zeta.py"},
        {"type": "blob", "path": "src/auth_service.py"},
    ]

    first = select_index_files(tree)
    second = select_index_files(list(reversed(tree)))

    assert first == second
    assert first[0] == "src/auth_service.py"


def test_graph_resolves_imports_and_calls():
    graph = KnowledgeGraph()
    graph.build(
        {
            "services/worker.py": "def run_job():\n    return 1\n",
            "app.py": (
                "from services.worker import run_job\n"
                "def start():\n"
                "    return run_job()\n"
            ),
        }
    )

    resolved = [edge for edge in graph.edges if edge["relation"] == "resolves_to"]

    assert any(edge["target"] == "services/worker.py" for edge in resolved)
    assert any(edge["target"].endswith("::run_job") for edge in resolved)


def test_graph_neighbors_honors_limit():
    graph = KnowledgeGraph()
    graph.build(
        {
            "many.py": (
                "def target():\n"
                "    one(); two(); three(); four(); five(); six(); seven()\n"
            )
        }
    )
    symbol = graph.find_symbols("target")[0]

    assert len(graph.neighbors(symbol["id"], depth=3, limit=3)) == 3


def test_unanswerable_agent_question_is_low_confidence(tmp_path, monkeypatch):
    _index(
        tmp_path,
        monkeypatch,
        {"orders.py": "def create_order(request):\n    return repository.save(request)\n"},
        analysis_id="negative",
    )

    result = RepositoryInvestigator("negative").investigate(
        "Where is quantum websocket encryption implemented?"
    )

    assert result.sources == []
    assert result.confidence == 0
    assert result.sufficient_evidence is False
    assert len(result.trace) <= 5


def test_class_methods_are_chunked_individually():
    """A class stored as one chunk exceeded the size cap and lost its later methods."""
    source = (
        "class Service:\n"
        '    """Docstring."""\n'
        "\n"
        "    def first_method(self):\n"
        "        return 'first result value'\n"
        "\n"
        "    def second_method(self):\n"
        "        return 'second result value'\n"
        "\n"
        "    def third_method(self):\n"
        "        return 'third result value'\n"
    )
    chunks = RAGService()._chunk_file("services/sample.py", source)
    joined = "\n".join(chunk.content for chunk in chunks)

    assert len(chunks) >= 3
    for name in ("first_method", "second_method", "third_method"):
        assert f"def {name}" in joined
    assert all(chunk.end_line >= chunk.start_line for chunk in chunks)


def test_oversized_regions_are_split_rather_than_truncated():
    from services.rag_service import MAX_CHUNK_CHARS

    body = "\n".join(f"    value_{index} = 'x' * 40" for index in range(400))
    source = f"def enormous():\n{body}\n"
    chunks = RAGService()._chunk_file("services/big.py", source)

    assert len(chunks) > 1
    assert all(len(chunk.content) <= MAX_CHUNK_CHARS for chunk in chunks)
    # Every source line survives somewhere in the index.
    assert "value_399" in "\n".join(chunk.content for chunk in chunks)


def test_rare_identifiers_outrank_common_words():
    service = RAGService()
    service._chunks = []
    for index in range(12):
        service._chunks.extend(
            service._chunk_file(f"noise_{index}.py", "def handler():\n    return 'tool calls here'\n")
        )
    service._chunks.extend(
        service._chunk_file("target.py", "def calculate_widget_entropy():\n    return 42\n")
    )
    service._prepare_lexical_index()

    results = service.search("calculate_widget_entropy", top_k=3)

    assert results
    assert results[0].chunk.file_path == "target.py"


def test_coverage_reports_share_of_repository_not_fetch_success():
    """Reporting indexed/eligible always neared 100% and overstated understanding."""
    from services.repository_indexer import build_repository_intelligence

    tree = [{"path": f"src/file_{i}.py", "type": "blob", "size": 40} for i in range(200)]
    sources = {item["path"]: "def handler():\n    return 1\n" for item in tree}

    _, stats = build_repository_intelligence(1, tree, sources.get)
    coverage = stats["index_coverage"]

    assert coverage["repository_files"] == 200
    assert coverage["indexed_files"] <= coverage["selection_cap"]
    assert coverage["coverage_percent"] < 100
    assert coverage["fetch_success_percent"] == 100.0
    assert coverage["selection_capped"] is True


def _result(path, score, start_line=1):
    """Build a ranked search result without going through the indexer."""
    from services.rag_service import CodeChunk, SearchResult

    return SearchResult(
        chunk=CodeChunk(
            file_path=path,
            content="body",
            start_line=start_line,
            end_line=start_line + 5,
        ),
        score=score,
    )


def test_chunk_budget_is_spent_on_distinct_files_first():
    """Five chunks of one file answered a file-level question with one file."""
    ranked = [
        _result("a.py", 0.90, 1),
        _result("a.py", 0.89, 20),
        _result("a.py", 0.88, 40),
        _result("b.py", 0.87),
        _result("c.py", 0.86),
    ]

    selected = RAGService._select_across_files(ranked, top_k=3)

    assert [item.chunk.file_path for item in selected] == ["a.py", "b.py", "c.py"]


def test_leftover_budget_falls_back_to_more_chunks_of_the_same_file():
    """A narrow match must still return full context, not a thinner answer."""
    ranked = [_result("a.py", 0.90, 1), _result("a.py", 0.89, 20), _result("a.py", 0.88, 40)]

    selected = RAGService._select_across_files(ranked, top_k=3)

    assert len(selected) == 3
    assert {item.chunk.file_path for item in selected} == {"a.py"}


def test_selection_never_exceeds_the_requested_budget():
    ranked = [_result(f"f{index}.py", 0.9 - index / 100) for index in range(10)]

    assert len(RAGService._select_across_files(ranked, top_k=4)) == 4


def test_search_returns_one_chunk_per_file_before_repeating_a_file(tmp_path, monkeypatch):
    body = "def handle_payment_capture(token):\n    return capture(token)\n"
    rag, _ = _index(
        tmp_path,
        monkeypatch,
        {
            "one.py": body + "\n\n" + body.replace("handle", "retry"),
            "two.py": body,
            "three.py": body,
        },
    )

    results = rag.search("handle payment capture", top_k=3)

    assert len({item.chunk.file_path for item in results}) == 3


def test_exact_symbol_match_does_not_discard_direct_evidence(tmp_path, monkeypatch):
    """The supplemental search used to replace, not merge, losing the answer."""
    files = {
        "utils.py": "def get_unicode_from_response(response):\n    return response.text\n",
        "models.py": "class Response:\n    def json(self):\n        return load(self.text)\n",
    }
    _index(tmp_path, monkeypatch, files, analysis_id="merge")

    investigation = RepositoryInvestigator("merge").investigate(
        "Where is get_unicode_from_response defined?"
    )

    assert "utils.py" in {source["file_path"] for source in investigation.sources}
