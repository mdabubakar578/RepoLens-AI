"""Retrieval benchmark over real open-source repositories.

The offline corpus in ``benchmarks/runner.py`` is hand-written, so it can only
act as a regression gate: its questions and its answers share an author. This
module removes that objection by measuring the same retrieval pipeline against
public repositories that nobody involved in this project wrote.

Method, following the CodeSearchNet convention:

1. Download a pinned archive of a real repository at an immutable tag.
2. Select and index files with the production selection policy.
3. Take each function's *developer-written* docstring as a natural-language
   query, and treat the file defining that function as the relevant file.
4. **Strip every docstring from the code before indexing**, so the query text
   is not present in the searched corpus. Without this the task is trivial.

The result is a labelled query set that neither the author of this project nor
the model chose: the queries are real developer prose and the ground truth is
the physical location of the code the prose describes.

Reproduce with::

    python -m benchmarks.real_world            # uses the on-disk archive cache
    python -m benchmarks.real_world --refresh  # re-download the pinned archives
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import random
import re
import statistics
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import config
from benchmarks.runner import _first_relevant_rank, _naive_whitespace_rank, _percentile
from services.investigator import RepositoryInvestigator
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import SCORING_WEIGHTS, RAGService
from services.repository_indexer import select_index_files

CACHE_DIR = Path(__file__).parent / ".cache"

#: Public repositories pinned at immutable tags. Chosen for domain variety and
#: for having documented public APIs, which is what makes docstring queries
#: available at all. None of these are authored or controlled by this project.
REPOSITORIES: tuple[dict, ...] = (
    {"owner": "pallets", "repo": "click", "ref": "8.1.7", "language": "Python"},
    {"owner": "psf", "repo": "requests", "ref": "v2.31.0", "language": "Python"},
    {"owner": "pallets", "repo": "flask", "ref": "3.0.0", "language": "Python"},
    {"owner": "python-attrs", "repo": "attrs", "ref": "23.1.0", "language": "Python"},
    {"owner": "encode", "repo": "httpx", "ref": "0.25.2", "language": "Python"},
    {"owner": "psf", "repo": "black", "ref": "23.12.1", "language": "Python"},
)

#: Query text must be prose a developer would plausibly type.
MIN_QUERY_CHARS = 40
MAX_QUERY_CHARS = 220
MIN_QUERY_WORDS = 6
QUERIES_PER_REPOSITORY = 25
SAMPLE_SEED = 20260905


@dataclass(frozen=True)
class RealQuery:
    """One docstring-derived query and the files that satisfy it."""

    repository: str
    symbol: str
    question: str
    expected_files: tuple[str, ...]
    #: True when no token of the defined symbol's name appears in the query, so
    #: the query cannot be answered by matching the identifier itself.
    hard: bool


def _archive_url(spec: dict) -> str:
    return (
        f"https://codeload.github.com/{spec['owner']}/{spec['repo']}"
        f"/zip/refs/tags/{spec['ref']}"
    )


def fetch_archive(spec: dict, refresh: bool = False) -> bytes:
    """Return the pinned repository archive, downloading once and caching it.

    The cache makes reruns deterministic and offline. The pinned tag makes the
    bytes reproducible for anyone else running this benchmark.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{spec['owner']}-{spec['repo']}-{spec['ref']}.zip"
    if cached.exists() and not refresh:
        return cached.read_bytes()

    request = Request(
        _archive_url(spec), headers={"User-Agent": config.GITHUB_API_USER_AGENT}
    )
    with urlopen(request, timeout=120) as response:
        payload = response.read()
    cached.write_bytes(payload)
    return payload


def read_repository(payload: bytes) -> dict[str, str]:
    """Decode the source files of an archive, keyed by repository-relative path."""
    files: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.file_size > 400_000:
                continue
            parts = info.filename.split("/", 1)
            if len(parts) != 2 or not parts[1]:
                continue
            path = parts[1]
            if not path.endswith((".py", ".js", ".ts", ".md", ".yml", ".yaml", ".toml")):
                continue
            try:
                files[path] = archive.read(info).decode("utf-8")
            except (UnicodeDecodeError, zipfile.BadZipFile):
                continue
    return files


