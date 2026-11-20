"""Unit tests for Resumable / Checkpointed Modernization (P19).

Engineered by Amal P S for AMstraLift Generation 2.
"""

import json
from pathlib import Path
from typer.testing import CliRunner

import pytest

from amstralift.checkpoints import (
    CheckpointManager,
    CheckpointStatus,
    CheckpointStore,
    ModernizationCheckpoint,
    PhaseCheckpoint,
)
from amstralift.cli import app
from amstralift.planning import ModernizationPlan, PhaseType, PlannedTransformation, TransformationStatus
from amstralift.planning.executor import PlanExecutor
from amstralift.planning.models import ExecutionPhase


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sample_plan(tmp_path: Path) -> ModernizationPlan:
    phase_0 = ExecutionPhase(
        phase_index=0,
        name="Dependencies",
        phase_type=PhaseType.DEPENDENCIES,
        description="Align dependencies",
        transformations=[
            PlannedTransformation(
                id="test:pkg-align",
                name="Package Alignment",
                status=TransformationStatus.SAFE,
                affected_file_patterns=["package.json"],
            )
        ],
    )
    phase_1 = ExecutionPhase(
        phase_index=1,
        name="Framework Migration",
        phase_type=PhaseType.FRAMEWORK_MIGRATION,
        description="Migrate framework",
        transformations=[
            PlannedTransformation(
                id="test:framework-core",
                name="Framework Core",
                status=TransformationStatus.SAFE,
                affected_file_patterns=["src/**/*"],
            )
        ],
    )
    phase_2 = ExecutionPhase(
        phase_index=2,
        name="Cleanup",
        phase_type=PhaseType.CLEANUP,
        description="Clean dead code",
        transformations=[
            PlannedTransformation(
                id="test:cleanup",
                name="Cleanup Scaffolding",
                status=TransformationStatus.SAFE,
                affected_file_patterns=["*"],
            )
        ],
    )

    return ModernizationPlan(
        repo_path=str(tmp_path),
        ecosystem="angular",
        current_version="17.0.0",
        target_version="v18",
        target_major=18,
        total_transformations=3,
        safe_count=3,
        phases=[phase_0, phase_1, phase_2],
    )


def test_checkpoint_store_save_load(tmp_path: Path, sample_plan: ModernizationPlan):
    store = CheckpointStore(tmp_path)
    cp = ModernizationCheckpoint(
        execution_id="exec_test_001",
        plan_id=sample_plan.plan_id,
        repo_path=str(tmp_path),
        ecosystem="angular",
        target_version="v18",
        base_commit_sha="a" * 40,
        current_commit_sha="a" * 40,
        total_phases=3,
        completed_phases_count=1,
        completed_transformation_ids=["test:pkg-align"],
        phases=[
            PhaseCheckpoint(
                phase_index=0,
                phase_name="Dependencies",
                phase_type=PhaseType.DEPENDENCIES,
                status=CheckpointStatus.COMPLETED,
                completed_transformations=["test:pkg-align"],
            )
        ],
    )

    saved_path = store.save(cp)
    assert saved_path.exists()

    loaded = store.load("exec_test_001")
    assert loaded.execution_id == "exec_test_001"
    assert loaded.completed_phases_count == 1
    assert "test:pkg-align" in loaded.completed_transformation_ids
    assert loaded.resumable is True


def test_checkpoint_store_tamper_detection(tmp_path: Path, sample_plan: ModernizationPlan):
    store = CheckpointStore(tmp_path)
    cp = ModernizationCheckpoint(
        execution_id="exec_tamper_001",
        plan_id=sample_plan.plan_id,
        repo_path=str(tmp_path),
        ecosystem="angular",
        target_version="v18",
        base_commit_sha="b" * 40,
        current_commit_sha="b" * 40,
        total_phases=3,
    )
    store.save(cp)

    # Tamper with the saved file directly
    cp_file = tmp_path / ".amstralift" / "checkpoints" / "exec_tamper_001.json"
    raw_data = json.loads(cp_file.read_text(encoding="utf-8"))
    raw_data["completed_transformation_ids"] = ["injected_rogue_transformation"]
    cp_file.write_text(json.dumps(raw_data), encoding="utf-8")

    # Load should detect hash mismatch
    loaded = store.load("exec_tamper_001")
    assert loaded.resumable is False
    assert loaded.status == CheckpointStatus.FAILED
    assert "integrity_error" in loaded.metadata


