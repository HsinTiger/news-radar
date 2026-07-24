from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_scheduler_has_redundant_off_peak_runs_only_at_approved_utc_hours() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adaptive-scheduler.yml").read_text(
        encoding="utf-8"
    )
    assert 'cron: "7,22,37,47 0,10,12 * * *"' in workflow
    assert 'cron: "12 * * * *"' not in workflow
    assert "adaptive_dispatch.py" in workflow


def test_scheduler_dispatch_remains_policy_output_gated() -> None:
    workflow = (ROOT / ".github" / "workflows" / "adaptive-scheduler.yml").read_text(
        encoding="utf-8"
    )
    assert "if: steps.policy.outputs.dispatch == 'true'" in workflow
    assert 'PLATFORMS: ${{ steps.policy.outputs.platforms }}' in workflow
