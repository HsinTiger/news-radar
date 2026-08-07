export const TOKEN_KEY = "hsintiger_social_ops_owner_token";
export const PLATFORM_ORDER = ["facebook", "instagram", "threads"];
export const SCHEDULER_HOURS_UTC = [0, 3, 10, 11, 12, 13];
export const SCHEDULER_HEALTH = Object.freeze({
  scheduler_delivery: Object.freeze({minute: 17, toleranceMs: 4 * 60 * 60 * 1000}),
  scheduler_watchdog_dispatch: Object.freeze({minute: 27, toleranceMs: 60 * 60 * 1000}),
  scheduler_watchdog_delivery: Object.freeze({minute: 27, toleranceMs: 60 * 60 * 1000}),
});

export const DEFAULT_EDITORIAL_CONTRACT = Object.freeze({
  schema_version: 3,
  publication_mode: "draft_only",
  podcast: Object.freeze({
    local_time: "12:00",
    drafts: 2,
    candidate_window_days: 7,
    depth: "weekly",
    article_kind: "podcast",
    target_chars: Object.freeze([4200, 6500]),
    research_sources: Object.freeze([5, 10]),
  }),
  company: Object.freeze({
    local_time: "Sun 09:00",
    drafts: 1,
    pick_and_compose: true,
    depth: "weekly",
    article_kind: "company",
    target_chars: Object.freeze([3800, 6000]),
    research_sources: Object.freeze([5, 10]),
  }),
  writer: Object.freeze({
    positioning: "第一人稱的真人編輯式深度分析",
    first_person: "以「我」呈現消化後的理解與判斷；不虛構親身經驗或採訪",
    podcast_method: "先提取引人入勝的對談摘要與觀點，再導出可獨立成立的問題",
    evidence_boundary: "區分主來源事實、延伸證據、作者推論與未知",
    source_strategy: "主來源消化 → 不同研究角度 → 5–10 個已讀取來源 → 主張—證據圖 → 資訊價值閘門",
    method_sources: "Firecrawl Deep Research + OpenSquilla Citation Planner + MoAI Claim Check（只採方法，不載入第三方 prompt）",
    article_forms: "調查型、論證型或自我成長型；自我成長仍要可檢驗",
    cognitive_load: "一節一個子問題、一段一件事；必要時加專有名詞註解",
    ending: "以本文特有、讀者能真正回覆的問題收尾",
  }),
});

const PENDING = new Set([
  "queued", "claimed", "dispatched", "processing", "content_queued",
  "source_queued", "partial", "quality_held",
]);

function nullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function readStoredToken({local, session}) {
  return local?.getItem(TOKEN_KEY) || session?.getItem(TOKEN_KEY) || "";
}

export function rememberToken(value, {remember, local, session}) {
  local?.removeItem(TOKEN_KEY);
  session?.removeItem(TOKEN_KEY);
  if (!value) return;
  (remember ? local : session)?.setItem(TOKEN_KEY, value);
}

export function forgetToken({local, session}) {
  local?.removeItem(TOKEN_KEY);
  session?.removeItem(TOKEN_KEY);
}

export function normalizeEditorialContract(value) {
  const incoming = value && typeof value === "object" ? value : {};
  const substack = incoming.substack && typeof incoming.substack === "object"
    ? incoming.substack
    : incoming;
  return {
    ...DEFAULT_EDITORIAL_CONTRACT,
    ...substack,
    podcast: {...DEFAULT_EDITORIAL_CONTRACT.podcast, ...(substack.podcast || {})},
    company: {...DEFAULT_EDITORIAL_CONTRACT.company, ...(substack.company || {})},
    writer: {...DEFAULT_EDITORIAL_CONTRACT.writer, ...(substack.writer || {})},
  };
}

export function platformSnapshot(platform, data = {}) {
  const counts = (data.platforms || []).filter(row => row.platform === platform);
  const engagement = (data.engagement || []).find(row => row.platform === platform) || {};
  const audience = (data.audience || []).find(row => row.platform === platform) || {};
  const quality = (data.content_quality || []).find(row => row.platform === platform) || {};
  const healthRows = (data.data_health || []).filter(row => row.platform === platform);
  const health = healthRows.find(row => row.metric === "daily_publish_cadence")
    || healthRows.find(row => row.metric === "latest_post_canary")
    || healthRows.find(row => row.metric === "engagement_api")
    || null;
  const published = counts
    .filter(row => row.status === "published")
    .reduce((sum, row) => sum + (nullableNumber(row.count) || 0), 0);
  const total = counts.reduce((sum, row) => sum + (nullableNumber(row.count) || 0), 0);
  const evaluated = nullableNumber(quality.evaluated);
  const publishReady = nullableNumber(quality.publish_ready_count);
  const primary = platform === "facebook"
    ? ["點擊中位數", engagement.median_clicks]
    : platform === "instagram"
      ? ["觸及中位數", engagement.median_reach]
      : ["瀏覽中位數", engagement.median_views];
  return {
    platform,
    published,
    total,
    lastPostedAt: counts.reduce((latest, row) => {
      const value = row.last_posted_at || null;
      return value && (!latest || value > latest) ? value : latest;
    }, null),
    followers: nullableNumber(audience.followers),
    followersDelta7d: nullableNumber(audience.followers_delta_7d),
    audienceCapturedAt: audience.captured_at || null,
    medianPrimaryLabel: primary[0],
    medianPrimary: nullableNumber(primary[1]),
    sampledPosts: nullableNumber(engagement.posts),
    engagementCapturedAt: engagement.last_captured_at || null,
    zeroActionRate: nullableNumber(engagement.zero_action_rate),
    qualityRate: evaluated && publishReady !== null
      ? Math.round((publishReady / evaluated) * 1000) / 10
      : null,
    qualityEvaluated: evaluated,
    topIssues: quality.top_issue_codes || [],
    health,
  };
}

