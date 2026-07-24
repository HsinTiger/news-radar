CREATE TABLE IF NOT EXISTS automation_state (
  id TEXT PRIMARY KEY CHECK(id='runtime'),
  mode TEXT NOT NULL CHECK(mode IN ('paused','recovery','live')),
  submission_processor TEXT NOT NULL CHECK(submission_processor IN ('paused','live')),
  source TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO automation_state(
  id,mode,submission_processor,source,detail,updated_at
) VALUES('runtime','paused','paused','migration','Awaiting canonical runtime sync',CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS recovery_experiments (
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL,
  platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
  experiment_type TEXT NOT NULL CHECK(experiment_type IN ('interest','trust','utility','format')),
  hypothesis TEXT NOT NULL,
  baseline_followers INTEGER,
  baseline_primary_metric TEXT NOT NULL,
  baseline_primary_value REAL,
  baseline_captured_at TEXT NOT NULL,
  content_format TEXT NOT NULL CHECK(content_format IN ('feed','carousel','reel')),
  actual_format TEXT CHECK(actual_format IN ('feed','carousel','reel')),
  actual_format_at TEXT,
  topic TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(draft_id,platform)
);

CREATE INDEX IF NOT EXISTS idx_recovery_platform_created
  ON recovery_experiments(platform,created_at DESC);
