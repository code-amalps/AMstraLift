"""Evidence Engine for AMstraLift.

Responsible for assembling, verifying, and cryptographically signing the Evidence Bundle.
Answers the foundational enterprise question:
"What did AMstraLift change, what did it refuse to change, what evidence supports each decision,
and what verification proves the resulting system is safe?"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from amstralift.core.crypto import compute_sha256
from amstralift.evidence.models import (
    CryptographicAttestation,
    EvidenceBundle,
    GateVerificationRecord,
    RefusalRecord,
    TransformationRecord,
    TransformationStatus,
)
from amstralift.evidence.refusal_engine import RefusalEngine

logger = logging.getLogger(__name__)


class EvidenceEngine:
    """Manages the creation, verification tracking, and cryptographic signing of EvidenceBundles."""

    def __init__(
        self,
        repo_name: str,
        repo_path: str,
        ecosystem: str,
        framework_from: str | None = None,
        framework_to: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.refusal_engine = RefusalEngine()
        self.bundle = EvidenceBundle(
            run_id=run_id or f"run_{uuid4().hex[:12]}",
            repo_name=repo_name,
            repo_path=repo_path,
            ecosystem=ecosystem,
            framework_from=framework_from,
            framework_to=framework_to,
        )

    # ── Tracking API ─────────────────────────────────────────────────────────

    def record_transformation(
        self,
        adapter: str,
        rule_id: str,
        file_path: str,
        status: TransformationStatus = TransformationStatus.APPLIED,
        ast_nodes_changed: int = 0,
        dependencies_changed: list[str] | None = None,
        diff_snippet: str | None = None,
        line_range: tuple[int, int] | None = None,
    ) -> TransformationRecord:
        """Record an applied or verified code or manifest transformation."""
        rec = TransformationRecord(
            adapter=adapter,
            rule_id=rule_id,
            file_path=file_path,
            status=status,
            ast_nodes_changed=ast_nodes_changed,
            dependencies_changed=dependencies_changed or [],
            diff_snippet=diff_snippet,
            line_range=line_range,
        )
        self.bundle.transformations_applied.append(rec)
        return rec

    def record_refusal(self, refusal: RefusalRecord) -> None:
        """Add a refusal record emitted by RefusalEngine."""
        self.bundle.transformations_refused.append(refusal)

    def update_verification_gates(
        self,
        clean_install: Literal["passed", "failed", "skipped", "uncertain"] = "passed",
        build: Literal["passed", "failed", "skipped", "uncertain"] = "passed",
        tests: Literal["passed", "failed", "skipped", "uncertain"] = "passed",
        post_rescan: Literal["passed", "failed", "skipped", "uncertain"] = "passed",
        manifest_diff: Literal["passed", "failed", "skipped", "uncertain"] = "passed",
        build_duration_seconds: float = 0.0,
        tests_run: int = 0,
        tests_passed: int = 0,
        tests_failed: int = 0,
        pre_cves_count: int = 0,
        post_cves_count: int = 0,
    ) -> GateVerificationRecord:
        """Update results for the 5 sandbox verification gates."""
        v = GateVerificationRecord(
            clean_install=clean_install,
            build=build,
            tests=tests,
            post_rescan=post_rescan,
            manifest_diff=manifest_diff,
            build_duration_seconds=build_duration_seconds,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            pre_cves_count=pre_cves_count,
            post_cves_count=post_cves_count,
        )
        self.bundle.verification = v

        # Compute overall status deterministically
        if not v.all_gates_passed or build == "failed" or tests == "failed":
            self.bundle.overall_status = "VERIFICATION_FAILED"
        elif len(self.bundle.transformations_refused) > 0:
            self.bundle.overall_status = "PARTIALLY_MODERNIZED"
        else:
            self.bundle.overall_status = "SUCCESS"

        return v

    def set_file_metrics(self, analyzed: int, modified: int) -> None:
        """Record count of analyzed and modified files."""
        self.bundle.files_analyzed = analyzed
        self.bundle.files_modified = modified

    # ── Cryptographic Attestation ────────────────────────────────────────────

    def sign_bundle(self, secret_key: bytes) -> CryptographicAttestation:
        """Cryptographically sign the canonical EvidenceBundle with HMAC-SHA256."""
        import hmac

        canonical_bytes = compute_canonical_evidence_payload(self.bundle)
        payload_hash = compute_sha256(canonical_bytes)
        signature = hmac.new(secret_key, canonical_bytes, "sha256").hexdigest()

        attestation = CryptographicAttestation(
            bundle_signature=signature,
            signature_algorithm="HMAC-SHA256",
            signer="AMstraLift Trusted Attestation Engine",
            signed_at=datetime.now(timezone.utc),
            payload_sha256=payload_hash,
        )
        self.bundle.attestation = attestation
        return attestation

    def verify_attestation(self, secret_key: bytes) -> bool:
        """Verify the cryptographic attestation of this engine's active bundle."""
        return verify_bundle_attestation(self.bundle, secret_key)

    # ── Export & Serialization ───────────────────────────────────────────────

    def to_json(self, indent: int = 2) -> str:
        """Export the complete EvidenceBundle as a validated, formatted JSON string."""
        return self.bundle.model_dump_json(indent=indent)

    def save_json(self, destination: Path | str) -> Path:
        """Write the EvidenceBundle to a file on disk."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        logger.info("Saved evidence bundle to %s", target)
        return target


def compute_canonical_evidence_payload(bundle: EvidenceBundle) -> bytes:
    """Build deterministic byte representation of bundle metadata for signing and verification."""
    payload = {
        "schema_version": bundle.schema_version,
        "bundle_id": bundle.bundle_id,
        "run_id": bundle.run_id,
        "repo_name": bundle.repo_name,
        "ecosystem": bundle.ecosystem,
        "overall_status": bundle.overall_status,
        "files_analyzed": bundle.files_analyzed,
        "files_modified": bundle.files_modified,
        "applied_count": len(bundle.transformations_applied),
        "refused_count": len(bundle.transformations_refused),
        "applied_ids": sorted(t.id for t in bundle.transformations_applied),
        "refused_ids": sorted(r.id for r in bundle.transformations_refused),
        "verification": {
            "clean_install": bundle.verification.clean_install,
            "build": bundle.verification.build,
            "tests": bundle.verification.tests,
            "post_rescan": bundle.verification.post_rescan,
            "manifest_diff": bundle.verification.manifest_diff,
            "gate_6_semantic_diff": bundle.verification.gate_6_semantic_diff,
        },
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def verify_bundle_attestation(bundle: EvidenceBundle, secret_key: bytes) -> bool:
    """Verify cryptographic authenticity and tamper-resistance of an EvidenceBundle.

    Returns True if and only if:
    1. An attestation block is present.
    2. The canonical payload SHA-256 matches payload_sha256.
    3. The HMAC-SHA256 signature matches with constant-time equality.
    """
    import hmac

    if not bundle.attestation:
        return False

    canonical_bytes = compute_canonical_evidence_payload(bundle)
    expected_hash = compute_sha256(canonical_bytes)

    if not hmac.compare_digest(expected_hash, bundle.attestation.payload_sha256):
        return False

    expected_sig = hmac.new(secret_key, canonical_bytes, "sha256").hexdigest()
    return hmac.compare_digest(expected_sig, bundle.attestation.bundle_signature)
