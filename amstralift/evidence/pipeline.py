"""Transformation Decision Pipeline for AMstraLift.

Implements the Phase P4 Partial Modernization state machine:
- Accepts candidate transformations
- Evaluates each candidate through RefusalEngine
- Isolates operations: applies safe candidates, refuses unsafe ones (leaving source untouched)
- Non-poisoning: a refusal does NOT abort or poison subsequent transformations
- Emits unified TransformationResult instances
- Records both applied and refused evidence into EvidenceEngine
- Reconciles overall status: SUCCESS vs PARTIALLY_MODERNIZED vs VERIFICATION_FAILED
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, Field

from amstralift.core.crypto import compute_file_sha256
from amstralift.evidence.evidence_engine import EvidenceEngine
from amstralift.evidence.models import (
    GateVerificationRecord,
    RefusalCategory,
    RefusalRecord,
    TransformationDecision,
    TransformationRecord,
    TransformationResult,
    TransformationStatus,
)
from amstralift.evidence.refusal_engine import RefusalEngine

logger = logging.getLogger(__name__)


class CandidateTransformation(BaseModel):
    """Specification of a candidate code or manifest transformation before decision."""

    id: str = Field(default_factory=lambda: f"cand_{uuid4().hex[:8]}")
    adapter: str
    rule_id: str
    file_path: str  # Relative or absolute path
    # Callable that performs the actual AST transformation: fn(target_file_path) -> (ast_nodes_changed, diff_snippet)
    apply_fn: Any | None = None
    # Explicit content inspector override if needed
    content_checker: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransformationDecisionPipeline:
    """Executes the transformation decision pipeline with non-poisoning safe partial modernization."""

    def __init__(
        self,
        evidence_engine: EvidenceEngine,
        repo_path: Path | str,
    ) -> None:
        self.evidence_engine = evidence_engine
        self.repo_path = Path(repo_path)
        self.refusal_engine = evidence_engine.refusal_engine
        self.results: list[TransformationResult] = []

    def evaluate_and_execute(
        self,
        candidates: list[CandidateTransformation],
    ) -> list[TransformationResult]:
        """Process candidate transformations through the decision engine.

        Guarantees:
        1. If refused: file on disk is left 100% untouched; RefusalRecord is emitted.
        2. Non-poisoning: a refused candidate NEVER aborts subsequent candidates.
        3. Transactional rollback: if an apply function raises an unhandled error,
           the file is rolled back byte-for-byte and marked as REFUSED (code_modified=False).
        """
        analyzed_files: set[str] = set()
        modified_files: set[str] = set()

        for candidate in candidates:
            target = (self.repo_path / candidate.file_path).resolve()
            analyzed_files.add(candidate.file_path)

            file_content = ""
            hash_before = ""
            if target.is_file():
                file_content = target.read_text(encoding="utf-8")
                hash_before = compute_file_sha256(str(target))

            # 1. Evaluate candidate for refusal triggers
            refusals_detected: list[RefusalRecord] = []
            if candidate.content_checker:
                ref = candidate.content_checker(file_content)
                if ref:
                    refusals_detected.append(ref)
            else:
                if candidate.adapter == "angular":
                    refusals_detected.extend(
                        self.refusal_engine.inspect_angular_file(candidate.file_path, file_content)
                    )
                elif candidate.adapter == "react":
                    refusals_detected.extend(
                        self.refusal_engine.inspect_react_file(candidate.file_path, file_content)
                    )
                elif candidate.adapter == "python":
                    refusals_detected.extend(
                        self.refusal_engine.inspect_python_file(candidate.file_path, file_content)
                    )
                elif candidate.adapter == "dotnet":
                    refusals_detected.extend(
                        self.refusal_engine.inspect_dotnet_file(candidate.file_path, file_content)
                    )

            # 2. Decision Path: REFUSE
            if refusals_detected:
                refusal = refusals_detected[0]
                # Invariant: verify file remained untouched
                if target.is_file():
                    assert compute_file_sha256(str(target)) == hash_before

                self.evidence_engine.record_refusal(refusal)
                result = TransformationResult(
                    id=f"res_{refusal.id[4:]}",
                    adapter=candidate.adapter,
                    rule_id=candidate.rule_id,
                    file_path=candidate.file_path,
                    decision=TransformationDecision.REFUSED,
                    refusal_record=refusal,
                    evidence_id=self.evidence_engine.bundle.bundle_id,
                )
                self.results.append(result)
                continue

            # 3. Decision Path: APPLY
            ast_nodes_changed = 0
            diff_snippet = None
            applied_successfully = True

            if candidate.apply_fn:
                try:
                    res = candidate.apply_fn(target)
                    if isinstance(res, tuple):
                        ast_nodes_changed, diff_snippet = res
                    elif isinstance(res, int):
                        ast_nodes_changed = res
                except Exception as exc:
                    logger.warning("Error applying transformation %s on %s: %s. Rolling back.", candidate.rule_id, candidate.file_path, exc)
                    # Transactional rollback to pre-transformation state
                    if target.is_file() and hash_before:
                        target.write_text(file_content, encoding="utf-8")
                        assert compute_file_sha256(str(target)) == hash_before

                    # Convert unhandled exception into a safe refusal record
                    refusal = self.refusal_engine.refuse(
                        adapter=candidate.adapter,
                        rule_id=candidate.rule_id,
                        file_path=candidate.file_path,
                        category=RefusalCategory.COMPILATION_RISK,
                        reason=f"Transformation execution threw unexpected error: {exc}",
                        manual_guidance="Inspect AST transform logic manually.",
                    )
                    self.evidence_engine.record_refusal(refusal)
                    result = TransformationResult(
                        id=f"res_{refusal.id[4:]}",
                        adapter=candidate.adapter,
                        rule_id=candidate.rule_id,
                        file_path=candidate.file_path,
                        decision=TransformationDecision.REFUSED,
                        refusal_record=refusal,
                        evidence_id=self.evidence_engine.bundle.bundle_id,
                    )
                    self.results.append(result)
                    applied_successfully = False

            if applied_successfully:
                modified_files.add(candidate.file_path)
                tr = self.evidence_engine.record_transformation(
                    adapter=candidate.adapter,
                    rule_id=candidate.rule_id,
                    file_path=candidate.file_path,
                    status=TransformationStatus.APPLIED,
                    ast_nodes_changed=ast_nodes_changed,
                    diff_snippet=diff_snippet,
                )
                result = TransformationResult(
                    id=f"res_{tr.id[3:]}",
                    adapter=candidate.adapter,
                    rule_id=candidate.rule_id,
                    file_path=candidate.file_path,
                    decision=TransformationDecision.APPLIED,
                    transformation_record=tr,
                    ast_nodes_changed=ast_nodes_changed,
                    files_modified=1,
                    evidence_id=self.evidence_engine.bundle.bundle_id,
                )
                self.results.append(result)

        self.evidence_engine.set_file_metrics(
            analyzed=len(analyzed_files),
            modified=len(modified_files),
        )
        return self.results

    def reconcile_modernization_status(
        self,
        gate_record: GateVerificationRecord,
    ) -> str:
        """Reconcile overall modernization status following 5-gate sandbox execution."""
        self.evidence_engine.bundle.verification = gate_record

        # Dominance rule: any required gate failure unconditionally dominates!
        if not gate_record.all_gates_passed:
            self.evidence_engine.bundle.overall_status = "VERIFICATION_FAILED"
        elif len(self.evidence_engine.bundle.transformations_refused) > 0:
            self.evidence_engine.bundle.overall_status = "PARTIALLY_MODERNIZED"
        else:
            self.evidence_engine.bundle.overall_status = "SUCCESS"

        return self.evidence_engine.bundle.overall_status
