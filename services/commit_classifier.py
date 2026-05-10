"""
services/commit_classifier.py
==============================
Rule-based + keyword classifier for commit types.
Groups commits by week and detects version milestones.
"""

import re
from datetime import datetime
from collections import defaultdict

# ── Commit Type Definitions ────────────────────────────────────────────────────
COMMIT_TYPES = {
    "feature":       {"label": "Feature",       "color": "#7c3aed", "icon": "✨"},
    "bugfix":        {"label": "Bug Fix",        "color": "#dc2626", "icon": "🐛"},
    "hotfix":        {"label": "Hotfix",         "color": "#ef4444", "icon": "🚨"},
    "refactor":      {"label": "Refactor",       "color": "#2563eb", "icon": "♻️"},
    "docs":          {"label": "Docs",           "color": "#0891b2", "icon": "📝"},
    "test":          {"label": "Tests",          "color": "#16a34a", "icon": "🧪"},
    "devops":        {"label": "DevOps",         "color": "#d97706", "icon": "⚙️"},
    "chore":         {"label": "Chore",          "color": "#64748b", "icon": "🔧"},
}

# ── Keyword Rules (order matters — first match wins) ─────────────────────────
KEYWORD_RULES = [
    ("hotfix",   ["hotfix", "urgent fix", "critical fix", "emergency"]),
    ("bugfix",   ["fix", "bug", "patch", "resolve", "revert", "broken", "error", "issue", "crash"]),
    ("feature",  ["feat", "feature", "add", "new", "implement", "introduce", "support", "create", "build", "integrate"]),
    ("refactor", ["refactor", "restructure", "cleanup", "clean up", "simplify", "optimize", "improve", "perf", "performance"]),
    ("docs",     ["doc", "docs", "readme", "changelog", "comment", "documentation", "licence", "license"]),
    ("test",     ["test", "spec", "unit test", "e2e", "integration test", "coverage", "assert"]),
    ("devops",   ["ci", "cd", "deploy", "docker", "kubernetes", "k8s", "workflow", "action", "pipeline", "build", "release", "version", "bump", "config", "env", "nginx", "aws", "gcp"]),
    ("chore",    ["chore", "merge", "wip", "initial", "init", "scaffold", "setup", "dependency", "deps", "update", "upgrade", "lock"]),
]

VERSION_TAG_PATTERN = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)


def classify_commits(commits: list[dict]) -> list[dict]:
    """Add 'commit_type' field to each commit dict."""
    for commit in commits:
        commit["commit_type"] = _classify_single(commit["message"])
    return commits


def group_commits(commits: list[dict]) -> list[dict]:
    """
    Group classified commits into weekly buckets with milestone detection.
    Returns list of group dicts sorted by date (newest first).
    """
    if not commits:
        return []

    classified = classify_commits(commits)

    # Split into dated and undated
    dated = [c for c in classified if c.get("date")]
    undated = [c for c in classified if not c.get("date")]

    # Group dated commits by ISO week
    week_map = defaultdict(list)
    for commit in dated:
        week_key = _week_key(commit["date"])
        week_map[week_key].append(commit)

    groups = []
    for week_key in sorted(week_map.keys(), reverse=True):
        week_commits = week_map[week_key]
        year, week = week_key
        label = _week_label(year, week, week_commits)
        type_counts = _count_types(week_commits)
        milestones = _find_milestones(week_commits)

        groups.append({
            "week_key": f"{year}-W{week:02d}",
            "label": label,
            "commits": week_commits,
            "commit_count": len(week_commits),
            "type_counts": type_counts,
            "milestones": milestones,
            "is_milestone_week": bool(milestones),
            "date_from": min(c["date"] for c in week_commits).strftime("%b %d"),
            "date_to": max(c["date"] for c in week_commits).strftime("%b %d, %Y"),
        })

    # Attach undated commits as their own group
    if undated:
        type_counts = _count_types(undated)
        groups.append({
            "week_key": "undated",
            "label": "Undated Commits",
            "commits": undated,
            "commit_count": len(undated),
            "type_counts": type_counts,
            "milestones": [],
            "is_milestone_week": False,
            "date_from": "",
            "date_to": "",
        })

    return groups


