#!/usr/bin/env python3
"""
cli.py — RepoLens AI Command Line Interface
Usage:
  python cli.py .               # Analyze current directory
  python cli.py /path/to/repo   # Analyze specific directory
"""

import argparse
import os
import sys

try:
    import git

    import config
    from services.architecture_analyzer import analyze_architecture
    from services.commit_classifier import group_commits, serialize_groups_for_prompt
    from services.gemini_client import gemini
    from services.github_service import (
        _extract_from_repo,  # Note: Using private function as no public wrapper exists
    )
    from services.repo_analyzer import analyze_repository
except ImportError as e:
    print(f"Error: Missing dependencies. Run 'pip install -r requirements.txt'. Detail: {e}")
    sys.exit(1)


def _get_local_file_tree(repo_path: str) -> list[dict]:
    """Walk a working copy using the same skip rules as remote repository analysis."""
    tree = []
    for root, dirs, files in os.walk(repo_path):
        # Prune in place so vendored trees (.venv, node_modules) are never descended into.
        dirs[:] = [d for d in dirs if d not in config.SKIP_DIRECTORIES]
        for file in files:
            if os.path.splitext(file)[1].lower() in config.SKIP_EXTENSIONS:
                continue
            full_path = os.path.join(root, file)
            path = os.path.relpath(full_path, repo_path).replace("\\", "/")
            try:
                size = os.path.getsize(full_path)
                tree.append({"path": path, "type": "blob", "size": size})
            except Exception:
                pass
            if len(tree) >= config.MAX_REPO_FILES:
                return tree
    return tree


def _get_local_file_contents(repo_path: str, file_tree: list[dict]) -> dict[str, str]:
    contents = {}
    for item in file_tree:
        try:
            with open(os.path.join(repo_path, item["path"]), encoding="utf-8") as f:
                contents[item["path"]] = f.read(config.MAX_FILE_SCAN_SIZE)
        except Exception:
            pass
    return contents


def _analyze_local_repository(repo_path: str, commits: list[dict]):
    """Build the file tree, contents, and deterministic analysis for a working copy."""
    file_tree = _get_local_file_tree(repo_path)
    file_contents = _get_local_file_contents(repo_path, file_tree)
    return file_tree, file_contents, analyze_repository(file_tree, file_contents, commits)


def handle_narrative(args, repo_path, commits):
    if not commits:
        print("No commits found in the repository.")
        return

    print(f"✅ Found {len(commits)} commits. Classifying and grouping...")
    groups = group_commits(commits)
    commit_data_text = serialize_groups_for_prompt(groups)

    if not gemini.is_available():
        print("\n⚠️  Gemini API key not configured. Showing demo output...")

    print(f"🤖 Generating {args.format if not args.all else 'all'} narrative(s) via Gemini AI...")

    if args.all:
        results = gemini.generate_all(commit_data_text, os.path.basename(repo_path))
        for fmt, text in results.items():
            print(f"\n{'=' * 60}\n# {fmt.upper()} NARRATIVE\n{'=' * 60}")
            print(text)
    else:
        result = gemini.generate_single(args.format, commit_data_text)
        print(f"\n{'=' * 60}")
        print(result)
        print(f"{'=' * 60}")

    print("\n✨ Analysis complete!")


def handle_architecture(repo_path, commits):
    file_tree, file_contents, repo_analysis = _analyze_local_repository(repo_path, commits)

    print("\n🏗️  Analyzing Architecture...")
    arch_report = analyze_architecture(file_tree, file_contents)

    languages = ", ".join(
        f"{name} ({share}%)" for name, share in list(repo_analysis.language_stats.items())[:5]
    )
    print(f"\nLanguages: {languages or 'Not detected'}")

    def _top(*categories, limit=5):
        # technologies are pre-sorted by confidence; keep the strongest signals only
        return [t for t in repo_analysis.technologies if t.category in categories][:limit]

    def _format(items):
        return ", ".join(f"{t.name} ({t.confidence:.2f})" for t in items) or "None detected"

    print("Frameworks: " + _format(_top("frontend", "backend", "runtime")))
    print("Databases: " + _format(_top("database")))
    print("Cloud/DevOps: " + _format(_top("devops")))

    if arch_report.patterns:
        print("\nArchitecture Patterns:")
        for pattern in arch_report.patterns[:5]:
            print(f"- {pattern['name']} (confidence {pattern['confidence']:.2f})")
    if arch_report.modules:
        print("\nModules: " + ", ".join(m["name"] for m in arch_report.modules))
    if arch_report.api_endpoints:
        print(f"\nAPI Endpoints ({len(arch_report.api_endpoints)}):")
        for endpoint in arch_report.api_endpoints[:10]:
            print(f"- {endpoint}")

    print("\nDirectory Summary:")
    print(repo_analysis.directory_summary or "No summary available.")

    print("\n✨ Architecture analysis complete!")


