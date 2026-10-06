"""AMstraLift Transformation Evidence & Refusal Engine.

Provides the foundational trust architecture:
- Decision Engine (Transform vs Refuse)
- Refusal Engine (non-invasive, zero source modification safety guarantee)
- Evidence Engine (assembly, HMAC-SHA256 attestation, JSON emission)
- Reporter (Markdown and standalone HTML evidence reports)
"""

from amstralift.evidence.evidence_engine import (
    EvidenceEngine,
    compute_canonical_evidence_payload,
    verify_bundle_attestation,
)
from amstralift.evidence.models import (
    CryptographicAttestation,
    EvidenceBundle,
    GateVerificationRecord,
    RefusalCategory,
    RefusalRecord,
    TransformationDecision,
    TransformationRecord,
    TransformationResult,
    TransformationStatus,
)
from amstralift.evidence.pipeline import (
    CandidateTransformation,
    TransformationDecisionPipeline,
)
from amstralift.evidence.refusal_engine import RefusalEngine
from amstralift.evidence.reporter import render_html_report, render_markdown_report

__all__ = [
    "CandidateTransformation",
    "CryptographicAttestation",
    "EvidenceBundle",
    "EvidenceEngine",
    "GateVerificationRecord",
    "RefusalCategory",
    "RefusalEngine",
    "RefusalRecord",
    "TransformationDecision",
    "TransformationDecisionPipeline",
    "TransformationRecord",
    "TransformationResult",
    "TransformationStatus",
    "compute_canonical_evidence_payload",
    "render_html_report",
    "render_markdown_report",
    "verify_bundle_attestation",
]
