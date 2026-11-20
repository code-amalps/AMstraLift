"""Deterministic Angular Self-Closing Tags Transformer.

Modernizes Angular templates (v15.1+) by converting empty component and custom element tags
(<app-header></app-header>) into concise self-closing tags (<app-header />).
Supports both external HTML templates (*.html) and inline component templates in TypeScript files (*.ts).
"""

from __future__ import annotations

import re
from pathlib import Path

from amstralift.core.workspace import safe_rglob


def transform_angular_self_closing(content: str) -> tuple[str, int]:
    """Transform empty Angular component/custom tags to modern self-closing tags.

    Examples:
        <app-header></app-header> -> <app-header />
        <app-header [title]="title"></app-header> -> <app-header [title]="title" />
        <router-outlet></router-outlet> -> <router-outlet />
        <ng-container></ng-container> -> <ng-container />

    Preserves:
        - Non-empty tags with child elements or text
        - HTML comments <!-- ... -->
        - Standard HTML non-void tags (div, span, p, button, etc.)
        - Already self-closing tags (<app-header />)
        - <script> and <style> block contents

    Returns:
        (transformed_html, count_of_converted_tags)
    """
    changes = 0
    length = len(content)
    i = 0

    while i < length:
        # 1. Skip comments <!-- ... -->
        if content.startswith("<!--", i):
            end_comment = content.find("-->", i + 4)
            if end_comment != -1:
                i = end_comment + 3
                continue
            else:
                break

        # 2. Skip <script> and <style> blocks
        script_m = re.match(r"^<script[\s>]", content[i:], re.IGNORECASE)
        if script_m:
            end_script = re.search(r"</script\s*>", content[i:], re.IGNORECASE)
            if end_script:
                i += end_script.end()
                continue
        style_m = re.match(r"^<style[\s>]", content[i:], re.IGNORECASE)
        if style_m:
            end_style = re.search(r"</style\s*>", content[i:], re.IGNORECASE)
            if end_style:
                i += end_style.end()
                continue

        # 3. Skip standalone closing tags </...
        if content.startswith("</", i):
            gt = content.find(">", i)
            if gt != -1:
                i = gt + 1
                continue
            else:
                break

        # 4. Check for opening component tag (<tag-name...)
        if content[i] == "<":
            # Match custom element tag name (must contain a hyphen, e.g. app-*, mat-*, router-outlet, ng-container)
            m = re.match(r"^<([a-zA-Z][a-zA-Z0-9]*-[a-zA-Z0-9\-_]*)([\s/>])", content[i:])
            if not m:
                i += 1
                continue

            tag_name = m.group(1)

            # Scan the opening tag to find its closing '>', respecting quotes
            tag_open_end = None
            in_q: str | None = None
            idx = i + len(tag_name) + 1
            while idx < length:
                ch = content[idx]
                if in_q:
                    if ch == in_q and (idx == 0 or content[idx - 1] != "\\"):
                        in_q = None
                elif ch in ('"', "'"):
                    in_q = ch
                elif ch == ">":
                    tag_open_end = idx + 1
                    break
                idx += 1

            if tag_open_end is None:
                # Malformed or unclosed tag
                i += 1
                continue

            # Check if tag is already self-closing (ends with '/>')
            before_gt = content[i : tag_open_end - 1].rstrip()
            if before_gt.endswith("/"):
                i = tag_open_end
                continue

            # Check if immediately following tag_open_end is only whitespace up to the closing tag
            ws_idx = tag_open_end
            while ws_idx < length and content[ws_idx] in (" ", "\t", "\r", "\n"):
                ws_idx += 1

            # Check if matching closing tag </tag_name> is present right after whitespace
            close_match = re.match(rf"^</{re.escape(tag_name)}\s*>", content[ws_idx:], re.IGNORECASE)
            if close_match:
                close_tag_end = ws_idx + close_match.end()

                # Extract attributes from opening tag
                attrs_raw = content[i + len(tag_name) + 1 : tag_open_end - 1]
                clean_attrs = attrs_raw.rstrip()
                if clean_attrs:
                    if clean_attrs[0] not in (" ", "\t", "\r", "\n"):
                        clean_attrs = " " + clean_attrs
                    replacement = f"<{tag_name}{clean_attrs} />"
                else:
                    replacement = f"<{tag_name} />"

                content = content[:i] + replacement + content[close_tag_end:]
                length = len(content)
                changes += 1
                i += len(replacement)
                continue
            else:
                i = tag_open_end
                continue
        else:
            i += 1

    return content, changes


def transform_inline_templates_in_ts(ts_content: str) -> tuple[str, int]:
    """Find component inline templates in TypeScript code and transform empty tags to self-closing."""
    total_changes = 0

    # 1. Backtick template literals: template: `...`
    def replace_backtick(match: re.Match[str]) -> str:
        nonlocal total_changes
        prefix = match.group(1)
        body = match.group(2)
        suffix = match.group(3)
        transformed_body, count = transform_angular_self_closing(body)
        total_changes += count
        return f"{prefix}{transformed_body}{suffix}"

    pattern_backtick = re.compile(r"(template\s*:\s*`)([\s\S]*?)(`)")
    content = pattern_backtick.sub(replace_backtick, ts_content)

    # 2. Quoted template strings: template: '...' or template: "..."
    def replace_quote(match: re.Match[str]) -> str:
        nonlocal total_changes
        prefix = match.group(1)
        body = match.group(3)
        suffix = match.group(4)
        transformed_body, count = transform_angular_self_closing(body)
        total_changes += count
        return f"{prefix}{transformed_body}{suffix}"

    pattern_quote = re.compile(r"""(template\s*:\s*(['"]))([\s\S]*?)(\2)""")
    content = pattern_quote.sub(replace_quote, content)

    return content, total_changes


def migrate_repository_self_closing(repo_path: Path) -> dict[str, int]:
    """Migrate all component templates in the repository to modern Angular self-closing tags.

    Transforms both external *.html templates and inline component templates in *.ts files.
    Returns dict mapping relative file paths to count of converted tags.
    """
    stats: dict[str, int] = {}

    # 1. Process *.html template files
    for html_file in safe_rglob(repo_path, "*.html"):
        try:
            original = html_file.read_text(encoding="utf-8")
            transformed, count = transform_angular_self_closing(original)
            if count > 0 and transformed != original:
                html_file.write_text(transformed, encoding="utf-8")
                rel_path = str(html_file.relative_to(repo_path))
                stats[rel_path] = count
        except Exception:
            continue

    # 2. Process *.ts files with inline templates
    for ts_file in safe_rglob(repo_path, "*.ts"):
        if ts_file.name.endswith((".spec.ts", ".test.ts", ".d.ts")):
            continue
        try:
            original = ts_file.read_text(encoding="utf-8")
            if "template" not in original:
                continue
            transformed, count = transform_inline_templates_in_ts(original)
            if count > 0 and transformed != original:
                ts_file.write_text(transformed, encoding="utf-8")
                rel_path = str(ts_file.relative_to(repo_path))
                stats[rel_path] = count
        except Exception:
            continue

    return stats
