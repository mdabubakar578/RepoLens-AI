from datetime import UTC, datetime

import config
from pages.analyze import _markdown_to_html, _prepare_chart_data
from services.architecture_analyzer import analyze_architecture
from services.cache_service import clear_all, get_cached, invalidate, set_cached
from services.commit_classifier import (
    build_contribution_insights,
    group_commits,
    serialize_groups_for_prompt,
)
from services.exporter import get_filename, to_markdown, to_text
from services.gemini_client import _generate_local_narratives
from services.github_service import (
    extract_owner_repo,
    extract_repo_name,
    parse_from_file,
    parse_from_text,
)
from services.repo_analyzer import (
    analyze_repository,
    compute_language_stats,
    detect_entry_points,
    parse_dependencies,
)
from services.serialization import load_json_list


def _sample_commits():
    return [
        {
            "hash": "abc12345",
            "message": "feat: add authentication",
            "author": "Asha",
            "date": datetime(2026, 8, 10, tzinfo=UTC),
            "tags": ["v1.0.0"],
            "changed_files": ["services/auth.py"],
            "is_noisy": False,
        },
        {
            "hash": "def67890",
            "message": "fix login crash",
            "author": "Ravi",
            "date": datetime(2026, 8, 11, tzinfo=UTC),
            "tags": [],
            "changed_files": ["services/auth.py"],
            "is_noisy": False,
        },
        {
            "hash": "12345678",
            "message": "docs: update README",
            "author": "Asha",
            "date": None,
            "tags": [],
            "changed_files": ["README.md"],
            "is_noisy": False,
        },
    ]


def test_commit_grouping_milestones_and_insights():
    commits = _sample_commits()
    groups = group_commits(commits)
    insights = build_contribution_insights(commits, groups)

    assert groups[0]["commit_count"] == 2
    assert groups[0]["milestones"][0]["tag"] == "v1.0.0"
    assert groups[-1]["week_key"] == "undated"
    assert insights["contributors_count"] == 2
    assert insights["top_contributors"][0]["name"] == "Asha"
    assert "[Feature]" in serialize_groups_for_prompt(groups)


def test_repository_analysis_combines_deterministic_signals():
    file_tree = [
        {"type": "blob", "path": "app.py", "size": 2000},
        {"type": "blob", "path": "services/auth.py", "size": 3000},
        {"type": "blob", "path": "requirements.txt", "size": 100},
        {"type": "blob", "path": "Dockerfile", "size": 200},
    ]
    file_contents = {
        "app.py": 'from flask import Flask\napp = Flask(__name__)\n# TODO: add rate limit',
        "services/auth.py": '"""Authentication service."""\nimport sqlite3',
        "requirements.txt": "flask>=3.1\ngitpython==3.1.50\n",
        "Dockerfile": "FROM python:3.12-slim",
    }

    analysis = analyze_repository(file_tree, file_contents, _sample_commits())

    technologies = {item.name for item in analysis.technologies}
    assert {"Flask", "Python", "SQLite", "Docker"} <= technologies
    assert analysis.dependencies["python"] == ["flask", "gitpython"]
    assert analysis.todos[0]["type"] == "TODO"
    assert analysis.entry_points == ["app.py"]
    assert analysis.hotspots[0]["file"] == "services/auth.py"
    assert 0 <= analysis.complexity_metrics["complexity_score"] <= 100


def test_dependency_parsers_cover_common_ecosystems():
    dependencies = parse_dependencies(
        {
            "requirements.txt": "flask==3.1\n# comment\npytest>=8\n",
            "package.json": '{"dependencies":{"react":"1"},"devDependencies":{"vite":"1"}}',
            "Cargo.toml": '[dependencies]\nserde = "1"\n',
            "go.mod": "require (\n  github.com/gin-gonic/gin v1.9.0\n)\n",
        }
    )

    assert dependencies["python"] == ["flask", "pytest"]
    assert set(dependencies["npm"]) == {"react", "vite"}
    assert "serde" in dependencies["cargo"]
    assert "github.com/gin-gonic/gin" in dependencies["go"]


def test_language_stats_support_github_bytes_and_extensions():
    tree = [
        {"type": "blob", "path": "main.py"},
        {"type": "blob", "path": "web/app.js"},
        {"type": "blob", "path": "README.md"},
    ]

    assert compute_language_stats(tree, {"Python": 75, "JavaScript": 25}) == {
        "Python": 75.0,
        "JavaScript": 25.0,
    }
    assert compute_language_stats(tree) == {"Python": 50.0, "JavaScript": 50.0}


def test_entry_point_detection_is_case_insensitive():
    tree = [
        {"type": "blob", "path": "src/main.py"},
        {"type": "blob", "path": "web/index.js"},
        {"type": "blob", "path": "notes.txt"},
    ]

    assert detect_entry_points(tree) == ["src/main.py", "web/index.js"]


