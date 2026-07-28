from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_health_exposes_truthful_meta_readiness() -> None:
    worker = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")

    assert "automation: runtime" in worker
    assert "async function currentRuntime(env)" in worker
    assert "meta_publish_now_enabled: metaPublishNowEnabled" in worker
    assert (
        'meta_publish_now_ready: metaPublishNowEnabled && submissionProcessor === "live"'
        in worker
    )
    assert '"processor_unavailable"' in worker
    assert '"editorial_title_required"' in worker
    assert '"source_too_short"' in worker


def test_submit_ui_uses_runtime_instead_of_a_permanent_pause() -> None:
    page = (ROOT / "substack-submit" / "index.html").read_text(encoding="utf-8")

    assert "async function loadRuntime()" in page
    assert "publishNowInput.disabled = !metaRuntime.ready" in page
    assert "automation.meta_publish_now_ready === true" in page
    assert "目前正式發布仍為 paused" not in page
    assert "品質檢查後立即發布到 Meta" in page


def test_substack_ui_defaults_to_the_remote_proven_fast_lane() -> None:
    page = (ROOT / "substack-submit" / "index.html").read_text(encoding="utf-8")

    assert 'name="substack-mode" value="draft_priority" checked' in page
    assert 'name="substack-mode" value="draft" checked' not in page
    assert "已有遠端實證的快速通道" in page


def test_dashboard_classifies_scheduler_delivery_against_expected_ticks() -> None:
    source = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

    assert 'scheduler_delivery: "GitHub 排程送達"' in source
    assert 'scheduler_watchdog_dispatch: "Cloudflare watchdog 派送"' in source
    assert 'scheduler_watchdog_delivery: "Cloudflare watchdog 到達 Actions"' in source
    assert "const SCHEDULER_HOURS_UTC = [0, 3, 10, 11, 12, 13]" in source
    assert "scheduler_delivery: { minute:17, toleranceMs:4 * 60 * 60 * 1000 }" in source
    assert "function ensureExpectedSchedulerHealth(rows,nowMs=Date.now())" in source
    assert 'detail:"heartbeat_not_persisted"' in source
    assert 'waiting ? "unknown" : "degraded"' in source


def test_cloudflare_watchdog_dispatches_only_the_governed_scheduler() -> None:
    worker = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    config = (ROOT / "cloudflare-worker" / "wrangler.toml").read_text(
        encoding="utf-8"
    )

    assert 'crons = ["27 0,3,10,11,12,13 * * *"]' in config
    assert "async scheduled(controller, env, ctx)" in worker
    assert 'const WATCHDOG_WORKFLOW = "adaptive-scheduler.yml"' in worker
    assert 'setup_only: "false"' in worker
    assert 'trigger_source: "cloudflare_watchdog"' in worker
    assert "watchdog_dispatch_id: dispatchId" in worker
    assert '"scheduler_watchdog_dispatch"' in worker
    assert "env.GITHUB_ACTIONS_TOKEN" in worker
    assert "function reconcileWatchdogLineage(rows)" in dashboard
    assert "dispatch_lineage_mismatch" in dashboard


def test_publish_now_setup_job_has_no_canonical_or_meta_write_authority() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish_now.yml").read_text(
        encoding="utf-8"
    )
    setup_job = workflow.split("  setup-canary:\n", 1)[1].split(
        "  publish-now:\n", 1
    )[0]

    assert "contents: read" in setup_job
    assert "--setup-only" in setup_job
    assert "actions/upload-artifact@v6" in setup_job
    assert "state_store.py pull" not in setup_job
    assert "state_store.py push" not in setup_job
    assert "FB_PAGE_ACCESS_TOKEN" not in setup_job
    assert "IG_ACCESS_TOKEN" not in setup_job
    assert "THREADS_ACCESS_TOKEN" not in setup_job
    assert "exact_copy_json:" in workflow
    assert '--exact-copy-json "$INPUT_EXACT_COPY_JSON"' in workflow


def test_deploy_config_arms_meta_only_with_live_processor() -> None:
    config = (ROOT / "cloudflare-worker" / "wrangler.toml").read_text(
        encoding="utf-8"
    )

    assert 'AUTOMATION_MODE = "recovery"' in config
    assert 'SUBMISSION_PROCESSOR_MODE = "live"' in config
    assert 'ENABLE_META_PUBLISH_NOW = "true"' in config
