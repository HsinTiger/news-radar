-- 0010 建了 uq_substack_public_post ON substack_drafts(remote_post_id)
--   WHERE remote_post_id IS NOT NULL
-- 用意是「一篇已發佈文章只能有一列」。但 worker 的 cleanString() 寫成
--   const text = typeof value === "string" ? value.trim() : "";
-- 也就是把 null 轉成空字串。空字串不是 NULL，於是每一筆「還沒發佈、沒有
-- post_id」的草稿都帶著 '' 進入這個唯一索引：第一筆進得去，第二筆開始
-- 全部撞牆。整個 batch 是原子的，所以一筆衝突就讓 37 筆全部寫不進去。
--
-- 結果是 Operational Sync 從 2026-08-06（0010 上線那天）起連續失敗上百次，
-- 而 D1 的 batch() 只回一個未捕捉的例外，Worker 再包成籠統的 500，
-- 十天下來沒有留下任何一條指向真因的線索。
--
-- 空字串本來就不是一篇已發佈文章，把它排除在索引之外才符合原意。
UPDATE substack_drafts SET remote_post_id = NULL WHERE remote_post_id = '';
UPDATE substack_drafts SET public_url     = NULL WHERE public_url     = '';
UPDATE substack_drafts SET published_at   = NULL WHERE published_at   = '';

DROP INDEX IF EXISTS uq_substack_public_post;
CREATE UNIQUE INDEX uq_substack_public_post
  ON substack_drafts(remote_post_id)
  WHERE remote_post_id IS NOT NULL AND remote_post_id != '';
