"""
services/gemini_client.py
=========================
Centralized Google Gemini API client with advanced error handling,
quota management, and safety-filter trap logic.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import config
from services.ai_prompts import NARRATIVE_PROMPTS

logger = logging.getLogger("repolens.gemini")

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    APIError = Exception


@dataclass
class GeminiResponse:
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    success: bool = True
    error: str = ""


ANALYSIS_PROMPTS = {
    "architecture_summary": """You are a senior software architect. Analyze the following repository structure and key file contents, then describe the architecture.

Repository: {repo_name}
Technologies detected: {technologies}

Directory structure:
{file_tree}

Key file contents:
{file_contents}

Provide a clear, structured analysis covering:
1. **Architecture Pattern** — What pattern does this follow? (MVC, layered, microservices, monolith, etc.)
2. **Module Organization** — How is the code organized? What are the key modules?
3. **Data Flow** — How does data flow through the application?
4. **API Layer** — Where are API endpoints defined? What style? (REST, GraphQL, etc.)
5. **Authentication** — Is there an auth module? What approach?
6. **Database Layer** — What database is used? Where are models/queries?
7. **Key Strengths** — What's well-structured?
8. **Improvement Opportunities** — What could be better?

Output in Markdown. Be specific and reference actual file paths.
""",
    "onboarding_guide": """You are a senior engineer writing a comprehensive onboarding guide for a new developer joining this project.

Repository: {repo_name}
Technologies: {technologies}
Architecture: {architecture_summary}
Recent activity: {recent_activity}

Directory structure:
{file_tree}

Write a welcoming, practical onboarding guide covering:
1. **Project Overview** — What does this project do?
2. **Tech Stack** — What technologies are used and why?
3. **Getting Started** — How to set up the development environment
4. **Architecture Overview** — How the codebase is structured
5. **Key Concepts** — Important patterns and conventions used
6. **Where to Start** — Recommended first files to read
7. **Common Tasks** — How to add a feature, fix a bug, run tests

Output in Markdown. Be practical and developer-friendly.
""",
    "security_review": """You are a security engineer reviewing a codebase for potential security concerns.

Repository: {repo_name}
Technologies: {technologies}

Key file contents:
{file_contents}

Identify potential security concerns:
1. **Hardcoded Secrets** — API keys, passwords, tokens in code
2. **Input Validation** — Missing sanitization or validation
3. **Authentication Issues** — Weak auth patterns
4. **Dependency Risks** — Known vulnerable patterns
5. **Configuration Issues** — Debug mode, exposed endpoints
6. **Data Exposure** — Sensitive data in logs or responses

For each finding, provide:
- Severity (Critical/High/Medium/Low)
- File path if identifiable
- Brief explanation
- Suggested fix

Output in Markdown. Be specific but not alarmist.
""",
    "repo_qa": """You are a strictly fact-based engineering assistant. Answer the user's question about this repository using ONLY the provided code chunks.

Repository: {repo_name}
Technologies: {technologies}

Relevant code context:
{context}

User question: {question}

Instructions:
- CRITICAL: You must only answer based on the provided code context.
- CRITICAL: Do not infer behavior outside provided repository evidence. Do not hallucinate features.
- If the context lacks sufficient information to answer the question, explicitly reply: "I do not have enough context to answer this."
- Cite your sources by explicitly quoting the `file_path` and `line_numbers` provided in the context blocks.
- Provide relevant code snippets when explaining logic.
- Be concise, direct, and professional.

