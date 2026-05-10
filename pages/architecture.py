"""
pages/architecture.py — Architecture insights page
Route: GET /architecture/<analysis_id>
"""
import json
from flask import Blueprint, render_template, abort
import database, config

architecture_bp = Blueprint("architecture", __name__, template_folder="../components")

@architecture_bp.get("/architecture/<int:analysis_id>")
def architecture(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis: abort(404)

    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    arch_data = extended.get("architecture", {})
    repo_meta = extended.get("metadata", {})

    raw_commits = []
    try: raw_commits = json.loads(analysis.get("raw_commits_json", "[]"))
    except Exception: pass

    return render_template("architecture_page.html",
        analysis=analysis, tech_data=tech_data, arch_data=arch_data,
        repo_meta=repo_meta, commits=raw_commits)
