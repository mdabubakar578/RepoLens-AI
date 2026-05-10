"""
pages/qa.py — Repository Q&A page
Routes:
  GET  /qa/<analysis_id>      → Q&A interface
  POST /qa/<analysis_id>/ask  → Handle question via AJAX
"""
import json, logging
from flask import Blueprint, render_template, abort, request, jsonify
import time
import database, config
from services.gemini_client import gemini
from services.rag_service import RAGService
from services.github_service import extract_owner_repo, fetch_file_content, fetch_file_tree

logger = logging.getLogger("repolens.qa")
qa_bp = Blueprint("qa", __name__, template_folder="../components")

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

    retrieval_start = time.time()
    
    # Load Disk-Persisted RAG Index
    rag = RAGService()
    sources = []
    rag_context_str = "No relevant code context found."
    
    if rag.load_index(str(analysis_id)):
        results = rag.search(question)
        if results:
            parts = []
            char_count = 0
            for r in results:
                # Max 12 chunks
                if len(sources) >= 12:
                    break
                    
                header = f"--- {r.chunk.file_path} (lines {r.chunk.start_line}-{r.chunk.end_line}) ---"
                chunk_str = f"{header}\n{r.chunk.content}"
                
                # Check ~16k char budget
                if char_count + len(chunk_str) > 16000:
                    break
                    
                parts.append(chunk_str)
                char_count += len(chunk_str)
                
                sources.append({
                    "file_path": r.chunk.file_path,
                    "start_line": r.chunk.start_line,
                    "end_line": r.chunk.end_line,
                    "score": round(r.score, 2),
                    "relevance": r.relevance
                })
            rag_context_str = "\n\n".join(parts)
            context_parts.append(f"Relevant code:\n{rag_context_str}")
    else:
        logger.warning(f"No RAG index found for analysis {analysis_id}")
        
    retrieval_ms = int((time.time() - retrieval_start) * 1000)

    context = "\n\n".join(context_parts) if context_parts else "No detailed context available."

    # Fallback generator
    def generate_fallback_answer(sources_list):
        if not sources_list:
            return "<span class='badge badge--warning'>Retrieval-Only Mode</span>\n\nNo relevant code context was found to answer your question."
        ans = "<span class='badge badge--warning'>Retrieval-Only Mode</span>\n\nAI generation is currently unavailable. I performed a semantic search and found the following relevant code sections:\n\n"
        for src in sources_list:
            score_pct = int(src['score'] * 100)
            ans += f"- `{src['file_path']}` (Lines {src['start_line']}-{src['end_line']}) — **{score_pct}% match**\n"
        ans += "\n*Expand the Evidence Drawer below to read the exact code.*"
        return ans

    # 1. Early Refusal: Skip Gemini entirely if no high-confidence chunks were found
    if not sources:
        logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
        logger.info(f"[RAG] generation_ms=0")
        logger.info(f"[RAG] provider=\"Gemini\"")
        logger.info(f"[RAG] fallback=True reason=\"no_chunks\"")
        return jsonify({
            "answer": generate_fallback_answer(sources),
            "sources": sources,
            "tokens_used": 0,
            "provider": "Retrieval-only fallback"
        })

    # 2. Skip if Gemini unavailable
    if not gemini.is_available():
        logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
        logger.info(f"[RAG] generation_ms=0")
        logger.info(f"[RAG] provider=\"Gemini\"")
        logger.info(f"[RAG] fallback=True reason=\"not_configured\"")
        return jsonify({
            "answer": generate_fallback_answer(sources),
            "sources": sources,
            "tokens_used": 0,
            "provider": "Retrieval-only fallback"
        })

    # Generate
    generation_start = time.time()
    resp = gemini.answer_question(repo_name=repo_name, technologies=technologies, context=context, question=question)
    generation_ms = int((time.time() - generation_start) * 1000)

    # 3. API Failure Fallback
    if not resp.success:
        logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
        logger.info(f"[RAG] generation_ms={generation_ms}")
        logger.info(f"[RAG] provider=\"Gemini\"")
        reason = "api_failure"
        if "quota" in resp.error.lower() or "exhausted" in resp.error.lower():
            reason = "quota_exhausted"
        logger.info(f"[RAG] fallback=True reason=\"{reason}\"")
        return jsonify({
            "answer": generate_fallback_answer(sources),
            "sources": sources,
            "tokens_used": 0,
            "provider": "Retrieval-only fallback"
        })

    # 4. Response Validation Layer
    ans_text = resp.content.strip()
    ans_lower = ans_text.lower()
    
    if len(ans_text) < 40 or any(refusal in ans_lower for refusal in [
        "i cannot access", "i don't have the repository", "as an ai model", "i cannot browse"
    ]):
        logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
        logger.info(f"[RAG] generation_ms={generation_ms}")
        logger.info(f"[RAG] provider=\"Gemini\"")
        logger.info(f"[RAG] fallback=True reason=\"validation_failed\"")
        return jsonify({
            "answer": generate_fallback_answer(sources),
            "sources": sources,
            "tokens_used": 0,
            "provider": "Retrieval-only fallback"
        })
        
    # Check citation
    cited = False
    for src in sources:
        if src['file_path'] in ans_text:
            cited = True
            break
            
    if not cited:
        ans_text += "\n\n> ⚠️ **Warning:** This response may not be fully grounded in the retrieved repository evidence."

    logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
    logger.info(f"[RAG] generation_ms={generation_ms}")
    logger.info(f"[RAG] provider=\"Gemini\"")
    logger.info(f"[RAG] fallback=False")
    return jsonify({
        "answer": ans_text,
        "sources": sources, 
        "tokens_used": resp.total_tokens,
        "provider": "Gemini"
    })
