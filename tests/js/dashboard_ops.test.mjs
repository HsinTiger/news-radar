import test from "node:test";
import assert from "node:assert/strict";

import {
  TOKEN_KEY,
  DEFAULT_EDITORIAL_CONTRACT,
  buildSubmissionPayload,
  buildTrendSeries,
  deriveAttention,
  effectiveSchedulerHealth,
  ensureExpectedSchedulerHealth,
  platformSnapshot,
  readStoredToken,
  rememberToken,
  summarizeRecentContent,
} from "../../dashboard/ops-core.mjs";


function memoryStore(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}


test("owner token keeps the existing cross-page storage contract", () => {
  assert.equal(TOKEN_KEY, "hsintiger_social_ops_owner_token");
  const local = memoryStore({[TOKEN_KEY]: "remembered"});
  const session = memoryStore({[TOKEN_KEY]: "temporary"});
  assert.equal(readStoredToken({local, session}), "remembered");

  rememberToken("new-token", {remember: false, local, session});
  assert.equal(local.getItem(TOKEN_KEY), null);
  assert.equal(session.getItem(TOKEN_KEY), "new-token");
});


test("editorial fallback matches the tracked owner decisions", () => {
  assert.equal(DEFAULT_EDITORIAL_CONTRACT.publication_mode, "draft_only");
  assert.deepEqual(DEFAULT_EDITORIAL_CONTRACT.podcast, {
    local_time: "12:00",
    drafts: 2,
    candidate_window_days: 7,
    depth: "weekly",
    target_chars: [2800, 4200],
  });
  assert.deepEqual(DEFAULT_EDITORIAL_CONTRACT.company, {
    local_time: "Sun 09:00",
    drafts: 1,
    pick_and_compose: true,
    depth: "weekly",
    target_chars: [2800, 4200],
  });
});


test("missing Meta analytics stay unknown instead of becoming zero", () => {
  const snapshot = platformSnapshot("facebook", {
    platforms: [{platform: "facebook", status: "published", count: 3, last_posted_at: "2026-08-06T01:00:00Z"}],
    engagement: [],
    audience: [{platform: "facebook", followers: 1200, followers_delta_7d: null}],
    content_quality: [],
    data_health: [],
  });
  assert.equal(snapshot.published, 3);
  assert.equal(snapshot.followers, 1200);
  assert.equal(snapshot.followersDelta7d, null);
  assert.equal(snapshot.medianPrimary, null);
  assert.equal(snapshot.qualityRate, null);
});


test("trend series preserves missing days as gaps", () => {
  const series = buildTrendSeries([
    {platform: "threads", day: "2026-08-04", actions: 4},
    {platform: "threads", day: "2026-08-06", actions: 9},
  ], "actions", ["2026-08-04", "2026-08-05", "2026-08-06"]);
  assert.deepEqual(series.threads, [4, null, 9]);
  assert.deepEqual(series.facebook, [null, null, null]);
});


test("attention queue separates workflow failure, degraded data and pending work", () => {
  const items = deriveAttention({
    dashboard: {
      data_health: [{platform: "system", metric: "substack_draft_worker", status: "degraded", detail: "pending_remote=1"}],
      recent_submissions: [{id: "submission-1", target: "substack", status: "queued", created_at: "2026-08-06T01:00:00Z"}],
    },
    workflows: [{workflowName: "News Radar · Full Cloud Pipeline", conclusion: "failure", updatedAt: "2026-08-06T04:00:00Z"}],
  });
  assert.deepEqual(new Set(items.map(item => item.kind)), new Set(["workflow", "data", "submission"]));
  assert.equal(items[0].severity, "critical");
});


test("submission payload preserves existing API modes", () => {
  assert.deepEqual(buildSubmissionPayload({
    target: "substack", sourceType: "url", content: "https://example.com", note: "深挖機制",
  }), {
    target: "substack", source_type: "url", content: "https://example.com",
    note: "深挖機制", platforms: [], mode: "draft_priority",
  });
  assert.equal(buildSubmissionPayload({
    target: "meta", sourceType: "text", content: "內容", note: "標題",
    platforms: ["facebook", "threads"], metaMode: "publish_now",
  }).mode, "publish_now");
});


