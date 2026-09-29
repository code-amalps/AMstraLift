"""Deterministic Angular Built-in Control Flow (@if, @for, @switch) Transformer.

Transforms legacy Angular structural directives (*ngIf, *ngFor) into modern
Angular 17+ control flow blocks (@if, @for) directly on HTML templates,
guaranteeing modernizations apply deterministically even when local node_modules has not been restored.
"""

from __future__ import annotations

import re
from pathlib import Path

VOID_TAGS = {
    "img",
    "input",
    "br",
    "hr",
    "meta",
    "link",
    "source",
    "track",
    "wbr",
    "area",
    "base",
    "col",
    "embed",
    "param",
}


def find_matching_close_tag(content: str, after_open_pos: int, tag_name: str) -> tuple[int, int] | None:
    """Find the matching </tag_name> for an opened tag, starting search from after_open_pos.

    Returns (close_tag_start, close_tag_end) or None.
    """
    depth = 1
    pos = after_open_pos
    length = len(content)

    token_pattern = re.compile(
        rf"(<!--|-->|</{re.escape(tag_name)}\s*>|<{re.escape(tag_name)}(\s|/|>))",
        re.IGNORECASE,
    )

    in_comment = False
    for match in token_pattern.finditer(content, pos):
        matched_str = match.group(0)
        start = match.start()
        end = match.end()

        if in_comment:
            if matched_str == "-->":
                in_comment = False
            continue

        if matched_str == "<!--":
            in_comment = True
            continue

        if matched_str.startswith("</"):
            depth -= 1
            if depth == 0:
                return (start, end)
        elif matched_str.startswith("<"):
            # Check if this start tag is self-closing by scanning to >
            tag_end = end
            in_q = None
            is_self_closing = False
            while tag_end < length:
                ch = content[tag_end]
                if in_q:
                    if ch == in_q:
                        in_q = None
                elif ch in ('"', "'"):
                    in_q = ch
                elif ch == ">":
                    if tag_end > 0 and content[tag_end - 1] == "/":
                        is_self_closing = True
                    break
                tag_end += 1

            if not is_self_closing and tag_name.lower() not in VOID_TAGS:
                depth += 1

    return None


