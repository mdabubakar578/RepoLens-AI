"""Application service for repository question answering."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field

import config
import database
from services.gemini_client import gemini
from services.github_service import extract_owner_repo, fetch_repository_archive
from services.investigator import Investigation, RepositoryInvestigator
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import RAGService

logger = logging.getLogger("repolens.qa")


@dataclass
class QAResult:
    """Serializable result returned by the repository Q&A use case."""

    answer: str
    sources: list[dict] = field(default_factory=list)
    provider: str = "RepoLens Investigator"
    fallback: bool = False
    warning: str | None = None
    tokens_used: int = 0
    agent_trace: list[dict] = field(default_factory=list)
    intent: str = "lookup"
    confidence: int = 0
    confidence_label: str = "Low"
    sufficient_evidence: bool = False
    status_code: int = 200

    def response_body(self) -> dict:
        """Return fields intended for the public JSON response."""
        body = asdict(self)
        body.pop("status_code")
        return body


class RepositoryQAService:
    """Coordinate validation, investigation, generation, and grounding checks."""

    def answer(self, analysis_id: int, question: str) -> QAResult:
        """Answer one repository question.

        Args:
            analysis_id: Persisted analysis identifier.
            question: User's natural-language repository question.

        Returns:
            A structured result containing an answer, evidence, and agent trace.
        """
        analysis = database.get_analysis_by_id(analysis_id)
        if not analysis:
            return self._error("Analysis not found", "Invalid analysis ID", 404)

        question = (question or "").strip()
        if not question:
            return self._error("Please provide a question", "Missing question", 400)
        if len(question) > config.MAX_QUESTION_CHARS:
            message = f"Question must be {config.MAX_QUESTION_CHARS} characters or fewer."
            return self._error(message, "Question too long", 400)

        self._rebuild_index_if_missing(analysis_id, analysis)

        investigation_started = time.monotonic()
        investigation = RepositoryInvestigator(str(analysis_id)).investigate(question)
        retrieval_ms = int((time.monotonic() - investigation_started) * 1000)
        context = self._build_context(analysis_id, investigation)

        if not gemini.is_available():
            self._log_metrics(retrieval_ms, 0, True, "not_configured")
            return self._fallback(investigation, "Gemini not configured")

        generation_started = time.monotonic()
        response = gemini.answer_question(
            repo_name=analysis.get("repo_name", ""),
            technologies=self._technology_names(analysis_id),
            context=context,
            question=question,
        )
        generation_ms = int((time.monotonic() - generation_started) * 1000)

        if not response.success:
            reason = (
                "quota_exhausted"
                if any(word in response.error.lower() for word in ("quota", "exhausted"))
                else "api_failure"
            )
            self._log_metrics(retrieval_ms, generation_ms, True, reason)
            return self._fallback(investigation, f"Gemini API error: {response.error}")

        answer = response.content.strip()
        if not self._valid_generated_answer(answer):
            self._log_metrics(retrieval_ms, generation_ms, True, "validation_failed")
            return self._fallback(investigation, "Model response failed grounding validation")

        warning = None
        if investigation.sources and not self._contains_citation(answer, investigation.sources):
            warning = "The response did not cite a retrieved file and may not be fully grounded."
            investigation.confidence = min(investigation.confidence, 49)
            investigation.confidence_label = "Low"

        self._log_metrics(retrieval_ms, generation_ms, False, "")
        return self._result_from_investigation(
            investigation,
            answer=answer,
            provider="Gemini",
            warning=warning,
            tokens_used=response.total_tokens,
        )

    @staticmethod
    def _rebuild_index_if_missing(analysis_id: int, analysis: dict) -> bool:
        """Restore retrieval evidence after hosted local storage is recycled.

        Free hosting tiers do not persist the index cache across restarts. One
        bounded public archive download rebuilds the chunks and graph so the
        investigator still has repository evidence to cite.

        Returns:
            Whether an index was rebuilt.
        """
        chunk_path = os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_chunks.json")
        if os.path.exists(chunk_path):
            return False
        repo_url = analysis.get("repo_url") or ""
        if "github.com" not in repo_url:
            return False
        try:
            owner, repo = extract_owner_repo(repo_url)
            metadata = database.get_extended_data(analysis_id).get("metadata") or {}
            branch = metadata.get("default_branch", "main")
            _, contents = fetch_repository_archive(owner, repo, branch)
            if not contents:
                return False
            RAGService().index_repository(str(analysis_id), contents)
            graph = KnowledgeGraph()
            graph.build(contents)
            graph.save(str(analysis_id))
            logger.info(
                "Rebuilt index %s from archive (%d files)", analysis_id, len(contents)
            )
            return True
        except Exception as exc:
            logger.warning("Could not rebuild index %s: %s", analysis_id, exc)
            return False

    @staticmethod
    def _build_context(analysis_id: int, investigation: Investigation) -> str:
        extended = database.get_extended_data(analysis_id)
        technology_data = extended.get("technologies", {})
        architecture = extended.get("architecture", {})
        parts = []
        if architecture.get("description"):
            parts.append(f"Architecture: {architecture['description']}")
        if isinstance(technology_data, dict) and technology_data.get("directory_summary"):
            parts.append(f"Directory structure:\n{technology_data['directory_summary']}")
        parts.append(
            f"Agent intent: {investigation.intent}\n"
            f"Evidence confidence: {investigation.confidence}%\n"
            f"Repository evidence:\n{investigation.context}"
        )
        return "\n\n".join(parts)

    @staticmethod
    def _technology_names(analysis_id: int) -> str:
        data = database.get_extended_data(analysis_id).get("technologies", {})
        technologies = data if isinstance(data, list) else data.get("technologies", [])
        return ", ".join(item.get("name", "") for item in technologies[:10])

    @staticmethod
    def _valid_generated_answer(answer: str) -> bool:
        refusals = (
            "i cannot access",
            "i don't have the repository",
            "as an ai model",
            "i cannot browse",
        )
        lowered = answer.lower()
        return len(answer) >= 40 and not any(text in lowered for text in refusals)

    @staticmethod
    def _contains_citation(answer: str, sources: list[dict]) -> bool:
        import os
        return any(
            source["file_path"] in answer or os.path.basename(source["file_path"]) in answer
            for source in sources
        )

    def _fallback(self, investigation: Investigation, warning: str) -> QAResult:
        if not investigation.sources:
            answer = (
                "**Retrieval-only mode**\n\n"
                "No relevant repository evidence was found. Try naming a file, symbol, route, or feature."
            )
        else:
            lines = [
                "**Retrieval-only mode**",
                "",
                "AI generation is unavailable. The investigator found these relevant sections:",
                "",
            ]
            lines.extend(
                f"- `{source['file_path']}` "
                f"(lines {source['start_line']}-{source['end_line']}) — "
                f"**{round(source['score'] * 100)}% match**"
                for source in investigation.sources
            )
            answer = "\n".join(lines)
        return self._result_from_investigation(
            investigation,
            answer=answer,
            provider="Retrieval-only fallback",
            warning=warning,
            fallback=True,
        )

    @staticmethod
    def _result_from_investigation(
        investigation: Investigation,
        *,
        answer: str,
        provider: str,
        warning: str | None,
        fallback: bool = False,
        tokens_used: int = 0,
    ) -> QAResult:
        return QAResult(
            answer=answer,
            sources=investigation.sources,
            provider=provider,
            fallback=fallback,
            warning=warning,
            tokens_used=tokens_used,
            agent_trace=investigation.trace,
            intent=investigation.intent,
            confidence=investigation.confidence,
            confidence_label=investigation.confidence_label,
            sufficient_evidence=investigation.sufficient_evidence,
        )

    @staticmethod
    def _error(answer: str, warning: str, status_code: int) -> QAResult:
        return QAResult(
            answer=answer,
            provider="Error",
            fallback=True,
            warning=warning,
            status_code=status_code,
        )

    @staticmethod
    def _log_metrics(retrieval_ms: int, generation_ms: int, fallback: bool, reason: str) -> None:
        logger.info(
            "qa_metrics retrieval_ms=%d generation_ms=%d fallback=%s reason=%s",
            retrieval_ms,
            generation_ms,
            fallback,
            reason or "none",
        )
