"""
app.py — Flask Application Factory
Registers all page blueprints, initializes DB, and sets up Jinja2.
"""

import logging
import os
import secrets

from flask import Flask, g, render_template_string

import config
import database

logger = logging.getLogger("repolens.app")


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "components"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
        static_url_path="/static",
    )

    app.secret_key = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_SIZE_BYTES

    if config.SECRET_KEY_IS_EPHEMERAL:
        logger.warning("SECRET_KEY is not configured; sessions reset when the process restarts")
    app.jinja_env.globals.update(
        APP_NAME=config.APP_NAME,
        APP_TAGLINE=config.APP_TAGLINE,
        APP_VERSION=config.APP_VERSION,
        NARRATIVE_FORMATS=config.NARRATIVE_FORMATS,
        ENABLE_HISTORY=config.ENABLE_HISTORY,
        ENABLE_ARCHITECTURE=config.ENABLE_ARCHITECTURE,
        ENABLE_QA=config.ENABLE_QA,
        ENABLE_RISK=config.ENABLE_RISK,
    )

    database.init_db()

    # Recover stale tasks from previous crashes
    from services.task_recovery import recover_stale_tasks

    recover_stale_tasks()

    # Ensure cache and temp directories exist
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    os.makedirs(config.TEMP_CLONE_DIR, exist_ok=True)

    # Register blueprints
    from pages.about import about_bp
    from pages.analyze import analyze_bp
    from pages.architecture import architecture_bp
    from pages.detail import detail_bp
    from pages.history import history_bp
    from pages.home import home_bp
    from pages.qa import qa_bp
    from pages.risk import risk_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(analyze_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(detail_bp)
    app.register_blueprint(about_bp)
    app.register_blueprint(architecture_bp)
    app.register_blueprint(qa_bp)
    app.register_blueprint(risk_bp)

    @app.get("/health")
    def health():
        """Return a dependency-free liveness response for deployment probes."""
        return {"status": "ok", "service": config.APP_NAME, "version": config.APP_VERSION}

    @app.errorhandler(404)
    def not_found(_error):
        return render_template_string(ERROR_404_HTML, APP_NAME=config.APP_NAME), 404

    @app.errorhandler(500)
    def server_error(error):
        logger.error("Unhandled application error", exc_info=error)
        return render_template_string(ERROR_500_HTML, APP_NAME=config.APP_NAME), 500

    @app.before_request
    def assign_csp_nonce():
        """Mint one script nonce per request so no inline script needs allowing."""
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def expose_csp_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def add_security_headers(response):
        """Apply browser security defaults without changing application content."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        # Scripts are allowed only by per-request nonce, so injected markup
        # cannot execute even if it reaches a rendered page. Style attributes
        # remain inline throughout the templates and are still permitted.
        nonce = getattr(g, "csp_nonce", "")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data:; "
            "font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'",
        )
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        return response

    return app


ERROR_404_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>404 — {{ APP_NAME }}</title>
<style>body{background:#0a0a14;color:#e2e8f0;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
h1{font-size:4rem;background:linear-gradient(135deg,#7c3aed,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent}p{color:#94a3b8}a{color:#a78bfa;text-decoration:none}</style></head>
<body><div><h1>404</h1><p>This page doesn't exist.</p><a href="/">← Back to home</a></div></body></html>
"""

ERROR_500_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>500 — {{ APP_NAME }}</title>
<style>body{background:#0a0a14;color:#e2e8f0;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
h1{font-size:4rem;color:#dc2626}p{color:#94a3b8;max-width:600px}a{color:#a78bfa;text-decoration:none}code{background:#1e1e2e;padding:4px 8px;border-radius:4px;font-size:.85rem}</style></head>
<body><div><h1>500</h1><p>Something went wrong. Please try again.</p><a href="/">← Back to home</a></div></body></html>
"""

if __name__ == "__main__":
    app = create_app()
    app.run(debug=config.DEBUG, host="0.0.0.0", port=5000)
