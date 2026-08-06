ALTER TABLE submissions ADD COLUMN external_post_id TEXT;
ALTER TABLE submissions ADD COLUMN result_url TEXT;
ALTER TABLE submissions ADD COLUMN published_at TEXT;

ALTER TABLE substack_drafts RENAME TO substack_drafts_v1;

CREATE TABLE substack_drafts (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  submission_id TEXT,
  editorial_kind TEXT NOT NULL CHECK(editorial_kind IN ('submission','podcast','company','editorial')),
  source_type TEXT NOT NULL,
  source_title TEXT NOT NULL,
  source_url TEXT,
  remote_draft_id TEXT,
  remote_post_id TEXT,
  public_url TEXT,
  status TEXT NOT NULL CHECK(status IN ('published','partial','draft_created','local_written','unknown')),
  written_at TEXT,
  drafted_at TEXT,
  published_at TEXT,
  updated_at TEXT NOT NULL
);

INSERT INTO substack_drafts(
  id,source_id,submission_id,editorial_kind,source_type,source_title,
  source_url,remote_draft_id,status,written_at,drafted_at,updated_at
)
SELECT id,source_id,submission_id,editorial_kind,source_type,source_title,
       source_url,remote_draft_id,status,written_at,drafted_at,updated_at
  FROM substack_drafts_v1;

DROP TABLE substack_drafts_v1;

CREATE INDEX idx_substack_drafts_kind_time
  ON substack_drafts(editorial_kind, COALESCE(published_at,drafted_at) DESC);
CREATE INDEX idx_substack_drafts_submission
  ON substack_drafts(submission_id)
  WHERE submission_id IS NOT NULL;
CREATE UNIQUE INDEX uq_substack_public_post
  ON substack_drafts(remote_post_id)
  WHERE remote_post_id IS NOT NULL;
