"""
services/gemini_client.py
=========================
Centralized Google Gemini API client with advanced error handling,
quota management, and safety-filter trap logic.
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass
from typing import Optional

import config
from services.grok_client import DEMO_OUTPUTS, NARRATIVE_PROMPTS

logger = logging.getLogger("repolens.gemini")

try:
    from google import genai
    from google.api_core.exceptions import ResourceExhausted, DeadlineExceeded, InvalidArgument, GoogleAPIError
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    ResourceExhausted = Exception
    DeadlineExceeded = Exception
    InvalidArgument = Exception
    GoogleAPIError = Exception


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
            logger.warning("GEMINI_API_KEY not configured — using demo mode")
            return

        try:
            self.model_name = config.GEMINI_MODEL
            self.client = genai.Client(api_key=key.strip())
            self._configured = True
            logger.info("Gemini API client initialized (model=%s)", self.model_name)
        except Exception as exc:
            logger.error("Failed to initialize Gemini client: %s", exc)

    def is_available(self) -> bool:
        return GEMINI_AVAILABLE and self._configured

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_all(self, commit_data_text: str, repo_name: str = "") -> dict[str, str]:
        if not self.is_available():
            return DEMO_OUTPUTS.copy()
        results: dict[str, str] = {}
        for fmt in ["release", "standup", "onboarding", "portfolio"]:
            resp = self._call_narrative(fmt, commit_data_text)
            results[fmt] = resp.content
        return results

    def generate_single(self, fmt: str, commit_data_text: str) -> str:
        if not self.is_available():
            return DEMO_OUTPUTS.get(fmt, "Demo output not available.")
        return self._call_narrative(fmt, commit_data_text).content

    def analyze_architecture(
        self, repo_name: str, technologies: str, file_tree: str, file_contents: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["architecture_summary"].format(
            repo_name=repo_name, technologies=technologies, file_tree=file_tree, file_contents=file_contents
        )
        return self._call(prompt, "You are a senior software architect specializing in codebase analysis.")

    def generate_onboarding(
        self, repo_name: str, technologies: str, architecture_summary: str, recent_activity: str, file_tree: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["onboarding_guide"].format(
            repo_name=repo_name, technologies=technologies, architecture_summary=architecture_summary,
            recent_activity=recent_activity, file_tree=file_tree
        )
        return self._call(prompt, "You are a senior engineer writing onboarding documentation.")

    def review_security(self, repo_name: str, technologies: str, file_contents: str) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["security_review"].format(
            repo_name=repo_name, technologies=technologies, file_contents=file_contents
        )
        return self._call(prompt, "You are a security engineer performing a code review.")

    def answer_question(
        self, repo_name: str, technologies: str, context: str, question: str
    ) -> GeminiResponse:
        prompt = ANALYSIS_PROMPTS["repo_qa"].format(
            repo_name=repo_name, technologies=technologies, context=context, question=question
        )
        return self._call(prompt, "You are a strictly fact-based engineering assistant.")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_narrative(self, fmt: str, commit_data_text: str) -> GeminiResponse:
        template = NARRATIVE_PROMPTS.get(fmt, NARRATIVE_PROMPTS["release"])
        prompt = template.format(commit_data=commit_data_text)
        resp = self._call(prompt, "You are a helpful AI assistant specialized in analyzing software development history.")
        if not resp.success:
            resp.content = DEMO_OUTPUTS.get(fmt, f"Error generating {fmt}.")
        return resp

    def _call(self, prompt: str, system: str = "") -> GeminiResponse:
        if not self.is_available():
            return GeminiResponse(content="", success=False, error="Gemini client not initialized")

        full_prompt = f"System: {system}\n\nUser: {prompt}" if system else prompt

        import concurrent.futures
        for attempt in range(1, 4):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=full_prompt
                    )
                    response = future.result(timeout=60)

                if not response or not hasattr(response, 'text'):
                    logger.warning("Empty or malformed response body from Gemini.")
                    return GeminiResponse(content="", success=False, error="Empty response body")

                text = response.text.strip()
                if "---END_COMMIT_DATA---" in text:
                    text = text.split("---END_COMMIT_DATA---")[-1].strip()

                total_tokens = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    total_tokens = response.usage_metadata.total_token_count

                return GeminiResponse(
                    content=text,
                    model=self.model_name,
                    total_tokens=total_tokens,
                    success=True
                )

            except concurrent.futures.TimeoutError:
                logger.error("Gemini call timed out (attempt %d/3)", attempt)
                if attempt == 3:
                    return GeminiResponse(content="", success=False, error="API timeout")
                time.sleep(2)
            except ResourceExhausted as exc:
                wait = min(2 ** attempt * 2, 30)
                logger.warning("Gemini Rate limited/Quota Exhausted (attempt %d/3): %s", attempt, exc)
                time.sleep(wait)
            except Exception as exc:
                # Catch-all for safety filters or other API errors
                err_msg = str(exc).lower()
                if "safety" in err_msg or "blocked" in err_msg:
                    logger.warning("Gemini Safety Filter Refusal: %s", exc)
                    return GeminiResponse(content="", success=False, error="Safety filter refusal")
                
                logger.error("Unexpected error in Gemini call (attempt %d/3): %s", attempt, exc)
                if attempt < 3:
                    time.sleep(2 ** attempt)

        return GeminiResponse(content="", success=False, error="API calls exhausted")

# ─── Global Singleton ─────────────────────────────────────────────────────────
gemini = GeminiClient()
grok = gemini  # Alias for compatibility
