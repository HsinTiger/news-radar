-- One logical (draft, platform, format) must occupy one dashboard row.
-- Historic failure rows used a draft-derived id while later successes used a
-- post-id-derived id, leaving both states visible.  Remove only failures that
-- have explicit published evidence, then move all draft-backed rows to the
-- deterministic identity emitted by sync_social_ops.py.

DELETE FROM platform_posts AS failed
WHERE failed.status = 'failed'
  AND failed.draft_id IS NOT NULL
  AND EXISTS (
    SELECT 1
      FROM platform_posts AS published
     WHERE published.draft_id = failed.draft_id
       AND published.platform = failed.platform
       AND published.format = failed.format
       AND published.status = 'published'
  );

UPDATE platform_posts
   SET id = 'post_' || draft_id || '_' || platform || '_' || format
 WHERE draft_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_platform_format
  ON platform_posts(draft_id, platform, format)
  WHERE draft_id IS NOT NULL;
