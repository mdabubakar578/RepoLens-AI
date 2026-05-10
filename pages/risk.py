"""
pages/risk.py — Repository risk analysis page
Route: GET /risk/<analysis_id>
"""
import json
from flask import Blueprint, render_template, abort
import database, config

risk_bp = Blueprint("risk", __name__, template_folder="../components")

@risk_bp.get("/risk/<int:analysis_id>")
def risk_analysis(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis: abort(404)

    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    raw_commits = []
    try: raw_commits = json.loads(analysis.get("raw_commits_json", "[]"))
    except Exception: pass

    return render_template("risk_page.html",
        analysis=analysis, tech_data=tech_data, commits=raw_commits)
