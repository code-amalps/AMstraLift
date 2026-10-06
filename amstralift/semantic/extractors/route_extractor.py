"""Route Extractor for Gate 6.

Extracts observable HTTP API endpoints across Angular, React, Express, FastAPI, Flask, and ASP.NET Core.
"""

from __future__ import annotations

import re
from pathlib import Path

from amstralift.semantic.models import RouteItem


class RouteExtractor:
    """Extracts observable API endpoints and application routes from project source files."""

    def extract_from_file(self, file_path: Path, rel_path: str) -> list[RouteItem]:
        routes: list[RouteItem] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return routes

        ext = file_path.suffix.lower()

        # ── 1. Python (FastAPI / Flask) ──────────────────────────────────────
        if ext == ".py":
            # FastAPI / Flask decorators: @app.get("/path"), @router.post("/path")
            py_matches = re.finditer(
                r"@(?:app|router|blueprint)\.(get|post|put|delete|patch|options|head)\s*\(\s*['\"]([^'\"]+)['\"]",
                content,
                re.IGNORECASE,
            )
            for m in py_matches:
                method = m.group(1).upper()
                path = m.group(2)
                routes.append(RouteItem(method=method, path=path, file_path=rel_path))

            # Flask @app.route("/path", methods=["GET", "POST"])
            flask_matches = re.finditer(
                r"@(?:app|blueprint)\.route\s*\(\s*['\"]([^'\"]+)['\"](?:[^)]*methods\s*=\s*\[([^\]]+)\])?",
                content,
            )
            for m in flask_matches:
                path = m.group(1)
                methods_raw = m.group(2)
                if methods_raw:
                    methods = re.findall(r"['\"]([a-zA-Z]+)['\"]", methods_raw)
                    for meth in methods:
                        routes.append(RouteItem(method=meth.upper(), path=path, file_path=rel_path))
                else:
                    routes.append(RouteItem(method="GET", path=path, file_path=rel_path))

        # ── 2. TypeScript / JavaScript (Express / NestJS / Angular / React) ──
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            # Express app.get("/api/orders", ...)
            express_matches = re.finditer(
                r"\b(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]",
                content,
                re.IGNORECASE,
            )
            for m in express_matches:
                method = m.group(1).upper()
                path = m.group(2)
                routes.append(RouteItem(method=method, path=path, file_path=rel_path))

            # NestJS @Get("/orders"), @Post("/orders")
            nestjs_matches = re.finditer(
                r"@(Get|Post|Put|Delete|Patch)\s*\(\s*['\"]?([^'\")\s]*)['\"]?\s*\)",
                content,
            )
            for m in nestjs_matches:
                method = m.group(1).upper()
                path = m.group(2) or "/"
                routes.append(RouteItem(method=method, path=path, file_path=rel_path))

            # Angular Routes: path: 'orders' or path: 'api/orders'
            ang_matches = re.finditer(
                r"path:\s*['\"]([^'\"]+)['\"]",
                content,
            )
            for m in ang_matches:
                path = m.group(1)
                if path:
                    norm_path = f"/{path.lstrip('/')}"
                    routes.append(RouteItem(method="GET", path=norm_path, file_path=rel_path))

        # ── 3. C# / .NET (ASP.NET Core Controllers & Minimal APIs) ───────────
        elif ext == ".cs":
            # Attribute routing: [HttpGet("orders")], [HttpPost("orders/{id}")]
            dotnet_attr_matches = re.finditer(
                r"\[Http(Get|Post|Put|Delete|Patch)(?:\s*\(\s*['\"]([^'\"]*)['\"]\s*\))?\]",
                content,
                re.IGNORECASE,
            )
            for m in dotnet_attr_matches:
                method = m.group(1).upper()
                subpath = m.group(2) or ""
                routes.append(RouteItem(method=method, path=f"/{subpath.lstrip('/')}", file_path=rel_path))

            # Minimal APIs: app.MapGet("/api/orders", ...), app.MapPost("/api/orders", ...)
            minimal_matches = re.finditer(
                r"\bapp\.Map(Get|Post|Put|Delete|Patch)\s*\(\s*['\"]([^'\"]+)['\"]",
                content,
                re.IGNORECASE,
            )
            for m in minimal_matches:
                method = m.group(1).upper()
                path = m.group(2)
                routes.append(RouteItem(method=method, path=path, file_path=rel_path))

        return routes

    def extract_from_directory(self, repo_path: Path) -> list[RouteItem]:
        """Recursively scan repository for all observable route contracts."""
        routes: list[RouteItem] = []
        ignored_dirs = {".git", "node_modules", "bin", "obj", ".venv", "dist", "build"}

        for p in repo_path.rglob("*"):
            if p.is_file() and not any(part in ignored_dirs for part in p.parts):
                if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".py", ".cs"):
                    rel = str(p.relative_to(repo_path)).replace("\\", "/")
                    routes.extend(self.extract_from_file(p, rel))

        # Deduplicate identical canonical routes in same file
        seen = set()
        unique_routes = []
        for r in routes:
            key = (r.canonical_id, r.file_path)
            if key not in seen:
                seen.add(key)
                unique_routes.append(r)

        return sorted(unique_routes, key=lambda x: (x.path, x.method))
