"""Deterministic Angular Constructor Dependency Injection to inject() Transformer.

Modernizes legacy constructor parameter injection into modern Angular 14+ / 16+ / 17+
`inject()` field declarations.
Handles parameter properties (private, protected, public, readonly), parameter decorators
(@Inject, @Optional, @Self, @SkipSelf, @Host), empty constructor removal, and
automatic '@angular/core' imports synchronization.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from amstralift.core.workspace import safe_rglob


def split_constructor_params(params_str: str) -> list[str]:
    """Split constructor parameter string by comma, respecting parens, generics, and braces."""
    params: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote: str | None = None

    for char in params_str:
        if in_quote:
            current.append(char)
            if char == in_quote:
                in_quote = None
            continue

        if char in ('"', "'", "`"):
            in_quote = char
            current.append(char)
        elif char in ("(", "[", "{", "<"):
            depth += 1
            current.append(char)
        elif char in (")", "]", "}", ">"):
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            p = "".join(current).strip()
            if p:
                params.append(p)
            current = []
        else:
            current.append(char)

    last = "".join(current).strip()
    if last:
        params.append(last)

    return params


def parse_parameter_property(param_str: str) -> dict[str, Any] | None:
    """Parse a single constructor parameter.

    If it has an access modifier (private, protected, public, readonly), returns a dict
    with parameter property metadata. Otherwise returns None.
    """
    raw = param_str.strip()

    # Check for access modifiers
    modifier_match = re.search(r"\b(private|protected|public|readonly)\b", raw)
    if not modifier_match:
        return None

    # Collect all modifiers
    modifiers: list[str] = []
    for mod in ("public", "protected", "private", "readonly"):
        if re.search(rf"\b{mod}\b", raw):
            modifiers.append(mod)

    # Check parameter decorators: @Inject, @Optional, @Self, @SkipSelf, @Host
    inject_token: str | None = None
    inject_match = re.search(r"@Inject\s*\(\s*([^)]+)\s*\)", raw)
    if inject_match:
        inject_token = inject_match.group(1).strip()

    options: dict[str, str] = {}
    if re.search(r"@Optional\s*\(\s*\)", raw):
        options["optional"] = "true"
    if re.search(r"@Self\s*\(\s*\)", raw):
        options["self"] = "true"
    if re.search(r"@SkipSelf\s*\(\s*\)", raw):
        options["skipSelf"] = "true"
    if re.search(r"@Host\s*\(\s*\)", raw):
        options["host"] = "true"

    # Strip decorators and modifiers to isolate 'name: Type'
    clean = raw
    clean = re.sub(r"@Inject\s*\(\s*[^)]+\s*\)", "", clean)
    clean = re.sub(r"@(Optional|Self|SkipSelf|Host)\s*\(\s*\)", "", clean)
    clean = re.sub(r"\b(private|protected|public|readonly)\b", "", clean)
    clean = clean.strip()

    if ":" not in clean:
        return None

    parts = clean.split(":", 1)
    name = parts[0].strip().rstrip("!?")
    type_annot = parts[1].strip()

    # If no explicit @Inject token, the token is the Type
    token = inject_token if inject_token else type_annot.rstrip(";").rstrip("?")

    return {
        "name": name,
        "type": type_annot,
        "token": token,
        "modifiers": modifiers,
        "options": options,
        "raw": raw,
    }


def transform_angular_inject(ts_content: str) -> tuple[str, int]:
    """Modernize constructor parameter injection into inject() field declarations.

    Returns:
        (transformed_content, count_of_injections_converted)
    """
    changes = 0
    content = ts_content

    # Match constructors inside classes:
    # Captures:
    # 1. Indentation
    # 2. Parameters inside constructor(...)
    # 3. Body inside { ... }
    ctor_pattern = re.compile(
        r"^([ \t]*)constructor\s*\(([\s\S]*?)\)\s*\{([\s\S]*?)\}",
        re.MULTILINE,
    )

    def _replace_constructor(match: re.Match[str]) -> str:
        nonlocal changes
        indent = match.group(1)
        params_raw = match.group(2)
        body_raw = match.group(3)

        raw_params = split_constructor_params(params_raw)
        if not raw_params:
            return match.group(0)

        injected_fields: list[str] = []
        remaining_params: list[str] = []

        for p_str in raw_params:
            prop = parse_parameter_property(p_str)
            if prop:
                # Convert to inject() field
                mod_str = " ".join(prop["modifiers"]) + " " if prop["modifiers"] else ""
                token = prop["token"]
                opts = prop["options"]
                if opts:
                    opt_str = "{ " + ", ".join(f"{k}: {v}" for k, v in opts.items()) + " }"
                    call_str = f"inject({token}, {opt_str})"
                else:
                    call_str = f"inject({token})"

                field_line = f"{indent}{mod_str}{prop['name']} = {call_str};"
                injected_fields.append(field_line)
                changes += 1
            else:
                remaining_params.append(p_str)

        if not injected_fields:
            return match.group(0)

        fields_block = "\n".join(injected_fields)

        # Handle constructor recreation or complete removal
        body_is_empty = not body_raw.strip()

        if not remaining_params and body_is_empty:
            # Completely remove constructor, only output fields
            return fields_block

        # Reconstruct constructor with remaining params
        if remaining_params:
            if "\n" in params_raw:
                formatted_params = "\n" + ",\n".join(f"{indent}  {p.strip()}" for p in remaining_params) + f",\n{indent}"
            else:
                formatted_params = ", ".join(p.strip() for p in remaining_params)
            new_ctor = f"{indent}constructor({formatted_params}) {{{body_raw}}}"
        else:
            new_ctor = f"{indent}constructor() {{{body_raw}}}"

        return f"{fields_block}\n\n{new_ctor}"

    content = ctor_pattern.sub(_replace_constructor, content)

    if changes > 0:
        content = _update_inject_imports(content)

    return content, changes


def _update_inject_imports(content: str) -> str:
    """Synchronize imports from '@angular/core' for inject()."""
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

    content_outside_import = content[:import_match.start()] + content[import_match.end():]

    has_inject_call = bool(re.search(r"\binject\s*[<(]", content_outside_import))
    has_remaining_inject_dec = bool(re.search(r"@Inject\s*\(", content_outside_import))
    has_remaining_optional = bool(re.search(r"@Optional\s*\(", content_outside_import))
    has_remaining_self = bool(re.search(r"@Self\s*\(", content_outside_import))
    has_remaining_skip_self = bool(re.search(r"@SkipSelf\s*\(", content_outside_import))
    has_remaining_host = bool(re.search(r"@Host\s*\(", content_outside_import))

    # Add inject if needed
    if has_inject_call and "inject" not in symbols_set:
        imported_symbols.append("inject")
        symbols_set.add("inject")

    # Remove legacy symbols if unused
    for symbol, still_used in [
        ("Inject", has_remaining_inject_dec),
        ("Optional", has_remaining_optional),
        ("Self", has_remaining_self),
        ("SkipSelf", has_remaining_skip_self),
        ("Host", has_remaining_host),
    ]:
        if not still_used and symbol in symbols_set:
            imported_symbols = [s for s in imported_symbols if s != symbol]
            symbols_set.discard(symbol)

    if "\n" in imports_raw:
        new_imports = ",\n  ".join(imported_symbols)
        new_stmt = f"import {{\n  {new_imports},\n}} from {quote_target};"
    else:
        new_imports = ", ".join(imported_symbols)
        new_stmt = f"import {{ {new_imports} }} from {quote_target};"

    return content.replace(full_stmt, new_stmt, 1)


def migrate_repository_inject(repo_path: Path) -> dict[str, int]:
    """Scan and modernize constructor DI into inject() across all TypeScript files in the repository.

    Returns:
        Mapping of relative file path string to count of injections modernized.
    """
    results: dict[str, int] = {}
    if not repo_path.exists():
        return results

    ts_files = safe_rglob(repo_path, ["*.ts", "**/*.ts"])

    for ts_path in ts_files:
        name_lower = ts_path.name.lower()
        if name_lower.endswith(".d.ts") or name_lower.endswith(".spec.ts"):
            continue

        try:
            content = ts_path.read_text(encoding="utf-8")
            if "constructor" not in content:
                continue

            new_content, count = transform_angular_inject(content)
            if count > 0 and new_content != content:
                ts_path.write_text(new_content, encoding="utf-8")
                rel_path = str(ts_path.relative_to(repo_path)).replace("\\", "/")
                results[rel_path] = count
        except Exception:
            pass

    return results
