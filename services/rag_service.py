"""
services/rag_service.py
=========================
Retrieval-Augmented Generation for repository Q&A.

Lexical identifier-aware search always runs. When RAG_USE_EMBEDDINGS is on and
sentence-transformers is installed, a local embedding ranker runs alongside it
and the two orderings are fused by reciprocal rank.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import os
import re
import warnings
from collections import Counter
from dataclasses import dataclass

import config

LOCAL_EMBEDDINGS_AVAILABLE = False
logger = logging.getLogger("repolens.rag")

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "me",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)
CODE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cpp",
        ".cs",
        ".css",
        ".dart",
        ".ex",
        ".exs",
        ".go",
        ".h",
        ".html",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
        ".yaml",
        ".yml",
    }
)
DOCUMENT_EXTENSIONS = frozenset({".md", ".rst", ".txt"})
DOCUMENT_QUERY_TERMS = frozenset({"docs", "documentation", "install", "readme", "setup"})
MAX_CHUNK_CHARS = config.RAG_CHUNK_SIZE * 4

# Reciprocal rank fusion constant. 60 is the value from the original paper and
# is not tuned here, deliberately: fitting it would reintroduce the free
# parameter that fusion exists to remove.
RRF_K = 60

# Named so the ablation harness can zero individual terms and report the effect.
SCORING_WEIGHTS = {
    "coverage": 0.52,
    "frequency": 0.10,
    "path": 0.13,
    "importance": 0.09,
    "code_bonus": 0.06,
    "definition_bonus": 0.10,
}
LOW_VALUE_PATH_PARTS = (
    ".changeset/",
    "/fixtures/",
    "/snapshots/",
    "changelog",
    "package-lock",
)

# Optional local embedding dependencies.
#
# A repository is capped at MAX_INDEX_FILES, which is a few thousand chunks at
# most, so similarity is one matrix multiply against a 384-column matrix. That
# needs numpy and nothing else: an approximate-nearest-neighbour index only
# earns its keep at a scale this application never reaches, and requiring one
# previously left the whole semantic path switched off wherever it would not
# install.
# numpy is imported on availability rather than on the config flag: the vector
# code paths reference it, and gating the import on configuration meant enabling
# embeddings at runtime raised NameError inside a broad except and degraded
# silently to lexical search.
try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - numpy ships with the embedding extra
    NUMPY_AVAILABLE = False

# sentence-transformers pulls in torch, so it stays behind the flag.
if config.RAG_USE_EMBEDDINGS and NUMPY_AVAILABLE:
    try:
        from sentence_transformers import SentenceTransformer

        LOCAL_EMBEDDINGS_AVAILABLE = True
    except ImportError:
        LOCAL_EMBEDDINGS_AVAILABLE = False


@dataclass
class CodeChunk:
    content: str
    file_path: str
    start_line: int = 0
    end_line: int = 0
    chunk_type: str = "code"  # code, docstring, config
    importance_score: float = 1.0


@dataclass
class SearchResult:
    chunk: CodeChunk
    score: float = 0.0
    relevance: str = "medium"
    matched_terms: tuple[str, ...] = ()
    term_coverage: float = 0.0


class RAGService:
    """Manages code chunking, embedding, and retrieval for repo Q&A."""

    def __init__(self):
        self._model = None
        self._vectors = None
        self._chunks: list[CodeChunk] = []
        self._chunk_terms: list[Counter[str]] = []
        self._path_terms: list[set[str]] = []
        self._document_frequency: Counter[str] = Counter()
        self._use_local = LOCAL_EMBEDDINGS_AVAILABLE

    def index_repository(self, analysis_id: str, file_contents: dict[str, str]) -> int:
        """Chunk and index all repository files. Returns chunk count."""
        self._chunks = []
        for path, content in file_contents.items():
            chunks = self._chunk_file(path, content)
            self._chunks.extend(chunks)

        if not self._chunks:
            return 0

        self._prepare_lexical_index()

        if self._use_local:
            try:
                self._build_embeddings()
            except Exception as exc:
                logger.warning("Embedding failed, falling back to keyword search: %s", exc)
                self._use_local = False

        logger.info("Indexed %d chunks from %d files", len(self._chunks), len(file_contents))
        self.save_index(analysis_id)
        return len(self._chunks)

    def save_index(self, analysis_id: str):
        """Serialize index and chunks to disk."""
        try:
            os.makedirs(config.INDEX_CACHE_DIR, exist_ok=True)
            if self._vectors is not None and self._use_local:
                np.save(
                    os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_vectors.npy"),
                    self._vectors,
                )
            if self._chunks:
                with open(os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_chunks.json"), "w") as f:
                    json.dump([c.__dict__ for c in self._chunks], f)
        except Exception as exc:
            logger.error("Failed to save RAG index for %s: %s", analysis_id, exc)

    def load_index(self, analysis_id: str) -> bool:
        """Load index and chunks from disk."""
        idx_path = os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_vectors.npy")
        chk_path = os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_chunks.json")
        self._chunks = []
        self._vectors = None
        if os.path.exists(chk_path):
            try:
                with open(chk_path) as file_handle:
                    self._chunks = [CodeChunk(**item) for item in json.load(file_handle)]
                self._prepare_lexical_index()
            except Exception as exc:
                logger.error("Failed to load chunks: %s", exc)
                return False
        if self._use_local and os.path.exists(idx_path):
            try:
                vectors = np.load(idx_path)
                # A stale vector file against freshly chunked text would score
                # every chunk against the wrong row, so refuse it outright.
                if len(vectors) == len(self._chunks):
                    self._vectors = vectors
                    if not self._model:
                        self._model = SentenceTransformer(config.EMBEDDING_MODEL)
                else:
                    logger.warning("Vector cache does not match chunks; ignoring it")
            except Exception as exc:
                logger.error("Failed to load embeddings: %s", exc)
                self._vectors = None
        return bool(self._chunks)

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        """Search indexed chunks for relevant code context."""
        if not self._chunks:
            return []

        k = top_k or config.RAG_TOP_K

        if len(self._chunk_terms) != len(self._chunks):
            self._prepare_lexical_index()

        if self._use_local and self._vectors is not None:
            return self._search_hybrid(query, k)

        return self._search_keyword(query, k)

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """Return normalized code-aware terms from prose, paths, or identifiers.

        Both complete identifiers and their snake_case, kebab-case, dotted, or
        camelCase components are retained. This lets an exact symbol query rank
        strongly while ordinary natural-language questions still match it.
        """
        text = re.sub(r"\bq\s*&\s*a\b", " qa ", text, flags=re.IGNORECASE)
        terms: list[str] = []
        for raw_token in TOKEN_PATTERN.findall(text):
            expanded = CAMEL_BOUNDARY.sub(" ", raw_token)
            segments = re.split(r"[.\s-]+", expanded)
            candidates = {raw_token.lower()}
            for segment in segments:
                normalized = segment.lower()
                candidates.add(normalized)
                candidates.update(part for part in normalized.split("_") if part)
            terms.extend(
                term for term in candidates if len(term) >= 2 and term not in STOP_WORDS
            )
        return terms

    def _prepare_lexical_index(self) -> None:
        """Precompute term frequencies so repeated questions remain inexpensive."""
        self._chunk_terms = [Counter(self.tokenize(chunk.content)) for chunk in self._chunks]
        self._path_terms = [set(self.tokenize(chunk.file_path)) for chunk in self._chunks]
        self._document_frequency = Counter()
        for frequencies, path_terms in zip(self._chunk_terms, self._path_terms, strict=True):
            self._document_frequency.update(set(frequencies) | path_terms)

    def _idf(self, term: str) -> float:
        """Return inverse document frequency for one term.

        Without this, a question's common words ("tool", "calls", "work") count
        as much as a rare identifier, so prose that happens to repeat the common
        words outranks the code that actually defines the symbol.
        """
        total = max(1, len(self._chunks))
        seen = self._document_frequency.get(term, 0)
        return math.log((total + 1) / (seen + 1)) + 1.0

    def _weighted_coverage(self, matched: set[str], query_terms: set[str]) -> float:
        """Return the share of query *information* the matched terms explain."""
        total = sum(self._idf(term) for term in query_terms)
        if total <= 0:
            return 0.0
        return sum(self._idf(term) for term in matched) / total

    @staticmethod
    def _relevance_label(score: float) -> str:
        """Map a normalized retrieval score to a user-facing evidence label."""
        if score >= 0.7:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    @staticmethod
    def _rank_and_prune(results: list[SearchResult], top_k: int) -> list[SearchResult]:
        """Keep deterministic results that remain competitive with the best evidence."""
        ranked = sorted(
            results,
            key=lambda result: (-result.score, result.chunk.file_path, result.chunk.start_line),
        )
        if not ranked:
            return []
        relative_floor = max(0.24, ranked[0].score * 0.65)
        eligible = [result for result in ranked if result.score >= relative_floor]
        return RAGService._select_across_files(eligible, top_k)

    @staticmethod
    def _select_across_files(ranked: list[SearchResult], top_k: int) -> list[SearchResult]:
        """Spend the chunk budget on distinct files before repeating a file.

        Taking the top_k highest-scoring chunks tends to return several chunks
        of one file, because a file that matches a query usually matches it in
        more than one place. Measured over 150 real-repository queries, five
        chunks collapsed to 2.69 unique files, which cost about nine points of
        file recall against a baseline that always offered five distinct files.

        The budget is unchanged: one chunk per file is taken first, and any
        slots left over -- because only a few files matched at all -- are
        filled with the next-best chunks, so a narrow match still returns full
        context rather than a thinner answer.
        """
        selected: list[SearchResult] = []
        deferred: list[SearchResult] = []
        seen_paths: set[str] = set()

        for result in ranked:
            if len(selected) >= top_k:
                break
            if result.chunk.file_path in seen_paths:
                deferred.append(result)
                continue
            seen_paths.add(result.chunk.file_path)
            selected.append(result)

        selected.extend(deferred[: max(0, top_k - len(selected))])

        return sorted(
            selected,
            key=lambda result: (-result.score, result.chunk.file_path, result.chunk.start_line),
        )

    def get_context_for_question(self, question: str) -> str:
        """Get formatted context string for a Q&A prompt."""
        results = self.search(question)
        if not results:
            return "No relevant code context found."

        parts = []
        for r in results:
            header = f"--- {r.chunk.file_path} (lines {r.chunk.start_line}-{r.chunk.end_line}) ---"
            parts.append(f"{header}\n{r.chunk.content}")
        return "\n\n".join(parts)

    # ── Chunking ──────────────────────────────────────────────────────────────

    def _chunk_file(self, path: str, content: str) -> list[CodeChunk]:
        """Split a file into meaningful chunks."""
        ext = os.path.splitext(path)[1].lower()
        lines = content.splitlines()

        if not lines:
            return []

        # For Python files, try function/class-level chunking
        if ext == ".py":
            chunks = self._chunk_python(path, content, lines)
            if chunks:
                return chunks

        # Default: sliding window chunking
        return self._chunk_sliding_window(path, lines)

    def _calculate_importance(self, path: str) -> float:
        """Calculate architectural importance score based on file path."""
        score = 1.0
        lower_path = path.lower()
        if lower_path.endswith(("main.py", "app.py", "index.js", "server.js", "main.go")):
            score += 0.3
        if any(p in lower_path for p in ["/services/", "/core/", "/domain/", "/usecases/"]):
            score += 0.2
        if any(p in lower_path for p in ["test", "spec", "vendor", "node_modules", ".min."]):
            score -= 0.4
        if lower_path.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".ini")):
            score -= 0.2
        if any(part in lower_path for part in LOW_VALUE_PATH_PARTS):
            score -= 0.6
        return round(score, 2)

    @staticmethod
    def _definition_span(node) -> tuple[int, int]:
        """Return the 1-based line span of a definition, including decorators."""
        starts = [node.lineno, *(item.lineno for item in getattr(node, "decorator_list", []))]
        return min(starts), getattr(node, "end_lineno", node.lineno)

    def _emit_region(
        self,
        path: str,
        lines: list[str],
        start_line: int,
        end_line: int,
        importance: float,
        chunk_type: str,
        covered: list[bool] | None = None,
    ) -> list[CodeChunk]:
        """Emit one region as one or more size-bounded chunks.

        Oversized regions are split across successive chunks rather than
        truncated. Truncation silently dropped the tail of every large class,
        so most method bodies never reached the index at all.
        """
        chunks: list[CodeChunk] = []
        if covered is not None:
            for index in range(start_line - 1, min(end_line, len(lines))):
                covered[index] = True

        buffer: list[str] = []
        buffer_start = start_line
        size = 0
        for offset, line in enumerate(lines[start_line - 1 : end_line], start=start_line):
            # +1 accounts for the newline that rejoins the buffer.
            if buffer and size + len(line) + 1 > MAX_CHUNK_CHARS:
                chunks.append(
                    self._build_chunk(path, buffer, buffer_start, offset - 1, chunk_type, importance)
                )
                buffer, buffer_start, size = [], offset, 0
            buffer.append(line)
            size += len(line) + 1
        if buffer:
            chunks.append(
                self._build_chunk(path, buffer, buffer_start, end_line, chunk_type, importance)
            )
        return [chunk for chunk in chunks if chunk is not None]

    @staticmethod
    def _build_chunk(
        path: str,
        buffer: list[str],
        start_line: int,
        end_line: int,
        chunk_type: str,
        importance: float,
    ) -> CodeChunk | None:
        text = "\n".join(buffer)
        if len(text.strip()) <= 20:
            return None
        return CodeChunk(
            content=text,
            file_path=path,
            start_line=start_line,
            end_line=end_line,
            chunk_type=chunk_type,
            importance_score=importance,
        )

    def _chunk_python(self, path: str, content: str, lines: list[str]) -> list[CodeChunk]:
        """Chunk Python files by AST boundaries, preserving module-level code.

        Classes are split per method rather than stored whole. A class held as a
        single chunk exceeded the size cap and lost its tail, so its methods were
        absent from retrieval and its citations spanned the entire class.
        """
        chunks: list[CodeChunk] = []
        importance = self._calculate_importance(path)
        covered = [False] * len(lines)
        # Analysed repositories are third-party code; their lint warnings are
        # not this service's to raise.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            try:
                tree = ast.parse(content)
            except (SyntaxError, ValueError):
                return self._chunk_sliding_window(path, lines)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = self._definition_span(node)
                chunks.extend(
                    self._emit_region(path, lines, start, end, importance, "code", covered)
                )
            elif isinstance(node, ast.ClassDef):
                start, end = self._definition_span(node)
                methods = [
                    child
                    for child in ast.iter_child_nodes(node)
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                # Class signature, docstring, and attributes stay together.
                header_end = self._definition_span(methods[0])[0] - 1 if methods else end
                if header_end >= start:
                    chunks.extend(
                        self._emit_region(
                            path, lines, start, header_end, importance, "code", covered
                        )
                    )
                for method in methods:
                    method_start, method_end = self._definition_span(method)
                    chunks.extend(
                        self._emit_region(
                            path, lines, method_start, method_end, importance, "code", covered
                        )
                    )
                # Any trailing class body after the last method.
                if methods:
                    last_end = self._definition_span(methods[-1])[1]
                    if end > last_end:
                        chunks.extend(
                            self._emit_region(
                                path, lines, last_end + 1, end, importance, "code", covered
                            )
                        )

        index = 0
        while index < len(lines):
            if not covered[index] and lines[index].strip():
                start = index
                while index < len(lines) and not covered[index]:
                    index += 1
                chunks.extend(
                    self._emit_region(path, lines, start + 1, index, importance, "module")
                )
            else:
                index += 1
        return chunks or self._chunk_sliding_window(path, lines)

    def _chunk_sliding_window(self, path: str, lines: list[str]) -> list[CodeChunk]:
        """Chunk by sliding window of N lines with overlap."""
        chunks = []
        window_size = max(1, config.RAG_CHUNK_SIZE // 40)  # ~40 chars per line
        overlap = max(1, config.RAG_CHUNK_OVERLAP // 40)
        i = 0

        imp_score = self._calculate_importance(path)
        while i < len(lines):
            end = min(i + window_size, len(lines))
            chunk_text = "\n".join(lines[i:end]).strip()
            if len(chunk_text) > 20:
                chunks.append(
                    CodeChunk(
                        content=chunk_text[:MAX_CHUNK_CHARS],
                        file_path=path,
                        start_line=i + 1,
                        end_line=end,
                        chunk_type="code",
                        importance_score=imp_score,
                    )
                )
            i += window_size - overlap

        return chunks

    # ── Search implementations ────────────────────────────────────────────────

    def _search_keyword(self, query: str, top_k: int) -> list[SearchResult]:
        """Rank chunks using identifier coverage, path evidence, and code priority."""
        query_terms = set(self.tokenize(query))
        if not query_terms:
            return []
        scored = []

        for index, chunk in enumerate(self._chunks):
            score, matched_terms, term_coverage = self._lexical_evidence(query_terms, index)
            if score < 0.24:
                continue
            scored.append(
                SearchResult(
                    chunk=chunk,
                    score=score,
                    relevance=self._relevance_label(score),
                    matched_terms=matched_terms,
                    term_coverage=term_coverage,
                )
            )

        return self._rank_and_prune(scored, top_k)

    def _lexical_evidence(
        self, query_terms: set[str], chunk_index: int
    ) -> tuple[float, tuple[str, ...], float]:
        """Measure how completely one chunk explains the meaningful query terms."""
        frequencies = self._chunk_terms[chunk_index]
        path_terms = self._path_terms[chunk_index]
        chunk = self._chunks[chunk_index]
        content_matches = query_terms.intersection(frequencies)
        path_matches = query_terms.intersection(path_terms)
        matched_terms = tuple(sorted(content_matches | path_matches))
        if not matched_terms:
            return 0.0, (), 0.0

        term_coverage = len(matched_terms) / len(query_terms)
        # Scored on information content, not raw term count.
        weighted_coverage = self._weighted_coverage(set(matched_terms), query_terms)
        frequency_strength = sum(
            min(frequencies.get(term, 0), 3) for term in content_matches
        ) / (3 * len(query_terms))
        path_coverage = self._weighted_coverage(path_matches, query_terms)
        importance = min(1.0, max(0.0, (chunk.importance_score - 0.4) / 1.1))
        extension = os.path.splitext(chunk.file_path)[1].lower()
        code_bonus = SCORING_WEIGHTS["code_bonus"] if extension in CODE_EXTENSIONS else 0
        definition_bonus = (
            SCORING_WEIGHTS["definition_bonus"]
            if self._contains_definition(chunk.content, query_terms)
            else 0
        )

        score = (
            SCORING_WEIGHTS["coverage"] * weighted_coverage
            + SCORING_WEIGHTS["frequency"] * frequency_strength
            + SCORING_WEIGHTS["path"] * path_coverage
            + SCORING_WEIGHTS["importance"] * importance
            + code_bonus
            + definition_bonus
        )
        if extension in DOCUMENT_EXTENSIONS and not query_terms.intersection(DOCUMENT_QUERY_TERMS):
            score *= 0.55
        return round(min(score, 1.0), 4), matched_terms, round(term_coverage, 4)

    @staticmethod
    def _contains_definition(content: str, query_terms: set[str]) -> bool:
        """Return whether a query term is declared as a common code symbol."""
        lowered = content.lower()
        for term in query_terms:
            symbol = term.rsplit(".", maxsplit=1)[-1]
            if len(symbol) < 5:
                continue
            pattern = (
                rf"\b(?:async\s+def|def|class|function|interface|func)\s+"
                rf"{re.escape(symbol)}\b"
            )
            if re.search(pattern, lowered):
                return True
        return False

    def _build_embeddings(self) -> None:
        """Encode every chunk once, normalised so similarity is a dot product."""
        if not self._model:
            self._model = SentenceTransformer(config.EMBEDDING_MODEL)

        texts = [f"{chunk.file_path}: {chunk.content[:1000]}" for chunk in self._chunks]
        self._vectors = self._model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        ).astype("float32")

    def _semantic_ranking(self, query: str, limit: int) -> list[int]:
        """Return chunk indices ordered by cosine similarity to the query."""
        query_vector = self._model.encode(
            [query], show_progress_bar=False, normalize_embeddings=True
        ).astype("float32")[0]
        similarity = self._vectors @ query_vector
        count = min(limit, len(similarity))
        top = np.argpartition(-similarity, count - 1)[:count]
        return [int(index) for index in top[np.argsort(-similarity[top])]]

    def _search_hybrid(self, query: str, top_k: int) -> list[SearchResult]:
        """Fuse the lexical and semantic rankings by reciprocal rank.

        The two scores are not on a comparable scale, so blending them needs a
        weight, and any weight would have to be fitted to whichever questions
        were used to choose it. Reciprocal rank fusion consumes only the
        orderings, which removes that free parameter entirely.

        Lexical retrieval remains a full ranker rather than a rescorer of
        semantic candidates, so an exact identifier match still surfaces even
        when the embedding model finds the question unlike the code.
        """
        if not self._model or self._vectors is None:
            return self._search_keyword(query, top_k)

        query_terms = set(self.tokenize(query))
        lexical: dict[int, tuple[float, tuple[str, ...], float]] = {}
        for index in range(len(self._chunks)):
            score, matched_terms, term_coverage = self._lexical_evidence(query_terms, index)
            if score >= 0.24:
                lexical[index] = (score, matched_terms, term_coverage)

        lexical_ranking = sorted(lexical, key=lambda index: -lexical[index][0])
        depth = max(top_k * 10, 50)
        semantic_ranking = self._semantic_ranking(query, depth)

        fused: dict[int, float] = {}
        for ranking in (lexical_ranking[:depth], semantic_ranking):
            for rank, index in enumerate(ranking, start=1):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank)

        results = []
        for index, fusion_score in fused.items():
            score, matched_terms, term_coverage = lexical.get(index, (0.0, (), 0.0))
            results.append(
                SearchResult(
                    chunk=self._chunks[index],
                    # Rescaled so the fused ordering survives the relative floor
                    # in _rank_and_prune, which is expressed against the best
                    # result rather than against an absolute threshold.
                    score=round(fusion_score * RRF_K, 4),
                    relevance=self._relevance_label(max(score, min(1.0, fusion_score * RRF_K))),
                    matched_terms=matched_terms,
                    term_coverage=term_coverage,
                )
            )
        return self._rank_and_prune(results, top_k)
