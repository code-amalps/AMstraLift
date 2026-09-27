"""Tests for cryptographic signing, hashing, and bundle verification."""

import pytest

from amstralift.core.crypto import (
    BundleVerificationError,
    compute_sha256,
    sign_bundle,
    verify_bundle,
)
from amstralift.core.models import (
    DependencyChange,
    DependencyTier,
    GateResult,
    GateStatus,
    GateSummary,
    MigrationClassification,
    UnsignedAdvisoryBundle,
)


@pytest.fixture
def sample_unsigned_bundle() -> UnsignedAdvisoryBundle:
    patch = "diff --git a/package.json b/package.json\n--- a/package.json\n+++ b/package.json\n"
    patch_hash = compute_sha256(patch)
    return UnsignedAdvisoryBundle(
        repo_url="https://github.com/my-org/angular-app.git",
        target_branch="main",
        base_commit_sha="a" * 40,
        head_commit_sha="b" * 40,
        patch=patch,
        patch_sha256=patch_hash,
        lockfile_hashes_before={"package-lock.json": "1" * 64},
        lockfile_hashes_after={"package-lock.json": "2" * 64},
        changes=[
            DependencyChange(
                package_name="@angular/core",
                from_version="^17.0.0",
                to_version="^18.0.0",
                tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
            )
        ],
        gate_summary=GateSummary(
            results=[
                GateResult(
                    name="test",
                    command="npm test",
                    status=GateStatus.REQUIRED_PASSED,
                    exit_code=0,
                )
            ]
        ),
        migration=MigrationClassification(
            touched_files=["package.json"],
            application_source_modified=False,
            manifest_or_lockfile_only=True,
            risk_level="LOW",
        ),
    )


def test_sign_and_verify_bundle_success(sample_unsigned_bundle):
    secret = b"my-super-secret-orchestrator-key-12345"
    signed = sign_bundle(sample_unsigned_bundle, secret_key=secret, ttl_seconds=3600)

    assert signed.signature.startswith("hmac-sha256:")
    assert not signed.is_expired()

    # Must verify cleanly
    verify_bundle(signed, secret_key=secret)


def test_verify_bundle_tampered_patch(sample_unsigned_bundle):
    secret = b"my-super-secret-orchestrator-key-12345"
    signed = sign_bundle(sample_unsigned_bundle, secret_key=secret)

    # Tamper with the patch text in the bundle
    signed.bundle.patch += "\n# Malicious injected change"

    with pytest.raises(BundleVerificationError, match="Bundle patch tampered"):
        verify_bundle(signed, secret_key=secret)


def test_verify_bundle_expired(sample_unsigned_bundle):
    secret = b"my-super-secret-orchestrator-key-12345"
    signed = sign_bundle(sample_unsigned_bundle, secret_key=secret, ttl_seconds=-10)

    assert signed.is_expired()
    with pytest.raises(BundleVerificationError, match="expired"):
        verify_bundle(signed, secret_key=secret)


def test_verify_bundle_wrong_key(sample_unsigned_bundle):
    secret1 = b"trusted-key-1"
    secret2 = b"different-key-2"
    signed = sign_bundle(sample_unsigned_bundle, secret_key=secret1)

    with pytest.raises(BundleVerificationError, match="Invalid bundle signature"):
        verify_bundle(signed, secret_key=secret2)
