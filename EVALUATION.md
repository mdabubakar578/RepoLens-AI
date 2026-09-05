# Evaluation

How RepoLens AI is measured, what the numbers support, and where they do not.

The headline results are measured on **six real open-source repositories that
this project did not write**, using **queries written by their own developers**.
An additional hand-written corpus exists, but it is a CI regression gate, not
evidence — the distinction is spelled out below.

```bash
python -m benchmarks.real_world --ablation   # real repositories (downloads once, then cached)
python -m benchmarks.runner --check          # offline regression gate
```

## What is being tested

The claim under test is narrow: *a bounded agent can retrieve and organise
useful repository evidence for a natural-language question, and can decline when
the evidence is absent.* Code generation, bug fixing, and general model quality
are out of scope.

| # | Question | Measured on |
|---|---|---|
| RQ1 | Does the retriever place the relevant source file in the top five results? | Real repositories |
| RQ2 | Does identifier-aware ranking beat a naive whitespace-overlap baseline? | Real repositories |
| RQ3 | Which scoring terms actually earn their weight? | Real repositories |
| RQ4 | Does the investigator reject a question about a feature that does not exist? | Offline gate |
| RQ5 | Does every investigation stay within the configured action limit? | Both |

## Real-repository corpus

| Repository | Tag | Source files | Indexed |
|---|---|---:|---:|
| pallets/click | 8.1.7 | 82 | 60 |
| psf/requests | v2.31.0 | 51 | 51 |
| pallets/flask | 3.0.0 | 101 | 60 |
| python-attrs/attrs | 23.1.0 | 81 | 60 |
| encode/httpx | 0.25.2 | 89 | 60 |
| psf/black | 23.12.1 | 323 | 60 |

727 source files, 5,491 indexed chunks, 20,937 graph edges. Every repository is
pinned at an immutable tag and the archive SHA-256 is recorded in the result
artifact, so the corpus is reproducible by anyone.

### Where the labels come from

This is the part that matters, and it follows the CodeSearchNet convention:

1. A query is the **first sentence of a function or class docstring**, written
   by that repository's own maintainers — not by this project's author and not
   by a language model.
2. The relevant file is the file that **physically defines** the documented
   symbol. Ground truth is a fact about the repository, not a judgement call.
3. **Every docstring is stripped from the code before indexing.** Without this
   the query text would sit verbatim in the searched corpus and the task would
   be a string match rather than retrieval.
4. A query is discarded if it names its own module (`sessions` against
   `sessions.py`), is shorter than 40 characters or fewer than 6 words, or
   contains a doctest.

25 queries are sampled per repository under a fixed seed: **150 queries total.**
The filters and the docstring stripping are covered by their own tests, because
a silent regression in either would inflate these numbers rather than fail.

## Results on real repositories

The baseline lowercases the question, splits on whitespace, and ranks files by
overlapping token count.

| Metric | RepoLens | Baseline |
|---|---:|---:|
| File Recall@5 | 0.513 | **0.600** |
| Mean reciprocal rank | **0.389** | 0.310 |

**Read that honestly: as shipped, the naive baseline retrieves more correct
files than RepoLens does.** RepoLens ranks better — when it finds the file, it
puts it higher — but it finds the file less often.

### Why, and what happens at equal budget

The two systems are not given the same output budget. The baseline always emits
five distinct files. RepoLens retrieves five *chunks*, and those chunks collapse
to **2.69 unique files on average**, because several chunks routinely come from
the same file. So the row above compares five guesses against roughly three.

Correcting for that in both directions:

| Comparison | RepoLens | Baseline |
|---|---:|---:|
| Recall, baseline cut to the same candidate count | **0.513** | 0.373 |
| MRR, baseline cut to the same candidate count | **0.389** | 0.249 |
| Recall@5, ranking read deep enough for five unique files | **0.607** | 0.600 |
| MRR, ranking read deep enough for five unique files | **0.434** | 0.310 |

At equal budget the ranking is clearly better on both metrics. At five unique
files each, recall is effectively tied (0.607 vs 0.600) while MRR is 40% higher.

**The actionable finding:** `RAG_TOP_K = 5` is a chunk budget being used to
answer a file-level question, and it costs roughly 9 points of recall
(0.513 → 0.607). That is a real defect this benchmark surfaced, and it is
recorded here rather than tuned away before reporting.

### The harder subset

36 of the 150 queries share no word with the name of the symbol being retrieved,
so they cannot be answered by matching the identifier.

