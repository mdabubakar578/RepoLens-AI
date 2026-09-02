# Evaluation

How RepoLens AI is measured, what the numbers support, and where they do not.

Everything here is reproducible offline with no API key:

```bash
python -m benchmarks.runner --check --ablation
```

## What is being tested

The claim under test is narrow: *a bounded agent can retrieve and organise useful repository evidence for a natural-language question, and can decline when the evidence is absent.* Code generation, bug fixing, and general model quality are out of scope.

| # | Question |
|---|---|
| RQ1 | Does the retriever place an expected source file in the top five results? |
| RQ2 | Does identifier-aware ranking beat a naive whitespace-overlap baseline? |
| RQ3 | Does the investigator reject a question about a feature that does not exist? |
| RQ4 | Does every investigation stay within the configured action limit? |
| RQ5 | Is the deterministic layer fast enough to feel interactive? |

## Corpus

A fixed, self-contained corpus defined in `benchmarks/runner.py`. No network, no model calls, so results do not drift with provider latency or model updates.

| Property | Value |
|---|---|
| Repository files | 14 |
| Selected as evidence | 13 (one low-value changeset file correctly excluded) |
| Indexed chunks | 17 |
| Graph nodes / edges | 50 / 51, of which 11 are resolved cross-file references |
| Languages | Python, JavaScript, Java, YAML |
| Labelled questions | 10 (9 positive, 1 negative control) |

A corpus hash is recorded in the result artifact, so changing a case or a source file changes the hash.

## Baseline

The baseline lowercases the question and chunk text, splits on whitespace, and ranks by overlapping token count. It represents a minimal search implementation, not a commercial product.

RepoLens adds identifier tokenisation (snake_case, camelCase, dotted paths), stop-word removal, IDF-weighted term coverage, path and source-type weighting, definition and exact-symbol bonuses, penalties for low-value documentation, and optional structural follow-up through the knowledge graph.

## Metrics

- **File Recall@5** — a positive case succeeds if an expected file appears in the first five unique source paths.
- **Mean reciprocal rank** — 1/rank of the first expected source; rewards putting the right file first rather than fifth.
- **Negative rejection** — a negative case succeeds only when the investigator returns no evidence, marks evidence insufficient, and assigns zero confidence.
- **Bounded trace rate** — the share of investigations within `AGENT_MAX_STEPS` (default 5).
- **Latency** — `perf_counter` around the deterministic investigator. Excludes network and model generation, so it is not end-to-end production latency.

Confidence is an application-defined evidence score from 0–100 combining term coverage, top-result strength, code-evidence ratio, exact-symbol support, and resolved graph relationships. It is a heuristic, not a calibrated probability that the answer is true.

## Results on the tuning questions

| Metric | RepoLens | Baseline |
|---|---:|---:|
| File Recall@5 | 100.0% | 77.8% |
| Mean reciprocal rank | 1.000 | 0.522 |
| Negative rejection | 100.0% | — |
| Bounded traces | 100.0% | — |
| Median latency | 5.1 ms | — |
| p95 latency | 20.4 ms | — |

## Results on held-out questions — and why they matter more

The ten questions above were used while tuning the ranking, so their scores measure fit, not generalisation. A second set of six questions was written afterwards and is never used to choose weights.

| Metric | RepoLens | Baseline |
|---|---:|---:|
| File Recall@5 | 1.000 | **1.000** |
| Mean reciprocal rank | **0.800** | 0.650 |
| Negative rejection | 100.0% | — |

**The honest reading:** on unseen questions the ranking places the correct file *higher*, but it does **not** retrieve more correct files than the naive baseline. The 22.2 percentage-point recall advantage on the tuning set does not reproduce out of sample.

The cause is corpus size: with 13 files, a naive baseline already saturates Recall@5, so this corpus cannot support a claim of general retrieval superiority. What it does support is ranking quality, negative rejection, and boundedness.

## Scoring ablation

`--ablation` disables one scoring term at a time and re-runs the tuning set.

| Variant | Recall@5 | Δ |
|---|---:|---:|
| Full scoring | 1.0000 | — |
| without term coverage | 0.5556 | **−0.4444** |
| without occurrence strength | 1.0000 | 0.0000 |
| without path relevance | 1.0000 | 0.0000 |
| without chunk importance | 1.0000 | 0.0000 |
| without code-type bonus | 1.0000 | 0.0000 |
| without definition bonus | 1.0000 | 0.0000 |

Only term coverage is load-bearing on this corpus. The other five weights are retained on design grounds, not measured benefit — stated here rather than hidden. Demonstrating their value needs a larger corpus with competing candidate files.

## Automated tests

97 tests cover routes and input validation, database operations and stale recovery, git-log parsing, deterministic analyzers, retrieval quality, graph resolution, the bounded investigator, Q&A fallback, exports, security headers, and Markdown escaping.

CI enforces dependency consistency, Ruff, byte-compilation, the full suite, 70% whole-project statement coverage, 80% combined coverage for the five core agent modules, and the benchmark gates. The last verified run: **97 passing, 77% whole-project, 85% core-agent**.

## Known limits

These bound every number above.

- **Structure resolves for Python and JavaScript/TypeScript only.** Other languages are searchable as text but contribute no graph edges, so dependency and change-impact questions degrade to text search.
- **At most 60 files are indexed per repository**, chosen by ranking. On a large repository that is a small fraction, and the Q&A page states the real percentage rather than implying full coverage.
- **Churn analysis requires real changed-file data.** Without it, no hotspots are reported rather than inferred ones.
- **The corpus is small and self-authored.** It is a regression gate, not evidence of accuracy on arbitrary repositories.
- Latency figures exclude network and model generation.