def strip_docstrings(source: str) -> str:
    """Blank out every docstring, preserving line count so offsets stay valid.

    The queries are the docstrings. Leaving them in the indexed text would make
    retrieval a verbatim string match and the benchmark meaningless.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for index in range(value.lineno - 1, min(value.end_lineno, len(lines))):
            lines[index] = ""
    return "\n".join(lines)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_WORD = re.compile(r"[A-Za-z][A-Za-z']+")
_IDENTIFIER_PART = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def _summary_sentence(docstring: str) -> str:
    """Return the first sentence of the summary paragraph of a docstring."""
    paragraph = docstring.strip().split("\n\n", 1)[0]
    paragraph = " ".join(paragraph.split())
    sentence = _SENTENCE_END.split(paragraph, 1)[0].strip()
    return sentence


def _symbol_tokens(name: str) -> set[str]:
    """Split an identifier into lowercase word tokens of meaningful length."""
    return {
        part.lower()
        for part in _IDENTIFIER_PART.findall(name)
        if len(part) >= 4
    }


def _stem(word: str) -> str:
    """Fold a trailing plural so "session" and "sessions" compare as equal."""
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _path_tokens(path: str) -> set[str]:
    parts = re.split(r"[/._-]", path.lower())
    return {_stem(part) for part in parts if len(part) >= 4}


def extract_queries(
    repository: str, indexed_files: dict[str, str]
) -> list[RealQuery]:
    """Derive labelled queries from the developer-written docstrings in a repo.

    Only indexed files are considered: this measures retrieval quality, not the
    separately reported file-selection cap.
    """
    definitions: dict[str, list[str]] = {}
    candidates: list[tuple[str, str, str]] = []  # (symbol, path, query)

    for path, source in indexed_files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            definitions.setdefault(node.name, []).append(path)
            if node.name.startswith("_"):
                continue
            docstring = ast.get_docstring(node)
            if not docstring:
                continue
            question = _summary_sentence(docstring)
            if not _is_usable_query(question, path):
                continue
            candidates.append((node.name, path, question))

    queries = []
    seen: set[str] = set()
    for symbol, path, question in candidates:
        if question.lower() in seen:
            continue
        seen.add(question.lower())
        expected = tuple(sorted(set(definitions.get(symbol, [path])) & set(indexed_files)))
        if not expected:
            continue
        lowered = question.lower()
        hard = not any(token in lowered for token in _symbol_tokens(symbol))
        queries.append(RealQuery(repository, symbol, question, expected, hard))
    return queries


def _is_usable_query(question: str, path: str) -> bool:
    """Reject prose that is too short, too long, or leaks the answer's path."""
    if not MIN_QUERY_CHARS <= len(question) <= MAX_QUERY_CHARS:
        return False
    words = _WORD.findall(question)
    if len(words) < MIN_QUERY_WORDS:
        return False
    if ">>>" in question or "```" in question or "::" in question:
        return False
    lowered = {_stem(word.lower()) for word in words}
    # Direct file leakage: a query naming its own module is not a retrieval test.
    return not (lowered & _path_tokens(path))


