"""Provider-neutral prompt templates for AI narrative generation."""

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
