"""
wsgi.py — WSGI entry point for production servers.

Gunicorn, Render, and PythonAnywhere can import the Flask app from here.
"""

from app import create_app

# WSGI servers commonly look for either `application` or `app`.
application = create_app()
app = application

if __name__ == "__main__":
    application.run()