export function buildTrendSeries(rows = [], metric = "actions", days = []) {
  const result = {};
  for (const platform of PLATFORM_ORDER) {
    result[platform] = days.map(day => {
      const row = rows.find(item => item.platform === platform && item.day === day);
      return row ? nullableNumber(row[metric]) : null;
    });
  }
  return result;
}

function latestTimestamp(values) {
  return values.filter(Boolean).sort().at(-1) || null;
}

function recentPostMetrics(row) {
  const metricNames = [
    "clicks", "likes", "comments", "shares", "saves", "replies", "reposts", "quotes",
  ];
  const metricValues = metricNames.map((name) => nullableNumber(row[name]));
  const measured = Boolean(row.metrics_captured_at)
    || metricValues.some((value) => value !== null)
    || [row.views, row.reach].some((value) => nullableNumber(value) !== null);
  const primary = row.platform === "facebook"
    ? ["點擊", nullableNumber(row.clicks)]
    : row.platform === "instagram"
      ? ["觸及", nullableNumber(row.reach)]
      : ["瀏覽", nullableNumber(row.views)];
  return {
    ...row,
    measured,
    actions: measured
      ? metricValues.reduce((sum, value) => sum + (value || 0), 0)
      : null,
    primaryLabel: primary[0],
    primaryValue: primary[1],
  };
}

export function summarizeRecentContent(rows = []) {
  const groups = new Map();
  for (const row of rows) {
    const id = row.draft_id || row.submission_id || row.source_url || row.id;
    if (!id) continue;
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(recentPostMetrics(row));
  }
  const items = [...groups.entries()].map(([id, platformRows]) => {
    const ordered = [...platformRows].sort((a, b) => {
      const platformOrder = PLATFORM_ORDER.indexOf(a.platform) - PLATFORM_ORDER.indexOf(b.platform);
      return platformOrder || String(b.posted_at || "").localeCompare(String(a.posted_at || ""));
    });
    const measuredRows = ordered.filter((row) => row.measured);
    const newestRow = [...ordered].sort((a, b) => String(b.posted_at || "").localeCompare(String(a.posted_at || "")))[0] || {};
    return {
      id,
      title: ordered.find((row) => row.title)?.title || ordered.find((row) => row.topic)?.topic || "未命名內容",
      topic: ordered.find((row) => row.topic)?.topic || null,
      sourceUrl: ordered.find((row) => row.source_url)?.source_url || null,
      format: newestRow.format || null,
      postedAt: latestTimestamp(ordered.map((row) => row.posted_at)),
      latestMetricsAt: latestTimestamp(ordered.map((row) => row.metrics_captured_at)),
      platforms: ordered,
      measuredPlatforms: measuredRows.length,
      totalActions: measuredRows.length
        ? measuredRows.reduce((sum, row) => sum + row.actions, 0)
        : null,
    };
  }).sort((a, b) => String(b.postedAt || "").localeCompare(String(a.postedAt || "")));
  const measuredItems = items.filter((item) => item.totalActions !== null);
  const top = [...measuredItems].sort((a, b) => b.totalActions - a.totalActions)[0] || null;
  const totalPlatformPosts = items.reduce((sum, item) => sum + item.platforms.length, 0);
  const measuredPlatformPosts = items.reduce((sum, item) => sum + item.measuredPlatforms, 0);
  return {
    items,
    insights: {
      totalContent: items.length,
      totalPlatformPosts,
      measuredPlatformPosts,
      coverageRate: totalPlatformPosts
        ? Math.round(measuredPlatformPosts / totalPlatformPosts * 1000) / 10
        : null,
      latestMetricsAt: latestTimestamp(items.map((item) => item.latestMetricsAt)),
      topContentId: top?.id || null,
      topTitle: top?.title || null,
      topActions: top?.totalActions ?? null,
    },
  };
}

