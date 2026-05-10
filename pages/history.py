"""
pages/history.py — Past analyses dashboard
Routes:
  GET /history  → paginated card grid with search
"""

from flask import Blueprint, render_template, request
import database
import config

history_bp = Blueprint("history", __name__, template_folder="../components")


@history_bp.get("/history")
def history():
    if not config.ENABLE_HISTORY:
        from flask import abort
        abort(404)

    search = request.args.get("q", "").strip()
    page   = max(1, int(request.args.get("page", 1)))
    per_page = 12

    analyses, total = database.get_all_analyses(search=search, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "history_page.html",
        analyses=analyses,
        search=search,
        page=page,
        total=total,
        total_pages=total_pages,
    )