def evaluate_repository(spec: dict, refresh: bool = False) -> dict:
    """Index one real repository and score retrieval over its docstring queries."""
    payload = fetch_archive(spec, refresh=refresh)
    repository = f"{spec['owner']}/{spec['repo']}"
    all_files = read_repository(payload)

    tree = [{"type": "blob", "path": path, "size": len(text)} for path, text in all_files.items()]
    selected_paths = select_index_files(tree, limit=config.MAX_INDEX_FILES)
    indexed_files = {path: all_files[path] for path in selected_paths if path in all_files}

    queries = extract_queries(repository, indexed_files)
    sampler = random.Random(f"{SAMPLE_SEED}:{repository}")
    if len(queries) > QUERIES_PER_REPOSITORY:
        queries = sampler.sample(queries, QUERIES_PER_REPOSITORY)
    queries.sort(key=lambda query: (query.symbol, query.question))

    # The searched corpus must not contain the query text.
    searchable = {
        path: strip_docstrings(text) if path.endswith(".py") else text
        for path, text in indexed_files.items()
    }

    rows = []
    with tempfile.TemporaryDirectory(prefix="repolens-real-") as temp_dir:
        previous_directory = os.getcwd()
        try:
            os.chdir(temp_dir)
            slug = spec["repo"]
            rag = RAGService()
            rag._use_local = False
            chunk_count = rag.index_repository(slug, searchable)

            graph = KnowledgeGraph()
            graph_stats = graph.build(searchable)
            graph.save(slug)

            investigator = RepositoryInvestigator(slug)
            for query in queries:
                started = time.perf_counter()
                investigation = investigator.investigate(query.question)
                latency_ms = (time.perf_counter() - started) * 1000
                source_paths = list(
                    dict.fromkeys(
                        source["file_path"] for source in investigation.sources
                    )
                )
                baseline_paths = _naive_whitespace_rank(query.question, searchable)

                # The investigator retrieves RAG_TOP_K *chunks*, which collapse
                # to fewer unique files, while the baseline always emits five
                # distinct files. Two extra measurements separate the effect of
                # that output budget from the quality of the ranking itself:
                # the baseline cut to the same number of candidates, and the
                # ranking read deep enough to offer five unique files.
                budget = max(1, len(source_paths[:5]))
                deep_paths = list(
                    dict.fromkeys(
                        match.chunk.file_path
                        for match in rag.search(query.question, top_k=40)
                    )
                )[:5]

                rows.append(
                    {
                        "repository": repository,
                        "symbol": query.symbol,
                        "question": query.question,
                        "expected_files": list(query.expected_files),
                        "hard": query.hard,
                        "unique_candidates": len(source_paths[:5]),
                        "rank": _first_relevant_rank(
                            source_paths[:5], query.expected_files
                        ),
                        "baseline_rank": _first_relevant_rank(
                            baseline_paths[:5], query.expected_files
                        ),
                        "matched_baseline_rank": _first_relevant_rank(
                            baseline_paths[:budget], query.expected_files
                        ),
                        "deep_rank": _first_relevant_rank(
                            deep_paths, query.expected_files
                        ),
                        "source_paths": source_paths[:5],
                        "confidence": investigation.confidence,
                        "tool_steps": len(investigation.trace),
                        "latency_ms": round(latency_ms, 3),
                    }
                )
        finally:
            os.chdir(previous_directory)

    return {
        "repository": repository,
        "ref": spec["ref"],
        "archive_sha256": hashlib.sha256(payload).hexdigest()[:12],
        "corpus": {
            "repository_files": len(all_files),
            "indexed_files": len(indexed_files),
            "selection_capped": len(all_files) > config.MAX_INDEX_FILES,
            "chunks": chunk_count,
            "graph_nodes": graph_stats["node_count"],
            "graph_edges": graph_stats["edge_count"],
            "docstring_queries": len(queries),
        },
        "metrics": _score(rows),
        "cases": rows,
    }


def _score(rows: list[dict]) -> dict:
    """Compute Recall@5 and MRR overall and on the harder identifier-free subset."""
    if not rows:
        return {}

    def summarise(subset: list[dict]) -> dict:
        if not subset:
            return {"queries": 0}
        def recall(field: str) -> float:
            return round(
                sum(row[field] is not None for row in subset) / len(subset), 4
            )

        def mrr(field: str) -> float:
            return round(
                sum(1 / row[field] if row[field] else 0 for row in subset)
                / len(subset),
                4,
            )

        return {
            "queries": len(subset),
            "file_recall_at_5": recall("rank"),
            "mean_reciprocal_rank": mrr("rank"),
            "baseline_file_recall_at_5": recall("baseline_rank"),
            "baseline_mean_reciprocal_rank": mrr("baseline_rank"),
            # Baseline cut to the number of candidates RepoLens actually
            # offered for the same query: equal output budget, so the
            # comparison isolates ranking quality.
            "matched_baseline_recall": recall("matched_baseline_rank"),
            "matched_baseline_mrr": mrr("matched_baseline_rank"),
            # RepoLens ranking read deep enough to fill five unique files.
            "deep_file_recall_at_5": recall("deep_rank"),
            "deep_mean_reciprocal_rank": mrr("deep_rank"),
            "mean_unique_candidates": round(
                sum(row["unique_candidates"] for row in subset) / len(subset), 2
            ),
        }

    latencies = [row["latency_ms"] for row in rows]
    overall = summarise(rows)
    overall["hard_subset"] = summarise([row for row in rows if row["hard"]])
    overall["bounded_trace_rate"] = round(
        sum(row["tool_steps"] <= config.AGENT_MAX_STEPS for row in rows) / len(rows), 4
    )
    overall["median_latency_ms"] = round(statistics.median(latencies), 3)
    overall["p95_latency_ms"] = round(_percentile(latencies, 0.95), 3)
    return overall


