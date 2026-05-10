"""
pages/about.py — Static how-it-works page
Route: GET /about
"""

from flask import Blueprint, render_template

about_bp = Blueprint("about", __name__, template_folder="../components")


@about_bp.get("/about")
def about():
    return render_template("about_page.html")
