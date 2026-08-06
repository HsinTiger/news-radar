CREATE TABLE IF NOT EXISTS substack_drafts (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  submission_id TEXT,
  editorial_kind TEXT NOT NULL CHECK(editorial_kind IN ('submission','podcast','company','editorial')),
  source_type TEXT NOT NULL,
  source_title TEXT NOT NULL,
  source_url TEXT,
  remote_draft_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('draft_created','local_written','unknown')),
  written_at TEXT,
  drafted_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_substack_drafts_kind_time
  ON substack_drafts(editorial_kind, drafted_at DESC);
CREATE INDEX IF NOT EXISTS idx_substack_drafts_submission
  ON substack_drafts(submission_id)
  WHERE submission_id IS NOT NULL;