def transform_angular_control_flow(html_content: str) -> tuple[str, int]:
    """Transform *ngIf and *ngFor directives in an Angular HTML template to @if and @for.

    Returns:
        (transformed_html, count_of_transformations)
    """
    changes = 0
    content = html_content
    attr_pattern = re.compile(r'\*(ngIf|ngFor)\s*=\s*(["\'])(.*?)\2', re.DOTALL)

    # Process iteratively until no more legacy directives remain
    # Max passes to prevent any infinite loop edge cases
    max_passes = 500
    passes = 0

    while passes < max_passes:
        passes += 1
        match = attr_pattern.search(content)
        if not match:
            break

        attr_kind = match.group(1)  # 'ngIf' or 'ngFor'
        raw_expr = match.group(3)
        attr_start = match.start()
        attr_end = match.end()

        # Find the opening tag '<' preceding this attribute
        tag_start = content.rfind("<", 0, attr_start)
        if tag_start == -1:
            # Corrupted markup, remove attribute to avoid loop
            content = content[:attr_start] + content[attr_end:]
            continue

        tag_match = re.match(r"^<([a-zA-Z0-9_-]+)", content[tag_start:])
        if not tag_match:
            content = content[:attr_start] + content[attr_end:]
            continue
        tag_name = tag_match.group(1)

        # Scan forward from attr_end to find the end of the opening tag '>'
        tag_open_end = -1
        in_q = None
        for i in range(attr_end, len(content)):
            ch = content[i]
            if in_q:
                if ch == in_q:
                    in_q = None
            elif ch in ('"', "'"):
                in_q = ch
            elif ch == ">":
                tag_open_end = i + 1
                break

        if tag_open_end == -1:
            content = content[:attr_start] + content[attr_end:]
            continue

        is_self_closing = (content[tag_open_end - 2:tag_open_end] == "/>") or (tag_name.lower() in VOID_TAGS)

        # Extract indentation of the line containing tag_start
        line_start = content.rfind("\n", 0, tag_start)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1
        indent = content[line_start:tag_start]
        indent_spaces = indent if indent.strip() == "" else "  "

        # Clean the opening tag by removing the attribute
        open_tag_before = content[tag_start:attr_start].rstrip()
        open_tag_after = content[attr_end:tag_open_end].lstrip()

        if open_tag_after.startswith(">"):
            clean_open_tag = open_tag_before + open_tag_after
        else:
            clean_open_tag = open_tag_before + " " + open_tag_after

        clean_open_tag = re.sub(r" +>", ">", clean_open_tag)
        clean_open_tag = re.sub(r" +/>", " />", clean_open_tag)

        # Prepare control flow block condition
        else_tmpl = None
        if attr_kind == "ngIf":
            cond = raw_expr.strip()
            # Handle '; else elseTmpl'
            else_m = re.search(r";\s*else\s+([a-zA-Z0-9_-]+)", cond)
            if else_m:
                else_tmpl = else_m.group(1)
                cond = cond[:else_m.start()].strip()

            if " as " in cond and "; as " not in cond:
                cond = re.sub(r"\s+as\s+", "; as ", cond, count=1)

            block_header = f"@if ({cond})"
        else:  # ngFor
            for_expr = raw_expr.strip()
            track_val = "$index"
            tb_match = re.search(r";\s*trackBy:\s*([a-zA-Z0-9_]+)", for_expr, re.IGNORECASE)
            if tb_match:
                track_val = tb_match.group(1)
                for_expr = re.sub(r";\s*trackBy:\s*[a-zA-Z0-9_]+", "", for_expr, flags=re.IGNORECASE).strip()

            # Additional let vars: let i = index
            extra_vars = []
            var_matches = re.finditer(r";\s*(?:let\s+)?([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_$]+)", for_expr)
            for vm in var_matches:
                extra_vars.append(f"; let {vm.group(1)} = {vm.group(2)}")
            for_expr = re.sub(r";\s*(?:let\s+)?[a-zA-Z0-9_]+\s*=\s*[a-zA-Z0-9_$]+", "", for_expr).strip()
            for_expr = for_expr.rstrip(";")
            extra_str = "".join(extra_vars)
            block_header = f"@for ({for_expr}; track {track_val}{extra_str})"

        if is_self_closing:
            replacement = f"{block_header} {{\n{indent_spaces}  {clean_open_tag}\n{indent_spaces}}}"
            content = content[:tag_start] + replacement + content[tag_open_end:]
            changes += 1
        else:
            close_info = find_matching_close_tag(content, tag_open_end, tag_name)
            if not close_info:
                content = content[:attr_start] + content[attr_end:]
                continue

            close_start, close_end = close_info
            inner_content = content[tag_open_end:close_start]

            if tag_name.lower() == "ng-container":
                replacement = f"{block_header} {{{inner_content}\n{indent_spaces}}}"
            else:
                replacement = f"{block_header} {{\n{indent_spaces}  {clean_open_tag}{inner_content}</{tag_name}>\n{indent_spaces}}}"

            if attr_kind == "ngIf" and else_tmpl:
                replacement += f" @else {{\n{indent_spaces}  <ng-container [ngTemplateOutlet]=\"{else_tmpl}\"></ng-container>\n{indent_spaces}}}"

            content = content[:tag_start] + replacement + content[close_end:]
            changes += 1

    return content, changes


def migrate_repository_control_flow(repo_path: Path) -> dict[str, int]:
    """Migrate all HTML templates in the repository to modern Angular control flow.

    Returns dict mapping modified file path relative to repo to count of transformations.
    """
    stats: dict[str, int] = {}
    for html_file in repo_path.glob("**/*.html"):
        parts = html_file.parts
        if "node_modules" in parts or "dist" in parts or ".git" in parts or ".angular" in parts:
            continue

        try:
            original = html_file.read_text(encoding="utf-8")
            transformed, count = transform_angular_control_flow(original)
            if count > 0 and transformed != original:
                html_file.write_text(transformed, encoding="utf-8")
                rel_path = str(html_file.relative_to(repo_path))
                stats[rel_path] = count
        except Exception:
            continue

    return stats