def run(refresh: bool = False) -> dict:
    """Evaluate every pinned repository and aggregate the results."""
    repositories = [evaluate_repository(spec, refresh=refresh) for spec in REPOSITORIES]
    rows = [row for result in repositories for row in result["cases"]]
    return {
        "benchmark_version": "1.0",
        "environment": {
            "network_used": True,
            "llm_used": False,
            "embeddings_enabled": False,
            "index_file_cap": config.MAX_INDEX_FILES,
            "agent_step_limit": config.AGENT_MAX_STEPS,
        },
        "repositories": [
            {key: result[key] for key in ("repository", "ref", "archive_sha256", "corpus", "metrics")}
            for result in repositories
        ],
        "aggregate": _score(rows),
        "cases": rows,
    }


def run_ablation(refresh: bool = False) -> list[dict]:
    """Disable one scoring term at a time and re-measure on the real corpus.

    The offline fixture is too small to separate the weights: a naive baseline
    already saturates it, so five of six terms showed a zero delta there. Real
    repositories supply the competing candidate files that make the comparison
    informative.
    """
    full = run(refresh=refresh)["aggregate"]
    rows = [
        {
            "variant": "full scoring",
            "file_recall_at_5": full["file_recall_at_5"],
            "mean_reciprocal_rank": full["mean_reciprocal_rank"],
            "recall_delta": 0.0,
            "mrr_delta": 0.0,
        }
    ]
    original = dict(SCORING_WEIGHTS)
    try:
        for name in original:
            SCORING_WEIGHTS.update(original)
            SCORING_WEIGHTS[name] = 0.0
            metrics = run()["aggregate"]
            rows.append(
                {
                    "variant": f"without {name}",
                    "file_recall_at_5": metrics["file_recall_at_5"],
                    "mean_reciprocal_rank": metrics["mean_reciprocal_rank"],
                    "recall_delta": round(
                        metrics["file_recall_at_5"] - full["file_recall_at_5"], 4
                    ),
                    "mrr_delta": round(
                        metrics["mean_reciprocal_rank"] - full["mean_reciprocal_rank"], 4
                    ),
                }
            )
    finally:
        SCORING_WEIGHTS.update(original)
    return rows


def _ablation_markdown(rows: list[dict]) -> str:
    lines = [
        "# Scoring ablation on real repositories",
        "",
        "Each row disables one scoring term and re-runs all 150 docstring",
        "queries across the six pinned repositories.",
        "",
        "| Variant | Recall@5 | Delta | MRR | Delta |",
        "|---|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {row['variant']} | {row['file_recall_at_5']:.4f} | "
        f"{row['recall_delta']:+.4f} | {row['mean_reciprocal_rank']:.4f} | "
        f"{row['mrr_delta']:+.4f} |"
        for row in rows
    )
    return "\n".join(lines)


