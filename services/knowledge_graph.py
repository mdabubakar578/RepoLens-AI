"""Explainable AST source graph used by the RepoLens Investigator."""

from __future__ import annotations

import ast
import json
import os
import re
import warnings
from collections import defaultdict, deque

JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
JS_RESERVED = frozenset(
    {
        "if", "for", "while", "switch", "catch", "return", "function", "class",
        "const", "let", "var", "await", "typeof", "new", "super", "this",
        "require", "import", "export", "default", "try", "else", "do", "throw",
    }
)
JS_DEFINITION_PATTERN = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\s*\*?\s+(?P<fname>[A-Za-z_$][\w$]*)"
    r"|class\s+(?P<cname>[A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+(?P<vname>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?(?:function\s*\*?\s*\(|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>))",
    re.M,
)
JS_IMPORT_PATTERN = re.compile(
    r"""import\s[^;'"]*?from\s*['"](?P<module>[^'"]+)['"]"""
    r"""|import\s*['"](?P<bare>[^'"]+)['"]"""
    r"""|require\s*\(\s*['"](?P<req>[^'"]+)['"]\s*\)""",
    re.S,
)
JS_ROUTE_PATTERN = re.compile(
    r"\b(?:app|router|server|api)\s*\.\s*"
    r"(?P<method>get|post|put|patch|delete|all)\s*\(\s*"
    r"""['"`](?P<route>[^'"`]+)['"`]"""
    r"(?:\s*,\s*(?P<handler>[A-Za-z_$][\w$]*))?"
)
JS_CALL_PATTERN = re.compile(r"\b(?P<callee>[A-Za-z_$][\w$]*)\s*\(")


