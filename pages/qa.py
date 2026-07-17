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
from services.github_service import extract_owner_repo, fetch_repository_archive

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
    try:
        analysis = database.get_analysis_by_id(analysis_id)
        if not analysis:
            return jsonify({
                "answer": "Analysis not found",
                "sources": [],
                "provider": "Error",
                "fallback": True,
                "warning": "Invalid analysis ID"
            }), 404

        data = request.get_json() or {}
        question = data.get("question", "").strip()
        if not question:
            return jsonify({
                "answer": "Please provide a question",
                "sources": [],
                "provider": "Error",
                "fallback": True,
                "warning": "Missing question"
            }), 400

        extended = database.get_extended_data(analysis_id)
        tech_data_dict = extended.get("technologies", {})
        if isinstance(tech_data_dict, list):
            tech_list = tech_data_dict
        else:
            tech_list = tech_data_dict.get("technologies", [])
            
        arch_data = extended.get("architecture", {})
        repo_name = analysis.get("repo_name", "")
        technologies = ", ".join(t.get("name", "") for t in tech_list[:10])

        # Build context from available data
        context_parts = []
        if arch_data.get("description"):
            context_parts.append(f"Architecture: {arch_data['description']}")
        
        dir_summary = tech_data_dict.get("directory_summary") if isinstance(tech_data_dict, dict) else ""
        if dir_summary:
            context_parts.append(f"Directory structure:\n{dir_summary}")

        retrieval_start = time.time()
        
        # Load Disk-Persisted RAG Index
        rag = RAGService()
        sources = []
        rag_context_str = "No relevant code context found."
        rag_missing = False
        
        try:
            index_loaded = rag.load_index(str(analysis_id))
            if not index_loaded and analysis.get("repo_url"):
                try:
                    owner, repo = extract_owner_repo(analysis["repo_url"])
                    branch = (extended.get("metadata") or {}).get("default_branch", "main")
                    _, recovered_contents = fetch_repository_archive(owner, repo, branch)
                    if recovered_contents:
                        rag.index_repository(str(analysis_id), recovered_contents)
                        index_loaded = True
                        logger.info("Rebuilt RAG index %s from %d archive files", analysis_id, len(recovered_contents))
                except Exception as rebuild_err:
                    logger.warning("Could not rebuild RAG index %s: %s", analysis_id, rebuild_err)

            if index_loaded:
                results = rag.search(question)
                if results:
                    parts = []
                    char_count = 0
                    for r in results:
                        if len(sources) >= 12:
                            break
                            
                        header = f"--- {r.chunk.file_path} (lines {r.chunk.start_line}-{r.chunk.end_line}) ---"
                        chunk_str = f"{header}\n{r.chunk.content}"
                        
                        if char_count + len(chunk_str) > 16000:
                            break
                            
                        parts.append(chunk_str)
                        char_count += len(chunk_str)
                        
                        sources.append({
                            "file_path": r.chunk.file_path,
                            "start_line": r.chunk.start_line,
                            "end_line": r.chunk.end_line,
                            "score": round(r.score, 2),
                            "relevance": r.relevance,
                            "snippet": r.chunk.content[:600],
                        })
                    rag_context_str = "\n\n".join(parts)
                    context_parts.append(f"Relevant code:\n{rag_context_str}")
            else:
                logger.warning(f"No RAG index found for analysis {analysis_id}")
                rag_missing = True
        except Exception as rag_err:
            logger.error(f"Error loading RAG index for {analysis_id}: {rag_err}")
            rag_missing = True
            
        retrieval_ms = int((time.time() - retrieval_start) * 1000)

        context = "\n\n".join(context_parts) if context_parts else "No detailed context available."

        # Fallback generator
        def generate_fallback_answer(sources_list):
            if not sources_list:
                return "**Source retrieval mode**\n\nNo matching source section was found. Try naming a feature, file, class, or function."
            ans = "**Source-grounded retrieval**\n\nThe generative model is unavailable, so RepoLens returned the strongest matching repository evidence without inventing an answer:\n\n"
            for src in sources_list:
                score_pct = int(src['score'] * 100)
                ans += f"- `{src['file_path']}` (lines {src['start_line']}-{src['end_line']}) - **{score_pct}% match**\n"
            ans += "\nExpand the evidence panel to inspect the retrieved source code."
            return ans

        warning_msg = None
        if rag_missing or not sources:
            warning_msg = "⚠ Semantic repository index unavailable. Answer generated using high-level repository metadata only."

        # Skip if Gemini unavailable
        if not gemini.is_available():
            logger.info(f"[RAG] retrieval_ms={retrieval_ms}")
            logger.info(f"[RAG] generation_ms=0")
            logger.info(f"[RAG] provider=\"Gemini\"")
            logger.info(f"[RAG] fallback=True reason=\"not_configured\"")
            return jsonify({
                "answer": generate_fallback_answer(sources),
                "sources": sources,
                "tokens_used": 0,
                "provider": "Retrieval-only fallback",
                "fallback": True,
                "warning": "Gemini generation is unavailable; verified repository retrieval is still active."
            })

        # Generate
        generation_start = time.time()
        resp = gemini.answer_question(repo_name=repo_name, technologies=technologies, context=context, question=question)
        generation_ms = int((time.time() - generation_start) * 1000)

        # API Failure Fallback
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
                "provider": "Retrieval-only fallback",
                "fallback": True,
                "warning": "AI generation is temporarily unavailable; verified repository retrieval is still active."
            })

        # Response Validation Layer
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
                "provider": "Retrieval-only fallback",
                "fallback": True,
                "warning": "Model refused to answer based on context."
            })
            
        # Check citation only if we have sources
        if sources:
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
            "provider": "Gemini",
            "fallback": False,
            "warning": warning_msg
        })
    except Exception as e:
        logger.error(f"QA endpoint error: {e}")
        return jsonify({
            "answer": "An unexpected server error occurred during QA processing. Please try again.",
            "sources": [],
            "provider": "Server Fallback",
            "fallback": True,
            "warning": "Internal Server Error"
        }), 500
