"""
wsgi.py — WSGI Entry Point for PythonAnywhere
==============================================
PythonAnywhere's web app configuration should point to this file.
It exports the 'application' object that the WSGI server expects.
"""

import sys
import os

# Ensure the project directory is in the path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import create_app

# The WSGI server (like Gunicorn or PythonAnywhere's internal server)
# looks for a variable named 'application' or 'app'.
application = create_app()

if __name__ == "__main__":
    application.run()
