"""
services/git_parser.py — COMPATIBILITY SHIM
Redirects all imports to services.github_service.
"""
from services.github_service import (
    parse_from_url, parse_from_file, parse_from_text,
    extract_repo_name, extract_owner_repo,
    fetch_repo_metadata, fetch_file_tree, fetch_file_content,
    fetch_branches, fetch_pull_requests,
    _extract_from_repo,
)
