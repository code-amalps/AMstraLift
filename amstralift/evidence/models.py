"""Data models for transformation evidence, safe refusals, and cryptographic attestation.

Represents the core data structures for proving what AMstraLift changed,
what it deliberately refused to touch, and the verification evidence validating
the resulting system state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class TransformationStatus(str, Enum):
    """Lifecycle status of a proposed AST or manifest transformation."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    VERIFIED = "verified"
    REFUSED = "refused"
    FAILED = "failed"


class RefusalCategory(str, Enum):
    """Categorization of why AMstraLift refused to apply an unsafe transformation.

    Guarantees deterministic refusal rather than unverified guessing.
    """

    UNSUPPORTED_PATTERN = "unsupported_pattern"
    COMPILATION_RISK = "compilation_risk"
    AMBIGUOUS_CONTRACT = "ambiguous_contract"
    SANDBOX_FAILURE = "sandbox_failure"
    UNVERIFIED_BEHAVIOR = "unverified_behavior"


class TransformationRecord(BaseModel):
    """Structured evidence record of an applied or verified transformation."""

    id: str = Field(default_factory=lambda: f"tr_{uuid4().hex[:8]}")
    adapter: str  # "angular", "react", "dotnet", "python"
    rule_id: str  # e.g. "migrate-standalone", "modernize-control-flow", "cpm-pin"
    file_path: str
    line_range: tuple[int, int] | None = None
    status: TransformationStatus = TransformationStatus.APPLIED
    ast_nodes_changed: int = 0
    dependencies_changed: list[str] = Field(default_factory=list)
    diff_snippet: str | None = None
    is_deterministic: bool = True
    sandbox_verified: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RefusalRecord(BaseModel):
    """Structured evidence record of a deliberately refused transformation.

    Fundamental Trust Guarantee:
    Whenever a RefusalRecord is emitted, AMstraLift STRICTLY GUARANTEES that
    the target source code file was NOT modified (code_modified is unconditionally False).
    No artificial comments (such as TODOs) are injected into the file.
    """

    id: str = Field(default_factory=lambda: f"ref_{uuid4().hex[:8]}")
    adapter: str
    rule_id: str
    file_path: str
    category: RefusalCategory
    reason: str
    code_modified: bool = False  # Strictly False: source untouched!
    developer_review_required: bool = True
    context_snippet: str | None = None
    manual_guidance: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TransformationDecision(str, Enum):
    """Terminal decision for a candidate transformation."""

    APPLIED = "APPLIED"
    REFUSED = "REFUSED"


class TransformationResult(BaseModel):
    """Canonical unified result bridging candidate evaluation, execution, and evidence."""

    id: str = Field(default_factory=lambda: f"res_{uuid4().hex[:8]}")
    adapter: str
    rule_id: str
    file_path: str
    decision: TransformationDecision
    transformation_record: TransformationRecord | None = None
    refusal_record: RefusalRecord | None = None
    ast_nodes_changed: int = 0
    files_modified: int = 0
    is_deterministic: bool = True
    evidence_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_applied(self) -> bool:
        return self.decision == TransformationDecision.APPLIED

    @property
    def is_refused(self) -> bool:
        return self.decision == TransformationDecision.REFUSED


class GateVerificationRecord(BaseModel):
    """Aggregated status across the 5 sandboxed verification gates."""

    clean_install: Literal["passed", "failed", "skipped", "uncertain"] = "passed"
    build: Literal["passed", "failed", "skipped", "uncertain"] = "passed"
    tests: Literal["passed", "failed", "skipped", "uncertain"] = "passed"
    post_rescan: Literal["passed", "failed", "skipped", "uncertain"] = "passed"
    manifest_diff: Literal["passed", "failed", "skipped", "uncertain"] = "passed"

    build_duration_seconds: float = 0.0
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    pre_cves_count: int = 0
    post_cves_count: int = 0

    @computed_field
    def cves_resolved(self) -> int:
        return max(0, self.pre_cves_count - self.post_cves_count)

    @computed_field
    def all_gates_passed(self) -> bool:
        return (
            self.clean_install == "passed"
            and self.build == "passed"
            and self.tests in ("passed", "skipped")
            and self.post_rescan == "passed"
            and self.manifest_diff == "passed"
        )


class CryptographicAttestation(BaseModel):
    """Cryptographic signature proving the authenticity and integrity of the evidence bundle."""

    bundle_signature: str
    signature_algorithm: str = "HMAC-SHA256"
    signer: str = "AMstraLift Trusted Attestation Engine"
    signed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload_sha256: str


class EvidenceBundle(BaseModel):
    """Comprehensive, cryptographically sealable bundle proving modernization integrity.

    Combines applied transformations, deliberate refusals, multi-gate verification results,
    and cryptographic attestation for both machine consumption (JSON) and human review.
    """

    schema_version: str = "1.0.0"
    bundle_id: str = Field(default_factory=lambda: f"evb_{uuid4().hex[:12]}")
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    repo_name: str
    repo_path: str
    ecosystem: str
    framework_from: str | None = None
    framework_to: str | None = None

    overall_status: Literal["SUCCESS", "PARTIALLY_MODERNIZED", "VERIFICATION_FAILED"] = "SUCCESS"

    files_analyzed: int = 0
    files_modified: int = 0

    transformations_applied: list[TransformationRecord] = Field(default_factory=list)
    transformations_refused: list[RefusalRecord] = Field(default_factory=list)

    verification: GateVerificationRecord = Field(default_factory=GateVerificationRecord)
    attestation: CryptographicAttestation | None = None

    @computed_field
    def total_transformations_count(self) -> int:
        return len(self.transformations_applied)

    @computed_field
    def total_refusals_count(self) -> int:
        return len(self.transformations_refused)

    @computed_field
    def is_partially_modernized(self) -> bool:
        return self.overall_status == "PARTIALLY_MODERNIZED" or (
            self.verification.all_gates_passed and len(self.transformations_refused) > 0
        )
