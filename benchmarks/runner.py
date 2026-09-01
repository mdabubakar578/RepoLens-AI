"""Deterministic multi-language retrieval and investigator benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import config
from services.investigator import RepositoryInvestigator
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import SCORING_WEIGHTS, RAGService
from services.repository_indexer import select_index_files

CORPUS = {
    "python_app/routes/auth.py": """
from flask import Blueprint, request
from python_app.services.auth_service import authenticate_user

auth = Blueprint("auth", __name__)

@auth.post("/login")
def login():
    payload = request.get_json()
    return authenticate_user(payload["email"], payload["password"])
""",
    "python_app/services/auth_service.py": """
from python_app.repositories.user_repository import find_user

def authenticate_user(email, password):
    user = find_user(email)
    if user and verify_password(password, user.password_hash):
        return {"token": issue_token(user.id)}
    return {"error": "invalid credentials"}, 401

def verify_password(password, password_hash):
    return password_hash == hash_password(password)
""",
    "python_app/repositories/user_repository.py": """
def find_user(email):
    return database.users.find_one({"email": email})
""",
    "web/src/routes/checkout.js": """
import { createCheckout } from "../controllers/checkoutController.js";
router.post("/checkout", createCheckout);
""",
    "web/src/controllers/checkoutController.js": """
import { chargeCard } from "../services/paymentService.js";

export async function createCheckout(request, response) {
  const receipt = await chargeCard(request.body.paymentToken, request.body.total);
  return response.status(201).json(receipt);
}
""",
    "web/src/services/paymentService.js": """
export async function chargeCard(paymentToken, amount) {
  return paymentGateway.capture({ paymentToken, amount });
}
""",
    "java/src/main/java/example/orders/OrderController.java": """
package example.orders;

@RestController
@RequestMapping("/orders")
public class OrderController {
    private final OrderService orderService;

    @PostMapping
    public Order create(@RequestBody OrderRequest request) {
        return orderService.createOrder(request);
    }
}
""",
    "java/src/main/java/example/orders/OrderService.java": """
package example.orders;

@Service
public class OrderService {
    private final OrderRepository repository;

    public Order createOrder(OrderRequest request) {
        return repository.save(Order.from(request));
    }
}
""",
    "java/src/main/java/example/orders/OrderRepository.java": """
package example.orders;

public interface OrderRepository extends JpaRepository<Order, Long> {
}
""",
    "platform/pages/qa.py": """
from flask import Blueprint, jsonify, request
from platform.services.qa_service import answer_repository_question

qa = Blueprint("qa", __name__)

@qa.post("/qa/<int:analysis_id>/ask")
def ask(analysis_id):
    result = answer_repository_question(analysis_id, request.json["question"])
    return jsonify(result)
""",
    "platform/services/qa_service.py": """
def answer_repository_question(analysis_id, question):
    evidence = investigator.investigate(analysis_id, question)
    return synthesizer.answer(question, evidence)
""",
    "config/application.yml": """
database:
  url: postgresql://localhost/orders
  pool_size: 10
""",
    "README.md": """
This repository contains routes, services, controllers, implementation notes,
request examples, user documentation, order documentation, and flow diagrams.
The words login, checkout, request, implemented, database, and service appear
here only as general documentation rather than executable behavior.
""",
    ".changeset/payment-release.md": """
