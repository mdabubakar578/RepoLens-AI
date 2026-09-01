"""
pages/risk.py — Repository risk analysis page
Route: GET /risk/<analysis_id>
"""

from flask import Blueprint, abort, render_template

import database
from services.serialization import load_json_list

risk_bp = Blueprint("risk", __name__, template_folder="../components")


@risk_bp.get("/risk/<int:analysis_id>")
def risk_analysis(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis:
        abort(404)

    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    arch_data = extended.get("architecture", {})
    if (
        isinstance(arch_data, dict)
        and isinstance(tech_data, dict)
        and "complexity_metrics" not in arch_data
    ):
        arch_data["complexity_metrics"] = tech_data.get("complexity_metrics", {})
    raw_commits = load_json_list(analysis.get("raw_commits_json"))

    return render_template(
        "risk_page.html",
        analysis=analysis,
        tech_data=tech_data,
        arch_data=arch_data,
        commits=raw_commits,
    )
