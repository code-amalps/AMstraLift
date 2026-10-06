"""Exported Symbol Extractor for Gate 6.

Extracts observable public/exported API symbols (classes, functions, interfaces, types) across TypeScript/JavaScript and Python.
"""

from __future__ import annotations

import re
from pathlib import Path

from amstralift.semantic.models import SymbolItem


class ExportedSymbolExtractor:
    """Extracts observable public API surface symbols across project modules."""

    def extract_from_file(self, file_path: Path, rel_path: str) -> list[SymbolItem]:
        symbols: list[SymbolItem] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return symbols

        ext = file_path.suffix.lower()

        # ── 1. TypeScript / JavaScript Exported Symbols ──────────────────────
        if ext in (".ts", ".tsx", ".js", ".jsx"):
            # export class ClassName
            for m in re.finditer(r"\bexport\s+(?:default\s+)?class\s+([A-Za-z0-9_$]+)", content):
                symbols.append(SymbolItem(name=m.group(1), kind="class", file_path=rel_path))

            # export function functionName
            for m in re.finditer(r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)", content):
                symbols.append(SymbolItem(name=m.group(1), kind="function", file_path=rel_path))

            # export interface InterfaceName
            for m in re.finditer(r"\bexport\s+interface\s+([A-Za-z0-9_$]+)", content):
                symbols.append(SymbolItem(name=m.group(1), kind="interface", file_path=rel_path))

            # export type TypeName
            for m in re.finditer(r"\bexport\s+type\s+([A-Za-z0-9_$]+)", content):
                symbols.append(SymbolItem(name=m.group(1), kind="type", file_path=rel_path))

            # export const / let / var symbol
            for m in re.finditer(r"\bexport\s+(?:const|let|var)\s+([A-Za-z0-9_$]+)", content):
                symbols.append(SymbolItem(name=m.group(1), kind="variable", file_path=rel_path))

        # ── 2. Python Public API Symbols ─────────────────────────────────────
        elif ext == ".py":
            # Check __all__ if explicitly defined
            all_match = re.search(r"__all__\s*=\s*\[([^\]]+)\]", content)
            if all_match:
                names = re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", all_match.group(1))
                for name in names:
                    symbols.append(SymbolItem(name=name, kind="public_api", file_path=rel_path))
            else:
                # Top-level classes
                for m in re.finditer(r"^(?:class)\s+([A-Za-z0-9_]+)", content, re.MULTILINE):
                    name = m.group(1)
                    if not name.startswith("_"):
                        symbols.append(SymbolItem(name=name, kind="class", file_path=rel_path))

                # Top-level functions
                for m in re.finditer(r"^(?:async\s+)?def\s+([A-Za-z0-9_]+)", content, re.MULTILINE):
                    name = m.group(1)
                    if not name.startswith("_"):
                        symbols.append(SymbolItem(name=name, kind="function", file_path=rel_path))

        # ── 3. C# Public Types ──────────────────────────────────────────────
        elif ext == ".cs":
            for m in re.finditer(r"\bpublic\s+(?:sealed\s+|static\s+|abstract\s+)?(class|interface|struct|record)\s+([A-Za-z0-9_]+)", content):
                kind = m.group(1)
                name = m.group(2)
                symbols.append(SymbolItem(name=name, kind=kind, file_path=rel_path))

        return symbols

    def extract_from_directory(self, repo_path: Path) -> list[SymbolItem]:
        symbols: list[SymbolItem] = []
        ignored_dirs = {".git", "node_modules", "bin", "obj", ".venv", "dist", "build"}

        for p in repo_path.rglob("*"):
            if p.is_file() and not any(part in ignored_dirs for part in p.parts):
                if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".py", ".cs"):
                    rel = str(p.relative_to(repo_path)).replace("\\", "/")
                    symbols.extend(self.extract_from_file(p, rel))

        seen = set()
        unique = []
        for s in symbols:
            key = (s.canonical_id, s.file_path)
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return sorted(unique, key=lambda x: (x.name, x.kind))