def handle_risk(repo_path, commits):
    _, _, repo_analysis = _analyze_local_repository(repo_path, commits)

    print("\n🚨 Analyzing Risks...")
    for risk in repo_analysis.risk_items:
        print(f"- [{risk['severity']}] {risk['title']}: {risk['description']}")
    if not repo_analysis.risk_items:
        print("No significant risks detected.")

    if repo_analysis.hotspots:
        print("\n🔥 Churn Hotspots:")
        for hotspot in repo_analysis.hotspots[:10]:
            print(
                f"- {hotspot['file']}: {hotspot['mentions']} changes, "
                f"{hotspot['authors']} author(s) ({hotspot['risk']})"
            )

    quality = repo_analysis.commit_quality
    if quality:
        print(f"\n📝 Commit Quality: {quality['score']}/100 (grade {quality['grade']})")

    print("\n📊 Computing Complexity...")
    complexity = repo_analysis.complexity_metrics
    print(f"Complexity Score: {complexity['complexity_score']}/100 ({complexity['complexity_label']})")
    print(f"Files Scanned: {complexity['file_count']}")
    print(f"Max Directory Depth: {complexity['max_directory_depth']}")
    for name, value in complexity["breakdown"].items():
        print(f"  - {name}: {value}")

    print("\n✨ Risk analysis complete!")


def main():
    parser = argparse.ArgumentParser(
        description="RepoLens AI — Transform Git history into meaningful narratives."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Narrative subcommand
    narrative_parser = subparsers.add_parser(
        "narrative", help="Generate meaningful narratives from Git history (default)"
    )
    narrative_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to the git repository (default: current directory)",
    )
    narrative_parser.add_argument(
        "--format",
        choices=["release", "standup", "onboarding", "portfolio"],
        default="release",
        help="Narrative format (default: release)",
    )
    narrative_parser.add_argument(
        "--all", action="store_true", help="Generate all narrative formats"
    )

    # Architecture subcommand
    arch_parser = subparsers.add_parser("architecture", help="Run architecture analysis")
    arch_parser.add_argument(
        "path", nargs="?", default=".", help="Path to the git repository"
    )

    # Risk subcommand
    risk_parser = subparsers.add_parser("risk", help="Run risk detection and complexity scoring")
    risk_parser.add_argument(
        "path", nargs="?", default=".", help="Path to the git repository"
    )

    # Backward compatibility: default to 'narrative' if no subcommand provided
    if len(sys.argv) == 1:
        sys.argv.append("narrative")
    elif (
        len(sys.argv) > 1
        and sys.argv[1] not in ["narrative", "architecture", "risk", "-h", "--help"]
        and not sys.argv[1].startswith("-")
    ):
        sys.argv.insert(1, "narrative")

    args = parser.parse_args()

    if not args.command:
        args.command = "narrative"

    repo_path = os.path.abspath(args.path)
    if not os.path.exists(os.path.join(repo_path, ".git")):
        print(f"Error: '{repo_path}' is not a git repository.")
        sys.exit(1)

    print(f"🔍 Analyzing repository: {os.path.basename(repo_path)}...")

    try:
        repo = git.Repo(repo_path)
        commits = _extract_from_repo(repo)

        if args.command == "narrative":
            handle_narrative(args, repo_path, commits)
        elif args.command == "architecture":
            handle_architecture(repo_path, commits)
        elif args.command == "risk":
            handle_risk(repo_path, commits)

    except Exception as e:
        print(f"Error during analysis: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
