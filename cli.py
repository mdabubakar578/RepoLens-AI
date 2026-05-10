#!/usr/bin/env python3
"""
cli.py — RepoLens AI Command Line Interface
Usage:
  python cli.py .               # Analyze current directory
  python cli.py /path/to/repo   # Analyze specific directory
"""

import sys
import os
import argparse
import time

# Add current directory to path so we can import services
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import git
    from services.git_parser import _extract_from_repo
    from services.commit_classifier import group_commits, serialize_groups_for_prompt
    from services.gemini_client import gemini
    import config
except ImportError as e:
    print(f"Error: Missing dependencies. Run 'pip install -r requirements.txt'. Detail: {e}")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="RepoLens AI — Transform Git history into meaningful narratives.")
    parser.add_argument("path", nargs="?", default=".", help="Path to the git repository (default: current directory)")
    parser.add_argument("--format", choices=["release", "standup", "onboarding", "portfolio"], default="release", help="Narrative format (default: release)")
    parser.add_argument("--all", action="store_true", help="Generate all narrative formats")
    
    args = parser.parse_args()
    
    repo_path = os.path.abspath(args.path)
    if not os.path.exists(os.path.join(repo_path, ".git")):
        print(f"Error: '{repo_path}' is not a git repository.")
        sys.exit(1)

    print(f"🔍 Analyzing repository: {os.path.basename(repo_path)}...")
    
    try:
        repo = git.Repo(repo_path)
        commits = _extract_from_repo(repo)
        
        if not commits:
            print("No commits found in the repository.")
            return

        print(f"✅ Found {len(commits)} commits. Classifying and grouping...")
        groups = group_commits(commits)
        commit_data_text = serialize_groups_for_prompt(groups)

        if not gemini.is_available():
            print("\n⚠️  Grok API key not configured. Showing demo output...")
        
        print(f"🤖 Generating {args.format if not args.all else 'all'} narrative(s) via Grok AI...")
        
        if args.all:
            results = gemini.generate_all(commit_data_text, os.path.basename(repo_path))
            for fmt, text in results.items():
                print(f"\n{'='*60}\n# {fmt.upper()} NARRATIVE\n{'='*60}")
                print(text)
        else:
            result = gemini.generate_single(args.format, commit_data_text)
            print(f"\n{'='*60}")
            print(result)
            print(f"{'='*60}")
            
        print("\n✨ Analysis complete!")

    except Exception as e:
        print(f"Error during analysis: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
