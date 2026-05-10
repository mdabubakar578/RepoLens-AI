"""
services/summarizer.py
========================
Orchestrates multi-prompt AI analysis combining repo_analyzer output
with grok_client for AI-powered summaries and insights.
"""
from __future__ import annotations
import logging
from services.grok_client import grok, GrokResponse
from services.repo_analyzer import RepoAnalysis, TechDetection
from services.architecture_analyzer import ArchitectureReport

logger = logging.getLogger("repolens.summarizer")

def generate_architecture_summary(
    repo_name: str, analysis: RepoAnalysis, arch_report: ArchitectureReport,
    file_tree_text: str, key_files_text: str
) -> str:
    """Generate AI-powered architecture summary."""
    tech_list = ", ".join(t.name for t in analysis.technologies[:15])
    if not grok.is_available():
        return _fallback_architecture_summary(repo_name, analysis, arch_report)
    resp = grok.analyze_architecture(
        repo_name=repo_name, technologies=tech_list,
        file_tree=file_tree_text, file_contents=key_files_text
    )
    return resp.content if resp.success else _fallback_architecture_summary(repo_name, analysis, arch_report)

def generate_onboarding_guide(
    repo_name: str, analysis: RepoAnalysis, arch_report: ArchitectureReport,
    file_tree_text: str, commit_summary: str
) -> str:
    """Generate AI-powered onboarding guide."""
    tech_list = ", ".join(t.name for t in analysis.technologies[:15])
    if not grok.is_available():
        return _fallback_onboarding(repo_name, analysis)
    resp = grok.generate_onboarding(
        repo_name=repo_name, technologies=tech_list,
        architecture_summary=arch_report.description,
        recent_activity=commit_summary, file_tree=file_tree_text
    )
    return resp.content if resp.success else _fallback_onboarding(repo_name, analysis)

def generate_security_review(repo_name: str, analysis: RepoAnalysis, key_files_text: str) -> str:
    """Generate AI security review."""
    tech_list = ", ".join(t.name for t in analysis.technologies[:15])
    if not grok.is_available():
        return "⚠️ **Demo Mode** — Add your Grok API key for security analysis."
    resp = grok.review_security(repo_name=repo_name, technologies=tech_list, file_contents=key_files_text)
    return resp.content if resp.success else "Security review unavailable."

def get_contributor_insights(commits: list[dict]) -> dict:
    """Analyze contributor productivity patterns."""
    from collections import defaultdict, Counter
    if not commits: return {"summary": "No commit data available"}
    author_commits = defaultdict(list)
    for c in commits:
        author_commits[c.get("author", "Unknown")].append(c)
    contributors = []
    for author, author_coms in sorted(author_commits.items(), key=lambda x: -len(x[1]))[:10]:
        types = Counter(c.get("commit_type", "chore") for c in author_coms)
        top_type = types.most_common(1)[0][0] if types else "chore"
        contributors.append({"name": author, "commits": len(author_coms), "primary_type": top_type,
            "types": dict(types)})
    return {"total_contributors": len(author_commits), "top_contributors": contributors,
        "summary": f"{len(author_commits)} contributors with {len(commits)} total commits"}

# ── Fallbacks ─────────────────────────────────────────────────────────────────

def _fallback_architecture_summary(repo_name: str, analysis: RepoAnalysis, arch: ArchitectureReport) -> str:
    parts = [f"# Architecture Analysis: {repo_name}\n"]
    parts.append("> ⚠️ **Demo Mode** — Add Grok API key for AI-powered analysis.\n")
    if arch.patterns:
        parts.append(f"## Architecture Pattern\n{arch.patterns[0]['description']}\n")
    if analysis.technologies:
        tech_list = "\n".join(f"- **{t.name}** ({t.category})" for t in analysis.technologies[:10])
        parts.append(f"## Technologies Detected\n{tech_list}\n")
    if arch.modules:
        mod_list = "\n".join(f"- **{m['name']}**: `{', '.join(m['directories'][:3])}`" for m in arch.modules[:8])
        parts.append(f"## Key Modules\n{mod_list}\n")
    parts.append(f"\n## Summary\n{arch.description}")
    return "\n".join(parts)

def _fallback_onboarding(repo_name: str, analysis: RepoAnalysis) -> str:
    parts = [f"# Onboarding Guide: {repo_name}\n"]
    parts.append("> ⚠️ **Demo Mode** — Add Grok API key for AI-powered onboarding.\n")
    if analysis.technologies:
        tech_list = ", ".join(t.name for t in analysis.technologies[:10])
        parts.append(f"## Tech Stack\n{tech_list}\n")
    parts.append(f"## Getting Started\n1. Clone the repository\n2. Install dependencies\n3. Run the development server\n")
    return "\n".join(parts)