| Metric | RepoLens | Baseline |
|---|---:|---:|
| File Recall@5 | 0.361 | **0.500** |
| Mean reciprocal rank | **0.292** | 0.265 |

On genuinely semantic queries the advantage largely disappears: MRR is barely
ahead and recall is behind. This is the honest limit of a lexical, IDF-weighted
retriever with no embedding model in the default configuration. Closing this gap
needs semantic embeddings, not more weight tuning.

## Scoring ablation

Each term is disabled in turn and all 150 real queries are re-run.

| Variant | Recall@5 | Δ | MRR | Δ |
|---|---:|---:|---:|---:|
| Full scoring | 0.5133 | — | 0.3891 | — |
| without coverage | 0.0533 | **−0.4600** | 0.0400 | −0.3491 |
| without code_bonus | 0.3933 | **−0.1200** | 0.3283 | −0.0608 |
| without importance | 0.4467 | −0.0666 | 0.3522 | −0.0369 |
| without frequency | 0.4867 | −0.0266 | 0.3766 | −0.0125 |
| without definition_bonus | 0.4933 | −0.0200 | 0.3641 | −0.0250 |
| without path | 0.5200 | **+0.0067** | 0.3913 | +0.0022 |

Five of six terms are load-bearing. Path relevance is not: removing it makes
retrieval very slightly *better*, so that weight is not currently earning its
place and is a candidate for removal.

This table is the clearest argument for using real repositories. The same
ablation on the hand-written corpus showed a zero delta for five of the six
terms — with only 13 toy files there were no competing candidates for the
weights to discriminate between, so the corpus could not see what they do.

## The offline regression gate

`benchmarks/runner.py` holds a 14-file corpus and 16 questions, all written by
this project's author. **It is not evidence of retrieval quality**, and its
previously reported 100% Recall@5 measured nothing more than a small corpus with
no competing candidates.

It is retained for what it genuinely provides, which the real benchmark cannot:

- A deterministic gate that runs in CI with no network and no API key.
- The **negative control** — a question about a feature that does not exist must
  return no evidence, mark evidence insufficient, and assign zero confidence.
  This passes at 100%.
- A **bounded-trace check** — every investigation stays within
  `AGENT_MAX_STEPS`. This also holds at 100% across all 150 real queries.

Read its numbers as "nothing regressed", never as "retrieval is accurate".

## Metrics

- **File Recall@5** — a query succeeds if a file defining the documented symbol
  appears in the first five unique source paths.
- **Mean reciprocal rank** — 1/rank of the first relevant file.
- **Negative rejection** — no evidence returned, evidence marked insufficient,
  and zero confidence.
- **Bounded trace rate** — share of investigations within `AGENT_MAX_STEPS`.
- **Latency** — `perf_counter` around the deterministic investigator. Median
  105 ms, p95 207 ms on the real corpus. Excludes network and model generation,
  so it is not end-to-end production latency.

Confidence is an application-defined evidence score from 0–100 combining term
coverage, top-result strength, code-evidence ratio, exact-symbol support, and
resolved graph relationships. It is a heuristic, not a calibrated probability
that the answer is true.

## Automated tests

111 tests cover routes and input validation, database operations and stale
recovery, git-log parsing, deterministic analyzers, retrieval quality, graph
resolution, the bounded investigator, Q&A fallback, exports, security headers,
Markdown escaping, and the benchmark's own leakage controls.

CI enforces dependency consistency, Ruff, byte-compilation, the full suite, 70%
whole-project statement coverage, 80% combined coverage for the five core agent
modules, and the offline benchmark gates. Last verified run: **111 passing, 76%
whole-project, 88% core-agent**.

## Known limits

These bound every number above.

- **The retriever is lexical by default.** On queries sharing no vocabulary with
  the code, it does not beat naive token overlap on recall.
- **At most 60 files are indexed per repository.** Five of the six benchmark
  repositories hit that cap, and queries are drawn only from indexed files, so
  these figures measure ranking quality and exclude the cost of the cap. The
  Q&A page reports the real coverage percentage to the user.
- **Structure resolves for Python and JavaScript/TypeScript only.** Other
  languages are searchable as text but contribute no graph edges.
- **All six benchmark repositories are Python libraries.** Results may not carry
  to applications, monorepos, or other languages.
- **Ground truth is definition location.** A query whose answer genuinely lives
  in several files is scored as satisfied by any one of them.
- **Churn analysis requires real changed-file data.** Without it, no hotspots are
  reported rather than inferred ones.
- Latency figures exclude network and model generation.
