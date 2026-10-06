"""AMstraLift Transformation Evidence & Refusal Engine.

Provides the foundational trust architecture:
- Decision Engine (Transform vs Refuse)
- Refusal Engine (non-invasive, zero source modification safety guarantee)
- Evidence Engine (assembly, HMAC-SHA256 attestation, JSON emission)
- Reporter (Markdown and standalone HTML evidence reports)
"""

from amstralift.evidence.evidence_engine import EvidenceEngine
from amstralift.evidence.models import (
    CryptographicAttestation,
    EvidenceBundle,
    GateVerificationRecord,
    RefusalCategory,
    RefusalRecord,
    TransformationRecord,
    TransformationStatus,
)
from amstralift.evidence.refusal_engine import RefusalEngine
from amstralift.evidence.reporter import render_html_report, render_markdown_report

__all__ = [
    "CryptographicAttestation",
    "EvidenceBundle",
    "EvidenceEngine",
    "GateVerificationRecord",
    "RefusalCategory",
    "RefusalEngine",
    "RefusalRecord",
    "TransformationRecord",
    "TransformationStatus",
    "render_html_report",
    "render_markdown_report",
]
