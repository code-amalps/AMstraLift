"""Deterministic Angular Standalone Component, Module, and Material Template Modernizer.

Handles:
1. Automated inference and injection of standalone @Component `standalone: true`, `imports: [...]`
   and `schemas: [...]` across ALL Angular components (regardless of file naming: *.component.ts or *.ts).
   Addresses Angular 19+ schematic regression where legacy components without `standalone: false`
   are skipped during `convert-to-standalone`, leading to missing imports.
2. Automated transformation of @NgModule declarations: moves components converted to standalone
   from `declarations: [...]` to `imports: [...]`, preventing Angular compiler errors.
3. Material template modernization (e.g. <mat-placeholder> -> <mat-label>, <mat-chip-list> -> <mat-chip-set>).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from amstralift.core.workspace import safe_rglob


EXCLUDED_DIRS = {
    "node_modules",
    ".angular",
    ".nx",
    ".turbo",
    ".next",
    ".nuxt",
    ".cache",
    ".git",
    "dist",
    "out-tsc",
    "coverage",
    ".venv",
    "venv",
    "env",
    "bin",
    "obj",
    "__pycache__",
}

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

MATERIAL_MODULE_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("<mat-card",), "MatCardModule", "@angular/material/card"),
    (
        ("<mat-button", "mat-button", "mat-raised-button", "mat-icon-button", "mat-stroked-button", "mat-flat-button", "mat-fab", "mat-mini-fab"),
        "MatButtonModule",
        "@angular/material/button",
    ),
    (("<mat-icon",), "MatIconModule", "@angular/material/icon"),
    (("<mat-form-field",), "MatFormFieldModule", "@angular/material/form-field"),
    (("matInput",), "MatInputModule", "@angular/material/input"),
    (("<mat-select",), "MatSelectModule", "@angular/material/select"),
    (("<mat-checkbox",), "MatCheckboxModule", "@angular/material/checkbox"),
    (("<mat-sidenav", "<mat-drawer"), "MatSidenavModule", "@angular/material/sidenav"),
    (("<mat-toolbar",), "MatToolbarModule", "@angular/material/toolbar"),
    (("<mat-list", "<mat-nav-list", "<mat-list-item"), "MatListModule", "@angular/material/list"),
    (("<mat-menu", "matMenuTriggerFor"), "MatMenuModule", "@angular/material/menu"),
    (("<mat-tab-group", "<mat-tab"), "MatTabsModule", "@angular/material/tabs"),
    (("matTooltip",), "MatTooltipModule", "@angular/material/tooltip"),
    (("<mat-progress-bar",), "MatProgressBarModule", "@angular/material/progress-bar"),
    (("<mat-spinner", "<mat-progress-spinner"), "MatProgressSpinnerModule", "@angular/material/progress-spinner"),
    (("<mat-dialog-content", "<mat-dialog-actions", "<mat-dialog-title"), "MatDialogModule", "@angular/material/dialog"),
    (("<mat-chip", "<mat-chip-set", "<mat-chip-list"), "MatChipsModule", "@angular/material/chips"),
    (("<mat-table",), "MatTableModule", "@angular/material/table"),
    (("<mat-paginator",), "MatPaginatorModule", "@angular/material/paginator"),
    (("<mat-sort",), "MatSortModule", "@angular/material/sort"),
    (("<mat-expansion-panel", "<mat-accordion"), "MatExpansionModule", "@angular/material/expansion"),
    (("<mat-slide-toggle",), "MatSlideToggleModule", "@angular/material/slide-toggle"),
    (("<mat-slider",), "MatSliderModule", "@angular/material/slider"),
    (("<mat-radio-group", "<mat-radio-button"), "MatRadioModule", "@angular/material/radio"),
    (("<mat-badge", "matBadge"), "MatBadgeModule", "@angular/material/badge"),
    (("<mat-divider",), "MatDividerModule", "@angular/material/divider"),
    (("<mat-autocomplete",), "MatAutocompleteModule", "@angular/material/autocomplete"),
    (("<mat-tree",), "MatTreeModule", "@angular/material/tree"),
    (("<mat-bottom-sheet",), "MatBottomSheetModule", "@angular/material/bottom-sheet"),
    (("<mat-stepper",), "MatStepperModule", "@angular/material/stepper"),
    (("<mat-snack-bar",), "MatSnackBarModule", "@angular/material/snack-bar"),
]

ROUTER_TOKENS = ("<router-outlet", "routerLink", "routerLinkActive", "router-link", "router-outlet")
REACTIVE_FORMS_TOKENS = ("[formGroup]", "formControlName", "[formControl]", "formArrayName")
REACTIVE_FORMS_CLASS_TOKENS = (
    "UntypedFormBuilder",
    "FormBuilder",
    "UntypedFormGroup",
    "FormGroup",
    "UntypedFormControl",
    "FormControl",
    "UntypedFormArray",
    "FormArray",
)
TEMPLATE_FORMS_TOKENS = ("[(ngModel)]", "[ngModel]", "ngForm", "ngModelGroup")
COMMON_TOKENS = (
    "*ngIf",
    "*ngFor",
    "[ngClass]",
    "[ngStyle]",
    "[ngSwitch]",
    "*ngSwitchCase",
    "*ngSwitchDefault",
    "*ngTemplateOutlet",
    "| async",
    "| date",
    "| currency",
    "| decimal",
    "| percent",
    "| json",
    "| slice",
    "| lowercase",
    "| uppercase",
    "| titlecase",
    "| keyvalue",
)


def compute_relative_import(from_file: Path, to_file: Path) -> str:
    """Compute relative import path from one TypeScript file to another."""
    rel = os.path.relpath(to_file, from_file.parent).replace("\\", "/")
    if rel.endswith(".ts"):
        rel = rel[:-3]
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


def _inject_top_imports(content: str, symbols_to_add: dict[str, str]) -> str:
    """Inject missing import { Symbol } from 'module'; statements at the top of the file."""
    # Purge any invalid single-letter symbols or chunk module paths
    clean_symbols = {
        sym: mod
        for sym, mod in symbols_to_add.items()
        if len(sym) >= 2 and ".d-" not in mod and "overlay.d" not in mod
    }
    if not clean_symbols:
        return content

    new_lines: list[str] = []
    modified_content = content

    for sym, mod in clean_symbols.items():
        # Check if symbol is already imported
        if re.search(rf"\b{re.escape(sym)}\b", modified_content.split("@Component")[0]):
            continue

        # Check if the module is already imported
        mod_pattern = rf"(?m)^import\s*\{{([^}}]+)\}}\s*from\s*['\"]{re.escape(mod)}['\"];?"
        match = re.search(mod_pattern, modified_content)
        if match:
            existing_symbols = [s.strip() for s in match.group(1).split(",") if s.strip()]
            if sym not in existing_symbols:
                existing_symbols.append(sym)
                replacement = f"import {{ {', '.join(existing_symbols)} }} from '{mod}';"
                modified_content = (
                    modified_content[:match.start()] + replacement + modified_content[match.end():]
                )
        else:
            new_lines.append(f"import {{ {sym} }} from '{mod}';")

    if new_lines:
        prefix = "\n".join(new_lines) + "\n"
        modified_content = prefix + modified_content

    return modified_content


def modernize_angular_material_templates(repo_path: Path) -> list[str]:
    """Modernize deprecated Angular Material template elements across the repository.

    - Replaces <mat-placeholder> with <mat-label> (removed in Material 15+)
    - Replaces <mat-chip-list> with <mat-chip-set> (migrated to MDC in Material 15+)
    """
    applied = []
    modified_count = 0

    for file_path in safe_rglob(repo_path, ["*.html", "*.ts"]):
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


def modernize_ngmodules_with_standalone_components(
    repo_path: Path, standalone_classes: set[str]
) -> list[str]:
    """Update NgModules by moving standalone component classes from declarations to imports."""
    if not standalone_classes:
        return []

    applied = []
    for ts_file in safe_rglob(repo_path, "*.ts"):
        if ts_file.name.endswith(".spec.ts") or ts_file.name.endswith(".d.ts"):
            continue

        try:
            content = ts_file.read_text(encoding="utf-8")
        except Exception:
            continue

        if "@NgModule" not in content:
            continue

        dec_match = re.search(r"@NgModule\s*\(\s*\{", content)
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
        decl_match = re.search(r"declarations\s*:\s*\[(.*?)\]", dec_content, re.DOTALL)
        if not decl_match:
            continue

        raw_decls = [s.strip() for s in decl_match.group(1).split(",") if s.strip()]
        moving = [s for s in raw_decls if s in standalone_classes]
        if not moving:
            continue

        remaining_decls = [s for s in raw_decls if s not in standalone_classes]

        # Update or create imports: [...]
        imp_match = re.search(r"imports\s*:\s*\[(.*?)\]", dec_content, re.DOTALL)
        if imp_match:
            existing_imps = [s.strip() for s in imp_match.group(1).split(",") if s.strip()]
            for s in moving:
                if s not in existing_imps:
                    existing_imps.append(s)
            new_imps_str = f"imports: [{', '.join(existing_imps)}]"
            new_dec_content = (
                dec_content[:imp_match.start()] + new_imps_str + dec_content[imp_match.end():]
            )
        else:
            new_imps_str = f"imports: [{', '.join(moving)}]"
            new_dec_content = dec_content.rstrip() + f",\n  {new_imps_str}"

        # Now update or remove declarations
        decl_in_new = re.search(r"declarations\s*:\s*\[(.*?)\]", new_dec_content, re.DOTALL)
        if decl_in_new:
            if remaining_decls:
                new_decl_str = f"declarations: [{', '.join(remaining_decls)}]"
                new_dec_content = (
                    new_dec_content[:decl_in_new.start()]
                    + new_decl_str
                    + new_dec_content[decl_in_new.end():]
                )
            else:
                # Remove declarations entirely
                prefix = new_dec_content[:decl_in_new.start()].rstrip()
                suffix = new_dec_content[decl_in_new.end():].lstrip()
                if prefix.endswith(","):
                    prefix = prefix[:-1].rstrip()
                elif suffix.startswith(","):
                    suffix = suffix[1:].lstrip()
                new_dec_content = prefix + ("\n  " if prefix and suffix else "") + suffix

        new_full_content = content[:open_brace + 1] + new_dec_content + content[dec_end:]
        try:
            ts_file.write_text(new_full_content, encoding="utf-8")
            cls_name_match = re.search(r"export\s+class\s+([A-Za-z0-9_]+)", content)
            mod_name = cls_name_match.group(1) if cls_name_match else ts_file.name
            applied.append(
                f"Modernized NgModule '{mod_name}' moving {len(moving)} component(s) from declarations to imports"
            )
        except Exception:
            pass

    return applied


def modernize_angular_standalone_components(repo_path: Path) -> list[str]:
    """Ensure all components in the repository have valid standalone imports and schemas.

    Solves the Angular 19+ standalone schematic regression where legacy components without
    `standalone: false` are skipped for `imports: [...]` injection, leading to template
    compilation errors for shared pipes, forms, routing, and material elements.
    """
    applied = []

    # 1. Locate SharedModule
    shared_files = safe_rglob(repo_path, "*shared.module.ts")
    shared_module_path = shared_files[0] if shared_files else None
    shared_dir = shared_module_path.parent if shared_module_path else None

    # 2. Build Component Registry mapping selectors to class name and path across all *.ts files
    component_files: list[Path] = []
    for f in safe_rglob(repo_path, "*.ts"):
        f_name = f.name
        f_posix = f.as_posix()
        # Exclude spec, test, declaration files, and bundler cache/chunk artifacts
        if (
            f_name.endswith(".spec.ts")
            or f_name.endswith(".test.ts")
            or f_name.endswith(".d.ts")
            or ".d-" in f_name
            or ".cache" in f_posix
            or "/dist/" in f_posix
            or "/out-tsc/" in f_posix
            or "/node_modules/" in f_posix
            or "/.angular/" in f_posix
        ):
            continue
        try:
            head = f.read_text(encoding="utf-8", errors="ignore")
            if "@Component" in head:
                component_files.append(f)
        except Exception:
            continue

    registry: dict[str, dict[str, Any]] = {}
    standalone_classes: set[str] = set()

    for comp_file in component_files:
        try:
            txt = comp_file.read_text(encoding="utf-8")
        except Exception:
            continue

        sel_match = re.search(r"selector\s*:\s*['\"]([^'\"]+)['\"]", txt)
        cls_match = re.search(r"export\s+class\s+([A-Za-z0-9_]+)", txt)
        if sel_match and cls_match:
            sel = sel_match.group(1).strip()
            cls_name = cls_match.group(1).strip()
            # Angular custom component selectors MUST contain a hyphen (e.g. app-*, rtcm-*)
            # and be at least 3 characters. Single-character names or non-hyphenated HTML tags must never be registered.
            if len(sel) < 3 or "-" not in sel or len(cls_name) < 2:
                continue
            is_shared = (shared_dir is not None and comp_file.is_relative_to(shared_dir))
            registry[sel] = {
                "class_name": cls_name,
                "path": comp_file,
                "is_shared": is_shared,
            }

    modified_count = 0

    # 3. Modernize Component Imports and mark standalone: true
    for comp_file in component_files:
        try:
            content = comp_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Purge any bogus declaration chunk imports (e.g. from '@angular/material/date-adapter.d-CtKXiXkO' or '@angular/cdk/overlay.d-BdoMyOhX')
        content_sanitized = False
        if ".d-" in content:
            chunk_import_pattern = re.compile(
                r"(?m)^import\s*(?:type\s+)?\{([^}]+)\}\s*from\s*['\"][^'\"]*\.d-[A-Za-z0-9_-]+[^'\"]*['\"];?\s*\n?"
            )
            c_matches = list(chunk_import_pattern.finditer(content))
            if c_matches:
                for cm in reversed(c_matches):
                    symbols = [s.strip() for s in cm.group(1).split(",") if s.strip()]
                    type_fallbacks = []
                    for sym in symbols:
                        clean_sym = sym.split()[-1]
                        rest_of_code = content[:cm.start()] + content[cm.end():]
                        if re.search(rf"\b{re.escape(clean_sym)}\b", rest_of_code):
                            type_fallbacks.append(f"type {clean_sym} = any;")
                    replacement = ("\n".join(type_fallbacks) + "\n") if type_fallbacks else ""
                    content = content[:cm.start()] + replacement + content[cm.end():]
                    content_sanitized = True

            generic_chunk_pattern = re.compile(
                r"(?m)^import\s+[^;]*from\s*['\"][^'\"]*\.d-[A-Za-z0-9_-]+[^'\"]*['\"];?\s*\n?"
            )
            if generic_chunk_pattern.search(content):
                content = generic_chunk_pattern.sub("", content)
                content_sanitized = True

        single_letter_pattern = re.compile(
            r"(?m)^import\s*\{\s*[A-Z]\s*\}\s*from\s*['\"][^'\"]*['\"];?\s*\n?"
        )
        if single_letter_pattern.search(content):
            content = single_letter_pattern.sub("", content)
            content_sanitized = True

        if "@Component" not in content:
            if content_sanitized:
                try:
                    comp_file.write_text(content, encoding="utf-8")
                except Exception:
                    pass
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
        class_content = content[dec_end:]

        # Extract component class name
        cls_match = re.search(r"export\s+class\s+([A-Za-z0-9_]+)", class_content)
        cls_name = cls_match.group(1) if cls_match else comp_file.stem

        # Extract template content
        template_content = ""
        tmpl_url_match = re.search(r"templateUrl\s*:\s*['\"]([^'\"]+)['\"]", dec_content)
        if tmpl_url_match:
            tmpl_path = (comp_file.parent / tmpl_url_match.group(1)).resolve()
            if tmpl_path.exists():
                try:
                    template_content = tmpl_path.read_text(encoding="utf-8")
                except Exception:
                    pass

        if not template_content:
            html_file = comp_file.with_suffix(".html")
            if html_file.exists():
                try:
                    template_content = html_file.read_text(encoding="utf-8")
                except Exception:
                    pass

        if not template_content:
            inline_match = re.search(r"template\s*:\s*(['\"`])(.*?)\1", dec_content, re.DOTALL)
            if inline_match:
                template_content = inline_match.group(2)

        # Strip HTML comments from template to prevent matching commented-out elements
        active_template = re.sub(r"<!--[\s\S]*?-->", "", template_content)
        combined_text = content + "\n" + active_template

        # Determine required imports and schemas
        imports_to_add: list[str] = []
        top_imports: dict[str, str] = {}
        schemas_to_add: list[str] = []

        is_shared_comp = (shared_dir is not None and comp_file.is_relative_to(shared_dir))

        # Check CommonModule
        if not is_shared_comp and shared_module_path:
            imports_to_add.append("SharedModule")
            top_imports["SharedModule"] = compute_relative_import(comp_file, shared_module_path)
        else:
            imports_to_add.append("CommonModule")
            top_imports["CommonModule"] = "@angular/common"

        # Check Angular Router
        has_router_elements = any(tok in template_content for tok in ROUTER_TOKENS)
        is_router_host = (
            comp_file.name == "app.component.ts"
            or cls_name == "AppComponent"
            or "shell" in comp_file.name.lower()
            or "shell" in cls_name.lower()
        )
        if has_router_elements or is_router_host:
            imports_to_add.append("RouterModule")
            top_imports["RouterModule"] = "@angular/router"

        # Check Reactive Forms
        has_reactive_forms = (
            any(tok in template_content for tok in REACTIVE_FORMS_TOKENS)
            or any(tok in class_content for tok in REACTIVE_FORMS_CLASS_TOKENS)
        )
        if has_reactive_forms:
            imports_to_add.append("ReactiveFormsModule")
            top_imports["ReactiveFormsModule"] = "@angular/forms"

        # Check Template-driven Forms
        has_template_forms = any(tok in template_content for tok in TEMPLATE_FORMS_TOKENS)
        if has_template_forms:
            imports_to_add.append("FormsModule")
            top_imports["FormsModule"] = "@angular/forms"

        # Check Angular Material Modules
        for tokens, mod_name, mod_pkg in MATERIAL_MODULE_RULES:
            if any(t in combined_text for t in tokens):
                imports_to_add.append(mod_name)
                top_imports[mod_name] = mod_pkg

        # Check FontAwesome
        if "<fa-icon" in combined_text:
            imports_to_add.append("FontAwesomeModule")
            top_imports["FontAwesomeModule"] = "@fortawesome/angular-fontawesome"

        # Check AG Grid
        if "<ag-grid-angular" in combined_text:
            imports_to_add.append("AgGridAngular")
            top_imports["AgGridAngular"] = "ag-grid-angular"

        # Check child components from registry
        for sel, info in registry.items():
            if info["path"] != comp_file and re.search(rf"<{re.escape(sel)}[\s>/]", active_template):
                if not info["is_shared"]:
                    child_cls = info["class_name"]
                    imports_to_add.append(child_cls)
                    top_imports[child_cls] = compute_relative_import(comp_file, info["path"])

        # Check custom elements / schemas
        if "<mwc-" in active_template or "*axLazyElement" in active_template:
            schemas_to_add.append("CUSTOM_ELEMENTS_SCHEMA")
            imports_to_add.append("LazyElementsModule")
            top_imports["LazyElementsModule"] = "@angular-extensions/elements"

        unique_needed = list(dict.fromkeys(imports_to_add))

        # Check existing decorator fields
        existing_imp_match = re.search(r"imports\s*:\s*\[(.*?)\]", dec_content, re.DOTALL)

        modified_dec = False
        new_dec_content = dec_content

        if existing_imp_match:
            existing_imports = [x.strip() for x in existing_imp_match.group(1).split(",") if x.strip()]
            cleaned_existing = [x for x in existing_imports if len(x) > 1 and x not in ("R", "D")]
            if "<ag-grid-angular" in combined_text and "AgGridModule" in cleaned_existing:
                cleaned_existing = ["AgGridAngular" if x == "AgGridModule" else x for x in cleaned_existing]
                top_imports["AgGridAngular"] = "ag-grid-angular"
            had_r = len(cleaned_existing) != len(existing_imports)
            missing_imps = [x for x in unique_needed if x not in cleaned_existing]
            if missing_imps or had_r:
                updated_imports = cleaned_existing + missing_imps
                new_imp_str = f"imports: [{', '.join(updated_imports)}]"
                new_dec_content = (
                    new_dec_content[:existing_imp_match.start()]
                    + new_imp_str
                    + new_dec_content[existing_imp_match.end():]
                )
                modified_dec = True
            # Also ensure standalone: true is present and convert standalone: false
            if re.search(r"\bstandalone\s*:\s*false\b", new_dec_content):
                new_dec_content = re.sub(r"\bstandalone\s*:\s*false\b", "standalone: true", new_dec_content)
                modified_dec = True
            elif not re.search(r"\bstandalone\s*:\s*true\b", new_dec_content):
                new_dec_content = "standalone: true,\n  " + new_dec_content.lstrip()
                modified_dec = True
        else:
            to_add_entries: list[str] = []
            if re.search(r"\bstandalone\s*:\s*false\b", new_dec_content):
                new_dec_content = re.sub(r"\bstandalone\s*:\s*false\b", "standalone: true", new_dec_content)
                modified_dec = True
            elif not re.search(r"\bstandalone\s*:\s*true\b", new_dec_content):
                to_add_entries.append("standalone: true")
            if unique_needed:
                to_add_entries.append(f"imports: [{', '.join(unique_needed)}]")
            if schemas_to_add and "schemas:" not in new_dec_content:
                to_add_entries.append(f"schemas: [{', '.join(schemas_to_add)}]")

            if to_add_entries:
                clean_dec = new_dec_content.rstrip()
                while clean_dec.endswith(","):
                    clean_dec = clean_dec[:-1].rstrip()
                if clean_dec:
                    new_dec_content = clean_dec + ",\n  " + ",\n  ".join(to_add_entries)
                else:
                    new_dec_content = "\n  " + ",\n  ".join(to_add_entries)
                modified_dec = True

        # Clean up any duplicate commas (e.g. ',,' from previous runs)
        fixed_dec = re.sub(r",\s*,+", ",", new_dec_content)
        if fixed_dec != new_dec_content:
            new_dec_content = fixed_dec
            modified_dec = True

        if content_sanitized:
            modified_dec = True

        if modified_dec:
            # Reconstruct content with updated decorator
            updated_full = content[:open_brace + 1] + new_dec_content + content[dec_end:]
            # Inject top-level imports
            updated_full = _inject_top_imports(updated_full, top_imports)

            # Handle schemas import if needed
            if schemas_to_add:
                core_import = re.search(r"import\s*\{([^}]+)\}\s*from\s*['\"]@angular/core['\"]", updated_full)
                if core_import:
                    if "CUSTOM_ELEMENTS_SCHEMA" not in core_import.group(1):
                        updated_full = (
                            updated_full[:core_import.start(1)]
                            + "CUSTOM_ELEMENTS_SCHEMA, "
                            + updated_full[core_import.start(1):]
                        )
                else:
                    updated_full = "import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';\n" + updated_full

            try:
                comp_file.write_text(updated_full, encoding="utf-8")
                modified_count += 1
                standalone_classes.add(cls_name)
            except Exception:
                pass
        else:
            standalone_classes.add(cls_name)

    if modified_count > 0:
        applied.append(f"Modernized {modified_count} component(s) with standalone: true, imports, and schemas")

    # 4. Modernize NgModules by moving standalone components from declarations to imports
    module_notes = modernize_ngmodules_with_standalone_components(repo_path, standalone_classes)
    applied.extend(module_notes)

    # 5. Sanitize importProvidersFrom calls across workspace (prevent NG0800)
    provider_notes = sanitize_import_providers_from(repo_path)
    applied.extend(provider_notes)

    return applied


def _find_matching_paren(text: str, open_idx: int) -> int:
    """Find index of closing paren matching text[open_idx], respecting quotes and comments."""
    depth = 0
    in_str = None
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == in_str and text[i - 1] != "\\":
                in_str = None
        elif c in ('"', "'", "`"):
            in_str = c
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            eol = text.find("\n", i + 2)
            if eol == -1:
                break
            i = eol
            continue
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            end_comment = text.find("*/", i + 2)
            if end_comment == -1:
                break
            i = end_comment + 1
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_args(inner_args: str) -> list[str]:
    """Split comma-separated arguments at top level (depth 0 of (), [], {})."""
    args = []
    current: list[str] = []
    depth = 0
    in_str = None
    i = 0
    n = len(inner_args)

    while i < n:
        c = inner_args[i]
        if in_str:
            if c == in_str and inner_args[i - 1] != "\\":
                in_str = None
            current.append(c)
        elif c in ('"', "'", "`"):
            in_str = c
            current.append(c)
        elif c == "/" and i + 1 < n and inner_args[i + 1] == "/":
            eol = inner_args.find("\n", i + 2)
            if eol == -1:
                current.append(inner_args[i:])
                break
            current.append(inner_args[i:eol])
            i = eol - 1
        elif c == "/" and i + 1 < n and inner_args[i + 1] == "*":
            end_comment = inner_args.find("*/", i + 2)
            if end_comment == -1:
                current.append(inner_args[i:])
                break
            current.append(inner_args[i:end_comment + 2])
            i = end_comment + 1
        elif c in "([{":
            depth += 1
            current.append(c)
        elif c in ")]}":
            depth -= 1
            current.append(c)
        elif c == "," and depth == 0:
            arg = "".join(current).strip()
            if arg:
                args.append(arg)
            current = []
        else:
            current.append(c)
        i += 1

    last_arg = "".join(current).strip()
    if last_arg:
        args.append(last_arg)

    return args


def _clean_unused_symbol_from_imports(content: str, symbol: str) -> str:
    """Remove symbol from named import statements if it is not referenced elsewhere."""
    lines = content.splitlines()
    non_import_code = "\n".join(
        line for line in lines if not re.match(r"^\s*import\s+", line)
    )
    if re.search(rf"\b{re.escape(symbol)}\b", non_import_code):
        return content

    # Find import { ..., symbol, ... } from '...'
    pattern = re.compile(
        r"(?m)^([ \t]*import\s*\{)([^}]+)(\}\s*from\s*['\"][^'\"]+['\"];?[ \t]*\r?\n?)"
    )

    def _replace_import(m: re.Match) -> str:
        prefix = m.group(1)
        raw_symbols = m.group(2)
        suffix = m.group(3)
        parts = [s.strip() for s in raw_symbols.split(",") if s.strip()]
        new_parts = [
            s for s in parts
            if s != symbol and not s.startswith(f"{symbol} as ") and not s.endswith(f" as {symbol}")
        ]
        if not new_parts:
            return ""
        if len(parts) == len(new_parts):
            return m.group(0)
        return f"{prefix} {', '.join(new_parts)} {suffix}"

    updated = pattern.sub(_replace_import, content)
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    return updated


def sanitize_import_providers_from_content(content: str) -> tuple[str, bool]:
    """Purge standalone components and AgGridModule from importProvidersFrom calls."""
    if "importProvidersFrom" not in content:
        return content, False

    pattern = re.compile(r"\bimportProvidersFrom\s*\(")
    matches = list(pattern.finditer(content))
    if not matches:
        return content, False

    modified = False
    purged_symbols: set[str] = set()

    for m in reversed(matches):
        start_call = m.start()
        open_paren = m.end() - 1
        close_paren = _find_matching_paren(content, open_paren)
        if close_paren == -1:
            continue

        args_str = content[open_paren + 1:close_paren]
        raw_args = _split_top_level_args(args_str)

        retained_args: list[str] = []
        call_changed = False

        for arg in raw_args:
            clean_arg = re.sub(r"/\*[\s\S]*?\*/", "", arg).strip()
            clean_arg = re.sub(r"(?m)//.*$", "", clean_arg).strip()

            base_ident = clean_arg.split(".")[0].split("(")[0].strip()
            is_ag_grid = base_ident in ("AgGridModule", "AgGridAngular")
            is_standalone_comp = (
                base_ident.endswith(("Component", "Directive", "Pipe"))
                and not base_ident.endswith("Module")
                and len(base_ident) > 4
            )

            if is_ag_grid or is_standalone_comp:
                call_changed = True
                purged_symbols.add(base_ident)
            else:
                retained_args.append(arg)

        if not call_changed:
            continue

        modified = True
        if retained_args:
            if "\n" in args_str:
                indent = "      "
                inner_formatted = "\n" + ",\n".join(indent + a.strip() for a in retained_args) + "\n    "
                replacement = f"importProvidersFrom({inner_formatted})"
            else:
                replacement = f"importProvidersFrom({', '.join(a.strip() for a in retained_args)})"
            content = content[:start_call] + replacement + content[close_paren + 1:]
        else:
            pre = content[:start_call]
            post = content[close_paren + 1:]

            post_l = post.lstrip(" \t")
            if post_l.startswith(","):
                post = post_l[1:]
            else:
                pre_r = pre.rstrip(" \t\r\n")
                if pre_r.endswith(","):
                    comma_idx = pre.rfind(",")
                    pre = pre[:comma_idx]

            content = pre + post
            purged_symbols.add("importProvidersFrom")

    if modified:
        content = re.sub(r"\[\s*,", "[", content)
        content = re.sub(r",\s*\]", "\n  ]", content)
        content = re.sub(r"\[\s*\n\s*\]", "[]", content)

        for sym in purged_symbols:
            content = _clean_unused_symbol_from_imports(content, sym)

    return content, modified


def sanitize_import_providers_from(repo_path: Path) -> list[str]:
    """Sanitize importProvidersFrom calls across the workspace.

    Removes standalone components (like AgGridAngular, AgGridModule, or any *Component)
    from importProvidersFrom(...) to prevent NG0800 runtime errors
    ('Importing providers supports NgModule or ModuleWithProviders but got a standalone component').
    If importProvidersFrom becomes empty, it is removed, and unused top-level imports are cleaned up.
    """
    applied = []
    modified_count = 0

    for ts_file in safe_rglob(repo_path, "*.ts"):
        f_name = ts_file.name
        f_posix = ts_file.as_posix()
        if (
            f_name.endswith(".spec.ts")
            or f_name.endswith(".test.ts")
            or f_name.endswith(".d.ts")
            or ".d-" in f_name
            or ".cache" in f_posix
            or "/dist/" in f_posix
            or "/out-tsc/" in f_posix
            or "/node_modules/" in f_posix
            or "/.angular/" in f_posix
        ):
            continue

        try:
            content = ts_file.read_text(encoding="utf-8")
        except Exception:
            continue

        if "importProvidersFrom" not in content:
            continue

        new_content, modified = sanitize_import_providers_from_content(content)
        if modified:
            try:
                ts_file.write_text(new_content, encoding="utf-8")
                modified_count += 1
            except Exception:
                pass

    if modified_count > 0:
        applied.append(
            f"Sanitized {modified_count} file(s) removing standalone components/AgGridModule from importProvidersFrom (prevents NG0800 runtime error)"
        )
    return applied
