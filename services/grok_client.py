"""
services/grok_client.py
========================
Centralized xAI Grok API client with retry logic, rate limiting,
timeout handling, token usage logging, and fallback responses.

Uses the OpenAI-compatible SDK pointed at https://api.x.ai/v1.
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import config

logger = logging.getLogger("repolens.grok")

try:
    from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError
    GROK_AVAILABLE = True
except ImportError:
    GROK_AVAILABLE = False
    APIError = Exception
    RateLimitError = Exception
    APITimeoutError = Exception
    APIConnectionError = Exception


# ─── Response Model ───────────────────────────────────────────────────────────

@dataclass
class GrokResponse:
    """Structured response from the Grok API."""
    content: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    success: bool = True
    error: str = ""


# ─── Prompt Templates ────────────────────────────────────────────────────────

NARRATIVE_PROMPTS = {
    "release": """You are a technical writer. Based ONLY on the commit data below, write professional Release Notes in Markdown.

Instructions:
- **Commit Context Understanding**: Instead of raw messages like "Added login", convert them into descriptive, professional summaries like "Implemented user authentication system".
- Group by week/sprint with `## Week of ...` headings
- Use bullet points: `- **[Type]** Short, clear description`
- Include a `### 🏷️ Milestones` section if any version tags exist
- End with a `### 📊 Summary` with commit counts by type
- Do NOT invent features not mentioned in commits
- Be concise and professional

---START_COMMIT_DATA---
{commit_data}
---END_COMMIT_DATA---

Output only the Markdown release notes. Start with `# Release Notes`.
""",

    "standup": """You are a team lead writing a weekly standup report. Based ONLY on the commit data below, write a clear standup summary.

Instructions:
- **Commit Context Understanding**: Convert fragmented commit messages into meaningful narratives.
- One paragraph per week: "This week the team..."
- Mention key features shipped, bugs fixed, and any milestones
- Use active voice and team-friendly language
- Keep each weekly paragraph to 3-5 sentences
- Do NOT invent work not shown in commits

---START_COMMIT_DATA---
{commit_data}
---END_COMMIT_DATA---

Output only the standup summary in Markdown. Start with `# Standup Summary`.
""",

    "onboarding": """You are a senior engineer writing an onboarding guide for a new team member. Based ONLY on the commit history below, tell the story of how this project evolved.

Instructions:
- Start with an introduction paragraph about the project
- Tell the story chronologically
- Explain what each major phase accomplished
- Highlight key architectural decisions visible from commits
- End with a "Current State" paragraph
- Be welcoming and educational

---START_COMMIT_DATA---
{commit_data}
---END_COMMIT_DATA---

Output only the onboarding story in Markdown. Start with `# Project History & Onboarding Guide`.
""",

    "portfolio": """You are a developer writing a professional portfolio README for this project. Based ONLY on the commit data below, write a compelling project description.

Instructions:
- `# Project Name` heading (infer from commit context)
- A 2-3 sentence project description
- `## ✨ Features` — bullet list of key features implemented
- `## 🛠️ Tech Signals` — infer technologies from commit messages
- `## 📈 Development Stats` — commit counts, active weeks, milestones
- `## 🏗️ Development Journey` — brief narrative
- Professional, impressive tone suitable for a portfolio

---START_COMMIT_DATA---
{commit_data}
---END_COMMIT_DATA---

Output only the portfolio README in Markdown. Start with `# ` followed by the project name.
""",
}

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

    "repo_qa": """You are an expert software engineer who deeply understands codebases. Answer the user's question about this repository using ONLY the provided context.

Repository: {repo_name}
Technologies: {technologies}

Relevant code context:
{context}

User question: {question}

Instructions:
- Answer based ONLY on the provided context
- Reference specific file paths and line numbers when possible
- Include relevant code snippets in your answer
- If the context doesn't contain enough information, say so clearly
- Be concise but thorough

Output in Markdown.
""",
}

DEMO_OUTPUTS = {
    "release": """# Release Notes

> ⚠️ **Demo Mode** — Add your Grok API key in `.env` for real AI output.

## Week of Apr 01, 2024 (3 commits)

- **[Feature]** Added user authentication with JWT tokens
- **[Feature]** Implemented dashboard homepage
- **[Bug Fix]** Fixed login redirect loop on mobile browsers

