import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_legacy_substack_submit_route_is_same_origin_compatibility_entry() -> None:
    html = (REPO / "substack-submit" / "index.html").read_text(encoding="utf-8")
    assert "../dashboard/?view=submit" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "removeItem" not in html


def test_unified_dashboard_exposes_owner_workflow_views() -> None:
    html = (REPO / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (REPO / "dashboard" / "app.js").read_text(encoding="utf-8")
    assert 'type="module"' in html
    for view in ("overview", "substack", "meta", "health", "submit"):
        assert f'data-view="{view}"' in html
    assert "ops-core.mjs" in app
    assert "clearToken()" not in app
    assert "response.status === 401" in app


def test_dashboard_versions_runtime_modules_to_avoid_stale_publish_capabilities() -> None:
    html = (REPO / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (REPO / "dashboard" / "app.js").read_text(encoding="utf-8")

    script_version = re.search(r'src="app\.js\?v=([^"]+)"', html)
    core_version = re.search(r'from "\./ops-core\.mjs\?v=([^"]+)"', app)

    assert script_version, "dashboard entry script must be cache-versioned"
    assert core_version, "dashboard core module must be cache-versioned"
    assert script_version.group(1) == core_version.group(1)


def test_public_runtime_refreshes_submission_capabilities() -> None:
    app = (REPO / "dashboard" / "app.js").read_text(encoding="utf-8")
    load_public = app.split("async function loadPublicData()", 1)[1].split(
        "async function loadPrivateData", 1
    )[0]

    assert "renderSubmissionMode();" in load_public


def test_dashboard_explains_scheduled_and_one_time_submission_routes() -> None:
    html = (REPO / "dashboard" / "index.html").read_text(encoding="utf-8")
    app = (REPO / "dashboard" / "app.js").read_text(encoding="utf-8")

    assert 'id="editorial-plan"' in html
    assert "最近 7 天的 Podcast" in html
    assert "怎麼賺錢、優勢能否維持、財務是否支持" in html
    assert "一次性投稿不分早上、下午" in html
    assert "morning / evening" in html
    assert "不是投稿時段" in html
    assert "立即進優先處理，不等 12:00 排程" in html
    assert "Substack 與 Meta 都只有在你明確選擇「立即發布」時才會公開" in html
    assert "立即發布到 Substack" in app
    assert "公開 URL 與 post ID" in app


def test_worker_contract_carries_substack_metadata_and_editorial_contract() -> None:
    worker = (REPO / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    migration = (
        REPO / "cloudflare-worker" / "migrations" / "0009_substack_operations.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS substack_drafts" in migration
    assert 'substack_drafts: listField(body, "substack_drafts")' in worker
    assert "recent_substack_drafts" in worker
    assert "editorial_contract" in worker
    assert "body_markdown" not in migration
    assert "content" not in migration