def _markdown_report(results: dict) -> str:
    aggregate = results["aggregate"]
    hard = aggregate["hard_subset"]
    total_files = sum(item["corpus"]["repository_files"] for item in results["repositories"])
    lines = [
        "# Real-repository retrieval results",
        "",
        "Six public repositories, pinned at immutable tags. Queries are the",
        "developer-written docstrings found in those repositories; the relevant",
        "file is the file defining the documented symbol. Every docstring is",
        "removed from the indexed text before retrieval runs, so no query text",
        "is present in the searched corpus.",
        "",
        "## Aggregate",
        "",
        f"{aggregate['queries']} queries over {total_files} source files in "
        f"{len(results['repositories'])} repositories.",
        "",
        "| Metric | RepoLens | Naive baseline |",
        "|---|---:|---:|",
        f"| File Recall@5 | {aggregate['file_recall_at_5']:.3f} "
        f"| {aggregate['baseline_file_recall_at_5']:.3f} |",
        f"| Mean reciprocal rank | {aggregate['mean_reciprocal_rank']:.3f} "
        f"| {aggregate['baseline_mean_reciprocal_rank']:.3f} |",
        "",
        "### Equal output budget",
        "",
        "The baseline always emits five distinct files. RepoLens retrieves five",
        f"*chunks*, which collapse to {aggregate['mean_unique_candidates']:.1f} unique",
        "files on average, so the table above compares unequal budgets. Cutting",
        "the baseline to the same number of candidates, and separately reading",
        "the RepoLens ranking deep enough to offer five unique files:",
        "",
        "| Metric | RepoLens | Baseline |",
        "|---|---:|---:|",
        f"| Recall, matched budget | {aggregate['file_recall_at_5']:.3f} "
        f"| {aggregate['matched_baseline_recall']:.3f} |",
        f"| MRR, matched budget | {aggregate['mean_reciprocal_rank']:.3f} "
        f"| {aggregate['matched_baseline_mrr']:.3f} |",
        f"| Recall@5, five unique files | {aggregate['deep_file_recall_at_5']:.3f} "
        f"| {aggregate['baseline_file_recall_at_5']:.3f} |",
        f"| MRR, five unique files | {aggregate['deep_mean_reciprocal_rank']:.3f} "
        f"| {aggregate['baseline_mean_reciprocal_rank']:.3f} |",
        "",
        "## Identifier-free subset",
        "",
        "Queries whose text shares no word with the name of the symbol being",
        "retrieved. These cannot be answered by matching the identifier.",
        "",
        "| Metric | RepoLens | Naive baseline |",
        "|---|---:|---:|",
        f"| Queries | {hard['queries']} | {hard['queries']} |",
        f"| File Recall@5 | {hard.get('file_recall_at_5', 0):.3f} "
        f"| {hard.get('baseline_file_recall_at_5', 0):.3f} |",
        f"| Mean reciprocal rank | {hard.get('mean_reciprocal_rank', 0):.3f} "
        f"| {hard.get('baseline_mean_reciprocal_rank', 0):.3f} |",
        "",
        "## Per repository",
        "",
        "| Repository | Tag | Files | Indexed | Queries | Recall@5 | Baseline | MRR | Baseline MRR |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results["repositories"]:
        metrics = item["metrics"]
        corpus = item["corpus"]
        lines.append(
            f"| {item['repository']} | {item['ref']} | "
            f"{corpus['repository_files']} | {corpus['indexed_files']} | "
            f"{metrics['queries']} | {metrics['file_recall_at_5']:.3f} | "
            f"{metrics['baseline_file_recall_at_5']:.3f} | "
            f"{metrics['mean_reciprocal_rank']:.3f} | "
            f"{metrics['baseline_mean_reciprocal_rank']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"Bounded traces: {aggregate['bounded_trace_rate']:.1%}. "
            f"Median latency {aggregate['median_latency_ms']:.1f} ms, "
            f"p95 {aggregate['p95_latency_ms']:.1f} ms.",
            "",
            "Reproduce with:",
            "",
            "    python -m benchmarks.real_world",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="benchmarks/real-world-results.json",
        help="JSON result path",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Re-download the pinned archives"
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="Also measure each scoring term's contribution on real repositories",
    )
    args = parser.parse_args()

    results = run(refresh=args.refresh)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(".md").write_text(
        _markdown_report(results) + "\n", encoding="utf-8"
    )
    print(_markdown_report(results))

    if args.ablation:
        ablation = run_ablation()
        Path("benchmarks/real-world-ablation.md").write_text(
            _ablation_markdown(ablation) + "\n", encoding="utf-8"
        )
        print("\n" + _ablation_markdown(ablation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