### 📊 Summary
| Type | Count |
|------|-------|
| Feature | 2 |
| Bug Fix | 1 |
""",
    "standup": """# Standup Summary

> ⚠️ **Demo Mode** — Add your Grok API key in `.env` for real AI output.

This week the team made significant progress. We shipped the user authentication system including JWT token support, and built out the main dashboard. We also resolved a critical bug affecting mobile users where the login page was caught in a redirect loop.
""",
    "onboarding": """# Project History & Onboarding Guide

> ⚠️ **Demo Mode** — Add your Grok API key in `.env` for real AI output.

Welcome to the team! The project began with foundational scaffolding and setup. Over the following weeks, the team built out core features including authentication, the main UI, and key business logic.

**Current State**: The project is actively developed with regular commits across features, bug fixes, and maintenance tasks.
""",
    "portfolio": """# My Project

> ⚠️ **Demo Mode** — Add your Grok API key in `.env` for real AI output.

A full-stack web application built from the ground up with modern development practices.

## ✨ Features
- User authentication and authorization
- Interactive dashboard with real-time data
- Mobile-responsive design

## 📈 Development Stats
- Multiple active development weeks
- Commits across features, bug fixes, and infrastructure
""",
}


# ─── Client ──────────────────────────────────────────────────────────────────

class GrokClient:
    """Centralized Grok API client with retry, timeout, and rate limit handling."""

    def __init__(self) -> None:
        self._client: Optional[OpenAI] = None
        self._configured = False

        if not GROK_AVAILABLE:
            logger.warning("OpenAI SDK not installed — Grok features disabled")
            return

        key = os.environ.get("XAI_API_KEY") or config.XAI_API_KEY
        if not key or key.strip() == "" or key == "YOUR_XAI_API_KEY_HERE":
            logger.warning("XAI_API_KEY not configured — using demo mode")
            return

        try:
            self._client = OpenAI(
                api_key=key.strip(),
                base_url=config.GROK_BASE_URL,
                timeout=config.GROK_TIMEOUT_SECONDS,
                max_retries=0,  # We handle retries ourselves
            )
            self._configured = True
            logger.info("Grok API client initialized (model=%s)", config.GROK_MODEL)
        except Exception as exc:
            logger.error("Failed to initialize Grok client: %s", exc)

    def is_available(self) -> bool:
        return GROK_AVAILABLE and self._configured and self._client is not None

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_all(self, commit_data_text: str, repo_name: str = "") -> dict[str, str]:
        """Generate all 4 narrative formats. Returns dict keyed by format name."""
        if not self.is_available():
            return DEMO_OUTPUTS.copy()

        results: dict[str, str] = {}
        for fmt in ["release", "standup", "onboarding", "portfolio"]:
            resp = self._call_narrative(fmt, commit_data_text)
            results[fmt] = resp.content
        return results

    def generate_single(self, fmt: str, commit_data_text: str) -> str:
        """Generate one narrative format."""
        if not self.is_available():
            return DEMO_OUTPUTS.get(fmt, "Demo output not available.")
        return self._call_narrative(fmt, commit_data_text).content

    def analyze_architecture(
        self, repo_name: str, technologies: str, file_tree: str, file_contents: str
    ) -> GrokResponse:
        """Generate architecture analysis from repo structure."""
        prompt = ANALYSIS_PROMPTS["architecture_summary"].format(
            repo_name=repo_name,
            technologies=technologies,
            file_tree=file_tree,
            file_contents=file_contents,
        )
        return self._call(
            prompt=prompt,
            system="You are a senior software architect specializing in codebase analysis.",
            max_tokens=config.GROK_MAX_TOKENS,
        )

    def generate_onboarding(
        self,
        repo_name: str,
        technologies: str,
        architecture_summary: str,
        recent_activity: str,
        file_tree: str,
    ) -> GrokResponse:
        """Generate comprehensive onboarding guide."""
        prompt = ANALYSIS_PROMPTS["onboarding_guide"].format(
            repo_name=repo_name,
            technologies=technologies,
            architecture_summary=architecture_summary,
            recent_activity=recent_activity,
            file_tree=file_tree,
        )
        return self._call(
            prompt=prompt,
            system="You are a senior engineer writing onboarding documentation.",
            max_tokens=config.GROK_MAX_TOKENS,
        )

    def review_security(
        self, repo_name: str, technologies: str, file_contents: str
    ) -> GrokResponse:
        """Run security smell detection on code."""
        prompt = ANALYSIS_PROMPTS["security_review"].format(
            repo_name=repo_name,
            technologies=technologies,
            file_contents=file_contents,
        )
        return self._call(
            prompt=prompt,
            system="You are a security engineer performing a code review.",
            max_tokens=config.GROK_MAX_TOKENS,
        )

    def answer_question(
        self, repo_name: str, technologies: str, context: str, question: str
    ) -> GrokResponse:
        """Answer a question about the repository using provided context."""
        prompt = ANALYSIS_PROMPTS["repo_qa"].format(
            repo_name=repo_name,
            technologies=technologies,
            context=context,
            question=question,
        )
        return self._call(
            prompt=prompt,
            system="You are an expert software engineer answering questions about codebases.",
            max_tokens=config.GROK_MAX_TOKENS,
        )

    def embed_text(self, text: str) -> list[float] | None:
        """Get embeddings from Grok API (if supported). Returns None on failure."""
        if not self.is_available() or not self._client:
            return None
        try:
            response = self._client.embeddings.create(
                model="v1", input=text
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.debug("Embedding not available via Grok API: %s", exc)
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_narrative(self, fmt: str, commit_data_text: str) -> GrokResponse:
        """Generate a single narrative format with fallback."""
        template = NARRATIVE_PROMPTS.get(fmt, NARRATIVE_PROMPTS["release"])
        prompt = template.format(commit_data=commit_data_text)
        resp = self._call(
            prompt=prompt,
            system="You are a helpful AI assistant specialized in analyzing software development history and generating clear, professional documentation.",
        )
        if not resp.success:
            resp.content = DEMO_OUTPUTS.get(fmt, f"Error generating {fmt}.")
        return resp

    def _call(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> GrokResponse:
        """Core API call with retry, timeout, and rate limit handling."""
        if not self._client:
            return GrokResponse(
                content="", success=False, error="Grok client not initialized"
            )

        last_error = ""
        for attempt in range(1, config.GROK_MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(
                    model=config.GROK_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                if not response or not response.choices:
                    last_error = "Empty response from Grok API"
                    continue

                text = response.choices[0].message.content or ""
                text = text.strip()

                # Strip any leaked prompt data
                if "---END_COMMIT_DATA---" in text:
                    text = text.split("---END_COMMIT_DATA---")[-1].strip()

                # Log token usage
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
                total_tokens = usage.total_tokens if usage else 0
                logger.info(
                    "Grok API: %d+%d=%d tokens (attempt %d)",
                    prompt_tokens, completion_tokens, total_tokens, attempt,
                )

                return GrokResponse(
                    content=text,
                    model=config.GROK_MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    success=True,
                )

            except RateLimitError as exc:
                wait = min(2 ** attempt * 2, 30)
                logger.warning("Rate limited (attempt %d/%d), waiting %ds: %s",
                               attempt, config.GROK_MAX_RETRIES, wait, exc)
                last_error = f"Rate limited: {exc}"
                time.sleep(wait)

            except APITimeoutError as exc:
                logger.warning("Timeout (attempt %d/%d): %s",
                               attempt, config.GROK_MAX_RETRIES, exc)
                last_error = f"Timeout: {exc}"
                time.sleep(2)

            except APIConnectionError as exc:
                logger.warning("Connection error (attempt %d/%d): %s",
                               attempt, config.GROK_MAX_RETRIES, exc)
                last_error = f"Connection error: {exc}"
                time.sleep(2)

            except APIError as exc:
                logger.error("API error (attempt %d/%d): %s",
                             attempt, config.GROK_MAX_RETRIES, exc)
                last_error = f"API error: {exc}"
                if attempt < config.GROK_MAX_RETRIES:
                    time.sleep(2 ** attempt)

            except Exception as exc:
                logger.error("Unexpected error in Grok call: %s", exc)
                last_error = str(exc)
                break

        logger.error("All %d Grok API attempts failed: %s",
                     config.GROK_MAX_RETRIES, last_error)
        return GrokResponse(content="", success=False, error=last_error)


# ─── Global Singleton ─────────────────────────────────────────────────────────
grok = GrokClient()

# Backward compatibility alias (used by existing pages/home.py import)
gemini = grok
