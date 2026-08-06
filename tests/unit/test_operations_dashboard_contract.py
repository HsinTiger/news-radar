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