Output in Markdown.
""",
}


class GeminiClient:
    """Centralized Gemini API client using modern google-genai SDK."""

    def __init__(self) -> None:
        self._configured = False

        if not GEMINI_AVAILABLE:
            logger.warning("google-genai SDK not installed — Gemini features disabled")
            return

        key = config.GEMINI_API_KEY
        if not key or key.strip() == "" or key == "YOUR_GEMINI_API_KEY_HERE":
            logger.warning("GEMINI_API_KEY not configured — using local fallbacks")
            return

        try:
            self.model_name = config.GEMINI_MODEL
            self.client = genai.Client(
                api_key=key.strip(),
                http_options=types.HttpOptions(timeout=config.AI_TIMEOUT_SECONDS * 1000),
            )
            self._configured = True
            logger.info("Gemini API client initialized (model=%s)", self.model_name)
        except Exception as exc:
            logger.error("Failed to initialize Gemini client: %s", exc)

    def is_available(self) -> bool:
        return GEMINI_AVAILABLE and self._configured

    # ── Public API ────────────────────────────────────────────────────────────
    def generate_all(self, commit_data_text: str, repo_name: str = "") -> dict[str, str]:
        """Generate independent formats concurrently with deterministic fallbacks."""
        formats = ("release", "standup", "onboarding", "portfolio")
        if not self.is_available():
            logger.info("Gemini unavailable; using local narrative generation")
            return _generate_local_narratives(commit_data_text, repo_name)
        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(formats), thread_name_prefix="narrative") as pool:
            futures = {
                pool.submit(self._call_narrative, fmt, commit_data_text): fmt for fmt in formats
            }
            for future in as_completed(futures):
                fmt = futures[future]
                try:
                    response = future.result()
                except Exception as exc:
                    logger.warning("Narrative %s failed: %s", fmt, exc)
                    response = GeminiResponse(content="", success=False, error=str(exc))
                results[fmt] = (
                    response.content
                    if response.success
                    else _generate_local_narrative(fmt, commit_data_text, repo_name)
                )
        return results

    def generate_single(self, fmt: str, commit_data_text: str) -> str:
        if not self.is_available():
            return _generate_local_narrative(fmt, commit_data_text)
        resp = self._call_narrative(fmt, commit_data_text)
        return resp.content if resp.success else _generate_local_narrative(fmt, commit_data_text)

    def analyze_architecture(
        self, repo_name: str, technologies: str, file_tree: str, file_contents: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["architecture_summary"].format(
            repo_name=repo_name,
            technologies=technologies,
            file_tree=file_tree,
            file_contents=file_contents,
        )
        return self._call(
            prompt,
            "You are a senior software architect specializing in codebase analysis.",
        )

    def generate_onboarding(
        self,
        repo_name: str,
        technologies: str,
        architecture_summary: str,
        recent_activity: str,
        file_tree: str,
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["onboarding_guide"].format(
            repo_name=repo_name,
            technologies=technologies,
            architecture_summary=architecture_summary,
            recent_activity=recent_activity,
            file_tree=file_tree,
        )
        return self._call(prompt, "You are a senior engineer writing onboarding documentation.")

    def review_security(
        self, repo_name: str, technologies: str, file_contents: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["security_review"].format(
            repo_name=repo_name, technologies=technologies, file_contents=file_contents
        )
        return self._call(prompt, "You are a security engineer performing a code review.")

    def answer_question(
        self, repo_name: str, technologies: str, context: str, question: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["repo_qa"].format(
            repo_name=repo_name,
            technologies=technologies,
            context=context,
            question=question,
        )
        return self._call(prompt, "You are a strictly fact-based engineering assistant.")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_narrative(self, fmt: str, commit_data_text: str) -> GeminiResponse:
        template = NARRATIVE_PROMPTS.get(fmt, NARRATIVE_PROMPTS["release"])
        prompt = template.format(commit_data=commit_data_text)
        resp = self._call(
            prompt,
            "You are a helpful AI assistant specialized in analyzing software development history.",
        )
        return resp

    def _call(self, prompt: str, system: str = "") -> GeminiResponse:
        if not self.is_available():
            return GeminiResponse(content="", success=False, error="Gemini client not initialized")

        full_prompt = f"System: {system}\n\nUser: {prompt}" if system else prompt

        last_error = "AI request unavailable"
        for attempt in range(1, config.AI_MAX_ATTEMPTS + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt,
                )
                if not response or not hasattr(response, "text"):
                    return GeminiResponse(content="", success=False, error="Empty response body")
                text = response.text.strip()
                if "---END_COMMIT_DATA---" in text:
                    text = text.split("---END_COMMIT_DATA---")[-1].strip()
                usage = getattr(response, "usage_metadata", None)
                return GeminiResponse(
                    content=text,
                    model=self.model_name,
                    total_tokens=getattr(usage, "total_token_count", 0) or 0,
                    success=True,
                )
            except APIError as exc:
                message = str(exc).lower()
                status_code = getattr(exc, "code", None)
                last_error = str(exc)[:300]
                if (
                    status_code == 400
                    and "api key" in message
                    and ("invalid" in message or "not valid" in message)
                ):
                    # Stop calling the API for every narrative and question. A
                    # restart after correcting the secret re-enables the client.
                    self._configured = False
                    logger.error(
                        "Gemini authentication failed; switching to retrieval/local fallback"
                    )
                    return GeminiResponse(
                        content="", success=False, error="Gemini authentication unavailable"
                    )
                if status_code == 404 or "not found" in message or "not supported" in message:
                    replacement = self._find_supported_text_model()
                    if replacement and replacement != self.model_name:
                        logger.warning(
                            "Gemini model %s unavailable; retrying with %s",
                            self.model_name,
                            replacement,
                        )
                        self.model_name = replacement
                        continue
                if "safety" in message or "blocked" in message:
                    return GeminiResponse(content="", success=False, error="Safety filter refusal")
                logger.warning("AI request failed on attempt %d: %s", attempt, exc)
            except Exception as exc:
                message = str(exc).lower()
                last_error = str(exc)[:300]
                if "safety" in message or "blocked" in message:
                    return GeminiResponse(content="", success=False, error="Safety filter refusal")
                logger.warning("AI request failed on attempt %d: %s", attempt, exc)
            if attempt < config.AI_MAX_ATTEMPTS:
                time.sleep(min(2**attempt, 4))
        return GeminiResponse(content="", success=False, error=last_error)

    def _find_supported_text_model(self) -> str | None:
        """Discover a usable Flash text model when a configured model expires."""
        try:
            candidates = []
            for model in self.client.models.list():
                name = (getattr(model, "name", "") or "").split("/")[-1]
                actions = set(getattr(model, "supported_actions", None) or [])
                lower = name.lower()
                if actions and "generateContent" not in actions:
                    continue
                if "gemini" not in lower or "flash" not in lower:
                    continue
                if any(
                    token in lower
                    for token in ("image", "tts", "live", "embedding", "robotics")
                ):
                    continue
                candidates.append(name)
            return _choose_supported_model(candidates)
        except Exception as exc:
            logger.warning("Could not discover Gemini models: %s", exc)
            return None


def _generate_local_narratives(commit_data_text: str, repo_name: str = "") -> dict[str, str]:
    return {
        fmt: _generate_local_narrative(fmt, commit_data_text, repo_name)
        for fmt in ["release", "standup", "onboarding", "portfolio"]
    }


def _choose_supported_model(names: list[str]) -> str | None:
    """Prefer stable, recent Flash models while remaining future compatible."""
    if not names:
        return None
    preferred = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    available = set(names)
    for name in preferred:
        if name in available:
            return name
    stable = [name for name in names if not any(tag in name for tag in ("preview", "exp"))]
    return sorted(stable or names, reverse=True)[0]


def _generate_local_narrative(fmt: str, commit_data_text: str, repo_name: str = "") -> str:
    repo_title = repo_name or "Repository"
    weeks = _parse_commit_sections(commit_data_text)
    total_commits = sum(len(section["items"]) for section in weeks)
    type_counts = _count_commit_labels(weeks)

    if fmt == "release":
        lines = [f"# Release Notes - {repo_title}", ""]
        for section in weeks:
            lines.append(f"## {section['title']}")
            lines.append("")
            lines.extend(
                f"- **[{item['label']}]** {item['message']}" for item in section["items"][:12]
            )
            lines.append("")
        lines.extend(_summary_table(total_commits, type_counts))
        return "\n".join(lines).strip()

    if fmt == "standup":
        lines = [f"# Standup Summary - {repo_title}", ""]
        for section in weeks:
            labels = _top_labels(section["items"])
            focus = ", ".join(labels) if labels else "general maintenance"
            examples = "; ".join(item["message"] for item in section["items"][:3])
            lines.append(f"## {section['title']}")
            lines.append(f"This period focused on {focus}. Key changes included: {examples}.")
            lines.append("")
        return "\n".join(lines).strip()

    if fmt == "onboarding":
        lines = [
            f"# Project History & Onboarding Guide - {repo_title}",
            "",
            f"This project has {total_commits} analyzed commits across {len(weeks)} activity group(s).",
            "The commit history shows how the codebase evolved through features, fixes, refactors, documentation, and operational work.",
            "",
            "## Recent Evolution",
        ]
        for section in weeks:
            lines.append(
                f"- **{section['title']}**: "
                + "; ".join(item["message"] for item in section["items"][:4])
            )
        lines.extend(
            [
                "",
                "## Suggested First Read",
                "Start with the newest activity group, then review the generated architecture and risk pages for structure, hotspots, and process quality.",
            ]
        )
        return "\n".join(lines).strip()

    lines = [
        f"# {repo_title}",
        "",
        f"RepoLens AI analyzed {total_commits} commits and grouped the work into readable project activity.",
        "",
        "## Development Signals",
    ]
    for label, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- **{label}:** {count} commit(s)")
    lines.extend(
        [
            "",
            "## Recent Work",
        ]
    )
    for section in weeks:
        lines.append(
            f"- **{section['title']}**: "
            + "; ".join(item["message"] for item in section["items"][:4])
        )
    return "\n".join(lines).strip()


def _parse_commit_sections(commit_data_text: str) -> list[dict]:
    sections: list[dict] = []
    current = None
    for raw_line in commit_data_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("## "):
            title = line[3:].strip()
            current = {"title": title, "items": []}
            sections.append(current)
            continue
        if current is None:
            current = {"title": "Analyzed Commits", "items": []}
            sections.append(current)
        item = _parse_commit_item(line)
        if item:
            current["items"].append(item)
    return [section for section in sections if section["items"]] or [
        {"title": "Analyzed Commits", "items": []}
    ]


def _parse_commit_item(line: str) -> dict | None:
    if "Milestones:" in line and not line.startswith("["):
        return None
    match = re.match(r"^\[(?P<label>[^\]]+)\]\s+(?P<message>.*?)(?:\s+\(by\s+.*\))?$", line)
    if match:
        return {
            "label": match.group("label").strip(),
            "message": match.group("message").strip(),
        }
    if line.startswith("["):
        return None
    return {"label": "Change", "message": line}


def _count_commit_labels(weeks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for section in weeks:
        for item in section["items"]:
            counts[item["label"]] = counts.get(item["label"], 0) + 1
    return counts


def _top_labels(items: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    return [
        label.lower()
        for label, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]


def _summary_table(total_commits: int, type_counts: dict[str, int]) -> list[str]:
    lines = [
        "### Summary",
        "",
        f"- Total commits analyzed: **{total_commits}**",
        "",
        "| Type | Count |",
        "|------|-------|",
    ]
    for label, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {label} | {count} |")
    return lines


# ─── Global Singleton ─────────────────────────────────────────────────────────
gemini = GeminiClient()