# ── Internal helpers ───────────────────────────────────────────────────────────

def _classify_single(message: str) -> str:
    msg_lower = message.lower().strip()
    for type_key, keywords in KEYWORD_RULES:
        for kw in keywords:
            if kw in msg_lower:
                return type_key
    return "chore"


def _week_key(dt: datetime) -> tuple[int, int]:
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    iso = dt.isocalendar()
    return (iso[0], iso[1])


def _week_label(year: int, week: int, commits: list[dict]) -> str:
    try:
        # Monday of that ISO week
        from datetime import date
        d = date.fromisocalendar(year, week, 1)
        return d.strftime("Week of %b %d, %Y")
    except Exception:
        return f"{year} Week {week}"


def _count_types(commits: list[dict]) -> dict:
    counts = defaultdict(int)
    for c in commits:
        counts[c["commit_type"]] += 1
    return dict(counts)


def _find_milestones(commits: list[dict]) -> list[dict]:
    milestones = []
    for commit in commits:
        # Version tags on the commit
        for tag in commit.get("tags", []):
            m = VERSION_TAG_PATTERN.match(tag)
            if m:
                milestones.append({"tag": tag, "commit": commit["hash"], "message": commit["message"]})
        # Version bump in message
        if VERSION_TAG_PATTERN.search(commit["message"]) and any(
            kw in commit["message"].lower() for kw in ["release", "version", "bump", "tag", "v1", "v2", "v3"]
        ):
            milestones.append({"tag": commit["message"][:40], "commit": commit["hash"], "message": commit["message"]})
    return milestones


def get_type_info(type_key: str) -> dict:
    """Return label, color, icon for a commit type key."""
    return COMMIT_TYPES.get(type_key, {"label": type_key.title(), "color": "#64748b", "icon": "•"})


def serialize_groups_for_prompt(groups: list[dict]) -> str:
    """Compact text serialization of groups for AI prompt (minimal tokens)."""
    lines = []
    for g in groups:
        lines.append(f"\n## {g['label']} ({g['commit_count']} commits)")
        if g["milestones"]:
            tags = ", ".join(m["tag"] for m in g["milestones"])
            lines.append(f"  🏷️ Milestones: {tags}")
        for commit in g["commits"][:30]:  # cap per group
            type_info = get_type_info(commit["commit_type"])
            lines.append(f"  [{type_info['label']}] {commit['message']} (by {commit['author']})")
    return "\n".join(lines)


def build_contribution_insights(commits: list[dict], groups: list[dict]) -> dict:
    """Return summary stats for UI and AI context around contributor activity."""
    contributors = defaultdict(int)
    type_counts = defaultdict(int)
    milestone_tags = []
    dated_commits = []

    for commit in commits:
        contributors[commit.get("author") or "Unknown"] += 1
        type_counts[commit.get("commit_type") or _classify_single(commit.get("message", ""))] += 1
        milestone_tags.extend(commit.get("tags", []))
        commit_date = _coerce_datetime(commit.get("date"))
        if commit_date:
            dated_commits.append(commit_date)

    top_contributors = sorted(
        contributors.items(),
        key=lambda item: (-item[1], item[0].lower()),
    )[:5]
    sorted_types = sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))

    active_weeks = len([group for group in groups if group.get("week_key") != "undated"])
    date_from = min(dated_commits, default=None)
    date_to = max(dated_commits, default=None)

    return {
        "total_commits": len(commits),
        "contributors_count": len(contributors),
        "top_contributors": [
            {"name": name, "commits": count} for name, count in top_contributors
        ],
        "type_breakdown": [
            {
                "type": type_key,
                "label": get_type_info(type_key)["label"],
                "count": count,
                "share": round((count / len(commits)) * 100) if commits else 0,
            }
            for type_key, count in sorted_types
        ],
        "active_weeks": active_weeks,
        "milestones": sorted(set(milestone_tags)),
        "date_from": date_from.strftime("%b %d, %Y") if date_from else "",
        "date_to": date_to.strftime("%b %d, %Y") if date_to else "",
        "has_dated_commits": bool(dated_commits),
    }


def _coerce_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value[:25], fmt)
                except ValueError:
                    continue
    return None
