-- 2026-11-13：投稿頁可以上傳截圖（主編有時沒辦法複製文字）。圖存在 D1，
-- Mac 端寫稿前用 owner token 取回、在本機做文字辨識。
CREATE TABLE IF NOT EXISTS submission_images (
  submission_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  mime TEXT NOT NULL,
  data_b64 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (submission_id, idx),
  FOREIGN KEY(submission_id) REFERENCES submissions(id)
);
