"""Configuration & Environment Variable Extractor for Gate 6.

Extracts observable configuration keys and environment variable dependencies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from amstralift.semantic.models import ConfigItem


class ConfigurationExtractor:
    """Extracts observable configuration keys and environment variables."""

    def extract_from_file(self, file_path: Path, rel_path: str) -> list[ConfigItem]:
        items: list[ConfigItem] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return items

        name = file_path.name.lower()

        # ── 1. .env files (.env, .env.example, .env.production) ──────────────
        if name.startswith(".env"):
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key = line.split("=", 1)[0].strip()
                    if key:
                        items.append(ConfigItem(key=key, source_file=rel_path))

        # ── 2. appsettings.json (.NET configuration files) ───────────────────
        elif name.startswith("appsettings") and name.endswith(".json"):
            try:
                data = json.loads(content)
                def _flatten_json(d: dict, prefix: str = ""):
                    for k, v in d.items():
                        new_key = f"{prefix}:{k}" if prefix else k
                        if isinstance(v, dict):
                            _flatten_json(v, new_key)
                        else:
                            items.append(ConfigItem(key=new_key, source_file=rel_path))
                if isinstance(data, dict):
                    _flatten_json(data)
            except Exception:
                pass

        # ── 3. Source Code Lookups ───────────────────────────────────────────
        else:
            # JavaScript/TypeScript process.env.KEY
            for m in re.finditer(r"\bprocess\.env\.([A-Z0-9_]+)", content):
                items.append(ConfigItem(key=m.group(1), source_file=rel_path))

            # Python os.environ['KEY'], os.environ.get('KEY'), os.getenv('KEY')
            for m in re.finditer(r"\bos\.(?:environ(?:\[|\.get\()|getenv\()\s*['\"]([A-Z0-9_]+)['\"]", content):
                items.append(ConfigItem(key=m.group(1), source_file=rel_path))

            # C# builder.Configuration["KEY"] or Configuration["KEY"]
            for m in re.finditer(r"\bConfiguration\[\s*['\"]([A-Za-z0-9_:]+)['\"]\s*\]", content):
                items.append(ConfigItem(key=m.group(1), source_file=rel_path))

        return items

    def extract_from_directory(self, repo_path: Path) -> list[ConfigItem]:
        items: list[ConfigItem] = []
        ignored_dirs = {".git", "node_modules", "bin", "obj", ".venv", "dist", "build"}

        for p in repo_path.rglob("*"):
            if p.is_file() and not any(part in ignored_dirs for part in p.parts):
                rel = str(p.relative_to(repo_path)).replace("\\", "/")
                items.extend(self.extract_from_file(p, rel))

        seen = set()
        unique = []
        for it in items:
            key = (it.key, it.source_file)
            if key not in seen:
                seen.add(key)
                unique.append(it)

        return sorted(unique, key=lambda x: x.key)
