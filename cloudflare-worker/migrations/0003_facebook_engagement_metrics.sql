ALTER TABLE engagement_snapshots ADD COLUMN engaged_users INTEGER NOT NULL DEFAULT 0;
ALTER TABLE engagement_snapshots ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0;