class KnowledgeGraph:
    """Records files, symbols, imports, calls, and Flask routes."""

    def __init__(self):
        self.nodes = {}
        self.edges = []
        self._outgoing = defaultdict(list)
        self._incoming = defaultdict(list)

    def build(self, file_contents):
        self.nodes, self.edges = {}, []
        for path, content in file_contents.items():
            self._add_node(path, "file", path=path, line=1)
            if path.endswith(".py"):
                self._parse_python(path, content)
            elif path.endswith(JS_EXTENSIONS):
                self._parse_javascript(path, content)
        self._resolve_references()
        self._reindex()
        return self.stats()

    # ── JavaScript and TypeScript ────────────────────────────────────────────

    @staticmethod
    def _strip_js_noise(content):
        """Blank out comments and string bodies so brace counting stays honest."""
        content = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), content, flags=re.S)
        content = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), content)
        content = re.sub(
            r"(['\"`])(?:\\.|(?!\1).)*\1",
            lambda m: m.group(1) + " " * (len(m.group(0)) - 2) + m.group(1),
            content,
            flags=re.S,
        )
        return content

    def _parse_javascript(self, path, content):
        """Record symbols, imports, routes, and calls without an LLM.

        A regex reader cannot match a real parser, but it resolves the same
        relationships the Python reader does, so dependency and impact
        questions stop returning nothing on JavaScript and TypeScript code.
        """
        text = self._strip_js_noise(content)
        line_starts = [0]
        for line in text.splitlines(keepends=True):
            line_starts.append(line_starts[-1] + len(line))

        def line_of(offset):
            low, high = 0, len(line_starts) - 1
            while low < high:
                mid = (low + high + 1) // 2
                if line_starts[mid] <= offset:
                    low = mid
                else:
                    high = mid - 1
            return low + 1

        definitions = []
        for match in JS_DEFINITION_PATTERN.finditer(text):
            name = match.group("fname") or match.group("cname") or match.group("vname")
            if not name or name in JS_RESERVED:
                continue
            kind = "class" if match.group("cname") else "function"
            start = line_of(match.start())
            end = line_of(self._js_block_end(text, match.end()))
            symbol_id = f"{path}::{name}"
            self._add_node(symbol_id, kind, name=name, path=path, line=start, end_line=end)
            self._add_edge(path, symbol_id, "defines")
            definitions.append((match.start(), self._js_block_end(text, match.end()), symbol_id))

        # Imports and routes carry their meaning inside string literals, which the
        # stripper blanks. Length is preserved, so offsets still line up.
        for match in JS_IMPORT_PATTERN.finditer(content):
            module = match.group("module") or match.group("bare") or match.group("req")
            if module:
                module_id = f"module::{module}"
                self._add_node(module_id, "module", name=module, importer=path)
                self._add_edge(path, module_id, "imports")

        for match in JS_ROUTE_PATTERN.finditer(content):
            route = {"method": match.group("method").upper(), "path": match.group("route")}
            route_id = f"route::{route['method']}::{route['path']}"
            self._add_node(route_id, "route", **route)
            handler = match.group("handler")
            if handler and f"{path}::{handler}" in self.nodes:
                self._add_edge(route_id, f"{path}::{handler}", "handled_by")
            else:
                self._add_edge(route_id, path, "handled_by")

        for match in JS_CALL_PATTERN.finditer(text):
            name = match.group("callee")
            if not name or name in JS_RESERVED:
                continue
            owner = path
            for start, end, symbol_id in definitions:
                if start <= match.start() <= end:
                    owner = symbol_id
                    break
            call_id = f"call::{name}"
            self._add_node(call_id, "call", name=name)
            self._add_edge(owner, call_id, "calls")

    @staticmethod
    def _js_block_end(text, search_from):
        """Return the offset closing the block that opens after a definition."""
        opening = text.find("{", search_from)
        if opening == -1:
            newline = text.find("\n", search_from)
            return newline if newline != -1 else len(text)
        depth = 0
        for offset in range(opening, len(text)):
            if text[offset] == "{":
                depth += 1
            elif text[offset] == "}":
                depth -= 1
                if depth == 0:
                    return offset
        return len(text)

    def _parse_python(self, path, content):
        # An analysed repository is someone else's code. Its invalid escapes
        # and deprecated constructs are not this service's warnings to raise
        # into the application log.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            try:
                tree = ast.parse(content)
            except (SyntaxError, ValueError):
                return
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                symbol_id = f"{path}::{node.name}"
                self._add_node(
                    symbol_id,
                    kind,
                    name=node.name,
                    path=path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
                self._add_edge(self._owner_id(path, node, parents), symbol_id, "defines")
                if not isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        route = self._route_from_decorator(decorator)
                        if route:
                            route_id = f"route::{route['method']}::{route['path']}"
                            self._add_node(route_id, "route", **route)
                            self._add_edge(route_id, symbol_id, "handled_by")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for module in modules:
                    if module:
                        module_id = f"module::{module}"
                        self._add_node(module_id, "module", name=module)
                        self._add_edge(path, module_id, "imports")
            elif isinstance(node, ast.Call):
                name = self._call_name(node.func)
                if name:
                    call_id = f"call::{name}"
                    self._add_node(call_id, "call", name=name)
                    self._add_edge(self._owner_id(path, node, parents), call_id, "calls")

    @staticmethod
    def _owner_id(path, node, parents):
        """Return the nearest enclosing symbol identifier for an AST node."""
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return f"{path}::{current.name}"
            current = parents.get(current)
        return path

    @staticmethod
    def _call_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = KnowledgeGraph._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    @staticmethod
    def _route_from_decorator(node):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or not node.args
        ):
            return None
        method = node.func.attr.lower()
        if method not in {"get", "post", "put", "delete", "patch", "route"}:
            return None
        value = node.args[0]
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return None
        if method == "route":
            method = "GET"
            for keyword in node.keywords:
                if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                    methods = [x.value for x in keyword.value.elts if isinstance(x, ast.Constant)]
                    method = ",".join(str(x).upper() for x in methods) or "GET"
        return {"method": method.upper(), "path": value.value}

    def _add_node(self, node_id, kind, **data):
        self.nodes.setdefault(node_id, {"id": node_id, "kind": kind, **data})

    def _add_edge(self, source, target, relation):
        edge = {"source": source, "target": target, "relation": relation}
        if edge not in self.edges:
            self.edges.append(edge)

    def _resolve_references(self):
        """Connect abstract import and call nodes to repository definitions."""
        modules: dict[str, set[str]] = defaultdict(set)
        symbols: dict[str, list[str]] = defaultdict(list)

        for node in self.nodes.values():
            if node["kind"] == "file" and node["id"].endswith(".py"):
                for alias in self._module_aliases(node["id"]):
                    modules[alias].add(node["id"])
            elif node["kind"] in {"function", "class"}:
                symbols[node.get("name", "").lower()].append(node["id"])

        known_files = {node["id"] for node in self.nodes.values() if node["kind"] == "file"}

        for node in list(self.nodes.values()):
            if node["kind"] == "module":
                resolved = self._resolve_javascript_import(node, known_files)
                if resolved:
                    self._add_edge(node["id"], resolved, "resolves_to")
                    continue
                module_name = node.get("name", "").lstrip(".")
                for path in sorted(modules.get(module_name, set())):
                    self._add_edge(node["id"], path, "resolves_to")
            elif node["kind"] == "call":
                call_name = node.get("name", "").rsplit(".", maxsplit=1)[-1].lower()
                for symbol_id in sorted(symbols.get(call_name, []))[:4]:
                    self._add_edge(node["id"], symbol_id, "resolves_to")

    @staticmethod
    def _resolve_javascript_import(node, known_files):
        """Resolve a relative JavaScript import to an indexed repository file.

        Bare specifiers such as "react" are third-party and stay unresolved,
        which is correct: they are not part of this repository.
        """
        specifier = node.get("name", "")
        importer = node.get("importer", "")
        if not specifier.startswith(".") or not importer:
            return None

        base = os.path.dirname(importer)
        target = os.path.normpath(os.path.join(base, specifier)).replace(os.sep, "/")
        candidates = [
            f"{target}{extension}" for extension in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
        ]
        candidates.insert(0, target)
        candidates.extend(
            f"{target}/index{extension}" for extension in (".ts", ".tsx", ".js", ".jsx")
        )
        for candidate in candidates:
            if candidate in known_files:
                return candidate
        return None

    @staticmethod
    def _module_aliases(path):
        """Return import names that can refer to a Python source path."""
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        aliases = {module}
        if module.startswith("src."):
            aliases.add(module[len("src.") :])
        return {alias for alias in aliases if alias}

    @staticmethod
    def _query_terms(text):
        """Extract meaningful full identifiers and identifier components."""
        stop_words = {
            "and", "are", "does", "for", "from", "how", "into", "is", "of",
            "on", "or", "that", "the", "this", "to", "what", "when", "where",
            "which", "why", "with",
        }
        terms = set()
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*", text):
            expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
            candidates = [raw, *re.split(r"[._\s-]+", expanded)]
            terms.update(
                candidate.lower()
                for candidate in candidates
                if len(candidate) >= 2 and candidate.lower() not in stop_words
            )
        return terms

    def _reindex(self):
        self._outgoing, self._incoming = defaultdict(list), defaultdict(list)
        for edge in self.edges:
            self._outgoing[edge["source"]].append(edge)
            self._incoming[edge["target"]].append(edge)

    def find_symbols(self, query, limit=10):
        terms = self._query_terms(query)
        if not terms:
            return []
        matches = []
        for node in self.nodes.values():
            if node["kind"] not in {"function", "class", "route", "file"}:
                continue
            name = str(node.get("name", "")).lower()
            node_text = f"{name} {node.get('path', '')} {node['id']}"
            node_terms = self._query_terms(node_text)
            exact_symbol = bool(name and name in terms)
            overlap = terms.intersection(node_terms)
            score = (8 if exact_symbol else 0) + (3 * len(overlap))
            if score:
                matches.append(
                    {
                        **node,
                        "match_score": score,
                        "match_reason": "exact_symbol" if exact_symbol else "term_overlap",
                    }
                )
        kind_order = {"function": 0, "class": 1, "route": 2, "file": 3}
        return sorted(
            matches,
            key=lambda item: (
                -item["match_score"],
                kind_order[item["kind"]],
                item["id"],
            ),
        )[:limit]

    def neighbors(self, node_id, depth=1, limit=20):
        if node_id not in self.nodes:
            return []
        found, seen, queue = [], {node_id}, deque([(node_id, 0)])
        while queue and len(found) < limit:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in self._outgoing[current] + self._incoming[current]:
                other = edge["target"] if edge["source"] == current else edge["source"]
                if other in seen:
                    continue
                if len(found) >= limit:
                    break
                seen.add(other)
                found.append({"edge": edge, "node": self.nodes.get(other, {"id": other})})
                queue.append((other, level + 1))
        return found

    def stats(self):
        kinds = defaultdict(int)
        for node in self.nodes.values():
            kinds[node["kind"]] += 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "kinds": dict(kinds),
        }

    def save(self, analysis_id):
        import config
        os.makedirs(config.INDEX_CACHE_DIR, exist_ok=True)
        with open(os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_graph.json"), "w", encoding="utf-8") as handle:
            json.dump({"nodes": list(self.nodes.values()), "edges": self.edges}, handle)

    def load(self, analysis_id):
        import config
        path = os.path.join(config.INDEX_CACHE_DIR, f"{analysis_id}_graph.json")
        if not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            self.nodes = {node["id"]: node for node in data.get("nodes", [])}
            self.edges = data.get("edges", [])
            self._reindex()
            return bool(self.nodes)
        except (OSError, ValueError, KeyError):
            return False
