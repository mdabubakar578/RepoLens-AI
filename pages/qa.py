"""
pages/qa.py — Repository Q&A page
Routes:
  GET  /qa/<analysis_id>      → Q&A interface
  POST /qa/<analysis_id>/ask  → Handle question via AJAX
"""
import json, logging
from flask import Blueprint, render_template, abort, request, jsonify
import database, config
from services.grok_client import grok
from services.rag_service import RAGService
from services.github_service import extract_owner_repo, fetch_file_content, fetch_file_tree

logger = logging.getLogger("repolens.qa")
qa_bp = Blueprint("qa", __name__, template_folder="../components")

# Cache RAG service instances per analysis
_rag_cache: dict[int, RAGService] = {}

@qa_bp.get("/qa/<int:analysis_id>")
def qa_page(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis: abort(404)
    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    return render_template("qa_page.html", analysis=analysis, tech_data=tech_data)

@qa_bp.post("/qa/<int:analysis_id>/ask")
def ask_question(analysis_id: int):
    analysis = database.get_analysis_by_id(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    data = request.get_json() or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Please provide a question"}), 400

    extended = database.get_extended_data(analysis_id)
    tech_data = extended.get("technologies", {})
    arch_data = extended.get("architecture", {})
    repo_name = analysis.get("repo_name", "")
    technologies = ", ".join(t["name"] for t in tech_data.get("technologies", [])[:10])

    # Build context from available data
    context_parts = []

    # Add architecture description
    if arch_data.get("description"):
        context_parts.append(f"Architecture: {arch_data['description']}")

    # Add directory summary
    if tech_data.get("directory_summary"):
        context_parts.append(f"Directory structure:\n{tech_data['directory_summary']}")

    # Try to use RAG for code-level context
    if analysis_id in _rag_cache:
        rag = _rag_cache[analysis_id]
        rag_context = rag.get_context_for_question(question)
        if rag_context != "No relevant code context found.":
            context_parts.append(f"Relevant code:\n{rag_context}")
    else:
        # Try to build RAG index on first question
        repo_url = analysis.get("repo_url", "")
        if "github.com" in repo_url:
            try:
                owner, repo = extract_owner_repo(repo_url)
                branch = extended.get("metadata", {}).get("default_branch", "main")
                tree = fetch_file_tree(owner, repo, branch)
                if tree:
                    file_contents = {}
                    # Fetch top relevant files
                    code_files = [f for f in tree if f["type"] == "blob" and f.get("size", 0) < 50000][:20]
                    for f in code_files:
                        content = fetch_file_content(owner, repo, f["path"], branch)
                        if content: file_contents[f["path"]] = content
                    if file_contents:
                        rag = RAGService()
                        rag.index_repository(file_contents)
                        _rag_cache[analysis_id] = rag
                        rag_context = rag.get_context_for_question(question)
                        if rag_context != "No relevant code context found.":
                            context_parts.append(f"Relevant code:\n{rag_context}")
            except Exception as exc:
                logger.warning("RAG indexing failed: %s", exc)

    # Add commit context
    try:
        commits = json.loads(analysis.get("raw_commits_json", "[]"))
        if commits:
            recent = commits[:10]
            commit_text = "\n".join(f"- {c.get('message','')} (by {c.get('author','')})" for c in recent)
            context_parts.append(f"Recent commits:\n{commit_text}")
    except Exception: pass

    context = "\n\n".join(context_parts) if context_parts else "No detailed context available."

    # Ask Grok
    if not grok.is_available():
        return jsonify({"answer": "⚠️ **Demo Mode** — Add your Grok API key in `.env` to enable Q&A.\n\nBased on available context, this repository appears to use " + (technologies or "various technologies") + ".", "sources": []})

    resp = grok.answer_question(repo_name=repo_name, technologies=technologies, context=context, question=question)

    return jsonify({"answer": resp.content if resp.success else "Sorry, I couldn't answer that question. Please try again.",
        "sources": [], "tokens_used": resp.total_tokens})