test("scheduler health becomes degraded after the expected tick tolerance", () => {
  const now = Date.parse("2026-08-06T14:30:00Z");
  const row = effectiveSchedulerHealth({
    platform: "system",
    metric: "scheduler_watchdog_dispatch",
    status: "healthy",
    captured_at: "2026-08-06T10:27:00Z",
    detail: "dispatch_id=old",
  }, now);
  assert.equal(row.status, "degraded");
  assert.match(row.detail, /expected_tick_missing/);
});


test("watchdog delivery must match the latest dispatch lineage", () => {
  const now = Date.parse("2026-08-06T13:40:00Z");
  const rows = ensureExpectedSchedulerHealth([
    {platform: "system", metric: "scheduler_delivery", status: "healthy", captured_at: "2026-08-06T13:17:00Z", detail: "ok"},
    {platform: "system", metric: "scheduler_watchdog_dispatch", status: "healthy", captured_at: "2026-08-06T13:27:00Z", detail: "dispatch_id=new"},
    {platform: "system", metric: "scheduler_watchdog_delivery", status: "healthy", captured_at: "2026-08-06T13:27:00Z", detail: "dispatch_id=old"},
  ], now);
  const delivery = rows.find((row) => row.metric === "scheduler_watchdog_delivery");
  assert.equal(delivery.status, "degraded");
  assert.match(delivery.detail, /dispatch_lineage_mismatch/);
});


test("recent content groups one article across Meta platforms with native metrics", () => {
  const summary = summarizeRecentContent([
    {
      id: "post-fb", draft_id: "draft-1", platform: "facebook", title: "AI 資本支出的回報週期",
      posted_at: "2026-08-06T02:00:00Z", metrics_captured_at: "2026-08-06T05:00:00Z",
      clicks: 5, likes: 1, comments: 0, shares: 0, saves: 0, replies: 0, reposts: 0, quotes: 0,
    },
    {
      id: "post-th", draft_id: "draft-1", platform: "threads", title: "AI 資本支出的回報週期",
      posted_at: "2026-08-06T02:05:00Z", metrics_captured_at: "2026-08-06T05:00:00Z",
      views: 100, likes: 3, comments: 0, shares: 0, saves: 0, replies: 2, reposts: 0, quotes: 0, clicks: 0,
    },
    {
      id: "post-ig", draft_id: "draft-2", platform: "instagram", title: "機器人供應鏈不是下一個手機週期",
      posted_at: "2026-08-05T03:00:00Z", metrics_captured_at: "2026-08-05T06:00:00Z",
      reach: 200, likes: 2, comments: 0, shares: 0, saves: 0, replies: 0, reposts: 0, quotes: 0, clicks: 0,
    },
  ]);
  assert.equal(summary.items.length, 2);
  assert.deepEqual(summary.items[0].platforms.map(item => item.platform), ["facebook", "threads"]);
  assert.equal(summary.items[0].totalActions, 11);
  assert.deepEqual(summary.items[0].platforms.map(item => [item.primaryLabel, item.primaryValue]), [
    ["點擊", 5], ["瀏覽", 100],
  ]);
  assert.equal(summary.insights.topContentId, "draft-1");
  assert.equal(summary.insights.measuredPlatformPosts, 3);
});


test("missing recent-post analytics remain unknown and lower coverage", () => {
  const summary = summarizeRecentContent([
    {id: "post-1", draft_id: "draft-1", platform: "facebook", title: "有發佈、尚未回讀", posted_at: "2026-08-06T02:00:00Z"},
  ]);
  assert.equal(summary.items[0].totalActions, null);
  assert.equal(summary.items[0].platforms[0].primaryValue, null);
  assert.equal(summary.insights.measuredPlatformPosts, 0);
  assert.equal(summary.insights.totalPlatformPosts, 1);
  assert.equal(summary.insights.topContentId, null);
});
