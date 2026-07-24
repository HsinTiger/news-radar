from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_setup_canary_script_has_hard_no_publish_contract() -> None:
    source = (ROOT / "scripts/recovery_setup_canary.py").read_text(encoding="utf-8")
    assert "publisher invoked during setup-only canary" in source
    assert '"--compose-only"' in source
    assert "publish_log_unchanged" in source
    assert "canonical_db_unchanged" in source
    assert '"hold_reason": hold_reason' in source
    assert 'os.environ["AUTOMATION_MODE"] = "recovery"' in source
    assert '"automation_mode": os.environ["AUTOMATION_MODE"]' in source
    assert "tempfile.TemporaryDirectory" in source


def test_setup_canary_workflow_is_read_only_and_has_no_meta_secrets() -> None:
    workflow = (
        ROOT / ".github/workflows/recovery-setup-canary.yml"
    ).read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert "models: read" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "state_store.py push" not in workflow
    assert "FB_PAGE_ACCESS_TOKEN" not in workflow
    assert "IG_ACCESS_TOKEN" not in workflow
    assert "THREADS_ACCESS_TOKEN" not in workflow
    assert "scripts/recovery_setup_canary.py" in workflow
