from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_scheduler_has_one_primary_and_backup_hour_per_recovery_slot() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adaptive-scheduler.yml").read_text(
        encoding="utf-8"
    )
    assert 'cron: "17 0,3 * * *"' in workflow
    assert 'cron: "17 10,11 * * *"' in workflow
    assert 'cron: "17 12,13 * * *"' in workflow
    assert 'cron: "12 * * * *"' not in workflow
    assert "adaptive_dispatch.py" in workflow


def test_scheduler_dispatch_remains_policy_output_gated() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adaptive-scheduler.yml").read_text(
        encoding="utf-8"
    )
    assert "steps.policy.outputs.dispatch == 'true' &&" in workflow
    assert "github.event_name == 'schedule' || inputs.setup_only == false" in workflow
    assert 'PLATFORMS: ${{ steps.policy.outputs.platforms }}' in workflow


def test_manual_scheduler_defaults_to_decision_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adaptive-scheduler.yml").read_text(
        encoding="utf-8"
    )

    assert "setup_only:" in workflow
    assert (
        'description: "Decision evidence only; never dispatch the publishing pipeline"'
        in workflow
    )
    assert "default: true" in workflow
