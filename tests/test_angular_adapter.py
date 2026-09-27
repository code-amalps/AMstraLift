"""Tests for Angular ecosystem adapter."""

import json
from pathlib import Path

from amstralift.adapters.angular import AngularAdapter, classify_angular_tier
from amstralift.core.models import DependencyTier


def test_classify_angular_tier():
    # Tier 3 (Critical / Auth / Security / Payments)
    assert classify_angular_tier("@angular/fire/auth") == DependencyTier.TIER_3_CRITICAL
    assert classify_angular_tier("msal-angular") == DependencyTier.TIER_3_CRITICAL
    assert classify_angular_tier("stripe-angular") == DependencyTier.TIER_3_CRITICAL

    # Tier 2 (State / Routing / Core behavior)
    assert classify_angular_tier("@angular/router") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_angular_tier("@angular/forms") == DependencyTier.TIER_2_VERIFY_BEHAVIOR
    assert classify_angular_tier("@ngrx/store") == DependencyTier.TIER_2_VERIFY_BEHAVIOR

    # Tier 1 (Safe utilities / dev tools)
    assert classify_angular_tier("tslib") == DependencyTier.TIER_1_SAFE
    assert classify_angular_tier("lodash") == DependencyTier.TIER_1_SAFE


def test_angular_adapter_detect(tmp_path: Path):
    adapter = AngularAdapter()

    # Empty repo -> False
    assert not adapter.detect(tmp_path)

    # Repo with package.json but not Angular -> False
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "4.18.0"}}), encoding="utf-8")
    assert not adapter.detect(tmp_path)

    # Repo with @angular/core -> True
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"@angular/core": "^17.0.0"}}), encoding="utf-8")
    assert adapter.detect(tmp_path)


def test_angular_adapter_apply_upgrade(tmp_path: Path):
    adapter = AngularAdapter()
    pkg_path = tmp_path / "package.json"
    lock_path = tmp_path / "package-lock.json"

    pkg_path.write_text(
        json.dumps(
            {
                "dependencies": {"@angular/core": "^17.0.0"},
                "devDependencies": {"typescript": "^5.2.0"},
            }
        ),
        encoding="utf-8",
    )
    lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@angular/core": {"version": "17.0.0"},
                }
            }
        ),
        encoding="utf-8",
    )

    from amstralift.core.models import DependencyChange

    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="^17.0.0",
            to_version="^18.1.0",
            change_type="direct",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        )
    ]

    adapter.apply_upgrade(tmp_path, changes)

    updated_pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert updated_pkg["dependencies"]["@angular/core"] == "^18.1.0"

    updated_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert updated_lock["packages"]["node_modules/@angular/core"]["version"] == "18.1.0"
