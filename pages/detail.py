"""
pages/detail.py — Shareable public analysis view
Routes:
  GET /share/<slug>  → read-only narrative view
  GET /card/<slug>   → shareable story card
"""

from flask import Blueprint, abort, render_template, request

import config
import database
from pages.analyze import _markdown_to_html
from services.commit_classifier import build_contribution_insights
from services.serialization import load_json_list

detail_bp = Blueprint("detail", __name__, template_folder="../components")


@detail_bp.get("/share/<slug>")
def share(slug: str):
    if not config.ENABLE_SHARE:
        abort(404)
    analysis = database.get_analysis_by_slug(slug)
    if not analysis:
        abort(404)

    active_format = request.args.get("fmt", config.DEFAULT_NARRATIVE_FORMAT)
    allowed = {k for k, _ in config.NARRATIVE_FORMATS}
    if active_format not in allowed:
        active_format = config.DEFAULT_NARRATIVE_FORMAT

    enriched = dict(analysis)
    for fmt_key, _ in config.NARRATIVE_FORMATS:
        raw = analysis.get(f"narrative_{fmt_key}") or ""
        enriched[f"narrative_{fmt_key}"] = _markdown_to_html(raw)

    raw_commits = load_json_list(analysis.get("raw_commits_json", "[]"))
    groups = load_json_list(analysis.get("grouped_commits_json", "[]"))
    insights = build_contribution_insights(raw_commits, groups)

    return render_template(
        "detail_page.html",
        analysis=enriched,
        active_format=active_format,
        share_url=request.url,
        insights=insights,
    )


@detail_bp.get("/card/<slug>")
def story_card(slug: str):
    analysis = database.get_analysis_by_slug(slug)
    if not analysis:
        abort(404)
    raw_commits = load_json_list(analysis.get("raw_commits_json", "[]"))
    groups = load_json_list(analysis.get("grouped_commits_json", "[]"))
    insights = build_contribution_insights(raw_commits, groups)
    return render_template("story_card.html", analysis=analysis, insights=insights)
