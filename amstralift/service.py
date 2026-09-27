"""End-to-end upgrade orchestration service.

Coordinates source preparation, Stage A sandbox execution, cryptographic signing,
and Stage B publishing while enforcing strict credential separation across all four adapters.
"""

import os
import shutil
import tempfile
from pathlib import Path

from amstralift.adapters.angular import AngularAdapter
from amstralift.adapters.base import BaseAdapter
from amstralift.adapters.dotnet import DotNetAdapter
from amstralift.adapters.python import PythonAdapter
from amstralift.adapters.react import ReactAdapter
from amstralift.core.crypto import sign_bundle
from amstralift.core.models import PullRequestProposal, SignedAdvisoryBundle
from amstralift.core.workspace import prepare_stage_a_workspace
from amstralift.execution.stage_a import run_stage_a
from amstralift.execution.stage_b import run_stage_b
from amstralift.governance.angular_lts import AngularLTSConfig
from amstralift.governance.python_runtime import PythonRuntimeConfig


class OrchestrationError(Exception):
    """Raised when the upgrade orchestration encounters an unrecoverable error."""

    pass


class UpgradeOrchestrator:
    """Coordinates the two-stage execution pipeline across ecosystem adapters."""

    def __init__(
        self,
        secret_key: bytes | None = None,
        angular_lts_config: AngularLTSConfig | None = None,
        python_runtime_config: PythonRuntimeConfig | None = None,
    ):
        # The secret HMAC key is held strictly by the orchestrator and Stage B
        self.secret_key = secret_key or os.urandom(32)
        self.adapters: dict[str, BaseAdapter] = {
            "angular": AngularAdapter(lts_config=angular_lts_config),
            "python": PythonAdapter(runtime_config=python_runtime_config),
            "dotnet": DotNetAdapter(),
            "react": ReactAdapter(),
        }

    def get_adapter(self, name: str) -> BaseAdapter:
        if name not in self.adapters:
            raise OrchestrationError(
                f"Unsupported ecosystem adapter: '{name}'. Supported: {list(self.adapters.keys())}"
            )
        return self.adapters[name]

    def auto_detect_ecosystem(self, repo_path: Path) -> str:
        """Automatically identify the repository ecosystem."""
        for name, adapter in self.adapters.items():
            if adapter.detect(repo_path):
                return name
        raise OrchestrationError(f"Unable to auto-detect a supported ecosystem for repository at {repo_path}")

    def run_upgrade(
        self,
        repo_path: Path,
        ecosystem: str | None = None,
        target_branch: str = "main",
        dry_run: bool = False,
    ) -> tuple[SignedAdvisoryBundle, PullRequestProposal]:
        """Execute complete upgrade workflow for a repository."""
        repo_path = repo_path.resolve()

        ecosystem_name = ecosystem or self.auto_detect_ecosystem(repo_path)
        adapter = self.get_adapter(ecosystem_name)

        if not adapter.detect(repo_path):
            raise OrchestrationError(
                f"Repository at {repo_path} is not recognized as a valid {ecosystem_name} project."
            )

        # Create temporary, isolated workspace for Stage A
        sandbox_dir = Path(tempfile.mkdtemp(prefix="amstralift_stage_a_"))

        try:
            # 1. Trusted Bootstrap: Prepare and scrub workspace
            prepare_stage_a_workspace(
                source_repo_path=repo_path,
                target_workspace_path=sandbox_dir,
            )

            # 2. Stage A Execution (Untrusted sandbox: no credentials, no HMAC key)
            unsigned_bundle = run_stage_a(
                workspace_path=sandbox_dir,
                adapter=adapter,
                repo_url=str(repo_path),
                target_branch=target_branch,
            )

            # 3. Trusted Host: Sign the bundle with HMAC-SHA256
            signed_bundle = sign_bundle(
                bundle=unsigned_bundle,
                secret_key=self.secret_key,
                ttl_seconds=7200,
            )

            # 4. Stage B Execution (Trusted publisher: verifies signature and publishes PR)
            pr_proposal = run_stage_b(
                signed_bundle=signed_bundle,
                target_repo_path=repo_path,
                secret_key=self.secret_key,
                dry_run=dry_run,
            )

            return signed_bundle, pr_proposal

        finally:
            # Always destroy the untrusted sandbox workspace
            shutil.rmtree(sandbox_dir, ignore_errors=True)
