"""
pages/architecture.py — Architecture insights page
Route: GET /architecture/<analysis_id>
"""

from flask import Blueprint, abort, render_template

import database
from services.serialization import load_json_list

architecture_bp = Blueprint("architecture", __name__, template_folder="../components")


@architecture_bp.get("/architecture/<int:analysis_id>")
def architecture(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis:
        abort(404)

    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    arch_data = extended.get("architecture", {})
    repo_meta = extended.get("metadata", {})

    raw_commits = load_json_list(analysis.get("raw_commits_json"))

    return render_template(
        "architecture_page.html",
        analysis=analysis,
        tech_data=tech_data,
        arch_data=arch_data,
        repo_meta=repo_meta,
        commits=raw_commits,
    )
