from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_upsert_refreshes_proven_post_format() -> None:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    assert "format=excluded.format,platform_post_id=excluded.platform_post_id" in source
    assert 'const API_VERSION = "2026-07-24.recovery-v7";' in source
