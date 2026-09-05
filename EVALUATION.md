# Evaluation

How RepoLens AI is measured, what the numbers support, and where they do not.

The headline results are measured on **six real open-source repositories that
this project did not write**, using **queries written by their own developers**.
An additional hand-written corpus exists, but it is a CI regression gate, not
evidence — the distinction is spelled out below.

```bash
python -m benchmarks.real_world --suite dev --ablation           # tuning suite
python -m benchmarks.real_world --suite heldout                  # never used to decide a change
python -m benchmarks.real_world --suite heldout --embeddings     # with the semantic ranker
python -m benchmarks.runner --check                              # offline regression gate
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

## Targets

Scores are only meaningful against a stated bar, so the bar is stated before
the results. On this task — retrieving the right file from roughly 60 indexed
candidates — a random five-file guess scores 0.083 and the naive lexical
baseline scores 0.49–0.60 depending on repository size.

| Band | Recall@5 | MRR | What it means in use |
|---|---:|---:|---|
| Baseline parity | ~0.60 | ~0.31 | No better than naive token overlap |
| Minimum viable | 0.70 | 0.50 | Right file in the top five 7 times in 10, usually at rank 1–2 |
| **Target** | **0.80** | **0.60** | Wrong only 1 question in 5; answer usually leads with the right file |
| Strong | 0.90 | 0.70 | Comparable to published dense retrievers on their own benchmarks |

Two conditions matter as much as the score:

1. **Consistency across suites.** A system that scores 0.80 on one set and 0.60
   on another has been fitted to the first. Suites should agree within about
   ±0.05.
2. **The identifier-free subset must move too.** Gains that appear only where
   the query already contains the symbol name are lexical luck, not
   understanding.

**Current status: target met with the semantic ranker, missed without it.**

| Configuration | Held-out Recall@5 | MRR | Verdict |
|---|---:|---:|---|
| Lexical only (deployed default) | 0.674 | 0.517 | Below target |
| Lexical + embeddings | **0.826** | **0.689** | **Target met** |

The identifier-free condition is also satisfied: that subset rose from 0.526 to
0.737, so the gain is not confined to queries that already contain the symbol
name. The consistency condition is **not** met — the suites sit 0.099 apart —
and the reason is corpus size rather than fitting, as set out below.

## Two suites, and why

Measuring and fixing on the same repositories produces a number that describes
those repositories. So the corpus is split, and the split is a working rule
rather than a label:

- **`dev`** — every defect is diagnosed and fixed here.
- **`heldout`** — run to report capability, never used to decide a change. It
  was added *after* the two fixes below had already landed.

A test asserts the suites are disjoint and that every repository is pinned to
an immutable tag, so neither discipline can erode quietly. When a future change
is driven by `heldout`, that suite is spent and a third must be added.

| Suite | Repository | Tag | Source files | Indexed |
|---|---|---|---:|---:|
| dev | pallets/click | 8.1.7 | 82 | 60 |
| dev | psf/requests | v2.31.0 | 51 | 51 |
| dev | pallets/flask | 3.0.0 | 101 | 60 |
| dev | python-attrs/attrs | 23.1.0 | 81 | 60 |
| dev | encode/httpx | 0.25.2 | 89 | 60 |
| dev | psf/black | 23.12.1 | 323 | 60 |
| heldout | pytest-dev/pytest | 7.4.4 | 269 | 60 |
| heldout | scrapy/scrapy | 2.11.0 | 359 | 60 |
| heldout | celery/celery | v5.3.6 | 367 | 60 |
| heldout | tornadoweb/tornado | v6.4.0 | 119 | 60 |
| heldout | Textualize/rich | v13.7.0 | 242 | 60 |
| heldout | paramiko/paramiko | 3.4.0 | 98 | 60 |

The held-out set is deliberately unlike the dev set: a test runner, a crawler,
a task queue, an async server, a terminal renderer, and an SSH implementation,
against six mostly small Python libraries. It is also twice the size — 1,454
source files against 727 — which turns out to matter.

Every repository is pinned to an immutable tag and the archive SHA-256 is
recorded in the result artifact, so the corpus is reproducible by anyone.

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

## Results

The baseline lowercases the question, splits on whitespace, and ranks files by
overlapping token count.

| Suite | Queries | Configuration | Recall@5 | Baseline | MRR | Baseline |
|---|---:|---|---:|---:|---:|---:|
| dev | 150 | lexical | 0.593 | 0.600 | 0.408 | 0.310 |
| dev | 150 | + embeddings | 0.727 | 0.600 | 0.582 | 0.310 |
| heldout | 138 | lexical | 0.674 | 0.493 | 0.517 | 0.283 |
| **heldout** | 138 | **+ embeddings** | **0.826** | 0.493 | **0.689** | 0.283 |

Two things are worth separating here.

**The held-out suite scores higher than dev in both configurations.** That is
the opposite of overfitting, and the reason is repository size. The dev
libraries are small — requests indexes 51 files, click 82 — and with that few
candidates a naive baseline already does well, the same saturation that made
the hand-written corpus useless, just milder. The held-out repositories average
242 files, so there are real distractors and ranking has something to do. Read
the dev rows as a hard floor, not as the headline.

**The semantic ranker is what clears the target.** Lexical retrieval alone
plateaus below it; adding embeddings adds 15 points of recall and 17 of MRR on
unseen repositories, and wins every one of the six individually.

### Equal output budget

The baseline always emits five distinct files; RepoLens averages 3.4–3.6, since
the score floor declines to offer weak evidence. Cut to the same candidate
count, the margin widens:

| Suite | Configuration | Recall, matched budget | Baseline |
|---|---|---:|---:|
| dev | lexical | **0.593** | 0.473 |
| heldout | lexical | **0.674** | 0.362 |
| heldout | + embeddings | **0.826** | 0.341 |

### Two defects this benchmark found and fixed

The first measurement was worse: **Recall@5 0.513 against the baseline's
0.600.** Two separate causes, both invisible to the hand-written corpus:

1. **The chunk budget was being spent on one file.** `RAG_TOP_K` selected the
   five highest-scoring *chunks*, and a file that matches a query usually
   matches it in several places, so five chunks collapsed to **2.69 unique
   files** — three guesses against the baseline's five. Selection is now
   file-aware: one chunk per file first, with leftover slots filled from the
   same files so a narrow match still returns full context. The chunk budget,
   and therefore the context size, is unchanged.
2. **Exact symbol matches discarded direct evidence.** When the graph found an
   exact symbol, the supplemental search *replaced* the first search's results
   instead of merging with them. In 6 of 150 queries this threw away a file the
   first search had already ranked first.

| Stage | Recall@5 | MRR | Unique files |
|---|---:|---:|---:|
| As first measured | 0.513 | 0.389 | 2.69 |
| File-aware selection | 0.567 | 0.403 | 3.50 |
| Merging symbol evidence | **0.593** | **0.408** | 3.55 |

That is +8.0 points of recall, against a ceiling of 0.607 for the same ranking
read deep enough to offer five unique files — so the budget defect is now
essentially closed, and what remains is the score floor deliberately declining
to offer weak evidence.

Both fixes carry regression tests, and neither was tuned against the benchmark:
the ranking function is untouched.

### The harder subset

Queries sharing no word with the name of the symbol being retrieved, so they
cannot be answered by matching the identifier. This is the closest thing here
to a test of understanding rather than string overlap.

| Suite | Configuration | Queries | Recall@5 | Baseline | MRR | Baseline |
|---|---|---:|---:|---:|---:|---:|
| dev | lexical | 36 | 0.417 | **0.500** | **0.303** | 0.265 |
| heldout | lexical | 38 | **0.526** | 0.368 | **0.425** | 0.159 |
| heldout | + embeddings | 38 | **0.737** | 0.368 | **0.575** | 0.159 |

This subset is where lexical retrieval was weakest, and it is where embeddings
help most: 0.526 to 0.737, twice the baseline. That matters more than the
headline, because a gain confined to queries already containing the symbol name
would be lexical luck rather than capability.

With lexical alone the picture is mixed: on dev these queries are the one place
the baseline still wins on recall, while on held-out RepoLens leads. So the
earlier reading that semantic queries were a flat weakness came from the
smaller suite alone — the weakness was real but showed up mainly where there
were few candidate files.

## How the target was reached

The prediction recorded here before the work was that the remaining gap was
semantic rather than a matter of weights, because the identifier-free subset
trailed the headline. That is what the ablation and subset data pointed at, and
it held: adding a semantic ranker moved the identifier-free subset furthest
(0.526 to 0.737), which is the signature of the diagnosis being right rather
than of a lucky change.

What was actually wrong with the existing embedding support:

1. **It was switched off by an unnecessary dependency.** The code required
   `faiss` as well as `sentence-transformers`. A repository is capped at 60
   files — a few thousand chunks — so similarity is one matrix multiply against
   a 384-column matrix. An approximate-nearest-neighbour index earns nothing at
   that scale, and requiring one, given how awkward `faiss-cpu` is to install,
   left the whole path dormant. It now runs on numpy and `faiss` has been
   dropped from the extra.
2. **It blended scores with a fitted weight.** The old path scored
   `0.7 * semantic + 0.3 * lexical`, two quantities on different scales, with
   constants nobody had measured. It also used lexical only to rescore
   candidates the semantic ranker had already surfaced, so an exact identifier
   match outside the semantic top-N could not be recovered. Both rankers now
   run in full and are combined by reciprocal rank fusion, which consumes only
   the orderings and so has no weight to fit.
3. **numpy was imported behind the config flag**, so enabling embeddings at
   runtime raised `NameError` inside a broad `except` and degraded silently to
   lexical search. Found by a test, not by the benchmark.

Fusion was chosen over pure embeddings on the dev suite, where embeddings alone
scored marginally higher on these queries. Docstring prose under-represents the
exact-symbol lookups (`Where is X defined?`) that users actually type and that
lexical ranking handles best, so the more robust combination was preferred over
the locally better one. That is a judgement, and it is recorded as one.

### What remains

| Gap | Evidence | Status |
|---|---|---|
| Suites disagree by 0.099 | dev 0.727, heldout 0.826 | Corpus size, not fitting — dev repositories are too small to discriminate |
| Semantic path is not deployed | torch does not fit the hosted free tier | Measured but off by default; see limits |
| 60-file index cap | 11 of 12 repositories capped | Unmeasured; queries are drawn only from indexed files |

The dev suite is now the weaker measurement, because its repositories are too
small. Replacing it with larger ones would improve the signal — and would spend
the held-out suite the moment its results drove that choice, so a third suite
would need to follow.

## Scoring ablation

Each term is disabled in turn and the 150 **dev** queries are re-run, lexical
only, since these are the lexical weights. The held-out suite is not used here:
an ablation exists to inform changes, which is exactly what that suite must not
do.

| Variant | Recall@5 | Δ | MRR | Δ |
|---|---:|---:|---:|---:|
| Full scoring | 0.5933 | — | 0.4080 | — |
| without coverage | 0.0667 | **−0.5266** | 0.0456 | −0.3624 |
| without code_bonus | 0.4400 | **−0.1533** | 0.3418 | −0.0662 |
| without importance | 0.4933 | **−0.1000** | 0.3639 | −0.0441 |
| without frequency | 0.5667 | −0.0266 | 0.3940 | −0.0140 |
| without definition_bonus | 0.6000 | +0.0067 | 0.3920 | −0.0160 |
| without path | 0.6000 | +0.0067 | 0.4111 | +0.0031 |

Four of six terms are clearly load-bearing, led by term coverage. Two are not:

- **Path relevance** improves both metrics when removed. It is not earning its
  place and is a candidate for deletion.
- **The definition bonus** raises recall slightly but costs MRR, so it is
  trading rank quality for coverage rather than adding information.

Neither weight was changed on the strength of this table — acting on it would
mean tuning on the same 150 queries used to report the headline result. It is
recorded as a finding, and testing it needs a second, unseen set of repositories.

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
  `AGENT_MAX_STEPS`. This also holds at 100% across all 288 real queries.

Read its numbers as "nothing regressed", never as "retrieval is accurate".

## Metrics

- **File Recall@5** — a query succeeds if a file defining the documented symbol
  appears in the first five unique source paths.
- **Mean reciprocal rank** — 1/rank of the first relevant file.
- **Negative rejection** — no evidence returned, evidence marked insufficient,
  and zero confidence.
- **Bounded trace rate** — share of investigations within `AGENT_MAX_STEPS`.
- **Latency** — `perf_counter` around the deterministic investigator. Median
  93–106 ms lexical, 168 ms with embeddings; p95 193–386 ms. Excludes network
  and model generation, so it is not end-to-end production latency. Embedding a
  repository at index time costs roughly five seconds on CPU.

Confidence is an application-defined evidence score from 0–100 combining term
coverage, top-result strength, code-evidence ratio, exact-symbol support, and
resolved graph relationships. It is a heuristic, not a calibrated probability
that the answer is true.

## Automated tests

128 tests cover routes and input validation, database operations and stale
recovery, git-log parsing, deterministic analyzers, retrieval quality, graph
resolution, the bounded investigator, Q&A fallback, exports, security headers,
Markdown escaping, file-diverse retrieval selection, semantic fusion and its
vector-cache handling, and the benchmark's own leakage and suite-separation
controls. The semantic tests use a stub encoder, so the suite never downloads a
model and stays offline.

CI enforces dependency consistency, Ruff, byte-compilation, the full suite, 70%
whole-project statement coverage, 80% combined coverage for the five core agent
modules, and the offline benchmark gates. Last verified run: **128 passing, 77%
whole-project, 93% core-agent**.

## Known limits

These bound every number above.

- **The semantic ranker is measured but not deployed.** Every headline number
  above marked "+ embeddings" requires `RAG_USE_EMBEDDINGS=true` and
  `requirements-embeddings.txt`, which pulls in torch. That does not fit the
  hosted free tier, so **the live deployment runs the lexical configuration** and
  scores 0.674, not 0.826. Quoting the higher number for the hosted service
  would be wrong.
- **Lexical-only retrieval plateaus below target.** Its identifier-free subset
  scores 0.526 against 0.674 overall, and on small repositories it loses to
  naive token overlap outright.
- **At most 60 files are indexed per repository.** Eleven of the twelve
  benchmark repositories hit that cap, and queries are drawn only from indexed
  files, so these figures measure ranking quality and exclude the cost of the
  cap entirely. On celery that means 60 files of 367. The Q&A page reports the
  real coverage percentage to the user.
- **Structure resolves for Python and JavaScript/TypeScript only.** Other
  languages are searchable as text but contribute no graph edges.
- **All twelve benchmark repositories are Python.** The held-out suite varies
  domain and size but not language, so the JavaScript/TypeScript path carries no
  measured retrieval evidence at all. Monorepos are untested.
- **Results depend strongly on repository size.** Recall@5 spans 0.593 to 0.826
  across suites and configurations, so any single figure quoted without both its
  corpus and its configuration is misleading.
- **Ground truth is definition location.** A query whose answer genuinely lives
  in several files is scored as satisfied by any one of them.
- **Churn analysis requires real changed-file data.** Without it, no hotspots are
  reported rather than inferred ones.
- Latency figures exclude network and model generation.
