"""
services/architecture_analyzer.py
===================================
Detects architecture patterns, module relationships, and generates
natural language architecture descriptions from file structure.
"""
from __future__ import annotations
import logging, re, os
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger("repolens.architecture")

ARCH_PATTERNS = {
    "MVC": {
        "indicators": ["models/", "views/", "controllers/", "model/", "view/", "controller/"],
        "description": "Model-View-Controller pattern separating data, presentation, and logic"
    },
    "Layered": {
        "indicators": ["services/", "repositories/", "controllers/", "routes/", "middleware/", "handlers/"],
        "description": "Layered architecture with clear separation of concerns"
    },
    "Microservices": {
        "indicators": ["docker-compose", "services/", "gateway/", "api-gateway/"],
        "description": "Microservices architecture with independently deployable services"
    },
    "Monolith": {
        "indicators": ["app.py", "main.py", "index.js", "server.js"],
        "description": "Monolithic application with centralized codebase"
    },
    "Clean Architecture": {
        "indicators": ["domain/", "usecases/", "use_cases/", "entities/", "interfaces/", "adapters/"],
        "description": "Clean Architecture with dependency inversion and domain-centric design"
    },
    "Component-Based": {
        "indicators": ["components/", "pages/", "layouts/", "hooks/", "stores/"],
        "description": "Component-based frontend architecture"
    },
}

MODULE_PATTERNS = {
    "Authentication": ["auth", "login", "signup", "register", "session", "jwt", "oauth", "passport"],
    "API Layer": ["routes", "endpoints", "api", "controllers", "handlers", "views"],
    "Data Layer": ["models", "schemas", "entities", "database", "db", "repositories", "migrations"],
    "Business Logic": ["services", "usecases", "use_cases", "domain", "logic", "core"],
    "Middleware": ["middleware", "interceptors", "guards", "filters", "pipes"],
    "Configuration": ["config", "settings", "env", "constants"],
    "Testing": ["tests", "test", "spec", "specs", "__tests__", "e2e"],
    "Static Assets": ["static", "public", "assets", "media", "uploads"],
    "Templates": ["templates", "views", "pages", "components", "layouts"],
    "Utilities": ["utils", "helpers", "lib", "common", "shared"],
}

@dataclass
class ArchitectureReport:
    patterns: list[dict] = field(default_factory=list)
    modules: list[dict] = field(default_factory=list)
    api_endpoints: list[str] = field(default_factory=list)
    description: str = ""
    insights: list[str] = field(default_factory=list)

def analyze_architecture(file_tree: list[dict], file_contents: dict[str, str] | None = None) -> ArchitectureReport:
    """Analyze repository architecture from file structure and contents."""
    report = ArchitectureReport()
    paths = [item["path"] for item in file_tree]
    dirs = set()
    for p in paths:
        parts = p.split("/")
        for i in range(len(parts)):
            dirs.add("/".join(parts[:i+1]))

    # Detect architecture patterns
    for pattern_name, info in ARCH_PATTERNS.items():
        matches = []
        for indicator in info["indicators"]:
            for d in dirs:
                if d.lower().endswith(indicator.rstrip("/")) or indicator in d.lower():
                    matches.append(d)
                    break
        if matches:
            confidence = min(len(matches) / len(info["indicators"]), 1.0)
            report.patterns.append({
                "name": pattern_name, "confidence": round(confidence, 2),
                "description": info["description"], "evidence": matches[:5]
            })
    report.patterns.sort(key=lambda p: -p["confidence"])

    # Detect modules
    dir_names = {p.split("/")[0].lower() for p in paths if "/" in p}
    for module_name, keywords in MODULE_PATTERNS.items():
        matched_dirs = [d for d in dir_names if any(kw in d for kw in keywords)]
        if matched_dirs:
            file_count = sum(1 for p in paths if any(p.lower().startswith(d) for d in matched_dirs))
            report.modules.append({
                "name": module_name, "directories": matched_dirs,
                "file_count": file_count
            })

    # Detect API endpoints from file contents
    if file_contents:
        endpoint_patterns = [
            re.compile(r'@(app|router|bp)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', re.IGNORECASE),
            re.compile(r'(app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', re.IGNORECASE),
        ]
        for path, content in file_contents.items():
            for pattern in endpoint_patterns:
                for match in pattern.finditer(content):
                    route = match.group(3) if len(match.groups()) >= 3 else match.group(2)
                    method = match.group(2).upper() if len(match.groups()) >= 2 else "GET"
                    report.api_endpoints.append(f"{method} {route}")

    # Generate insights
    report.insights = _generate_insights(report, file_tree)
    report.description = _build_description(report)
    return report

def _generate_insights(report: ArchitectureReport, file_tree: list[dict]) -> list[str]:
    insights = []
    if report.patterns:
        top = report.patterns[0]
        insights.append(f"Repository follows a **{top['name']}** architecture pattern ({top['description'].lower()})")
    if report.modules:
        module_names = [m["name"] for m in report.modules]
        insights.append(f"Key modules identified: {', '.join(module_names)}")
    if report.api_endpoints:
        insights.append(f"{len(report.api_endpoints)} API endpoints detected")
    # File distribution
    dir_counts = defaultdict(int)
    for item in file_tree:
        if "/" in item["path"]:
            dir_counts[item["path"].split("/")[0]] += 1
    if dir_counts:
        top_dir = max(dir_counts, key=dir_counts.get)
        insights.append(f"Largest directory: `{top_dir}/` ({dir_counts[top_dir]} files)")
    return insights

def _build_description(report: ArchitectureReport) -> str:
    parts = []
    if report.patterns:
        p = report.patterns[0]
        parts.append(f"The repository uses a **{p['name']}** architecture. {p['description']}.")
    if report.modules:
        mod_list = ", ".join(f"**{m['name']}** (`{', '.join(m['directories'][:2])}`)" for m in report.modules[:5])
        parts.append(f"Key modules include: {mod_list}.")
    if report.api_endpoints:
        parts.append(f"The API layer exposes {len(report.api_endpoints)} endpoints.")
    return " ".join(parts) if parts else "Architecture analysis could not determine a clear pattern."
