"""HTTP routes for repository question answering."""

from flask import Blueprint, abort, jsonify, render_template, request

import database
from services.qa_service import RepositoryQAService

qa_bp = Blueprint("qa", __name__, template_folder="../components")
qa_service = RepositoryQAService()


@qa_bp.get("/qa/<int:analysis_id>")
def qa_page(analysis_id: int):
    """Render the question-answering page for an existing analysis."""
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis:
        abort(404)
    extended = database.get_extended_data(analysis_id)
    return render_template(
        "qa_page.html",
        analysis=analysis,
        tech_data=extended.get("technologies", {}),
    )


@qa_bp.post("/qa/<int:analysis_id>/ask")
def ask_question(analysis_id: int):
    """Validate HTTP input and serialize the Q&A application result."""
    payload = request.get_json(silent=True) or {}
    result = qa_service.answer(analysis_id, payload.get("question", ""))
    return jsonify(result.response_body()), result.status_code
