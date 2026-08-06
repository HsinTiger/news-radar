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


def test_direct_publish_sync_does_not_invent_submission_foreign_keys() -> None:
    worker = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")

    assert "(SELECT id FROM submissions WHERE id=?)" in worker


def test_submit_ui_uses_runtime_instead_of_a_permanent_pause() -> None:
    page = (ROOT / "substack-submit" / "index.html").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

    assert '../dashboard/?view=submit' in page
    assert 'name="meta-mode" value="publish_now"' in dashboard
    assert "meta_publish_now_ready === true" in app
    assert "publishInput.disabled = !ready" in app
    assert "目前正式發布仍為 paused" not in page
    assert "立即發布到 Meta" in app


def test_substack_ui_defaults_to_the_remote_proven_fast_lane() -> None:
    page = (ROOT / "substack-submit" / "index.html").read_text(encoding="utf-8")
    dashboard = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    core = (ROOT / "dashboard" / "ops-core.mjs").read_text(encoding="utf-8")

    assert '../dashboard/?view=submit' in page
    assert 'name="target" value="substack" checked' in dashboard
    assert 'name="substack-mode" value="draft_priority" checked' in dashboard
    assert 'name="substack-mode" value="publish_now"' in dashboard
    assert 'target === "substack" ? substackMode : metaMode' in core
    assert "預設建立草稿" in dashboard
    assert "完成寫稿、封面與品質閘門後公開發布" in dashboard


def test_substack_publish_now_is_capability_gated_and_requires_public_evidence() -> None:
    worker = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
    migration = (
        ROOT / "cloudflare-worker" / "migrations" / "0010_substack_publish_now.sql"
    ).read_text(encoding="utf-8")
    local_db = (ROOT / "src" / "db.py").read_text(encoding="utf-8")

    assert "substack_publish_now_enabled" in worker
    assert "substack_publish_now_ready" in worker
    assert "Substack publish-now requires public post evidence" in worker
    assert "Substack published sync requires public post evidence" in worker
    assert "external_post_id" in worker
    assert "result_url" in worker
    assert "substack_publish_now_ready === true" in app
    assert "ADD COLUMN external_post_id" in migration
    assert "substack_post_url" in local_db


def test_dashboard_classifies_scheduler_delivery_against_expected_ticks() -> None:
    source = (ROOT / "dashboard" / "ops-core.mjs").read_text(encoding="utf-8")
    app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")

    assert "SCHEDULER_HOURS_UTC = [0, 3, 10, 11, 12, 13]" in source
    assert "scheduler_delivery: Object.freeze({minute: 17" in source
    assert "function ensureExpectedSchedulerHealth(" in source
    assert 'detail: "heartbeat_not_persisted"' in source
    assert 'waiting ? "unknown" : "degraded"' in source
    assert "ensureExpectedSchedulerHealth(state.dashboard.data_health" in app


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
    core = (ROOT / "dashboard" / "ops-core.mjs").read_text(encoding="utf-8")
    assert "function reconcileWatchdogLineage(rows)" in core
    assert "dispatch_lineage_mismatch" in core


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
    assert "report_submission_state:" in workflow
    assert (
        "if: always() && inputs.report_submission_state && inputs.submission_id != ''"
        in workflow
    )


def test_deploy_config_arms_meta_only_with_live_processor() -> None:
    config = (ROOT / "cloudflare-worker" / "wrangler.toml").read_text(
        encoding="utf-8"
    )

    assert 'AUTOMATION_MODE = "recovery"' in config
    assert 'SUBMISSION_PROCESSOR_MODE = "live"' in config
    assert 'ENABLE_META_PUBLISH_NOW = "true"' in config


def test_meta_carousel_readback_is_separate_from_publish_and_fail_closed() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "meta-carousel-readback.yml"
    ).read_text(encoding="utf-8")

    assert "scripts/verify_meta_carousel.py" in workflow
    assert "--record-canonical" in workflow
    assert "scripts/state_store.py push" in workflow
    assert "scripts/sync_social_ops.py" in workflow
    assert "scripts/publish_now.py" not in workflow
    assert "run_publish_queue" not in workflow
    assert "actions/upload-artifact@v6" in workflow
