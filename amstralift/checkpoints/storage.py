"""Persistent storage and cryptographic verification for modernization checkpoints (P19).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from amstralift.checkpoints.models import CheckpointStatus, ModernizationCheckpoint


class CheckpointStore:
    """Manages disk persistence and integrity verification for modernization checkpoints."""

    def __init__(self, workspace_path: Path | str) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.checkpoints_dir = self.workspace_path / ".amstralift" / "checkpoints"

    def _ensure_dir(self) -> None:
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def _get_path(self, execution_id: str) -> Path:
        # Sanitize execution_id to avoid path traversal
        clean_id = execution_id.replace("/", "_").replace("\\", "_")
        return self.checkpoints_dir / f"{clean_id}.json"

    def compute_integrity_hash(self, checkpoint: ModernizationCheckpoint) -> str:
        """Compute deterministic SHA-256 hash over completed state."""
        payload = {
            "execution_id": checkpoint.execution_id,
            "plan_id": checkpoint.plan_id,
            "base_commit_sha": checkpoint.base_commit_sha,
            "current_commit_sha": checkpoint.current_commit_sha,
            "completed_transformations": sorted(checkpoint.completed_transformation_ids),
            "completed_phases_count": checkpoint.completed_phases_count,
        }
        raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()

    def save(self, checkpoint: ModernizationCheckpoint) -> Path:
        """Atomically persist checkpoint to disk with SHA-256 evidence hash."""
        self._ensure_dir()
        target_file = self._get_path(checkpoint.execution_id)
        temp_file = target_file.with_suffix(".tmp")

        # Update evidence hash before writing
        checkpoint.evidence_hash = self.compute_integrity_hash(checkpoint)

        data = checkpoint.model_dump_json(indent=2)
        temp_file.write_text(data, encoding="utf-8")
        temp_file.replace(target_file)
        return target_file

    def load(self, execution_id: str) -> ModernizationCheckpoint:
        """Load and verify checkpoint from disk."""
        target_file = self._get_path(execution_id)
        if not target_file.exists():
            raise FileNotFoundError(f"Checkpoint for execution '{execution_id}' does not exist at {target_file}")

        content = target_file.read_text(encoding="utf-8")
        checkpoint = ModernizationCheckpoint.model_validate_json(content)

        # Integrity check
        expected_hash = self.compute_integrity_hash(checkpoint)
        if checkpoint.evidence_hash and checkpoint.evidence_hash != expected_hash:
            checkpoint.resumable = False
            checkpoint.status = CheckpointStatus.FAILED
            checkpoint.metadata["integrity_error"] = "Checkpoint evidence hash mismatch (tampering or truncation detected)"

        return checkpoint

    def list_checkpoints(self) -> list[ModernizationCheckpoint]:
        """List all available checkpoints ordered by updated_at descending."""
        if not self.checkpoints_dir.exists():
            return []

        checkpoints: list[ModernizationCheckpoint] = []
        for file in self.checkpoints_dir.glob("*.json"):
            try:
                content = file.read_text(encoding="utf-8")
                cp = ModernizationCheckpoint.model_validate_json(content)
                checkpoints.append(cp)
            except Exception:
                continue

        # Sort newest first
        checkpoints.sort(key=lambda c: c.updated_at, reverse=True)
        return checkpoints

    def get_latest_resumable(self) -> ModernizationCheckpoint | None:
        """Return the most recent checkpoint that can be resumed."""
        cps = self.list_checkpoints()
        for cp in cps:
            if cp.resumable and cp.status in (CheckpointStatus.IN_PROGRESS, CheckpointStatus.PAUSED, CheckpointStatus.FAILED):
                return cp
        return None

    def delete(self, execution_id: str) -> bool:
        """Delete a checkpoint file."""
        target_file = self._get_path(execution_id)
        if target_file.exists():
            target_file.unlink()
            return True
        return False
