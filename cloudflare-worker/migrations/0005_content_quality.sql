CREATE TABLE IF NOT EXISTS content_quality_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
  captured_at TEXT NOT NULL,
  window_days INTEGER NOT NULL,
  candidates INTEGER NOT NULL,
  evaluated INTEGER NOT NULL,
  evidence_coverage REAL NOT NULL,
  pass_count INTEGER NOT NULL,
  warn_count INTEGER NOT NULL,
  rewrite_count INTEGER NOT NULL,
  block_count INTEGER NOT NULL,
  publish_ready_count INTEGER NOT NULL,
  top_issue_codes_json TEXT NOT NULL DEFAULT '[]',
  guard_version TEXT NOT NULL,
  UNIQUE(platform, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_quality_platform_captured
  ON content_quality_snapshots(platform, captured_at DESC);
