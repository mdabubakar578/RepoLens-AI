"""
pages/home.py — Landing page + repository analysis pipeline
Routes:
  GET  /         → renders landing page
  POST /analyze  → starts background analysis, redirects to loading page
  GET  /loading/<id> → polling page
  GET  /status/<id>  → AJAX status check
"""
import re, uuid, logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
import database, config
from services.github_service import extract_repo_name
from services.analysis_task import start_background_analysis

logger = logging.getLogger("repolens.home")
home_bp = Blueprint("home", __name__, template_folder="../components")

@home_bp.get("/")
def index():
    return render_template("home_page.html")

@home_bp.post("/analyze")
def analyze():
    input_mode = request.form.get("input_mode", "url").strip()
    format_pref = request.form.get("format_pref", config.DEFAULT_NARRATIVE_FORMAT)

    repo_url = ""
    repo_name = "Repository"
    input_data = ""

    if input_mode == "url":
        repo_url = request.form.get("repo_url", "").strip()
        if not repo_url:
            flash("Please enter a repository URL.", "error")
            return redirect(url_for("home.index"))
        repo_name = extract_repo_name(repo_url)
        input_data = repo_url
    elif input_mode == "file":
        file = request.files.get("git_log_file")
        if not file or not file.filename:
            flash("Please upload a git log .txt file.", "error")
            return redirect(url_for("home.index"))
        input_data = file.read().decode("utf-8", errors="replace")
        if len(input_data) > config.MAX_PASTE_CHARS:
            flash(f"File too large. Max {config.MAX_PASTE_CHARS:,} chars.", "error")
            return redirect(url_for("home.index"))
        repo_url = f"uploaded:{file.filename}"
        repo_name = file.filename.replace(".txt", "").replace("-", " ").title()
    elif input_mode == "paste":
        input_data = request.form.get("raw_commits", "").strip()
        if not input_data:
            flash("Please paste your git log output.", "error")
            return redirect(url_for("home.index"))
        if len(input_data) > config.MAX_PASTE_CHARS:
            flash(f"Input too large. Max {config.MAX_PASTE_CHARS:,} chars.", "error")
            return redirect(url_for("home.index"))
        repo_url = "pasted:raw"
        repo_name = "Pasted Repository"
    else:
        flash("Unknown input mode.", "error")
        return redirect(url_for("home.index"))

    slug = _make_slug(repo_name)

    # Create DB record with status='pending'
    analysis_id = database.save_analysis(
        slug=slug, repo_url=repo_url, repo_name=repo_name,
        input_mode=input_mode, raw_commits=[], grouped_commits=[], commit_count=0
    )

    # Start background task
    start_background_analysis(analysis_id, input_mode, input_data, format_pref)

    # Redirect to loading page
    return redirect(url_for("home.loading", analysis_id=analysis_id, fmt=format_pref))

@home_bp.get("/loading/<int:analysis_id>")
def loading(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis: return redirect(url_for("home.index"))
    if analysis.get("status") == "done":
        fmt = request.args.get("fmt", config.DEFAULT_NARRATIVE_FORMAT)
        return redirect(url_for("analyze.result", analysis_id=analysis_id, fmt=fmt))
    if analysis.get("status") == "error":
        flash(analysis.get("error_message", "Analysis failed."), "error")
        return redirect(url_for("home.index"))
    
    fmt = request.args.get("fmt", config.DEFAULT_NARRATIVE_FORMAT)
    return render_template("loading_page.html", analysis=analysis, fmt=fmt)

@home_bp.get("/status/<int:analysis_id>")
def status(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis: return jsonify({"status": "error", "message": "Not found"}), 404
    return jsonify({
        "status": analysis.get("status"),
        "error": analysis.get("error_message")
    })

def _make_slug(repo_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (repo_name or "repo").lower()).strip("-")
    base = (base[:20] or "repo").strip("-") or "repo"
    return f"{base}-{uuid.uuid4().hex[:6]}"