def test_checkpoint_manager_lifecycle(tmp_path: Path, sample_plan: ModernizationPlan):
    mgr = CheckpointManager(tmp_path)
    cp = mgr.start_execution(sample_plan, dry_run=True)

    assert cp.status == CheckpointStatus.IN_PROGRESS
    assert cp.completed_phases_count == 0
    assert len(cp.phases) == 3

    # Phase 0 success
    mgr.record_phase_start(cp, 0)
    mgr.record_phase_success(cp, 0, ["test:pkg-align"], duration_seconds=1.2)

    loaded = mgr.store.load(cp.execution_id)
    assert loaded.completed_phases_count == 1
    assert "test:pkg-align" in loaded.completed_transformation_ids

    # Phase 1 failure
    mgr.record_phase_start(cp, 1)
    mgr.record_phase_failure(
        cp,
        1,
        failed_transformation="test:framework-core",
        error_message="Simulation fault injection",
    )

    loaded_fail = mgr.store.load(cp.execution_id)
    assert loaded_fail.status == CheckpointStatus.FAILED
    assert loaded_fail.phases[1].failed_transformation == "test:framework-core"

    # Prepare resumption
    res_cp, res_plan, start_phase = mgr.prepare_resumption(cp.execution_id)
    assert start_phase == 1
    assert len(res_plan.phases) == 2  # Phase 1 & 2 remaining
    assert res_plan.phases[0].phase_index == 1
    assert res_cp.status == CheckpointStatus.IN_PROGRESS


def test_plan_executor_automatic_checkpointing(tmp_path: Path, sample_plan: ModernizationPlan):
    executor = PlanExecutor()
    res = executor.execute_plan(
        plan=sample_plan,
        workspace_path=tmp_path,
        dry_run=True,
        enable_checkpointing=True,
    )

    assert res.success is True
    assert res.execution_id != ""

    store = CheckpointStore(tmp_path)
    cp = store.load(res.execution_id)
    assert cp.status == CheckpointStatus.COMPLETED
    assert cp.completed_phases_count == 3
    assert len(cp.completed_transformation_ids) == 3


def test_cli_checkpoints_command_empty(runner: CliRunner, tmp_path: Path):
    res = runner.invoke(app, ["checkpoints", "--repo", str(tmp_path)])
    assert res.exit_code == 0
    assert "No modernization checkpoints found" in res.stdout


def test_cli_checkpoints_command_with_data(runner: CliRunner, tmp_path: Path, sample_plan: ModernizationPlan):
    mgr = CheckpointManager(tmp_path)
    cp = mgr.start_execution(sample_plan)
    mgr.record_phase_success(cp, 0, ["test:pkg-align"])

    # Table output
    res = runner.invoke(app, ["checkpoints", "--repo", str(tmp_path)])
    assert res.exit_code == 0
    assert "Modernization Execution Checkpoints" in res.stdout
    assert cp.execution_id in res.stdout

    # JSON output
    res_json = runner.invoke(app, ["checkpoints", "--repo", str(tmp_path), "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert len(data) >= 1
    assert data[0]["execution_id"] == cp.execution_id


def test_cli_resume_command(runner: CliRunner, tmp_path: Path, sample_plan: ModernizationPlan):
    mgr = CheckpointManager(tmp_path)
    cp = mgr.start_execution(sample_plan)
    mgr.record_phase_success(cp, 0, ["test:pkg-align"])
    mgr.record_phase_failure(cp, 1, "test:framework-core", "Simulated build error")

    # Resume the failed run in dry-run mode
    res = runner.invoke(app, ["resume", cp.execution_id, "--repo", str(tmp_path), "--dry-run"])
    assert res.exit_code == 0
    assert "Resuming Modernization Execution (P19)" in res.stdout
    assert "Phase 2" in res.stdout
    assert "Modernization successfully resumed" in res.stdout

    # Verify final checkpoint completed
    final_cp = mgr.store.load(cp.execution_id)
    assert final_cp.status == CheckpointStatus.COMPLETED
