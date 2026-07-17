"""
wsgi.py — WSGI entry point for production servers.

Gunicorn, Render, and PythonAnywhere can import the Flask app from here.
"""

import sys
import os

# Ensure the project directory is in the path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import create_app

# WSGI servers commonly look for either `application` or `app`.
application = create_app()
app = application

if __name__ == "__main__":
    application.run()
