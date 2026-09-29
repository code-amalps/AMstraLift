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


def test_update_npm_lockfile_syncs_companion_devkits_and_prunes_obsolete(tmp_path: Path):
    """Verify companion @angular-devkit packages (core, schematics, architect) are synced

    to match @angular/cli, obsolete packages (build-optimizer, build-webpack) are removed,
    and nested node_modules are pruned.
    """
    lock_file = tmp_path / "package-lock.json"
    initial_data = {
        "name": "angular-app",
        "lockfileVersion": 2,
        "packages": {
            "": {
                "devDependencies": {
                    "@angular/cli": "^12.2.18",
                    "@angular-devkit/build-angular": "^12.2.18",
                }
            },
            "node_modules/@angular/cli": {
                "version": "12.2.18",
                "integrity": "sha512-oldcli",
            },
            "node_modules/@angular-devkit/build-angular": {
                "version": "12.2.18",
                "integrity": "sha512-oldbuild",
            },
            "node_modules/@angular-devkit/core": {
                "version": "12.2.18",
                "integrity": "sha512-oldcore",
                "resolved": "https://registry.npmjs.org/@angular-devkit/core/-/core-12.2.18.tgz",
            },
            "node_modules/@angular-devkit/schematics": {
                "version": "12.2.18",
                "integrity": "sha512-oldschem",
            },
            "node_modules/@angular-devkit/architect": {
                "version": "0.1202.18",
                "integrity": "sha512-oldarch",
            },
            "node_modules/@angular-devkit/build-optimizer": {
                "version": "0.1202.18",
            },
            "node_modules/@angular-devkit/build-angular/node_modules/@types/estree": {
                "version": "0.0.50",
            },
        },
        "dependencies": {
            "@angular/cli": {
                "version": "12.2.18",
            },
            "@angular-devkit/build-angular": {
                "version": "12.2.18",
            },
            "@angular-devkit/core": {
                "version": "12.2.18",
                "integrity": "sha512-oldcore",
            },
            "@angular-devkit/build-optimizer": {
                "version": "0.1202.18",
            },
        },
    }
    lock_file.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

    changes = [
        DependencyChange(
            package_name="@angular/cli",
            from_version="^12.2.18",
            to_version="^19.2.25",
            change_type="dev",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
        DependencyChange(
            package_name="@angular-devkit/build-angular",
            from_version="^12.2.18",
            to_version="^19.2.25",
            change_type="dev",
            tier=DependencyTier.TIER_2_VERIFY_BEHAVIOR,
        ),
    ]

    res = update_npm_lockfile(tmp_path, changes)
    assert res is True

    updated = json.loads(lock_file.read_text(encoding="utf-8"))
    packages = updated["packages"]
    deps = updated["dependencies"]

    # 1. Companion packages updated to match CLI target version (19.2.25)
    assert packages["node_modules/@angular-devkit/core"]["version"] == "19.2.25"
    assert "integrity" not in packages["node_modules/@angular-devkit/core"]
    assert "resolved" not in packages["node_modules/@angular-devkit/core"]

    assert packages["node_modules/@angular-devkit/schematics"]["version"] == "19.2.25"
    assert packages["node_modules/@angular-devkit/architect"]["version"] == "19.2.25"

    assert deps["@angular-devkit/core"]["version"] == "19.2.25"
    assert "integrity" not in deps["@angular-devkit/core"]

    # 2. Obsolete devkit packages removed
    assert "node_modules/@angular-devkit/build-optimizer" not in packages
    assert "@angular-devkit/build-optimizer" not in deps

    # 3. Nested legacy dependencies removed
    assert "node_modules/@angular-devkit/build-angular/node_modules/@types/estree" not in packages

