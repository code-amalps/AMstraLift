"""Deterministic Angular Standalone Component and Material Template Modernizer.

Handles:
1. Automated inference and injection of standalone @Component `imports: [...]` and `schemas: [...]`
   to address Angular 19+ schematic regression where components without `standalone: false`
   are skipped during `convert-to-standalone`, leading to missing imports.
2. Material template modernization (e.g. <mat-placeholder> -> <mat-label>, <mat-chip-list> -> <mat-chip-set>).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


EXCLUDED_DIRS = {"node_modules", ".angular", ".nx", ".git", "dist", "coverage", ".venv"}

M2_PALETTES = (
    "red-palette",
    "pink-palette",
    "indigo-palette",
    "purple-palette",
    "deep-purple-palette",
    "blue-palette",
    "light-blue-palette",
    "cyan-palette",
    "teal-palette",
    "green-palette",
    "light-green-palette",
    "lime-palette",
    "yellow-palette",
    "amber-palette",
    "orange-palette",
    "deep-orange-palette",
    "brown-palette",
    "grey-palette",
    "gray-palette",
    "blue-grey-palette",
    "blue-gray-palette",
    "light-theme-background-palette",
    "dark-theme-background-palette",
    "light-theme-foreground-palette",
    "dark-theme-foreground-palette",
)

M2_FUNCTIONS = (
    "define-light-theme",
    "define-dark-theme",
    "define-palette",
    "get-contrast-color-from-palette",
    "get-color-from-palette",
    "get-color-config",
    "get-typography-config",
    "get-density-config",
    "define-typography-config",
    "define-legacy-typography-config",
    "define-typography-level",
    "define-rem-typography-config",
)


def compute_relative_import(from_file: Path, to_file: Path) -> str:
    """Compute relative import path from one TypeScript file to another."""
    rel = os.path.relpath(to_file, from_file.parent).replace("\\", "/")
    if rel.endswith(".ts"):
        rel = rel[:-3]
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def modernize_angular_material_templates(repo_path: Path) -> list[str]:
    """Modernize deprecated Angular Material template elements across the repository.

    - Replaces <mat-placeholder> with <mat-label> (removed in Material 15+)
    - Replaces <mat-chip-list> with <mat-chip-set> (migrated to MDC in Material 15+)
    """
    applied = []
    modified_count = 0

    for ext in ("*.html", "*.ts"):
        for file_path in repo_path.rglob(ext):
            if any(p in EXCLUDED_DIRS for p in file_path.parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            modified = False
            if "<mat-placeholder" in content:
                content = content.replace("<mat-placeholder", "<mat-label").replace("</mat-placeholder>", "</mat-label>")
                modified = True
            if "<mat-chip-list" in content:
                content = content.replace("<mat-chip-list", "<mat-chip-set").replace("</mat-chip-list>", "</mat-chip-set>")
                modified = True

            if modified:
                try:
                    file_path.write_text(content, encoding="utf-8")
                    modified_count += 1
                except Exception:
                    pass

    if modified_count > 0:
        applied.append(
            f"Modernized {modified_count} template(s) replacing deprecated Material elements (<mat-placeholder> -> <mat-label>, <mat-chip-list> -> <mat-chip-set>)"
        )
    return applied


def modernize_angular_standalone_components(repo_path: Path) -> list[str]:
    """Ensure all components in the repository have valid standalone imports and schemas.

    Solves the Angular 19+ standalone schematic regression where legacy components without
    `standalone: false` are skipped for `imports: [...]` injection, leading to template
    compilation errors for shared pipes and elements.
    """
    applied = []

    # 1. Locate SharedModule
    shared_files = list(repo_path.rglob("*shared.module.ts"))
    shared_module_path = shared_files[0] if shared_files else None
    shared_dir = shared_module_path.parent if shared_module_path else None

    # 2. Build Component Registry mapping selectors to class name and path
    component_files = [
        f for f in repo_path.rglob("*.component.ts")
        if not any(p in EXCLUDED_DIRS for p in f.parts)
    ]

    registry: dict[str, dict[str, Any]] = {}
    for comp_file in component_files:
        try:
            txt = comp_file.read_text(encoding="utf-8")
        except Exception:
            continue

        sel_match = re.search(r"selector\s*:\s*['\"]([^'\"]+)['\"]", txt)
        cls_match = re.search(r"export\s+class\s+([A-Za-z0-9_]+)", txt)
        if sel_match and cls_match:
            is_shared = (shared_dir is not None and comp_file.is_relative_to(shared_dir))
            registry[sel_match.group(1)] = {
                "class_name": cls_match.group(1),
                "path": comp_file,
                "is_shared": is_shared,
            }

    modified_count = 0

    # 3. Modernize Component Imports
    for comp_file in component_files:
        try:
            content = comp_file.read_text(encoding="utf-8")
        except Exception:
            continue

        if "@Component" not in content:
            continue

        dec_match = re.search(r"@Component\s*\(\s*\{", content)
        if not dec_match:
            continue

        open_brace = content.find("{", dec_match.start())
        depth = 1
        i = open_brace + 1
        in_str = None
        dec_end = -1
        while i < len(content):
            c = content[i]
            if in_str:
                if c == in_str and content[i - 1] != "\\":
                    in_str = None
            elif c in ('"', "'", "`"):
                in_str = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    dec_end = i
                    break
            i += 1

        if dec_end == -1:
            continue

        dec_content = content[open_brace + 1:dec_end]

        # Extract template content
        template_content = ""
        html_file = comp_file.with_suffix(".html")
        if html_file.exists():
            try:
                template_content = html_file.read_text(encoding="utf-8")
            except Exception:
                template_content = ""
        else:
            inline_match = re.search(r"template\s*:\s*(['\"`])(.*?)\1", dec_content, re.DOTALL)
            if inline_match:
                template_content = inline_match.group(2)

        combined_text = content + "\n" + template_content

        # Handle existing imports if present
        if "imports:" in dec_content:
            existing_imp_match = re.search(r"imports\s*:\s*\[(.*?)\]", dec_content, re.DOTALL)
            if existing_imp_match:
                existing_imports = [x.strip() for x in existing_imp_match.group(1).split(",") if x.strip()]
                extra_top = []
                modified_existing = False

                if "<mat-sidenav" in template_content and "MatSidenavModule" not in existing_imports:
                    existing_imports.append("MatSidenavModule")
                    extra_top.append("import { MatSidenavModule } from '@angular/material/sidenav';")
                    modified_existing = True
                if "<mat-toolbar" in template_content and "MatToolbarModule" not in existing_imports:
                    existing_imports.append("MatToolbarModule")
                    extra_top.append("import { MatToolbarModule } from '@angular/material/toolbar';")
                    modified_existing = True

                if modified_existing:
                    new_dec = (
                        dec_content[:existing_imp_match.start(1)]
                        + ", ".join(existing_imports)
                        + dec_content[existing_imp_match.end(1):]
                    )
                    new_top = "\n".join(extra_top) + "\n" if extra_top else ""
                    content = new_top + content[:open_brace + 1] + new_dec + content[dec_end:]
                    try:
                        comp_file.write_text(content, encoding="utf-8")
                        modified_count += 1
                    except Exception:
                        pass
            continue

        # Injects imports where missing
        imports_to_add: list[str] = []
        top_imports: dict[str, str] = {}
        schemas_to_add: list[str] = []

        is_shared_comp = (shared_dir is not None and comp_file.is_relative_to(shared_dir))

        if is_shared_comp:
            imports_to_add.append("CommonModule")
            top_imports["CommonModule"] = "@angular/common"
            if "<mat-card" in combined_text:
                imports_to_add.append("MatCardModule")
                top_imports["MatCardModule"] = "@angular/material/card"
            if "<mat-icon" in combined_text:
                imports_to_add.append("MatIconModule")
                top_imports["MatIconModule"] = "@angular/material/icon"
            if any(btn in combined_text for btn in ("mat-button", "mat-raised-button", "mat-icon-button", "mat-stroked-button", "mat-flat-button")):
                imports_to_add.append("MatButtonModule")
                top_imports["MatButtonModule"] = "@angular/material/button"
            if "<fa-icon" in combined_text:
                imports_to_add.append("FontAwesomeModule")
                top_imports["FontAwesomeModule"] = "@fortawesome/angular-fontawesome"
        else:
            if shared_module_path:
                imports_to_add.append("SharedModule")
                rel_shared = compute_relative_import(comp_file, shared_module_path)
                top_imports["SharedModule"] = rel_shared
            else:
                imports_to_add.append("CommonModule")
                top_imports["CommonModule"] = "@angular/common"

        # Check router
        if comp_file.name == "app.component.ts" or "<router-outlet" in template_content or "routerLink" in template_content:
            imports_to_add.append("RouterModule")
            top_imports["RouterModule"] = "@angular/router"

        # Check Sidenav & Toolbar
        if "<mat-sidenav" in template_content:
            imports_to_add.append("MatSidenavModule")
            top_imports["MatSidenavModule"] = "@angular/material/sidenav"
        if "<mat-toolbar" in template_content:
            imports_to_add.append("MatToolbarModule")
            top_imports["MatToolbarModule"] = "@angular/material/toolbar"

        # Check child components from registry
        for sel, info in registry.items():
            if info["path"] != comp_file and f"<{sel}" in template_content:
                if not info["is_shared"]:
                    cls_name = info["class_name"]
                    imports_to_add.append(cls_name)
                    top_imports[cls_name] = compute_relative_import(comp_file, info["path"])

        # Check custom elements / schemas
        if "<mwc-" in template_content or "*axLazyElement" in template_content:
            schemas_to_add.append("CUSTOM_ELEMENTS_SCHEMA")
            imports_to_add.append("LazyElementsModule")
            top_imports["LazyElementsModule"] = "@angular-extensions/elements"

        unique_imports = list(dict.fromkeys(imports_to_add))
        if not unique_imports:
            continue

        to_inject = f",\n  imports: [{', '.join(unique_imports)}]"
        if schemas_to_add and "schemas:" not in dec_content:
            to_inject += f",\n  schemas: [{', '.join(schemas_to_add)}]"

        # Add top import statements
        new_top_lines = []
        for sym, mod in top_imports.items():
            if not re.search(rf"\b{sym}\b", content.split("@Component")[0]):
                new_top_lines.append(f"import {{ {sym} }} from '{mod}';")

        if schemas_to_add:
            core_import = re.search(r"import\s*\{([^}]+)\}\s*from\s*['\"]@angular/core['\"]", content)
            if core_import:
                if "CUSTOM_ELEMENTS_SCHEMA" not in core_import.group(1):
                    content = content[:core_import.start(1)] + "CUSTOM_ELEMENTS_SCHEMA, " + content[core_import.start(1):]
                    dec_match = re.search(r"@Component\s*\(\s*\{", content)
                    if dec_match:
                        open_brace = content.find("{", dec_match.start())
                        depth = 1
                        i = open_brace + 1
                        in_str = None
                        dec_end = -1
                        while i < len(content):
                            c = content[i]
                            if in_str:
                                if c == in_str and content[i - 1] != "\\":
                                    in_str = None
                            elif c in ('"', "'", "`"):
                                in_str = c
                            elif c == "{":
                                depth += 1
                            elif c == "}":
                                depth -= 1
                                if depth == 0:
                                    dec_end = i
                                    break
                            i += 1
            else:
                new_top_lines.append("import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';")

        new_top_str = "\n".join(new_top_lines) + ("\n" if new_top_lines else "")
        new_content = new_top_str + content[:dec_end] + to_inject + content[dec_end:]
        try:
            comp_file.write_text(new_content, encoding="utf-8")
            modified_count += 1
        except Exception:
            pass

    if modified_count > 0:
        applied.append(f"Modernized {modified_count} standalone component(s) with required imports and schemas")

    return applied
