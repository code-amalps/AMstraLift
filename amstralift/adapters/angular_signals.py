"""Deterministic Angular Signal Inputs and Signal Outputs Transformer.

Modernizes legacy `@Input()` and `@Output()` decorator properties into modern Angular 17.1+ / 18+
Signal APIs (`input()`, `input.required()`, `output()`).
Preserves full determinism, formatting, and automatically manages `@angular/core` imports.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from amstralift.core.workspace import safe_rglob


def parse_input_decorator_args(args_str: str | None) -> dict[str, Any]:
    """Parse the arguments passed to @Input(...).

    Examples:
        @Input() -> {}
        @Input('myAlias') -> {'alias': "'myAlias'"}
        @Input({ required: true }) -> {'required': True}
        @Input({ required: true, alias: 'custom' }) -> {'required': True, 'alias': "'custom'"}
        @Input({ transform: booleanAttribute }) -> {'transform': 'booleanAttribute'}
    """
    if not args_str or not args_str.strip():
        return {}

    raw = args_str.strip()

    # Case: @Input('alias') or @Input("alias")
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        return {"alias": raw}

    # Case: @Input({ ... })
    res: dict[str, Any] = {}
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw[1:-1].strip()

        # Check required: true
        if re.search(r"\brequired\s*:\s*true\b", inner):
            res["required"] = True
        elif re.search(r"\brequired\s*:\s*false\b", inner):
            res["required"] = False

        # Check alias
        alias_match = re.search(r"\balias\s*:\s*(['\"][^'\"]+['\"])", inner)
        if alias_match:
            res["alias"] = alias_match.group(1)

        # Check transform
        transform_match = re.search(r"\btransform\s*:\s*([^,}]+)", inner)
        if transform_match:
            res["transform"] = transform_match.group(1).strip()

    return res


def parse_output_decorator_args(args_str: str | None) -> dict[str, Any]:
    """Parse the arguments passed to @Output(...).

    Examples:
        @Output() -> {}
        @Output('myAlias') -> {'alias': "'myAlias'"}
        @Output({ alias: 'myAlias' }) -> {'alias': "'myAlias'"}
    """
    if not args_str or not args_str.strip():
        return {}

    raw = args_str.strip()
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        return {"alias": raw}

    res: dict[str, Any] = {}
    if raw.startswith("{") and raw.endswith("}"):
        alias_match = re.search(r"\balias\s*:\s*(['\"][^'\"]+['\"])", raw)
        if alias_match:
            res["alias"] = alias_match.group(1)

    return res


def build_input_options_str(options: dict[str, Any]) -> str:
    """Build the options object string for input(...) or input.required(...)."""
    parts: list[str] = []
    if "alias" in options:
        parts.append(f"alias: {options['alias']}")
    if "transform" in options:
        parts.append(f"transform: {options['transform']}")

    if parts:
        return "{ " + ", ".join(parts) + " }"
    return ""


def parse_query_decorator_args(args_str: str) -> tuple[str, dict[str, str], bool]:
    """Parse predicate, options, and whether required: true was specified.

    Returns:
        (predicate, options_dict, is_required)
    """
    raw = args_str.strip()
    if not raw:
        return "", {}, False

    # Check for options object separated by comma
    match = re.search(r"^(.*?),\s*(\{.*\})\s*$", raw, re.DOTALL)
    if match:
        predicate = match.group(1).strip()
        opts_raw = match.group(2).strip()[1:-1].strip()
    else:
        predicate = raw
        opts_raw = ""

    options: dict[str, str] = {}
    is_required = False

    if opts_raw:
        if re.search(r"\brequired\s*:\s*true\b", opts_raw):
            is_required = True
        read_match = re.search(r"\bread\s*:\s*([^,}]+)", opts_raw)
        if read_match:
            options["read"] = read_match.group(1).strip()

    return predicate, options, is_required


def _build_query_options_str(options: dict[str, str]) -> str:
    """Format query options dictionary into a TypeScript object literal string."""
    parts = [f"{k}: {v}" for k, v in options.items()]
    return "{ " + ", ".join(parts) + " }" if parts else ""


def transform_angular_signals(ts_content: str) -> tuple[str, int]:
    """Transform @Input() and @Output() property declarations to input() and output().

    Returns:
        (transformed_code, count_of_changes)
    """
    changes = 0
    content = ts_content

    # Regex for @Input property declaration:
    # Captures:
    # 1. Indentation
    # 2. Decorator args: @Input(...)
    # Captures:
    # 1. Indentation
    # 2. Pre-modifier (public, protected, private, readonly)
    # 3. Decorator args: @Input(...)
    # 4. Post-modifier (public, protected, private, readonly)
    # 5. Property name
    # 6. Definite assignment ! or optional ?
    # 7. Type annotation (: Type)
    # 8. Initializer (= value)
    input_pattern = re.compile(
        r"^([ \t]*)"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"@Input\s*\((.*?)\)\s*"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"([a-zA-Z0-9_$]+)"
        r"([!?])?"
        r"(?:\s*:\s*([^=;]+?))?"
        r"(?:\s*=\s*([^;]+?))?"
        r";\s*$",
        re.MULTILINE,
    )

    def _replace_input(match: re.Match[str]) -> str:
        nonlocal changes
        indent = match.group(1)
        mod1 = match.group(2)
        decorator_args_raw = match.group(3)
        mod2 = match.group(4)
        prop_name = match.group(5)
        flag = match.group(6)  # '!' or '?' or None
        type_annot = match.group(7)
        initial_val = match.group(8)

        # Do not transform getters or setters
        if prop_name in ("get", "set"):
            return match.group(0)

        options = parse_input_decorator_args(decorator_args_raw)
        is_required = options.get("required") is True

        type_str = type_annot.strip() if type_annot else None
        init_str = initial_val.strip() if initial_val else None

        modifiers = [m for m in (mod1, mod2) if m]
        mod_prefix = " ".join(modifiers) + " " if modifiers else ""

        opt_str = build_input_options_str(options)

        changes += 1

        if is_required:
            # input.required<Type>([options])
            type_param = f"<{type_str}>" if type_str else ""
            call_args = f"({opt_str})" if opt_str else "()"
            return f"{indent}{mod_prefix}{prop_name} = input.required{type_param}{call_args};"

        # Not required:
        if init_str is not None:
            # input<Type>(init_str[, options])
            type_param = f"<{type_str}>" if type_str else ""
            if opt_str:
                return f"{indent}{mod_prefix}{prop_name} = input{type_param}({init_str}, {opt_str});"
            return f"{indent}{mod_prefix}{prop_name} = input{type_param}({init_str});"
        else:
            # uninitialized optional input: input<Type | undefined>(undefined[, options])
            if type_str:
                if type_str.endswith("| undefined") or type_str.startswith("undefined |"):
                    effective_type = type_str
                else:
                    effective_type = f"{type_str} | undefined"
            else:
                effective_type = "any"

            type_param = f"<{effective_type}>"
            if opt_str:
                return f"{indent}{mod_prefix}{prop_name} = input{type_param}(undefined, {opt_str});"
            return f"{indent}{mod_prefix}{prop_name} = input{type_param}(undefined);"

    content = input_pattern.sub(_replace_input, content)

    # Regex for @Output property declaration:
    output_pattern = re.compile(
        r"^([ \t]*)"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"@Output\s*\((.*?)\)\s*"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"([a-zA-Z0-9_$]+)"
        r"(?:\s*:\s*(?:EventEmitter|any)(?:<([^>]+)>)?)?"
        r"\s*=\s*new\s+EventEmitter(?:<([^>]+)>)?\s*\([^)]*\)\s*;\s*$",
        re.MULTILINE,
    )

    def _replace_output(match: re.Match[str]) -> str:
        nonlocal changes
        indent = match.group(1)
        mod1 = match.group(2)
        decorator_args_raw = match.group(3)
        mod2 = match.group(4)
        prop_name = match.group(5)
        type_from_var = match.group(6)
        type_from_inst = match.group(7)

        # Do not transform getters or setters
        if prop_name in ("get", "set"):
            return match.group(0)

        options = parse_output_decorator_args(decorator_args_raw)
        opt_parts: list[str] = []
        if "alias" in options:
            opt_parts.append(f"alias: {options['alias']}")
        opt_str = ("{ " + ", ".join(opt_parts) + " }") if opt_parts else ""

        event_type = type_from_inst or type_from_var
        event_type = event_type.strip() if event_type else None

        modifiers = [m for m in (mod1, mod2) if m]
        mod_prefix = " ".join(modifiers) + " " if modifiers else ""

        changes += 1

        type_param = f"<{event_type}>" if event_type else ""
        call_args = f"({opt_str})" if opt_str else "()"

        return f"{indent}{mod_prefix}{prop_name} = output{type_param}{call_args};"

    content = output_pattern.sub(_replace_output, content)

    # Regex for @ViewChild, @ViewChildren, @ContentChild, @ContentChildren:
    query_pattern = re.compile(
        r"^([ \t]*)"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"@(ViewChild|ViewChildren|ContentChild|ContentChildren)\s*\((.*?)\)\s*"
        r"(?:(public|protected|private|readonly)\s+)?"
        r"([a-zA-Z0-9_$]+)"
        r"([!?])?"
        r"(?:\s*:\s*([^;]+?))?"
        r";\s*$",
        re.MULTILINE,
    )

    def _replace_query(match: re.Match[str]) -> str:
        nonlocal changes
        indent = match.group(1)
        mod1 = match.group(2)
        decorator_name = match.group(3)
        decorator_args_raw = match.group(4)
        mod2 = match.group(5)
        prop_name = match.group(6)
        flag = match.group(7)
        type_annot = match.group(8)

        # Do not transform getters or setters
        if prop_name in ("get", "set"):
            return match.group(0)

        predicate, options, is_required = parse_query_decorator_args(decorator_args_raw)
        opt_str = _build_query_options_str(options)

        fn_map = {
            "ViewChild": "viewChild",
            "ViewChildren": "viewChildren",
            "ContentChild": "contentChild",
            "ContentChildren": "contentChildren",
        }
        target_fn = fn_map[decorator_name]
        if is_required and target_fn in ("viewChild", "contentChild"):
            target_fn = f"{target_fn}.required"

        modifiers = [m for m in (mod1, mod2) if m]
        mod_prefix = " ".join(modifiers) + " " if modifiers else ""

        changes += 1

        type_param = ""
        if type_annot:
            clean_type = type_annot.strip()
            ql_match = re.search(r"QueryList\s*<\s*(.+)\s*>", clean_type)
            if ql_match:
                inner_type = ql_match.group(1).strip()
                if predicate.startswith(("'", '"')):
                    type_param = f"<{inner_type}>"
            else:
                if predicate.startswith(("'", '"')):
                    type_param = f"<{clean_type}>"
                elif clean_type != predicate and clean_type != f"{predicate} | undefined":
                    if "read" in options:
                        type_param = f"<{clean_type}>"

        call_args = f"({predicate}, {opt_str})" if opt_str else f"({predicate})"
        return f"{indent}{mod_prefix}{prop_name} = {target_fn}{type_param}{call_args};"

    content = query_pattern.sub(_replace_query, content)

    if changes > 0:
        content = _update_angular_core_imports(content)

    return content, changes


def _update_angular_core_imports(content: str) -> str:
    """Synchronize imports from '@angular/core'.

    - Adds 'input', 'output', 'viewChild', 'viewChildren', 'contentChild', 'contentChildren' if used.
    - Removes legacy decorators and 'QueryList' if no longer used.
    """
    import_match = re.search(
        r"import\s*\{\s*([^}]+)\s*\}\s*from\s*(['\"]@angular/core['\"]);?",
        content,
    )
    if not import_match:
        return content

    full_stmt = import_match.group(0)
    imports_raw = import_match.group(1)
    quote_target = import_match.group(2)

    imported_symbols = [s.strip() for s in imports_raw.split(",") if s.strip()]
    symbols_set = set(imported_symbols)

    # Check for remaining occurrences outside the import statement
    content_outside_import = content[:import_match.start()] + content[import_match.end():]
    has_remaining_input_decorator = bool(re.search(r"@Input\s*\(", content_outside_import))
    has_remaining_output_decorator = bool(re.search(r"@Output\s*\(", content_outside_import))
    has_remaining_event_emitter = bool(re.search(r"\bEventEmitter\b", content_outside_import))

    has_remaining_view_child = bool(re.search(r"@ViewChild\s*\(", content_outside_import))
    has_remaining_view_children = bool(re.search(r"@ViewChildren\s*\(", content_outside_import))
    has_remaining_content_child = bool(re.search(r"@ContentChild\s*\(", content_outside_import))
    has_remaining_content_children = bool(re.search(r"@ContentChildren\s*\(", content_outside_import))
    has_remaining_query_list = bool(re.search(r"\bQueryList\b", content_outside_import))

    has_signal_input = bool(re.search(r"\binput(?:\.required)?\s*[<(]", content_outside_import))
    has_signal_output = bool(re.search(r"\boutput\s*[<(]", content_outside_import))
    has_signal_view_child = bool(re.search(r"\bviewChild(?:\.required)?\s*[<(]", content_outside_import))
    has_signal_view_children = bool(re.search(r"\bviewChildren\s*[<(]", content_outside_import))
    has_signal_content_child = bool(re.search(r"\bcontentChild(?:\.required)?\s*[<(]", content_outside_import))
    has_signal_content_children = bool(re.search(r"\bcontentChildren\s*[<(]", content_outside_import))

    # Add signal imports if needed
    for symbol, needed in [
        ("input", has_signal_input),
        ("output", has_signal_output),
        ("viewChild", has_signal_view_child),
        ("viewChildren", has_signal_view_children),
        ("contentChild", has_signal_content_child),
        ("contentChildren", has_signal_content_children),
    ]:
        if needed and symbol not in symbols_set:
            imported_symbols.append(symbol)
            symbols_set.add(symbol)

    # Remove legacy symbols if unused
    for symbol, still_used in [
        ("Input", has_remaining_input_decorator),
        ("Output", has_remaining_output_decorator),
        ("EventEmitter", has_remaining_event_emitter),
        ("ViewChild", has_remaining_view_child),
        ("ViewChildren", has_remaining_view_children),
        ("ContentChild", has_remaining_content_child),
        ("ContentChildren", has_remaining_content_children),
        ("QueryList", has_remaining_query_list),
    ]:
        if not still_used and symbol in symbols_set:
            imported_symbols = [s for s in imported_symbols if s != symbol]
            symbols_set.discard(symbol)

    # Reconstruct the import statement
    if "\n" in imports_raw:
        new_imports = ",\n  ".join(imported_symbols)
        new_stmt = f"import {{\n  {new_imports},\n}} from {quote_target};"
    else:
        new_imports = ", ".join(imported_symbols)
        new_stmt = f"import {{ {new_imports} }} from {quote_target};"

    return content.replace(full_stmt, new_stmt, 1)


def migrate_repository_signals(repo_path: Path) -> dict[str, int]:
    """Scan and migrate all TypeScript component files in the repository to Signal inputs and outputs.

    Returns:
        Mapping of relative file path string to number of signals modernized.
    """
    results: dict[str, int] = {}
    if not repo_path.exists():
        return results

    # Search all .ts files, excluding spec files and declaration files
    ts_files = safe_rglob(repo_path, ["*.ts", "**/*.ts"])

    for ts_path in ts_files:
        name_lower = ts_path.name.lower()
        if name_lower.endswith(".d.ts") or name_lower.endswith(".spec.ts"):
            continue

        try:
            content = ts_path.read_text(encoding="utf-8")
            if "@Input" not in content and "@Output" not in content:
                continue

            new_content, count = transform_angular_signals(content)
            if count > 0 and new_content != content:
                ts_path.write_text(new_content, encoding="utf-8")
                rel_path = str(ts_path.relative_to(repo_path)).replace("\\", "/")
                results[rel_path] = count
        except Exception:
            pass

    return results
