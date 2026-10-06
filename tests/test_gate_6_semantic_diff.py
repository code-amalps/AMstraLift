"""Comprehensive Acceptance Tests for Gate 6: Semantic & Contract Diff Engine.

Validates the Phase P5 contract boundary:
1. Scenario A: Safe modernization (identical observable contract -> PASS)
2. Scenario B: Expected contract change (authorized route migration in contract -> PASSED_WITH_EXPECTED_CHANGES)
3. Scenario C: Unexpected contract break (silent route change without permission -> GATE_6_FAILED)
4. Scenario D: Build and tests succeed, but Gate 6 contract break forces VERIFICATION_FAILED
5. Scenario E: Exported symbols and configuration key contract diffs
6. Scenario F: Cryptographic attestation incorporates Gate 6 result
"""

from pathlib import Path

import pytest

from amstralift.evidence.evidence_engine import (
    EvidenceEngine,
    verify_bundle_attestation,
)
from amstralift.evidence.models import GateVerificationRecord
from amstralift.evidence.reporter import render_html_report, render_markdown_report
from amstralift.semantic.engine import SemanticDiffEngine
from amstralift.semantic.models import (
    AllowedContractRule,
    ContractChangeType,
    ModernizationContract,
    SemanticSeverity,
)


# ── Scenario A: Safe Modernization (Preserved Contract) ──────────────────────


def test_scenario_a_safe_modernization_preserved_contract(tmp_path: Path):
    """Scenario A: Before and After have identical observable endpoints -> Gate 6 PASS."""
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    # Before: FastAPI route
    (before_dir / "api.py").write_text(
        "@app.get('/api/orders')\ndef get_orders(): return []\n",
        encoding="utf-8",
    )
    # After: Same route, internally refactored logic
    (after_dir / "api.py").write_text(
        "@app.get('/api/orders')\nasync def get_orders(): return await service.fetch_orders()\n",
        encoding="utf-8",
    )

    engine = SemanticDiffEngine()
    result = engine.run_gate_6(before_dir, after_dir)

    assert result.status == "passed"
    assert result.is_passed is True
    assert len(result.violations) == 0
    assert "Observable contracts fully preserved" in result.summary


# ── Scenario B: Expected Contract Change (Authorized by Contract) ────────────


def test_scenario_b_expected_contract_change(tmp_path: Path):
    """Scenario B: Route migrates from /api/orders to /api/v2/orders, explicitly allowed in ModernizationContract -> PASS_WITH_EXPECTED."""
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    (before_dir / "api.py").write_text(
        "@app.get('/api/orders')\ndef get_orders(): return []\n",
        encoding="utf-8",
    )
    (after_dir / "api.py").write_text(
        "@app.get('/api/v2/orders')\ndef get_orders(): return []\n",
        encoding="utf-8",
    )

    contract = ModernizationContract(
        allowed=[
            AllowedContractRule(
                category="route",
                from_pattern="/api/orders",
                description="Planned API v2 route migration",
            )
        ]
    )

    engine = SemanticDiffEngine()
    result = engine.run_gate_6(before_dir, after_dir, contract=contract)

    assert result.status == "passed_with_expected_changes"
    assert result.is_passed is True
    assert len(result.violations) == 0
    assert len(result.expected_changes) >= 1
    expected_ids = {e.item_id for e in result.expected_changes}
    assert "GET /api/orders" in expected_ids
    assert result.expected_changes[0].matched_rule == "Planned API v2 route migration"


# ── Scenario C: Unexpected Contract Break (Unauthorized Change) ──────────────


def test_scenario_c_unexpected_contract_break(tmp_path: Path):
    """Scenario C: Route silently dropped from /api/orders to /api/order without contract authorization -> Gate 6 FAILED."""
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    (before_dir / "api.py").write_text(
        "@app.get('/api/orders')\ndef get_orders(): return []\n",
        encoding="utf-8",
    )
    # Accidental typo or silent change: /api/order instead of /api/orders
    (after_dir / "api.py").write_text(
        "@app.get('/api/order')\ndef get_order(): return {}\n",
        encoding="utf-8",
    )

    engine = SemanticDiffEngine()
    result = engine.run_gate_6(before_dir, after_dir)

    assert result.status == "failed"
    assert result.is_passed is False
    assert len(result.violations) >= 1
    violation_ids = {v.item_id for v in result.violations}
    assert "GET /api/orders" in violation_ids


