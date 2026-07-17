import io
import sys
import types
import unittest
import zipfile

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from services.gemini_client import _generate_local_narrative
from services.github_service import _parse_repository_archive
from services.rag_service import RAGService


class ProductionFallbackTests(unittest.TestCase):
    def test_milestones_do_not_inflate_commit_total(self):
        commit_data = """
## Week of Jul 13, 2026 (2 commits)
  Milestones: v2.0
  [Feature] Add repository retrieval (by Abu)
  [Fix] Handle missing API keys (by Abu)
## Week of Jul 06, 2026 (1 commits)
  [Docs] Document the fallback (by Abu)
"""
        output = _generate_local_narrative("release", commit_data, "RepoLens")
        self.assertIn("Total commits analyzed: **3**", output)
        self.assertNotIn("| Change |", output)

    def test_archive_fallback_extracts_key_source_files(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("RepoLens-main/README.md", "# RepoLens")
            archive.writestr("RepoLens-main/app.py", "from flask import Flask\napp = Flask(__name__)\n")
            archive.writestr("RepoLens-main/services/rag_service.py", "class RAGService:\n    pass\n")
            archive.writestr("RepoLens-main/assets/logo.png", b"\x00\x01binary")
        tree, contents = _parse_repository_archive(payload.getvalue())
        paths = {item["path"] for item in tree}
        self.assertIn("README.md", paths)
        self.assertIn("app.py", paths)
        self.assertIn("services/rag_service.py", contents)
        self.assertNotIn("assets/logo.png", contents)

    def test_keyword_rag_returns_cited_source_lines_without_vectors(self):
        rag = RAGService()
        rag._use_local = False
        rag._chunks = rag._chunk_file(
            "services/rag_service.py",
            "def search_repository(question):\n    return retrieve_source_chunks(question)\n",
        )
        results = rag.search("retrieve source chunks", top_k=3)
        self.assertTrue(results)
        self.assertEqual("services/rag_service.py", results[0].chunk.file_path)
        self.assertGreaterEqual(results[0].chunk.start_line, 1)


if __name__ == "__main__":
    unittest.main()