The payment, checkout, request, chargeCard, database, and service documentation
was updated for a release. This file does not implement runtime behavior.
""",
}

@dataclass(frozen=True)
class BenchmarkCase:
    """One labeled repository question."""

    case_id: str
    language: str
    question: str
    expected_files: tuple[str, ...] = ()
    negative: bool = False


CASES = (
    BenchmarkCase(
        "python_login_flow",
        "Python",
        "How does the login request flow from route to service?",
        ("python_app/routes/auth.py", "python_app/services/auth_service.py"),
    ),
    BenchmarkCase(
        "python_exact_definition",
        "Python",
        "Where is authenticate_user defined?",
        ("python_app/services/auth_service.py",),
    ),
    BenchmarkCase(
        "python_impact",
        "Python",
        "What calls find_user when the user lookup changes?",
        ("python_app/services/auth_service.py",),
    ),
    BenchmarkCase(
        "javascript_checkout_flow",
        "JavaScript",
        "How does the checkout request reach chargeCard?",
        (
            "web/src/controllers/checkoutController.js",
            "web/src/services/paymentService.js",
        ),
    ),
    BenchmarkCase(
        "javascript_exact_definition",
        "JavaScript",
        "Where is chargeCard implemented?",
        ("web/src/services/paymentService.js",),
    ),
    BenchmarkCase(
        "java_controller",
        "Java",
        "Which Java controller accepts new orders?",
        ("java/src/main/java/example/orders/OrderController.java",),
    ),
    BenchmarkCase(
        "java_exact_definition",
        "Java",
        "Where is createOrder implemented?",
        ("java/src/main/java/example/orders/OrderService.java",),
    ),
    BenchmarkCase(
        "config_lookup",
        "YAML",
        "Where is the database URL configured?",
        ("config/application.yml",),
    ),
    BenchmarkCase(
        "qa_phrase_normalization",
        "Python",
        "How does the Q&A request flow?",
        ("platform/pages/qa.py", "platform/services/qa_service.py"),
    ),
    BenchmarkCase(
        "negative_unknown_feature",
        "Cross-language",
        "Where is quantum websocket encryption implemented?",
        negative=True,
    ),
)


def _naive_whitespace_rank(question: str, files: dict[str, str]) -> list[str]:
    """Return the baseline ranking used for an explicit before/after comparison."""
    terms = {
        token.strip(".,?!:;()[]{}").lower()
        for token in question.split()
        if len(token.strip(".,?!:;()[]{}")) >= 2
    }
    scored = []
    for path, content in files.items():
        lowered_content = content.lower()
        lowered_path = path.lower()
        score = sum(
            lowered_content.count(term) + (3 if term in lowered_path else 0)
            for term in terms
        )
        if score:
            scored.append((score, path))
    return [path for _, path in sorted(scored, key=lambda item: (-item[0], item[1]))]


def _first_relevant_rank(paths: list[str], expected: tuple[str, ...]) -> int | None:
    for rank, path in enumerate(paths, start=1):
        if path in expected:
            return rank
    return None


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


HELDOUT_CASES = (
    BenchmarkCase(
        "heldout_password_check",
        "Python",
        "Which module verifies a user's password?",
        ("python_app/services/auth_service.py",),
    ),
    BenchmarkCase(
        "heldout_user_lookup",
        "Python",
        "What code looks up a user by email address?",
        ("python_app/repositories/user_repository.py",),
    ),
    BenchmarkCase(
        "heldout_payment_capture",
        "JavaScript",
        "What happens after the payment token is captured?",
        ("web/src/services/paymentService.js",),
    ),
    BenchmarkCase(
        "heldout_order_persistence",
        "Java",
        "Where does an order get persisted?",
        (
            "java/src/main/java/example/orders/OrderService.java",
            "java/src/main/java/example/orders/OrderRepository.java",
        ),
    ),
    BenchmarkCase(
        "heldout_pool_size",
        "YAML",
        "How is the connection pool size set?",
        ("config/application.yml",),
    ),
    BenchmarkCase(
        "heldout_negative_graphql",
        "Cross-language",
        "Where is the GraphQL subscription resolver defined?",
        negative=True,
    ),
)


def run_benchmark(cases: tuple = CASES) -> dict:
    """Run the benchmark without network or LLM calls and return serializable results."""
    file_tree = [{"type": "blob", "path": path} for path in CORPUS]
    selected_paths = select_index_files(file_tree, limit=60)
    selected_files = {path: CORPUS[path] for path in selected_paths}
    corpus_hash = hashlib.sha256(
        json.dumps(CORPUS, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    rows = []
    with tempfile.TemporaryDirectory(prefix="repolens-benchmark-") as temp_dir:
        previous_directory = os.getcwd()
        try:
            os.chdir(temp_dir)
            rag = RAGService()
            rag._use_local = False
            index_started = time.perf_counter()
            chunk_count = rag.index_repository("benchmark", selected_files)
            index_ms = (time.perf_counter() - index_started) * 1000

            graph = KnowledgeGraph()
            graph_stats = graph.build(selected_files)
            graph.save("benchmark")
            resolved_edges = sum(
                edge["relation"] == "resolves_to" for edge in graph.edges
            )

            for case in cases:
                started = time.perf_counter()
                investigation = RepositoryInvestigator("benchmark").investigate(case.question)
                latency_ms = (time.perf_counter() - started) * 1000
                source_paths = list(
                    dict.fromkeys(source["file_path"] for source in investigation.sources)
                )
                baseline_paths = _naive_whitespace_rank(case.question, selected_files)
                rows.append(
                    {
                        **asdict(case),
                        "expected_files": list(case.expected_files),
                        "rank": _first_relevant_rank(source_paths[:5], case.expected_files),
                        "baseline_rank": _first_relevant_rank(
                            baseline_paths[:5], case.expected_files
                        ),
                        "source_paths": source_paths[:5],
                        "confidence": investigation.confidence,
                        "sufficient_evidence": investigation.sufficient_evidence,
                        "tool_steps": len(investigation.trace),
                        "latency_ms": round(latency_ms, 3),
                    }
                )
        finally:
            os.chdir(previous_directory)

    positive_rows = [row for row in rows if not row["negative"]]
    negative_rows = [row for row in rows if row["negative"]]
    recall = sum(row["rank"] is not None for row in positive_rows) / len(positive_rows)
    baseline_recall = (
        sum(row["baseline_rank"] is not None for row in positive_rows)
        / len(positive_rows)
    )
    mrr = sum(1 / row["rank"] if row["rank"] else 0 for row in positive_rows) / len(
        positive_rows
    )
    baseline_mrr = sum(
        1 / row["baseline_rank"] if row["baseline_rank"] else 0
        for row in positive_rows
    ) / len(positive_rows)
    negative_rejection = sum(
        not row["source_paths"] and row["confidence"] < 40 for row in negative_rows
    ) / max(1, len(negative_rows))
    latencies = [row["latency_ms"] for row in rows]

    return {
        "benchmark_version": "1.0",
        "corpus_hash": corpus_hash,
        "environment": {
            "python": platform.python_version(),
            "embeddings_enabled": False,
            "network_used": False,
            "llm_used": False,
            "agent_step_limit": config.AGENT_MAX_STEPS,
        },
        "corpus": {
            "repository_files": len(CORPUS),
            "selected_files": len(selected_files),
            "excluded_low_value_files": sorted(set(CORPUS) - set(selected_files)),
            "chunks": chunk_count,
            "graph_nodes": graph_stats["node_count"],
            "graph_edges": graph_stats["edge_count"],
            "resolved_edges": resolved_edges,
            "languages": sorted({case.language for case in cases if not case.negative}),
        },
        "metrics": {
            "file_recall_at_5": round(recall, 4),
            "mean_reciprocal_rank": round(mrr, 4),
            "baseline_file_recall_at_5": round(baseline_recall, 4),
            "baseline_mean_reciprocal_rank": round(baseline_mrr, 4),
            "recall_improvement_percentage_points": round(
                (recall - baseline_recall) * 100, 2
            ),
            "negative_rejection_rate": round(negative_rejection, 4),
            "bounded_trace_rate": round(
                sum(row["tool_steps"] <= config.AGENT_MAX_STEPS for row in rows)
                / len(rows),
                4,
            ),
            "median_latency_ms": round(statistics.median(latencies), 3),
            "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
            "index_latency_ms": round(index_ms, 3),
        },
        "cases": rows,
    }


def _markdown_report(results: dict) -> str:
    metrics = results["metrics"]
    corpus = results["corpus"]
    lines = [
        "# RepoLens AI Evaluation Results",
        "",
        "This is a controlled, offline benchmark. It measures labeled-file retrieval,",
        "negative-question rejection, bounded agent execution, and local latency.",
        "It does not claim production-scale or universal code-understanding accuracy.",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| File Recall@5 | {metrics['file_recall_at_5']:.1%} |",
        f"| Mean reciprocal rank | {metrics['mean_reciprocal_rank']:.3f} |",
        f"| Naive baseline Recall@5 | {metrics['baseline_file_recall_at_5']:.1%} |",
        f"| Recall improvement | {metrics['recall_improvement_percentage_points']:.2f} pp |",
        f"| Negative rejection | {metrics['negative_rejection_rate']:.1%} |",
        f"| Bounded traces | {metrics['bounded_trace_rate']:.1%} |",
        f"| Median investigator latency | {metrics['median_latency_ms']:.3f} ms |",
        f"| P95 investigator latency | {metrics['p95_latency_ms']:.3f} ms |",
        f"| Index build latency | {metrics['index_latency_ms']:.3f} ms |",
        "",
        "## Corpus",
        "",
        (
            f"{corpus['selected_files']} selected files, {corpus['chunks']} chunks, "
            f"{corpus['graph_nodes']} graph nodes, and {corpus['resolved_edges']} "
            "resolved call/import edges."
        ),
        "",
        "## Cases",
        "",
        "| Case | Language | Rank | Baseline rank | Confidence | Steps |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in results["cases"]:
        rank = row["rank"] if row["rank"] is not None else "-"
        baseline_rank = (
            row["baseline_rank"] if row["baseline_rank"] is not None else "-"
        )
        lines.append(
            f"| {row['case_id']} | {row['language']} | {rank} | "
            f"{baseline_rank} | {row['confidence']}% | {row['tool_steps']} |"
        )
    lines.extend(
        [
            "",
            "Reproduce with:",
            "",
            "    python -m benchmarks.runner --check",
            "",
        ]
    )
    return "\n".join(lines)


def run_ablation() -> list[dict]:
    """Re-run the tuning set with each scoring term disabled, one at a time.

    Reports what each hand-tuned weight actually contributes, so the constants
    are justified by measurement rather than assertion.
    """
    baseline = run_benchmark()["metrics"]
    rows = [
        {
            "variant": "full scoring",
            "disabled": "-",
            "file_recall_at_5": baseline["file_recall_at_5"],
            "mean_reciprocal_rank": baseline["mean_reciprocal_rank"],
            "recall_delta": 0.0,
        }
    ]
    original = dict(SCORING_WEIGHTS)
    try:
        for name in original:
            SCORING_WEIGHTS.update(original)
            SCORING_WEIGHTS[name] = 0.0
            metrics = run_benchmark()["metrics"]
            rows.append(
                {
                    "variant": f"without {name}",
                    "disabled": name,
                    "file_recall_at_5": metrics["file_recall_at_5"],
                    "mean_reciprocal_rank": metrics["mean_reciprocal_rank"],
                    "recall_delta": round(
                        metrics["file_recall_at_5"] - baseline["file_recall_at_5"], 4
                    ),
                }
            )
    finally:
        SCORING_WEIGHTS.update(original)
    return rows


def _ablation_markdown(rows: list[dict]) -> str:
    lines = [
        "# Scoring ablation",
        "",
        "Each row disables one scoring term and re-runs the tuning question set.",
        "A recall delta of 0.00 means the term is not load-bearing for this corpus.",
        "",
        "| Variant | Recall@5 | MRR | Recall delta |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['variant']} | {row['file_recall_at_5']:.4f} | "
        f"{row['mean_reciprocal_rank']:.4f} | {row['recall_delta']:+.4f} |"
        for row in rows
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="docs/evaluation-results.json",
        help="JSON result path",
    )
    parser.add_argument("--check", action="store_true", help="Fail if quality gates regress")
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="Also measure each scoring term's contribution",
    )
    args = parser.parse_args()

    results = run_benchmark()
    # Reported separately: these questions were written after tuning and are
    # never used to choose weights, so they are the honest generalization signal.
    results["heldout"] = run_benchmark(HELDOUT_CASES)["metrics"]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(".md").write_text(
        _markdown_report(results) + "\n", encoding="utf-8"
    )
    print(json.dumps(results["metrics"], indent=2))
    print("\nheld-out set (never tuned on):")
    print(json.dumps(results["heldout"], indent=2))

    if args.ablation:
        ablation = run_ablation()
        Path("docs/evaluation-ablation.md").write_text(
            _ablation_markdown(ablation) + "\n", encoding="utf-8"
        )
        print("\nablation:")
        for row in ablation:
            print(f"  {row['variant']:26} recall={row['file_recall_at_5']:.4f} "
                  f"delta={row['recall_delta']:+.4f}")

    if args.check:
        metrics = results["metrics"]
        passed = (
            metrics["file_recall_at_5"] >= 0.8
            and metrics["negative_rejection_rate"] == 1.0
            and metrics["bounded_trace_rate"] == 1.0
        )
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
