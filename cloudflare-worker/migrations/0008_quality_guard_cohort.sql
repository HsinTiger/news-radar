ALTER TABLE content_quality_snapshots
  ADD COLUMN legacy_excluded_count INTEGER NOT NULL DEFAULT 0;
