"""Unit tests for exact resolved dependency versions snapshot and --ignore-scripts."""

import json
from pathlib import Path
from amstralift.core.crypto import sign_bundle, verify_bundle
from amstralift.core.models import UnsignedAdvisoryBundle
from amstralift.core.resolved_versions import (
    capture_resolved_versions,
    extract_dotnet_resolved_versions,
    extract_npm_resolved_versions,
    extract_python_resolved_versions,
)


def test_extract_npm_resolved_versions_v3(tmp_path: Path):
    lockfile = tmp_path / "package-lock.json"
    data = {
        "name": "sample-app",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "sample-app", "version": "1.0.0"},
            "node_modules/@angular/core": {"version": "18.2.13"},
            "node_modules/@angular/common": {"version": "18.2.13"},
            "node_modules/rxjs": {"version": "7.8.1"},
            "node_modules/tslib": {"version": "2.6.3"},
        },
    }
    lockfile.write_text(json.dumps(data), encoding="utf-8")

    resolved = extract_npm_resolved_versions(lockfile)
    assert resolved == {
        "@angular/common": "18.2.13",
        "@angular/core": "18.2.13",
        "rxjs": "7.8.1",
        "tslib": "2.6.3",
    }


def test_extract_npm_resolved_versions_v1(tmp_path: Path):
    lockfile = tmp_path / "package-lock.json"
    data = {
        "name": "legacy-app",
        "lockfileVersion": 1,
        "dependencies": {
            "@angular/core": {"version": "14.2.0"},
            "rxjs": {
                "version": "6.6.7",
                "dependencies": {
                    "tslib": {"version": "1.14.1"},
                },
            },
        },
    }
    lockfile.write_text(json.dumps(data), encoding="utf-8")

    resolved = extract_npm_resolved_versions(lockfile)
    assert resolved == {
        "@angular/core": "14.2.0",
        "rxjs": "6.6.7",
        "tslib": "1.14.1",
    }


def test_extract_dotnet_resolved_versions(tmp_path: Path):
    lockfile = tmp_path / "packages.lock.json"
    data = {
        "version": 1,
        "dependencies": {
            "net8.0": {
                "Microsoft.AspNetCore.OpenApi": {
                    "type": "Direct",
                    "requested": "[8.0.0, )",
                    "resolved": "8.0.8",
                },
                "Newtonsoft.Json": {
                    "type": "Transitive",
                    "resolved": "13.0.3",
                },
            }
        },
    }
    lockfile.write_text(json.dumps(data), encoding="utf-8")

    resolved = extract_dotnet_resolved_versions(tmp_path)
    assert resolved == {
        "Microsoft.AspNetCore.OpenApi": "8.0.8",
        "Newtonsoft.Json": "13.0.3",
    }


def test_extract_python_resolved_versions_requirements(tmp_path: Path):
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(
        "fastapi==0.111.0\nuvicorn==0.30.1\npydantic==2.8.2\n# comment\npytest>=8.0.0\n",
        encoding="utf-8",
    )

    resolved = extract_python_resolved_versions(tmp_path)
    assert resolved == {
        "fastapi": "0.111.0",
        "pydantic": "2.8.2",
        "uvicorn": "0.30.1",
    }


def test_capture_resolved_versions_dispatches_correctly(tmp_path: Path):
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text(
        json.dumps({
            "lockfileVersion": 3,
            "packages": {
                "node_modules/react": {"version": "18.3.1"},
            },
        }),
        encoding="utf-8",
    )

    resolved = capture_resolved_versions(tmp_path, "react")
    assert resolved == {"react": "18.3.1"}


def test_signed_bundle_attests_resolved_versions():
    from amstralift.core.crypto import compute_sha256

    patch_content = "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n"
    bundle = UnsignedAdvisoryBundle(
        repo_url="https://github.com/example/repo",
        base_commit_sha="0000000000000000000000000000000000000000",
        head_commit_sha="1111111111111111111111111111111111111111",
        patch=patch_content,
        patch_sha256=compute_sha256(patch_content),
        resolved_versions={"@angular/core": "18.2.13", "rxjs": "7.8.1"},
    )

    key = b"super-secret-hmac-key-for-attestation"
    signed = sign_bundle(bundle, key)

    assert signed.bundle.resolved_versions == {"@angular/core": "18.2.13", "rxjs": "7.8.1"}
    verify_bundle(signed, key)
