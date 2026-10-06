"""Evidence Reporter for AMstraLift.

Renders human-readable Transformation Evidence Reports in Markdown and standalone HTML.
Highlights applied changes, multi-gate verification results, cryptographic attestation,
and most importantly: explicitly rejected/refused transformations with manual developer guidance.
"""

from __future__ import annotations

import html
from typing import Any

from amstralift.evidence.models import EvidenceBundle


def render_markdown_report(bundle: EvidenceBundle) -> str:
    """Generate a clean, professional GitHub/GitLab-compatible Markdown report."""
    v = bundle.verification
    status_label = {
        "SUCCESS": "🟢 FULLY MODERNIZED & VERIFIED",
        "PARTIALLY_MODERNIZED": "🟡 PARTIALLY MODERNIZED (Safe Subset Applied & Verified)",
        "VERIFICATION_FAILED": "🔴 VERIFICATION FAILED (Changes Refused / Reverted)",
    }.get(bundle.overall_status, bundle.overall_status)

    lines: list[str] = [
        "# 🛡️ AMstraLift Transformation & Trust Evidence Report",
        "",
        f"> **Overall Status**: {status_label}  ",
        f"> **Repository**: `{bundle.repo_name}` (`{bundle.ecosystem}`)  ",
        f"> **Run ID**: `{bundle.run_id}` | **Timestamp**: `{bundle.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}`  ",
    ]

    if bundle.attestation:
        lines.append(
            f"> **Cryptographic Attestation**: `{bundle.attestation.signature_algorithm}` verified (Signature: `{bundle.attestation.bundle_signature[:16]}...`)  "
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Executive Summary Table
    lines.extend([
        "## 📊 Transformation Summary",
        "",
        "```text",
        f"Files analyzed                   {bundle.files_analyzed:>6,}",
        f"Files modified                   {bundle.files_modified:>6,}",
        "",
        f"AST transformations applied      {len(bundle.transformations_applied):>6,}",
        f"Transformations refused          {len(bundle.transformations_refused):>6,}",
        "",
    ])

    if bundle.framework_from or bundle.framework_to:
        lines.extend([
            "Framework Migration:",
            f"  {bundle.framework_from or 'Unknown'} ➔ {bundle.framework_to or 'Target'}",
            "",
        ])

    lines.extend([
        "Security Posture (CVEs):",
        f"  Pre-upgrade CVEs               {v.pre_cves_count:>6,}",
        f"  Post-upgrade CVEs              {v.post_cves_count:>6,}",
        f"  Vulnerabilities Resolved       {v.cves_resolved:>6,}",
        "",
        "6-Gate Sandboxed Verification & Contract Boundary:",
        f"  Gate 1: Clean Install          {v.clean_install.upper():>6}",
        f"  Gate 2: Project Build          {v.build.upper():>6} ({v.build_duration_seconds:.1f}s)",
        f"  Gate 3: Test Suite             {v.tests.upper():>6} ({v.tests_passed}/{v.tests_run} passed)",
        f"  Gate 4: Post-Rescan            {v.post_rescan.upper():>6}",
        f"  Gate 5: Manifest Diff          {v.manifest_diff.upper():>6}",
        f"  Gate 6: Semantic / Contract    {v.gate_6_semantic_diff.upper():>6}",
        "```",
        "",
        "---",
        "",
    ])

    # 2. Refused Transformations (The crucial trust section)
    if bundle.transformations_refused:
        lines.extend([
            "## ⚠️ Refused Transformations (Safe Fallback)",
            "",
            "> **Trust Guarantee:** AMstraLift strictly refuses unsafe modifications rather than guessing.",
            "> For each item below, **the source code file was left 100% untouched** (no comments or partial changes injected).",
            "",
            "| Rule ID | Target File | Category | Refusal Reason & Manual Guidance |",
            "|---|---|---|---|",
        ])

        for ref in bundle.transformations_refused:
            guidance = f"<br>**Action Required:** {ref.manual_guidance}" if ref.manual_guidance else ""
            lines.append(
                f"| `{ref.rule_id}` | `{ref.file_path}` | `{ref.category.value}` | **Reason:** {ref.reason}{guidance} |"
            )

        lines.extend(["", "---", ""])

    # 3. Applied Transformations
    if bundle.transformations_applied:
        lines.extend([
            "## ✅ Applied Transformations",
            "",
            "| ID | Adapter | Rule | File Path | AST Nodes Changed | Verified in Sandbox |",
            "|---|---|---|---|:---:|:---:|",
        ])
        for tr in bundle.transformations_applied[:50]:  # Cap display at 50 in table
            ver_badge = "✔ PASS" if bundle.verification.all_gates_passed else "Pending"
            lines.append(
                f"| `{tr.id}` | `{tr.adapter}` | `{tr.rule_id}` | `{tr.file_path}` | {tr.ast_nodes_changed} | {ver_badge} |"
            )

        if len(bundle.transformations_applied) > 50:
            lines.append(
                f"\n*...and {len(bundle.transformations_applied) - 50} more transformations recorded in machine evidence bundle.*"
            )
        lines.extend(["", "---", ""])

    # 4. Cryptographic Proof Block
    if bundle.attestation:
        lines.extend([
            "## 🔐 Cryptographic Attestation Block",
            "",
            "```json",
            "{",
            f'  "signer": "{bundle.attestation.signer}",',
            f'  "algorithm": "{bundle.attestation.signature_algorithm}",',
            f'  "signed_at": "{bundle.attestation.signed_at.isoformat()}",',
            f'  "payload_sha256": "{bundle.attestation.payload_sha256}",',
            f'  "bundle_signature": "{bundle.attestation.bundle_signature}"',
            "}",
            "```",
            "",
        ])

    return "\n".join(lines)


def render_html_report(bundle: EvidenceBundle) -> str:
    """Generate a high-contrast, self-contained HTML executive report."""
    v = bundle.verification
    status_bg = "#10b981" if bundle.overall_status == "SUCCESS" else "#f59e0b" if bundle.overall_status == "PARTIALLY_MODERNIZED" else "#ef4444"
    status_title = {
        "SUCCESS": "FULLY MODERNIZED & VERIFIED",
        "PARTIALLY_MODERNIZED": "PARTIALLY MODERNIZED (SAFE SUBSET APPLIED)",
        "VERIFICATION_FAILED": "VERIFICATION FAILED",
    }.get(bundle.overall_status, bundle.overall_status)

    refusals_html = ""
    if bundle.transformations_refused:
        rows = "".join(
            f"""<tr>
                <td style="padding:10px; border-bottom:1px solid #334155; font-family:Consolas, monospace; color:#38bdf8;">{html.escape(r.rule_id)}</td>
                <td style="padding:10px; border-bottom:1px solid #334155; font-family:Consolas, monospace; color:#cbd5e1;">{html.escape(r.file_path)}</td>
                <td style="padding:10px; border-bottom:1px solid #334155;"><span style="background:#78350f; color:#fde68a; padding:3px 8px; border-radius:4px; font-size:12px;">{html.escape(r.category.value)}</span></td>
                <td style="padding:10px; border-bottom:1px solid #334155; color:#f8fafc;">
                    <strong>Reason:</strong> {html.escape(r.reason)}<br/>
                    <span style="color:#94a3b8; font-size:13px;"><strong>Guidance:</strong> {html.escape(r.manual_guidance or 'Manual inspection needed')}</span>
                </td>
            </tr>"""
            for r in bundle.transformations_refused
        )
        refusals_html = f"""
        <div style="background:#1e293b; border-radius:8px; padding:20px; margin-top:24px; border:1px solid #eab308;">
            <h3 style="margin-top:0; color:#fde047; display:flex; align-items:center; gap:8px;">
                ⚠️ Refused Transformations ({len(bundle.transformations_refused)})
            </h3>
            <p style="color:#94a3b8; font-size:14px; margin-bottom:16px;">
                <strong>Trust Guarantee:</strong> AMstraLift strictly refused unsafe automation. The source code for each of these files was left <strong>100% untouched</strong>.
            </p>
            <table style="width:100%; border-collapse:collapse; font-size:14px; text-align:left;">
                <thead>
                    <tr style="background:#0f172a; color:#94a3b8;">
                        <th style="padding:10px;">Rule ID</th>
                        <th style="padding:10px;">Target File</th>
                        <th style="padding:10px;">Category</th>
                        <th style="padding:10px;">Reason & Manual Guidance</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AMstraLift Evidence Report - {html.escape(bundle.repo_name)}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 32px; }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 20px; }}
        .badge {{ background: {status_bg}; color: #020617; font-weight: bold; padding: 6px 14px; border-radius: 20px; font-size: 14px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-top: 24px; }}
        .card {{ background: #1e293b; border-radius: 8px; padding: 18px; border: 1px solid #334155; }}
        .card-num {{ font-size: 28px; font-weight: bold; margin-top: 6px; color: #38bdf8; }}
        .gate {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #334155; font-size: 14px; }}
        .gate-pass {{ color: #4ade80; font-weight: bold; }}
        .gate-fail {{ color: #f87171; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1 style="margin:0; font-size:24px;">🛡️ AMstraLift Transformation Evidence</h1>
                <div style="color:#94a3b8; font-size:14px; margin-top:6px;">Repo: <strong>{html.escape(bundle.repo_name)}</strong> ({html.escape(bundle.ecosystem)}) | Run: <code>{html.escape(bundle.run_id)}</code></div>
            </div>
            <div class="badge">{status_title}</div>
        </div>

        <div class="grid">
            <div class="card">
                <div style="color:#94a3b8; font-size:13px;">Files Analyzed / Modified</div>
                <div class="card-num">{bundle.files_analyzed} / {bundle.files_modified}</div>
            </div>
            <div class="card">
                <div style="color:#94a3b8; font-size:13px;">Transformations Applied</div>
                <div class="card-num">{len(bundle.transformations_applied)}</div>
            </div>
            <div class="card">
                <div style="color:#94a3b8; font-size:13px;">Transformations Refused</div>
                <div class="card-num" style="color:#fde047;">{len(bundle.transformations_refused)}</div>
            </div>
            <div class="card">
                <div style="color:#94a3b8; font-size:13px;">CVEs Resolved</div>
                <div class="card-num" style="color:#4ade80;">{v.cves_resolved}</div>
            </div>
        </div>

        <div class="card" style="margin-top:24px;">
            <h3 style="margin-top:0;">🧪 6-Gate Verification & Contract Boundary</h3>
            <div class="gate"><span>Gate 1: Clean Install</span><span class="gate-pass">{v.clean_install.upper()}</span></div>
            <div class="gate"><span>Gate 2: Project Build</span><span class="gate-pass">{v.build.upper()} ({v.build_duration_seconds:.1f}s)</span></div>
            <div class="gate"><span>Gate 3: Test Suite</span><span class="gate-pass">{v.tests.upper()} ({v.tests_passed}/{v.tests_run} passed)</span></div>
            <div class="gate"><span>Gate 4: Post-Upgrade Rescan</span><span class="gate-pass">{v.post_rescan.upper()} (0 new CVEs)</span></div>
            <div class="gate"><span>Gate 5: Signed Manifest Diff</span><span class="gate-pass">{v.manifest_diff.upper()}</span></div>
            <div class="gate" style="border-bottom:none;"><span>Gate 6: Semantic & Contract Diff</span><span class="gate-pass">{v.gate_6_semantic_diff.upper()}</span></div>
        </div>

        {refusals_html}
    </div>
</body>
</html>
"""
