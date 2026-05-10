"""
services/rag_service.py
=========================
Retrieval-Augmented Generation for repository Q&A.
Tier 1: API-based (sends chunked context to Grok). Always available.
Tier 2: Local FAISS embeddings (optional, if sentence-transformers installed).
"""
from __future__ import annotations
import logging, os, re
from dataclasses import dataclass, field
import config

logger = logging.getLogger("repolens.rag")

# Try to import local embedding dependencies
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    LOCAL_EMBEDDINGS_AVAILABLE = True
except ImportError:
    LOCAL_EMBEDDINGS_AVAILABLE = False

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

@dataclass
class CodeChunk:
    content: str
    file_path: str
    start_line: int = 0
    end_line: int = 0
    chunk_type: str = "code"  # code, docstring, config

@dataclass
class SearchResult:
    chunk: CodeChunk
    score: float = 0.0
    relevance: str = "medium"

class RAGService:
    """Manages code chunking, embedding, and retrieval for repo Q&A."""

    def __init__(self):
        self._model = None
        self._index = None
        self._chunks: list[CodeChunk] = []
        self._use_local = LOCAL_EMBEDDINGS_AVAILABLE and FAISS_AVAILABLE

    def index_repository(self, file_contents: dict[str, str]) -> int:
        """Chunk and index all repository files. Returns chunk count."""
        self._chunks = []
        for path, content in file_contents.items():
            chunks = self._chunk_file(path, content)
            self._chunks.extend(chunks)

        if not self._chunks:
            return 0

        if self._use_local:
            try:
                self._build_faiss_index()
            except Exception as exc:
                logger.warning("FAISS indexing failed, falling back to keyword search: %s", exc)
                self._use_local = False

        logger.info("Indexed %d chunks from %d files", len(self._chunks), len(file_contents))
        return len(self._chunks)

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        """Search indexed chunks for relevant code context."""
        if not self._chunks:
            return []

        k = top_k or config.RAG_TOP_K

        if self._use_local and self._index is not None:
            return self._search_faiss(query, k)

        return self._search_keyword(query, k)

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

    def _chunk_python(self, path: str, content: str, lines: list[str]) -> list[CodeChunk]:
        """Chunk Python files by function/class boundaries."""
        chunks = []
        current_start = 0
        current_lines: list[str] = []

        for i, line in enumerate(lines):
            if re.match(r'^(class |def |async def )', line) and current_lines:
                chunk_text = "\n".join(current_lines).strip()
                if len(chunk_text) > 20:
                    chunks.append(CodeChunk(
                        content=chunk_text[:config.RAG_CHUNK_SIZE * 4],
                        file_path=path, start_line=current_start + 1,
                        end_line=i, chunk_type="code"
                    ))
                current_start = i
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            chunk_text = "\n".join(current_lines).strip()
            if len(chunk_text) > 20:
                chunks.append(CodeChunk(
                    content=chunk_text[:config.RAG_CHUNK_SIZE * 4],
                    file_path=path, start_line=current_start + 1,
                    end_line=len(lines), chunk_type="code"
                ))

        return chunks

    def _chunk_sliding_window(self, path: str, lines: list[str]) -> list[CodeChunk]:
        """Chunk by sliding window of N lines with overlap."""
        chunks = []
        window_size = max(1, config.RAG_CHUNK_SIZE // 40)  # ~40 chars per line
        overlap = max(1, config.RAG_CHUNK_OVERLAP // 40)
        i = 0

        while i < len(lines):
            end = min(i + window_size, len(lines))
            chunk_text = "\n".join(lines[i:end]).strip()
            if len(chunk_text) > 20:
                chunks.append(CodeChunk(
                    content=chunk_text[:config.RAG_CHUNK_SIZE * 4],
                    file_path=path, start_line=i + 1,
                    end_line=end, chunk_type="code"
                ))
            i += window_size - overlap

        return chunks

    # ── Search implementations ────────────────────────────────────────────────

    def _search_keyword(self, query: str, top_k: int) -> list[SearchResult]:
        """Simple keyword-based search fallback."""
        query_terms = set(query.lower().split())
        scored = []

        for chunk in self._chunks:
            content_lower = chunk.content.lower()
            path_lower = chunk.file_path.lower()

            score = 0.0
            for term in query_terms:
                if term in content_lower:
                    score += content_lower.count(term) * 0.1
                if term in path_lower:
                    score += 0.5

            if score > 0:
                scored.append(SearchResult(
                    chunk=chunk, score=min(score, 1.0),
                    relevance="high" if score > 0.5 else "medium"
                ))

        scored.sort(key=lambda r: -r.score)
        return scored[:top_k]

    def _build_faiss_index(self):
        """Build FAISS index from chunks using sentence-transformers."""
        if not self._model:
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

        texts = [f"{c.file_path}: {c.content[:500]}" for c in self._chunks]
        embeddings = self._model.encode(texts, show_progress_bar=False)
        embeddings = np.array(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        self._index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(embeddings)
        self._index.add(embeddings)

    def _search_faiss(self, query: str, top_k: int) -> list[SearchResult]:
        """Search using FAISS vector similarity."""
        if not self._model or not self._index:
            return self._search_keyword(query, top_k)

        query_embedding = self._model.encode([query])
        query_embedding = np.array(query_embedding, dtype="float32")
        faiss.normalize_L2(query_embedding)

        scores, indices = self._index.search(query_embedding, min(top_k, len(self._chunks)))
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._chunks):
                continue
            results.append(SearchResult(
                chunk=self._chunks[idx], score=float(score),
                relevance="high" if score > 0.7 else "medium" if score > 0.4 else "low"
            ))
        return results
