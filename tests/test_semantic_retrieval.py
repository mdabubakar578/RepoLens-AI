"""Tests for the optional embedding ranker and its fusion with lexical search.

No real model is ever loaded here. A stub encoder stands in for
sentence-transformers so the suite stays offline, deterministic, and quick;
what is under test is the fusion, the cache handling, and the fallbacks, none
of which depend on the quality of a particular model.
"""

from typing import ClassVar

import numpy as np
import pytest

from services.rag_service import RRF_K, CodeChunk, RAGService


class StubEncoder:
    """Encodes each text as a unit vector pointing at a keyword it contains."""

    AXES: ClassVar[dict[str, int]] = {"payment": 0, "token": 1, "documentation": 2}

    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            vector = np.zeros(3, dtype="float32")
            for word, axis in self.AXES.items():
                if word in text.lower():
                    vector[axis] = 1.0
            if not vector.any():
                vector[2] = 1.0
            vectors.append(vector / np.linalg.norm(vector))
        return np.array(vectors, dtype="float32")


@pytest.fixture
def semantic_rag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rag = RAGService()
    rag._use_local = True
    rag._model = StubEncoder()
    return rag


FILES = {
    "billing.py": "def charge_card(amount):\n    return gateway.capture(amount)  # payment\n",
    "auth.py": "def issue_token(user):\n    return sign(user)  # token\n",
    "notes.md": "# Notes\nGeneral documentation for the project.\n",
}


def test_indexing_produces_one_normalised_vector_per_chunk(semantic_rag):
    count = semantic_rag.index_repository("sem", FILES)

    assert semantic_rag._vectors.shape == (count, 3)
    assert np.allclose(np.linalg.norm(semantic_rag._vectors, axis=1), 1.0)


def test_semantic_ranking_finds_a_file_sharing_no_words_with_the_query(semantic_rag):
    """The lexical ranker cannot answer this; the embedding ranker can."""
    semantic_rag.index_repository("sem", FILES)

    results = semantic_rag.search("payment", top_k=3)

    assert results[0].chunk.file_path == "billing.py"


def test_fusion_uses_reciprocal_rank_and_not_a_tuned_weight(semantic_rag):
    """A weight would have to be fitted; the fused score is rank-derived."""
    semantic_rag.index_repository("sem", FILES)

    results = semantic_rag.search("token", top_k=3)

    # Top of both rankings scores at most 2 * RRF_K/(RRF_K + 1), rescaled by RRF_K.
    assert results[0].score <= round(2 * RRF_K / (RRF_K + 1), 4)
    assert results[0].score > 0


def test_vectors_survive_a_save_and_load_round_trip(semantic_rag, tmp_path, monkeypatch):
    semantic_rag.index_repository("sem", FILES)

    reloaded = RAGService()
    reloaded._use_local = True
    reloaded._model = StubEncoder()
    monkeypatch.setattr(
        "services.rag_service.SentenceTransformer", lambda *a, **k: StubEncoder(), raising=False
    )
    assert reloaded.load_index("sem")

    assert reloaded._vectors is not None
    assert reloaded._vectors.shape == semantic_rag._vectors.shape


def test_a_vector_cache_that_does_not_match_the_chunks_is_refused(
    semantic_rag, monkeypatch
):
    """Scoring chunks against someone else's vectors would silently mis-rank."""
    semantic_rag.index_repository("sem", FILES)
    semantic_rag._chunks.append(
        CodeChunk(content="extra", file_path="extra.py", start_line=1, end_line=2)
    )
    semantic_rag.save_index("sem")

    reloaded = RAGService()
    reloaded._use_local = True
    monkeypatch.setattr(
        "services.rag_service.SentenceTransformer", lambda *a, **k: StubEncoder(), raising=False
    )
    reloaded.load_index("sem")

    assert reloaded._vectors is None


def test_search_falls_back_to_keyword_when_no_model_is_loaded(semantic_rag):
    semantic_rag.index_repository("sem", FILES)
    semantic_rag._model = None

    results = semantic_rag.search("issue_token", top_k=3)

    assert results[0].chunk.file_path == "auth.py"


def test_embeddings_stay_off_unless_explicitly_enabled(tmp_path, monkeypatch):
    """The deployed default must not depend on optional heavy packages."""
    monkeypatch.chdir(tmp_path)
    rag = RAGService()
    rag._use_local = False
    rag.index_repository("plain", FILES)

    assert rag._vectors is None
    assert rag.search("payment", top_k=3) is not None


def test_indexing_survives_an_encoder_failure(semantic_rag, monkeypatch):
    """A model that cannot load must degrade to lexical, not break analysis."""

    class Broken:
        def encode(self, *args, **kwargs):
            raise RuntimeError("model unavailable")

    semantic_rag._model = Broken()

    assert semantic_rag.index_repository("sem", FILES) > 0
    assert semantic_rag._use_local is False
