"""Semantic Diff Engine for Gate 6: Semantic & Contract Diff.

Coordinates route, symbol, and configuration extractors, computes before/after diffs,
and evaluates contract compliance against ModernizationContract.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from amstralift.semantic.extractors.config_extractor import ConfigurationExtractor
from amstralift.semantic.extractors.route_extractor import RouteExtractor
from amstralift.semantic.extractors.symbol_extractor import ExportedSymbolExtractor
from amstralift.semantic.models import (
    ContractChangeType,
    Gate6Result,
    ModernizationContract,
    SemanticDiffItem,
    SemanticSeverity,
    SemanticSnapshot,
)

logger = logging.getLogger(__name__)


class SemanticDiffEngine:
    """Computes observable contract diffs and verifies modernization contract conformance."""

    def __init__(self) -> None:
        self.route_extractor = RouteExtractor()
        self.symbol_extractor = ExportedSymbolExtractor()
        self.config_extractor = ConfigurationExtractor()

    def take_snapshot(self, repo_path: Path | str) -> SemanticSnapshot:
        """Scan repository and create an observable contract snapshot."""
        path = Path(repo_path)
        routes = self.route_extractor.extract_from_directory(path)
        symbols = self.symbol_extractor.extract_from_directory(path)
        configs = self.config_extractor.extract_from_directory(path)

        return SemanticSnapshot(
            repo_path=str(path),
            routes=routes,
            exported_symbols=symbols,
            config_keys=configs,
        )

    def diff_snapshots(
        self,
        before: SemanticSnapshot,
        after: SemanticSnapshot,
    ) -> list[SemanticDiffItem]:
        """Compute observable differences between before and after contract snapshots."""
        diffs: list[SemanticDiffItem] = []

        # ── 1. Compare Routes ────────────────────────────────────────────────
        before_routes = {r.canonical_id: r for r in before.routes}
        after_routes = {r.canonical_id: r for r in after.routes}

        for c_id, r_before in before_routes.items():
            if c_id not in after_routes:
                diffs.append(
                    SemanticDiffItem(
                        category="route",
                        change_type=ContractChangeType.ROUTE_REMOVED,
                        item_id=c_id,
                        before_value=r_before.path,
                        after_value=None,
                        severity=SemanticSeverity.BREAKING,
                        file_path=r_before.file_path,
                        explanation=f"Observable route '{c_id}' was removed.",
                    )
                )

        for c_id, r_after in after_routes.items():
            if c_id not in before_routes:
                diffs.append(
                    SemanticDiffItem(
                        category="route",
                        change_type=ContractChangeType.ROUTE_ADDED,
                        item_id=c_id,
                        before_value=None,
                        after_value=r_after.path,
                        severity=SemanticSeverity.COMPATIBLE,
                        file_path=r_after.file_path,
                        explanation=f"New observable route '{c_id}' was added.",
                    )
                )

        # ── 2. Compare Exported Symbols ──────────────────────────────────────
        before_symbols = {s.canonical_id: s for s in before.exported_symbols}
        after_symbols = {s.canonical_id: s for s in after.exported_symbols}

        for c_id, s_before in before_symbols.items():
            if c_id not in after_symbols:
                diffs.append(
                    SemanticDiffItem(
                        category="symbol",
                        change_type=ContractChangeType.SYMBOL_REMOVED,
                        item_id=c_id,
                        before_value=s_before.name,
                        after_value=None,
                        severity=SemanticSeverity.BREAKING,
                        file_path=s_before.file_path,
                        explanation=f"Exported symbol '{c_id}' was removed.",
                    )
                )

        for c_id, s_after in after_symbols.items():
            if c_id not in before_symbols:
                diffs.append(
                    SemanticDiffItem(
                        category="symbol",
                        change_type=ContractChangeType.SYMBOL_ADDED,
                        item_id=c_id,
                        before_value=None,
                        after_value=s_after.name,
                        severity=SemanticSeverity.COMPATIBLE,
                        file_path=s_after.file_path,
                        explanation=f"New exported symbol '{c_id}' was added.",
                    )
                )

        # ── 3. Compare Configuration Keys ────────────────────────────────────
        before_configs = {c.canonical_id: c for c in before.config_keys}
        after_configs = {c.canonical_id: c for c in after.config_keys}

        for c_id, c_before in before_configs.items():
            if c_id not in after_configs:
                diffs.append(
                    SemanticDiffItem(
                        category="config",
                        change_type=ContractChangeType.CONFIG_KEY_REMOVED,
                        item_id=c_id,
                        before_value=c_before.key,
                        after_value=None,
                        severity=SemanticSeverity.BREAKING,
                        file_path=c_before.source_file,
                        explanation=f"Configuration key / environment variable '{c_id}' was removed.",
                    )
                )

        for c_id, c_after in after_configs.items():
            if c_id not in before_configs:
                diffs.append(
                    SemanticDiffItem(
                        category="config",
                        change_type=ContractChangeType.CONFIG_KEY_ADDED,
                        item_id=c_id,
                        before_value=None,
                        after_value=c_after.key,
                        severity=SemanticSeverity.COMPATIBLE,
                        file_path=c_after.source_file,
                        explanation=f"New configuration key '{c_id}' was introduced.",
                    )
                )

        return diffs

    def evaluate_contract(
        self,
        diffs: list[SemanticDiffItem],
        contract: ModernizationContract | None = None,
    ) -> Gate6Result:
        """Evaluate semantic diffs against declared ModernizationContract rules."""
        active_contract = contract or ModernizationContract()

        violations: list[SemanticDiffItem] = []
        expected_changes: list[SemanticDiffItem] = []
        compatible_additions: list[SemanticDiffItem] = []

        for diff in diffs:
            # Check if this change matches an explicit authorized rule in contract.allowed
            matched = False
            for rule in active_contract.allowed:
                if rule.matches(diff):
                    diff.is_expected = True
                    diff.severity = SemanticSeverity.EXPECTED
                    diff.matched_rule = rule.description or "allowed_rule"
                    expected_changes.append(diff)
                    matched = True
                    break

            if matched:
                continue

            # If not explicitly authorized, check severity
            if diff.severity == SemanticSeverity.BREAKING:
                violations.append(diff)
            elif diff.severity == SemanticSeverity.COMPATIBLE:
                if active_contract.allow_safe_additions:
                    compatible_additions.append(diff)
                else:
                    violations.append(diff)

        # Status determination
        if violations:
            status = "failed"
            summary = f"Gate 6 FAILED: {len(violations)} unexpected contract violation(s) detected."
        elif expected_changes:
            status = "passed_with_expected_changes"
            summary = f"Gate 6 PASSED: {len(expected_changes)} expected contract change(s) verified against ModernizationContract."
        else:
            status = "passed"
            summary = "Gate 6 PASSED: Observable contracts fully preserved (0 contract violations)."

        return Gate6Result(
            status=status,
            violations=violations,
            expected_changes=expected_changes,
            compatible_additions=compatible_additions,
            summary=summary,
        )

    def run_gate_6(
        self,
        repo_before: Path | str,
        repo_after: Path | str,
        contract: ModernizationContract | None = None,
    ) -> Gate6Result:
        """Execute complete Gate 6 verification comparing before and after repository states."""
        before_snap = self.take_snapshot(repo_before)
        after_snap = self.take_snapshot(repo_after)
        diffs = self.diff_snapshots(before_snap, after_snap)
        return self.evaluate_contract(diffs, contract)
