"""Cryptographic signing, hashing, and bundle verification.

The HMAC signing key is held ONLY by the trusted orchestrator/host
and is never exposed to the untrusted Stage A sandbox worker.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

from amstralift.core.models import SignedAdvisoryBundle, UnsignedAdvisoryBundle


class BundleVerificationError(Exception):
    """Raised when bundle integrity, authenticity, or freshness fails."""

    pass


def compute_sha256(data: str | bytes) -> str:
    """Compute SHA-256 hex digest of string or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _canonical_payload(bundle: UnsignedAdvisoryBundle, expires_at: datetime) -> bytes:
    """Deterministic byte representation of bundle metadata for signing."""
    payload = {
        "bundle_id": bundle.bundle_id,
        "run_id": bundle.run_id,
        "repo_url": bundle.repo_url,
        "target_branch": bundle.target_branch,
        "base_commit_sha": bundle.base_commit_sha,
        "head_commit_sha": bundle.head_commit_sha,
        "patch_sha256": bundle.patch_sha256,
        "lockfile_hashes_after": bundle.lockfile_hashes_after,
        "highest_tier": bundle.highest_tier.value,
        "expires_at": expires_at.isoformat(),
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def sign_bundle(
    bundle: UnsignedAdvisoryBundle,
    secret_key: bytes,
    ttl_seconds: int = 7200,
) -> SignedAdvisoryBundle:
    """Sign an advisory bundle using HMAC-SHA256.

    MUST be called only by the trusted orchestrator.
    """
    if not secret_key:
        raise ValueError("Secret key must not be empty")

    # Verify that patch_sha256 matches actual patch payload before signing
    expected_patch_hash = compute_sha256(bundle.patch)
    if bundle.patch_sha256 != expected_patch_hash:
        raise BundleVerificationError(
            f"Patch hash mismatch before signing: declared {bundle.patch_sha256} vs actual {expected_patch_hash}"
        )

    now = datetime.now(timezone.utc)
    from datetime import timedelta

    expires_at = now + timedelta(seconds=ttl_seconds)

    payload_bytes = _canonical_payload(bundle, expires_at)
    sig = hmac.new(secret_key, payload_bytes, hashlib.sha256).hexdigest()

    return SignedAdvisoryBundle(
        bundle=bundle,
        issued_at=now,
        expires_at=expires_at,
        signature=f"hmac-sha256:{sig}",
    )


def verify_bundle(
    signed_bundle: SignedAdvisoryBundle,
    secret_key: bytes,
) -> None:
    """Verify bundle signature, expiry, and internal patch consistency.

    Executed in Stage B before any publishing action.
    """
    if signed_bundle.is_expired():
        raise BundleVerificationError(f"Bundle {signed_bundle.bundle.bundle_id} expired at {signed_bundle.expires_at}")

    # Verify signature format
    if not signed_bundle.signature.startswith("hmac-sha256:"):
        raise BundleVerificationError("Unsupported or malformed signature algorithm")

    declared_sig = signed_bundle.signature.split(":", 1)[1]
    payload_bytes = _canonical_payload(signed_bundle.bundle, signed_bundle.expires_at)
    expected_sig = hmac.new(secret_key, payload_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(declared_sig, expected_sig):
        raise BundleVerificationError("Invalid bundle signature: cryptographic check failed")

    # Verify patch content matches patch_sha256
    actual_patch_hash = compute_sha256(signed_bundle.bundle.patch)
    if signed_bundle.bundle.patch_sha256 != actual_patch_hash:
        raise BundleVerificationError(
            f"Bundle patch tampered: declared {signed_bundle.bundle.patch_sha256} vs actual {actual_patch_hash}"
        )
