"""Bounded, read-only repository investigation agent."""

from __future__ import annotations

from dataclasses import dataclass, field

import config
from services.knowledge_graph import KnowledgeGraph
from services.rag_service import CODE_EXTENSIONS, RAGService


@dataclass
class Investigation:
    question: str
    intent: str
    trace: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    context: str = ""
    confidence: int = 0
    confidence_label: str = "Low"
    sufficient_evidence: bool = False


class RepositoryInvestigator:
    """Executes a transparent plan capped by AGENT_MAX_STEPS."""

    def __init__(self, analysis_id):
        self.analysis_id = str(analysis_id)
        self.rag = RAGService()
        self.graph = KnowledgeGraph()
        self.rag_available = self.rag.load_index(self.analysis_id)
        self.graph_available = self.graph.load(self.analysis_id)

    def investigate(self, question):
        intent = self._classify_intent(question)
        result = Investigation(question=question, intent=intent)
        retrieved = []

        if self._can_act(result):
            retrieved.extend(
                self._search_and_record(question, result, "Locate direct evidence for the question")
            )

        symbols = []
        if self.graph_available and self._can_act(result):
            symbols = self.graph.find_symbols(question, limit=6)
            best_symbol = symbols[0].get("name", symbols[0]["id"]) if symbols else "none"
            self._record_step(
                result,
                tool="find_symbol",
                tool_input=question,
                reason="Anchor the question to a concrete repository symbol",
                observation=f"Found {len(symbols)} graph nodes; best match: {best_symbol}",
            )

        relationships = []
        if (
            symbols
            and intent in {"impact", "flow", "architecture"}
            and self._can_act(result)
        ):
            relationships = self.graph.neighbors(symbols[0]["id"], depth=3, limit=12)
            self._record_step(
                result,
                tool="trace_dependencies",
                tool_input=symbols[0]["id"],
                reason="Follow callers, definitions, and resolved imports for structural impact",
                observation=(
                    f"Found {len(relationships)} connected definitions or dependencies"
                ),
            )

        if (
            self._can_act(result)
            and self._should_expand(retrieved, symbols, intent)
        ):
            supplemental_query = self._supplemental_query(question, intent, symbols, relationships)
            if supplemental_query != question:
                supplemental_matches = self._search_and_record(
                    supplemental_query, result, "Close the remaining evidence gap"
                )
                has_exact_symbol = any(
                    node.get("match_reason") == "exact_symbol" for node in symbols
                )
                if has_exact_symbol:
                    retrieved = supplemental_matches
                else:
                    retrieved.extend(supplemental_matches)

        retrieved = self._deduplicate(retrieved)[:10]
        result.sources = [self._source(item) for item in retrieved]
        result.context = self._build_context(retrieved, symbols, relationships)
        result.confidence = self._confidence(question, retrieved, symbols, relationships)
        result.confidence_label = (
            "High" if result.confidence >= 75 else "Medium" if result.confidence >= 50 else "Low"
        )
        result.sufficient_evidence = bool(retrieved) and result.confidence >= 50
        return result

    @staticmethod
    def _can_act(result):
        """Return whether another read-only tool action fits the configured budget."""
        return len(result.trace) < max(1, config.AGENT_MAX_STEPS)

    @staticmethod
    def _record_step(
        result,
        *,
        tool: str,
        tool_input: str,
        reason: str,
        observation: str,
    ) -> None:
        """Append one explainable plan-act-observe trace entry."""
        result.trace.append(
            {
                "step": len(result.trace) + 1,
                "phase": "act-observe",
                "tool": tool,
                "input": tool_input,
                "reason": reason,
                "observation": observation,
            }
        )

    def _search_and_record(self, query, result, reason):
        """Search the code index and record the strongest observable evidence."""
        matches = self.rag.search(query, top_k=5) if self.rag_available else []
        if matches:
            best = matches[0]
            coverage = round(best.term_coverage * 100)
            observation = (
                f"Found {len(matches)} chunks; best: {best.chunk.file_path}; "
                f"query coverage: {coverage}%"
            )
        else:
            observation = "No relevant indexed code chunks found"
        self._record_step(
            result,
            tool="search_code",
            tool_input=query,
            reason=reason,
            observation=observation,
        )
        return matches

    @staticmethod
    def _classify_intent(question):
        lowered = question.lower()
        if any(
            term in lowered for term in ("impact", "break", "change", "modify", "affected", "risk")
        ):
            return "impact"
        if any(term in lowered for term in ("flow", "trace", "request", "called", "work")):
            return "flow"
        if any(term in lowered for term in ("architecture", "structure", "module", "design")):
            return "architecture"
        return "lookup"

    @staticmethod
    def _supplemental_query(question, intent, symbols, relationships):
        """Create one evidence-gap query from the best observed graph anchor."""
        exact_symbols = [
            node
            for node in symbols
            if node.get("match_reason") == "exact_symbol" and node.get("name")
        ]
        if exact_symbols:
            related_names = [
                item["node"].get("name", "")
                for item in relationships
                if item["node"].get("kind") in {"function", "class"}
                and item["node"].get("name")
            ]
            anchors = dict.fromkeys([exact_symbols[0]["name"], *related_names[:2]])
            return " ".join(anchors)
        question_terms = RAGService.tokenize(question)
        anchor = " ".join(question_terms[:4])
        suffixes = {
            "impact": "callers imports dependencies",
            "flow": "route handler service response",
            "architecture": "entrypoint service configuration",
        }
        return f"{anchor} {suffixes.get(intent, '')}".strip()

    @staticmethod
    def _should_expand(retrieved, symbols, intent):
        """Decide whether another search can materially improve the evidence."""
        structural_question = intent in {"impact", "flow", "architecture"}
        if not structural_question:
            return False
        has_exact_symbol = any(
            node.get("match_reason") == "exact_symbol" for node in symbols
        )
        if not retrieved:
            return True
        best_coverage = max(item.term_coverage for item in retrieved)
        return has_exact_symbol or best_coverage < 0.6

    @staticmethod
    def _deduplicate(results):
        best = {}
        for item in results:
            key = (item.chunk.file_path, item.chunk.start_line, item.chunk.end_line)
            if key not in best or item.score > best[key].score:
                best[key] = item
        return sorted(best.values(), key=lambda item: -item.score)

    @staticmethod
    def _source(item):
        return {
            "file_path": item.chunk.file_path,
            "start_line": item.chunk.start_line,
            "end_line": item.chunk.end_line,
            "score": round(item.score, 2),
            "relevance": item.relevance,
            "matched_terms": list(item.matched_terms),
            "term_coverage": round(item.term_coverage, 2),
        }

    @staticmethod
    def _build_context(retrieved, symbols, relationships):
        parts = []
        for item in retrieved:
            chunk = item.chunk
            parts.append(
                f"--- {chunk.file_path} (lines {chunk.start_line}-{chunk.end_line}) ---\n{chunk.content}"
            )
        if symbols:
            parts.append(
                "--- STRUCTURAL SYMBOLS ---\n"
                + "\n".join(
                    f"{node['kind']}: {node.get('name', node['id'])} in "
                    f"{node.get('path', 'repository')}:{node.get('line', 0)}"
                    for node in symbols
                )
            )
        if relationships:
            parts.append(
                "--- DEPENDENCY EVIDENCE ---\n"
                + "\n".join(
                    f"{item['edge']['source']} --{item['edge']['relation']}--> {item['edge']['target']}"
                    for item in relationships
                )
            )
        return "\n\n".join(parts)[:18000] or "No repository evidence found."

    @staticmethod
    def _confidence(question, retrieved, symbols, relationships):
        question_terms = set(RAGService.tokenize(question))
        has_exact_symbol = any(
            node.get("match_reason") == "exact_symbol" for node in symbols
        )
        if not retrieved:
            return min(35, (20 if has_exact_symbol else 0) + len(relationships))

        evidence_terms = set()
        code_sources = 0
        for item in retrieved[:5]:
            evidence_terms.update(
                RAGService.tokenize(f"{item.chunk.file_path} {item.chunk.content}")
            )
            if item.chunk.file_path.lower().endswith(tuple(CODE_EXTENSIONS)):
                code_sources += 1

        matched_question_terms = question_terms.intersection(evidence_terms)
        term_coverage = (
            len(matched_question_terms) / len(question_terms)
            if question_terms
            else 0.0
        )
        top_score = max(item.score for item in retrieved[:5])
        code_ratio = code_sources / min(5, len(retrieved))
        structural_evidence = min(1.0, len(relationships) / 6)
        confidence = round(
            35 * term_coverage
            + 25 * top_score
            + 15 * code_ratio
            + 15 * has_exact_symbol
            + 10 * structural_evidence
        )

        if code_ratio == 0:
            confidence = min(confidence, 39)
        if not has_exact_symbol and term_coverage < 0.5:
            confidence = min(confidence, 39)
        elif not has_exact_symbol and term_coverage < 0.75:
            confidence = min(confidence, 74)
        return min(100, confidence)
