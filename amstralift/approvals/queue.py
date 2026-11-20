"""Persistent approval queue and decision signing engine (P23).

Engineered by Amal P S for AMstraLift Generation 3 (Enterprise Scale).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Optional

from amstralift.approvals.models import (
    ApprovalRequest,
    ApprovalStatus,
    HumanDecision,
)


class ApprovalQueue:
    """Manages pending human approval queues and cryptographically signs review decisions."""

    DEFAULT_APPROVALS_DIR = Path(".amstralift") / "approvals"

    @classmethod
    def submit(
        cls, request: ApprovalRequest, base_dir: Optional[Path] = None
    ) -> ApprovalRequest:
        """Submit an approval request into the persistent queue."""
        directory = (base_dir / cls.DEFAULT_APPROVALS_DIR) if base_dir else cls.DEFAULT_APPROVALS_DIR
        directory.mkdir(parents=True, exist_ok=True)

        req_file = directory / f"{request.request_id}.json"
        req_file.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return request

    @classmethod
    def get(cls, request_id: str, base_dir: Optional[Path] = None) -> ApprovalRequest:
        """Retrieve an approval request from the queue by ID."""
        directory = (base_dir / cls.DEFAULT_APPROVALS_DIR) if base_dir else cls.DEFAULT_APPROVALS_DIR
        req_file = directory / f"{request_id}.json"
        if not req_file.is_file():
            raise FileNotFoundError(f"Approval request '{request_id}' not found in queue.")

        data = json.loads(req_file.read_text(encoding="utf-8"))
        return ApprovalRequest(**data)

    @classmethod
    def list_requests(
        cls,
        status: Optional[ApprovalStatus] = None,
        base_dir: Optional[Path] = None,
    ) -> list[ApprovalRequest]:
        """List all queued approval requests, optionally filtered by status."""
        directory = (base_dir / cls.DEFAULT_APPROVALS_DIR) if base_dir else cls.DEFAULT_APPROVALS_DIR
        if not directory.is_dir():
            return []

        requests: list[ApprovalRequest] = []
        for f in directory.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                req = ApprovalRequest(**data)
                if status is None or req.status == status:
                    requests.append(req)
            except Exception:
                pass

        requests.sort(key=lambda r: r.created_at, reverse=True)
        return requests

    @classmethod
    def approve(
        cls,
        request_id: str,
        reviewer_name: str,
        reviewer_email: str,
        rationale: str,
        secret_key: str = "amstralift-approval-secret",
        base_dir: Optional[Path] = None,
    ) -> ApprovalRequest:
        """Approve a queued modernization proposal with a cryptographically sealed signature."""
        req = cls.get(request_id, base_dir=base_dir)

        decision = HumanDecision(
            decision=ApprovalStatus.APPROVED,
            reviewer_name=reviewer_name.strip(),
            reviewer_email=reviewer_email.strip(),
            rationale=rationale.strip(),
        )
        decision.signature = decision.generate_signature(secret_key)

        req.decision = decision
        req.status = ApprovalStatus.APPROVED
        req.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return cls.submit(req, base_dir=base_dir)

    @classmethod
    def reject(
        cls,
        request_id: str,
        reviewer_name: str,
        reviewer_email: str,
        rationale: str,
        secret_key: str = "amstralift-approval-secret",
        base_dir: Optional[Path] = None,
    ) -> ApprovalRequest:
        """Reject a queued modernization proposal with a cryptographically sealed signature."""
        req = cls.get(request_id, base_dir=base_dir)

        decision = HumanDecision(
            decision=ApprovalStatus.REJECTED,
            reviewer_name=reviewer_name.strip(),
            reviewer_email=reviewer_email.strip(),
            rationale=rationale.strip(),
        )
        decision.signature = decision.generate_signature(secret_key)

        req.decision = decision
        req.status = ApprovalStatus.REJECTED
        req.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return cls.submit(req, base_dir=base_dir)
