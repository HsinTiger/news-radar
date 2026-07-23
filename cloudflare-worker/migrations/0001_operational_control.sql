PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  target TEXT NOT NULL CHECK(target IN ('meta','substack')),
  source_type TEXT NOT NULL CHECK(source_type IN ('url','text','youtube')),
  content TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  platforms_json TEXT NOT NULL DEFAULT '[]',
  requested_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  claimed_at TEXT,
  lease_until TEXT,
  workflow_run_url TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_status_created
  ON submissions(status, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  subject_id TEXT,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_created
  ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor_action_created
  ON audit_events(actor, action, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_posts (
  id TEXT PRIMARY KEY,
  draft_id TEXT,
  submission_id TEXT,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
  format TEXT NOT NULL CHECK(format IN ('feed','carousel','reel')),
  platform_post_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('planned','published','failed','deleted','unknown')),
  title TEXT,
  topic TEXT,
  source_url TEXT,
  posted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(submission_id) REFERENCES submissions(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_post_id
  ON platform_posts(platform, platform_post_id)
  WHERE platform_post_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_platform_format_published
  ON platform_posts(draft_id, platform, format)
  WHERE status='published' AND draft_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS engagement_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
  platform_post_id TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  post_age_hours INTEGER,
  views INTEGER NOT NULL DEFAULT 0,
  reach INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0,
  saves INTEGER NOT NULL DEFAULT 0,
  replies INTEGER NOT NULL DEFAULT 0,
  reposts INTEGER NOT NULL DEFAULT 0,
  quotes INTEGER NOT NULL DEFAULT 0,
  metric_status TEXT NOT NULL DEFAULT 'ok',
  raw_summary_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(platform, platform_post_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_engagement_platform_captured
  ON engagement_snapshots(platform, captured_at DESC);

CREATE TABLE IF NOT EXISTS learning_proposals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected','applied','superseded')),
  summary TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  proposed_change_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_status_created
  ON learning_proposals(status, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_items (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_url TEXT,
  title TEXT NOT NULL,
  topic TEXT,
  evidence_summary TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  first_seen_at TEXT NOT NULL,
  last_used_at TEXT,
  use_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_knowledge_topic_used
  ON knowledge_items(topic, last_used_at DESC);