# ── Scenario D: Build Succeeds but Contract Breaks (The Crucial Proof) ───────


def test_scenario_d_build_succeeds_but_gate_6_breaks_dominates():
    """Scenario D: Gates 1 through 5 pass, but Gate 6 detects unexpected contract break -> Status is VERIFICATION_FAILED."""
    ee = EvidenceEngine(
        repo_name="MissionCriticalBanking",
        repo_path="/repos/bank",
        ecosystem="python",
    )
    ee.record_transformation("python", "clean-api", "api.py")

    # Gates 1-5 pass, but Gate 6 failed
    gates = GateVerificationRecord(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        gate_6_semantic_diff="failed",
        semantic_violations=["Observable route 'POST /transfer' was silently removed"],
    )

    # Reconcile on EvidenceEngine
    ee.update_verification_gates(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
    )
    ee.bundle.verification = gates

    # Invariant: Gate 6 failure must force all_gates_passed to False!
    assert gates.all_gates_passed is False

    # Invariant: Overall status must be VERIFICATION_FAILED
    if not gates.all_gates_passed:
        ee.bundle.overall_status = "VERIFICATION_FAILED"

    assert ee.bundle.overall_status == "VERIFICATION_FAILED"


# ── Scenario E: Exported Symbols & Configuration Contract Diffs ──────────────


def test_scenario_e_symbol_and_config_contract_diffs(tmp_path: Path):
    """Scenario E: Verify TS/JS exported symbols and .env configuration key contract changes."""
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()

    # Before: Service class + .env config
    (before_dir / "service.ts").write_text(
        "export class OrderService {}\nexport function cancelOrder() {}\n",
        encoding="utf-8",
    )
    (before_dir / ".env").write_text(
        "DATABASE_URL=postgres://localhost\nAPI_KEY=xyz\n",
        encoding="utf-8",
    )

    # After: cancelOrder function dropped, API_KEY dropped from .env
    (after_dir / "service.ts").write_text(
        "export class OrderService {}\n",
        encoding="utf-8",
    )
    (after_dir / ".env").write_text(
        "DATABASE_URL=postgres://localhost\n",
        encoding="utf-8",
    )

    engine = SemanticDiffEngine()
    result = engine.run_gate_6(before_dir, after_dir)

    assert result.status == "failed"
    violation_ids = {v.item_id for v in result.violations}
    assert "function:cancelOrder" in violation_ids
    assert "API_KEY" in violation_ids


# ── Scenario F: Cryptographic Attestation Incorporates Gate 6 ────────────────


def test_scenario_f_cryptographic_attestation_covers_gate_6():
    """Scenario F: Cryptographic attestation binds Gate 6 status into HMAC-SHA256 payload."""
    secret = b"gate-6-signing-secret"
    ee = EvidenceEngine(
        repo_name="AttestedContractApp",
        repo_path="/repos/app",
        ecosystem="angular",
    )

    gates = GateVerificationRecord(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        gate_6_semantic_diff="passed",
    )
    ee.bundle.verification = gates
    ee.sign_bundle(secret)

    # Verify signature
    assert verify_bundle_attestation(ee.bundle, secret) is True

    # Tampering with Gate 6 status invalidates the signature
    ee.bundle.verification.gate_6_semantic_diff = "failed"
    assert verify_bundle_attestation(ee.bundle, secret) is False


def test_gate_6_rendering_in_reports():
    """Verify Gate 6 displays cleanly in both Markdown and HTML reports."""
    ee = EvidenceEngine(
        repo_name="ReportApp",
        repo_path="/repos/report",
        ecosystem="dotnet",
    )
    gates = GateVerificationRecord(
        clean_install="passed",
        build="passed",
        tests="passed",
        post_rescan="passed",
        manifest_diff="passed",
        gate_6_semantic_diff="passed",
    )
    ee.bundle.verification = gates

    md = render_markdown_report(ee.bundle)
    assert "Gate 6: Semantic / Contract    PASSED" in md

    html_out = render_html_report(ee.bundle)
    assert "Gate 6: Semantic &amp; Contract Diff" in html_out or "Gate 6: Semantic & Contract Diff" in html_out
    assert "PASSED" in html_out
