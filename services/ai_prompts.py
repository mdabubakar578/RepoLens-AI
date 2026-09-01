"""Provider-neutral prompt templates for AI narrative generation.

One template per configured narrative format. The retired standup and
portfolio templates restated release content in another voice.
"""

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
}
