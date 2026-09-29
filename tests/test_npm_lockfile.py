"""Unit tests for npm package-lock.json updater."""

import json
from pathlib import Path

from amstralift.adapters.npm_lockfile import update_npm_lockfile
from amstralift.core.models import DependencyChange, DependencyTier


def test_update_npm_lockfile_no_lockfile(tmp_path: Path):
    result = update_npm_lockfile(tmp_path, [])
    assert result is False


def test_update_npm_lockfile_deterministic_edit_and_strip_integrity(tmp_path: Path):
    lock_file = tmp_path / "package-lock.json"
    initial_data = {
        "name": "my-app",
        "lockfileVersion": 2,
        "packages": {
            "": {
                "dependencies": {
                    "@angular/core": "^18.0.0",
                },
                "devDependencies": {
                    "typescript": "^5.4.0",
                },
            },
            "node_modules/@angular/core": {
                "version": "18.0.0",
                "integrity": "sha512-oldhash123",
                "resolved": "https://registry.npmjs.org/@angular/core/-/core-18.0.0.tgz",
            },
            "node_modules/typescript": {
                "version": "5.4.0",
                "integrity": "sha512-oldts",
            },
        },
        "dependencies": {
            "@angular/core": {
                "version": "18.0.0",
                "integrity": "sha512-oldhash123",
            },
            "typescript": {
                "version": "5.4.0",
            },
        },
    }
    lock_file.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

    changes = [
        DependencyChange(
            package_name="@angular/core",
            from_version="^18.0.0",
            to_version="^19.2.25",
            change_type="direct",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="typescript",
            from_version="^5.4.0",
            to_version="^5.6.3",
            change_type="dev",
            tier=DependencyTier.TIER_1_SAFE,
        ),
    ]

    res = update_npm_lockfile(tmp_path, changes)
    assert res is True

    updated = json.loads(lock_file.read_text(encoding="utf-8"))
    # Verify versions are updated exactly
    assert updated["packages"]["node_modules/@angular/core"]["version"] == "19.2.25"
    assert updated["packages"]["node_modules/typescript"]["version"] == "5.6.3"
    assert updated["packages"][""]["dependencies"]["@angular/core"] == "^19.2.25"
    assert updated["packages"][""]["devDependencies"]["typescript"] == "^5.6.3"
    assert updated["dependencies"]["@angular/core"]["version"] == "19.2.25"
    assert updated["dependencies"]["typescript"]["version"] == "5.6.3"

    # Verify stale integrity and resolved fields were stripped to prevent EINTEGRITY errors
    assert "integrity" not in updated["packages"]["node_modules/@angular/core"]
    assert "resolved" not in updated["packages"]["node_modules/@angular/core"]
    assert "integrity" not in updated["dependencies"]["@angular/core"]