def test_architecture_analysis_detects_pattern_modules_and_routes():
    tree = [
        {"type": "blob", "path": "routes/auth.py"},
        {"type": "blob", "path": "services/auth_service.py"},
        {"type": "blob", "path": "repositories/users.py"},
    ]
    report = analyze_architecture(
        tree,
        {"routes/auth.py": '@bp.post("/login")\ndef login():\n    return service.login()'},
    )

    assert report.patterns[0]["name"] == "Layered"
    assert "POST /login" in report.api_endpoints
    assert any(module["name"] == "API Layer" for module in report.modules)
    assert "Layered" in report.description


def test_cache_round_trip_invalidation_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path))

    set_cached("owner/repo", {"value": 7}, "_meta")
    assert get_cached("owner/repo", "_meta") == {"value": 7}

    invalidate("owner/repo")
    assert get_cached("owner/repo", "_meta") is None

    set_cached("first", 1)
    set_cached("second", 2)
    assert clear_all() == 2


def test_expired_cache_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CACHE_TTL_SECONDS", -1)
    set_cached("expired", {"value": 1})

    assert get_cached("expired") is None
    assert list(tmp_path.iterdir()) == []


def test_exporters_create_safe_downloads():
    markdown = to_markdown("# Result", "owner/repo", "release").decode()
    text = to_text("# Result\n- **Done**", "owner/repo", "release").decode()
    filename = get_filename("Owner/My Repo", "release", "md")

    assert "Generated by RepoLens AI" in markdown
    assert "• Done" in text
    assert filename.startswith("repolens-ai_owner-my_repo_release_")
    assert filename.endswith(".md")


def test_safe_json_list_deserialization():
    assert load_json_list('[{"id": 1}]') == [{"id": 1}]
    assert load_json_list('{"id": 1}') == []
    assert load_json_list("not-json") == []
    assert load_json_list(None) == []


def test_git_log_parsers_support_oneline_full_and_multiline():
    oneline = parse_from_text("abcdef1 feat: add login")
    full = parse_from_file(
        "abcdef123456|fix auth|Asha|asha@example.com|2026-08-10T10:00:00Z|tag: v1.2.0"
    )
    multiline = parse_from_text(
        "commit abcdef123456789\n"
        "Author: Asha <asha@example.com>\n"
        "Date: 2026-08-10\n\n"
        "    Implement repository analysis\n"
    )

    assert oneline[0]["message"] == "feat: add login"
    assert full[0]["tags"] == ["v1.2.0"]
    assert full[0]["date"] is not None
    assert multiline[0]["author"] == "Asha"
    assert multiline[0]["message"] == "Implement repository analysis"


def test_repository_name_and_owner_extraction():
    url = "https://github.com/openai/example.git"

    assert extract_repo_name(url) == "openai/example"
    assert extract_owner_repo(url) == ("openai", "example")


def test_local_narratives_cover_every_output_format():
    commit_text = (
        "## Week of Aug 10, 2026 (2 commits)\n"
        "[Feature] Add login (by Asha)\n"
        "[Bug Fix] Fix redirect (by Ravi)\n"
    )

    narratives = _generate_local_narratives(commit_text, "owner/repo")

    assert set(narratives) == {"release", "standup", "onboarding", "portfolio"}
    assert all("owner/repo" in narrative for narrative in narratives.values())
    assert "Total commits analyzed: **2**" in narratives["release"]


def test_chart_data_and_markdown_rendering():
    groups = [
        {
            "week_key": "2026-W32",
            "label": "Week of Aug 03, 2026",
            "commit_count": 4,
            "type_counts": {"feature": 2, "bugfix": 1, "hotfix": 0},
        }
    ]

    chart = _prepare_chart_data(groups)
    rendered = _markdown_to_html(
        "# Heading\n\n- first\n- second\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    )

    assert chart["features"] == [2]
    assert chart["fixes"] == [1]
    assert chart["others"] == [1]
    assert "<h1>Heading</h1>" in rendered
    assert "<ul>" in rendered
    assert "<table>" in rendered


def test_markdown_escapes_untrusted_code_spans():
    """Commit messages are untrusted, so code spans must never emit live markup."""
    rendered = _markdown_to_html("Fixed `<img src=x onerror=alert(1)>` in the parser.")

    assert "<img" not in rendered
    assert "<code>&lt;img src=x onerror=alert(1)&gt;</code>" in rendered


def test_markdown_renders_fenced_code_blocks():
    rendered = _markdown_to_html('# Title\n\n```python\nprint("hi" if 1 < 2 else "<b>")\n```')

    assert "__CODE_BLOCK_" not in rendered
    assert "<pre><code>" in rendered
    assert "&lt;b&gt;" in rendered
    assert "<b>" not in rendered