export function deriveAttention({dashboard = {}, workflows = []} = {}) {
  const items = [];
  const seenWorkflow = new Set();
  for (const run of workflows) {
    const name = run.workflowName || run.name || "workflow";
    if (seenWorkflow.has(name)) continue;
    seenWorkflow.add(name);
    if (run.conclusion === "failure" || run.conclusion === "cancelled") {
      items.push({
        id: `workflow-${name}`,
        kind: "workflow",
        severity: "critical",
        title: `${name} 未通過`,
        detail: "這代表執行或驗證失敗，不等於資料 collector 全部中斷。",
        observedAt: run.updatedAt || run.createdAt || null,
        url: run.url || null,
      });
    }
  }
  for (const row of dashboard.data_health || []) {
    if (!new Set(["error", "degraded"]).has(row.status)) continue;
    items.push({
      id: `health-${row.platform}-${row.metric}`,
      kind: "data",
      severity: row.status === "error" ? "critical" : "warning",
      title: `${row.metric} · ${row.status}`,
      detail: row.detail || "資料健康訊號沒有補充說明。",
      observedAt: row.captured_at || null,
    });
  }
  for (const row of dashboard.recent_submissions || []) {
    if (!PENDING.has(row.status)) continue;
    items.push({
      id: `submission-${row.id}`,
      kind: "submission",
      severity: row.status === "quality_held" ? "warning" : "info",
      title: `${row.target === "substack" ? "Substack" : "Meta"} 投稿仍在 ${row.status}`,
      detail: row.note || row.id,
      observedAt: row.updated_at || row.created_at || null,
    });
  }
  const rank = {critical: 0, warning: 1, info: 2};
  return items.sort((a, b) => (rank[a.severity] - rank[b.severity])
    || String(b.observedAt || "").localeCompare(String(a.observedAt || "")));
}

export function buildSubmissionPayload({
  target,
  sourceType,
  content,
  note = "",
  platforms = [],
  metaMode = "queue",
  substackMode = "draft_priority",
}) {
  return {
    target,
    source_type: sourceType,
    content,
    note,
    platforms: target === "meta" ? [...new Set(platforms)] : [],
    mode: target === "substack" ? substackMode : metaMode,
  };
}

export function isPendingStatus(status) {
  return PENDING.has(status);
}

export function lastExpectedSchedulerTick(nowMs, minute) {
  const now = new Date(nowMs);
  const starts = [
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - 1),
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  ];
  return Math.max(...starts.flatMap((start) => SCHEDULER_HOURS_UTC.map((hour) =>
    start + hour * 60 * 60 * 1000 + minute * 60 * 1000
  )).filter((value) => value <= nowMs));
}

export function effectiveSchedulerHealth(item, nowMs = Date.now()) {
  const timing = SCHEDULER_HEALTH[item.metric];
  if (!timing || item.status !== "healthy") return {...item};
  const capturedMs = Date.parse(item.captured_at || "");
  const expectedMs = lastExpectedSchedulerTick(nowMs, timing.minute);
  if (Number.isFinite(capturedMs) && capturedMs + 5 * 60 * 1000 >= expectedMs) {
    return {...item};
  }
  const waiting = nowMs - expectedMs <= timing.toleranceMs;
  return {
    ...item,
    status: waiting ? "unknown" : "degraded",
    detail: `${waiting ? "awaiting_expected_tick" : "expected_tick_missing"}; expected_utc=${new Date(expectedMs).toISOString()}; tolerance_minutes=${timing.toleranceMs / 60000}; ${item.detail || "no heartbeat detail"}`,
  };
}

function healthDetailField(item, key) {
  const match = String(item?.detail || "").match(new RegExp(`(?:^|;\\s*)${key}=([^;]+)`));
  return match ? match[1].trim() : "";
}

export function reconcileWatchdogLineage(rows) {
  const result = rows.map((row) => ({...row}));
  const dispatch = result.find((item) => item.metric === "scheduler_watchdog_dispatch");
  const deliveryIndex = result.findIndex((item) => item.metric === "scheduler_watchdog_delivery");
  if (!dispatch || deliveryIndex < 0 || dispatch.status !== "healthy" || result[deliveryIndex].status !== "healthy") {
    return result;
  }
  const dispatchId = healthDetailField(dispatch, "dispatch_id");
  const deliveryId = healthDetailField(result[deliveryIndex], "dispatch_id");
  if (dispatchId && dispatchId === deliveryId) return result;
  result[deliveryIndex] = {
    ...result[deliveryIndex],
    status: "degraded",
    detail: `dispatch_lineage_mismatch; expected_dispatch_id=${dispatchId || "missing"}; ${result[deliveryIndex].detail || "delivery detail missing"}`,
  };
  return result;
}

export function ensureExpectedSchedulerHealth(rows = [], nowMs = Date.now()) {
  const result = rows.map((row) => effectiveSchedulerHealth(row, nowMs));
  for (const metric of Object.keys(SCHEDULER_HEALTH)) {
    if (result.some((item) => item.metric === metric)) continue;
    result.push(effectiveSchedulerHealth({
      platform: "system",
      metric,
      status: "healthy",
      captured_at: null,
      detail: "heartbeat_not_persisted",
    }, nowMs));
  }
  return reconcileWatchdogLineage(result);
}
