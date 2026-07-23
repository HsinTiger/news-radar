PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS audience_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
  captured_at TEXT NOT NULL,
  followers INTEGER,
  followers_delta_7d INTEGER,
  source TEXT NOT NULL DEFAULT 'platform_api',
  metric_status TEXT NOT NULL DEFAULT 'unknown',
  raw_summary_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(platform, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_audience_platform_captured
  ON audience_snapshots(platform, captured_at DESC);

CREATE TABLE IF NOT EXISTS data_health_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads','system')),
  metric TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('healthy','degraded','unknown','error')),
  detail TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL,
  UNIQUE(platform, metric, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_data_health_platform_captured
  ON data_health_snapshots(platform, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_platform_posts_posted_at
  ON platform_posts(platform, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_last_used
  ON knowledge_items(last_used_at DESC);
