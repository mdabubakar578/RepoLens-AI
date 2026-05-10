# RepoLens AI 🔍

AI-powered GitHub repository analysis and insights.

RepoLens AI is a web-based developer productivity tool designed to transform unstructured Git commit data into structured project stories, release notes, and contribution insights using AI.

## ✨ Features

- **Automated Commit Extraction**: Fetch commits directly from GitHub URLs or upload/paste git logs.
  - To generate a compatible log file, run:
    `git log --pretty=format:"%H|%s|%an|%ae|%ad|%D" --date=iso > my-log.txt`
- **AI-Driven Analysis**: Intelligently group commits into features, bug fixes, refactors, and more.
- **Commit Context Understanding**: Raw messages like "Added login" are converted into professional, achievement-oriented summaries using Grok AI.
- **Team Velocity Timeline**: Visual bar charts showing commit frequency and work mix over time (powered by Chart.js).
- **Shareable Story Cards**: Generate beautiful, embeddable HTML cards summarizing your repository's journey.
- **CLI Tool**: Run analysis directly from your terminal with `python cli.py .`.
- **Noisy Commit Filter**: Auto-detect and collapse "noise" commits (fix, wip, asdf) with a single toggle.
- **Narrative Generation**:
  - 📝 **Release Notes**: Professional summaries for users.
  - 🤝 **Standup Reports**: Team-focused weekly updates.
  - 🚀 **Onboarding Guides**: Chronological project evolution stories.
  - 💼 **Portfolio READMEs**: Impressive project descriptions for developers.
- **Contributor Insights**: Visualize top contributors, work mix, and project milestones.
- **Shareable Results**: Generate public links to share your project's story.

## 🛠️ Architecture

The application follows a simplified, developer-friendly architecture:
- **Single-File Logic**: Each page is implemented in a single Python file containing its routing and logic.
- **Centralized Config**: All global settings, API keys, and database paths are managed in `config.py`.
- **Lightweight Backend**: Powered by Flask and SQLite for zero-config setup.
- **Modern UI**: Dark-themed, mobile-responsive glassmorphism design.

##  Project Structure

- `app.py`: Application entry point and factory.
- `config.py`: Central configuration file.
- `pages/`: Page-specific logic and routing.
- `components/`: UI templates and reusable fragments.
- `static/`: Global styles and assets.
- `services/`: Core business logic (AI, Git, Exporting).
- `data/`: Local SQLite database storage.

---
Built with ❤️ for developers who value clear documentation and project understanding.
