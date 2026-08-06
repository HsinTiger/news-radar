/**
 * HsinTiger Social Ops control-plane API.
 *
 * The browser never receives a GitHub credential. Owner requests are written
 * to D1; a GitHub Actions poller with a separate service token claims work.
 */

const ALLOWED_ORIGIN = "https://hsintiger.github.io";
const API_VERSION = "2026-08-06.substack-publish-now-v1";
const OWNER_RATE_LIMIT_PER_MINUTE = 10;
const TARGETS = new Set(["meta", "substack"]);
const SOURCE_TYPES = new Set(["url", "text", "youtube"]);
const META_MODES = new Set(["publish_now", "queue"]);
const PLATFORMS = new Set(["facebook", "instagram", "threads"]);
const WATCHDOG_REPOSITORY = "HsinTiger/news-radar";
const WATCHDOG_WORKFLOW = "adaptive-scheduler.yml";
const POLLER_WORKFLOW = "submission-poller.yml";

const LATEST_AUDIENCE_SQL = `WITH ranked AS (
  SELECT *,ROW_NUMBER() OVER(PARTITION BY platform ORDER BY captured_at DESC) AS rn
  FROM audience_snapshots
)
SELECT latest.platform,latest.captured_at,latest.followers,
  CASE
    WHEN latest.followers_delta_7d IS NOT NULL THEN latest.followers_delta_7d
    ELSE latest.followers - (
      SELECT older.followers
      FROM audience_snapshots older
      WHERE older.platform=latest.platform
        AND datetime(older.captured_at) <= datetime(latest.captured_at,'-7 day')
      ORDER BY datetime(older.captured_at) DESC
      LIMIT 1
    )
  END AS followers_delta_7d,
  latest.metric_status
FROM ranked latest
WHERE latest.rn=1`;

const LATEST_ENGAGEMENT_SQL = `WITH ranked AS (
  SELECT *,ROW_NUMBER() OVER(
    PARTITION BY platform,platform_post_id ORDER BY captured_at DESC
  ) AS snapshot_rank
  FROM engagement_snapshots
), latest AS (
  SELECT *,likes+comments+shares+saves+replies+reposts+quotes+clicks AS actions_total
  FROM ranked WHERE snapshot_rank=1
), ordered AS (
  SELECT *,
    ROW_NUMBER() OVER(PARTITION BY platform ORDER BY views) AS views_rank,
    ROW_NUMBER() OVER(PARTITION BY platform ORDER BY reach) AS reach_rank,
    ROW_NUMBER() OVER(PARTITION BY platform ORDER BY clicks) AS clicks_rank,
    ROW_NUMBER() OVER(PARTITION BY platform ORDER BY actions_total) AS actions_rank,
    COUNT(*) OVER(PARTITION BY platform) AS platform_count
  FROM latest
)
SELECT platform,COUNT(*) AS posts,MAX(captured_at) AS last_captured_at,
  ROUND(AVG(views),1) AS avg_views,ROUND(AVG(reach),1) AS avg_reach,
  ROUND(AVG(clicks),1) AS avg_clicks,ROUND(AVG(actions_total),1) AS avg_actions,
  ROUND(AVG(CASE WHEN views_rank IN ((platform_count+1)/2,(platform_count+2)/2)
    THEN views END),1) AS median_views,
  ROUND(AVG(CASE WHEN reach_rank IN ((platform_count+1)/2,(platform_count+2)/2)
    THEN reach END),1) AS median_reach,
  ROUND(AVG(CASE WHEN clicks_rank IN ((platform_count+1)/2,(platform_count+2)/2)
    THEN clicks END),1) AS median_clicks,
  ROUND(AVG(CASE WHEN actions_rank IN ((platform_count+1)/2,(platform_count+2)/2)
    THEN actions_total END),1) AS median_actions,
  ROUND(100.0*SUM(CASE WHEN actions_total=0 THEN 1 ELSE 0 END)/COUNT(*),1)
    AS zero_action_rate,
  SUM(actions_total) AS actions,
  SUM(CASE WHEN metric_status='ok' THEN 1 ELSE 0 END) AS healthy_posts
FROM ordered GROUP BY platform`;

class HTTPError extends Error {
  constructor(status, message, code = "request_error") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchSchedulerWatchdog(controller, env));
  },
  async fetch(request, env) {
    const cors = corsHeaders(request);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    try {
      const url = new URL(request.url);
      if (request.method === "GET" && url.pathname === "/health") {
        const [row, runtime] = await Promise.all([
          env.DB.prepare("SELECT 1 AS ok").first(),
          currentRuntime(env),
        ]);
        return reply(
          { ok: row?.ok === 1, version: API_VERSION, automation: runtime },
          200,
          cors,
        );
      }
      if (url.pathname === "/" || url.searchParams.has("action")) {
        throw new HTTPError(
          410,
          "Legacy action proxy removed. Use the authenticated submissions API.",
          "legacy_proxy_removed",
        );
      }
      if (request.method === "POST" && url.pathname === "/api/submissions") {
        await requireToken(request, env.OWNER_TOKEN, "owner");
        await enforceOwnerRateLimit(env);
        return await createSubmission(request, env, cors);
      }
      if (request.method === "GET" && url.pathname === "/api/submissions") {
        await requireToken(request, env.OWNER_TOKEN, "owner");
        return await listSubmissions(url, env, cors);
      }
      if (request.method === "GET" && url.pathname === "/api/dashboard") {
        await requireToken(request, env.OWNER_TOKEN, "owner");
        return await dashboard(env, cors);
      }
      const proposalDecisionMatch = url.pathname.match(
        /^\/api\/learning-proposals\/([A-Za-z0-9_-]{8,160})\/decision$/,
      );
      if (request.method === "POST" && proposalDecisionMatch) {
        await requireToken(request, env.OWNER_TOKEN, "owner");
        return await decideLearningProposal(request, env, proposalDecisionMatch[1], cors);
      }
      if (
        request.method === "GET" &&
        url.pathname === "/api/service/submissions/next"
      ) {
        await requireToken(request, env.SERVICE_TOKEN, "service");
        return await claimNextSubmission(env, cors);
      }
      const statusMatch = url.pathname.match(
        /^\/api\/service\/submissions\/([A-Za-z0-9_-]{8,80})\/status$/,
      );
      if (request.method === "POST" && statusMatch) {
        await requireToken(request, env.SERVICE_TOKEN, "service");
        return await updateSubmissionStatus(request, env, statusMatch[1], cors);
      }
      if (request.method === "POST" && url.pathname === "/api/service/sync") {
        await requireToken(request, env.SERVICE_TOKEN, "service");
        return await syncOperationalData(request, env, cors);
      }
      if (request.method === "POST" && url.pathname === "/api/service/events") {
        await requireToken(request, env.SERVICE_TOKEN, "service");
        return await recordServiceEvent(request, env, cors);
      }
      if (
        request.method === "GET" &&
        url.pathname === "/api/service/learning-proposals/decisions"
      ) {
        await requireToken(request, env.SERVICE_TOKEN, "service");
        return await listLearningDecisions(env, cors);
      }
      throw new HTTPError(404, "Not found", "not_found");
    } catch (error) {
      if (error instanceof HTTPError) {
        return reply({ ok: false, error: error.code, message: error.message }, error.status, cors);
      }
      console.error("unhandled", error);
      return reply(
        { ok: false, error: "internal_error", message: "Internal error" },
        500,
        cors,
      );
    }
  },
};

async function recordWatchdogDispatch(env, status, detail, capturedAt) {
  await env.DB.prepare(
    `INSERT INTO data_health_snapshots(platform,metric,status,detail,captured_at)
     VALUES(?,?,?,?,?)
     ON CONFLICT(platform,metric,captured_at) DO UPDATE SET
       status=excluded.status,detail=excluded.detail`,
  )
    .bind("system", "scheduler_watchdog_dispatch", status, detail, capturedAt)
    .run();
  await audit(
    env,
    "cloudflare_cron",
    "dispatch_governed_scheduler",
    WATCHDOG_WORKFLOW,
    status,
    { detail, captured_at: capturedAt },
  );
}

async function dispatchSchedulerWatchdog(controller, env) {
  const capturedAt = new Date().toISOString();
  const dispatchId = crypto.randomUUID();
  const scheduledTime = Number.isFinite(controller?.scheduledTime)
    ? new Date(controller.scheduledTime).toISOString()
    : capturedAt;
  const cron = String(controller?.cron || "unknown").slice(0, 80);
  const mode = String(env.AUTOMATION_MODE || "paused").trim().toLowerCase();
  const prefix = `event=cloudflare_cron; dispatch_id=${dispatchId}; scheduled_time=${scheduledTime}; cron=${cron}`;
  if (!new Set(["live", "recovery"]).has(mode)) {
    await recordWatchdogDispatch(
      env,
      "unknown",
      `${prefix}; dispatch=skipped; reason=automation_${mode || "unset"}`,
      capturedAt,
    );
    return;
  }
  if (!env.GITHUB_ACTIONS_TOKEN) {
    await recordWatchdogDispatch(
      env,
      "error",
      `${prefix}; dispatch=failed; reason=github_actions_token_missing`,
      capturedAt,
    );
    throw new Error("scheduler watchdog GitHub credential is unavailable");
  }

  let response;
  try {
    response = await fetch(
      `https://api.github.com/repos/${WATCHDOG_REPOSITORY}/actions/workflows/${WATCHDOG_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "hsintiger-news-radar-scheduler-watchdog",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: {
            now: "",
            setup_only: "false",
            trigger_source: "cloudflare_watchdog",
            watchdog_dispatch_id: dispatchId,
          },
        }),
      },
    );
  } catch (error) {
    await recordWatchdogDispatch(
      env,
      "error",
      `${prefix}; dispatch=failed; reason=github_network_error`,
      capturedAt,
    );
    throw error;
  }
  if (response.status !== 204) {
    await recordWatchdogDispatch(
      env,
      "error",
      `${prefix}; dispatch=failed; github_status=${response.status}`,
      capturedAt,
    );
    throw new Error(`scheduler watchdog dispatch rejected with HTTP ${response.status}`);
  }
  await recordWatchdogDispatch(
    env,
    "healthy",
    `${prefix}; dispatch=accepted; workflow=${WATCHDOG_WORKFLOW}`,
    capturedAt,
  );
}

/**
 * Kick the submission poller the moment a submission lands.
 *
 * 2026-07-29: submission-poller.yml is declared as a five-minute cron, but
 * GitHub coalesces and deprioritises scheduled workflows under load — observed
 * actual cadence was 1–3 HOURS between ticks. A submission therefore sat in
 * `queued` for hours before anything claimed it, which reads on the console as
 * "投稿了但沒在寫稿". The cron stays as the safety net; this makes the common
 * case immediate.
 *
 * Never throws: a submission that is safely persisted must not be reported as
 * failed just because the nudge did not land. The cron will still pick it up.
 */
async function nudgeSubmissionPoller(env, submissionId) {
  const now = new Date().toISOString();
  const note = async (status, detail) => {
    try {
      await env.DB.prepare(
        "INSERT INTO audit_events(actor,action,subject_id,status,metadata_json,created_at) VALUES('system','nudge_submission_poller',?,?,?,?)",
      )
        .bind(submissionId, status, JSON.stringify({ detail, workflow: POLLER_WORKFLOW }), now)
        .run();
    } catch {
      /* audit must never break the submission path */
    }
  };

  if (!env.GITHUB_ACTIONS_TOKEN) {
    await note("skipped", "github_actions_token_missing");
    return;
  }
  try {
    const response = await fetch(
      `https://api.github.com/repos/${WATCHDOG_REPOSITORY}/actions/workflows/${POLLER_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
          "Content-Type": "application/json",
          "User-Agent": "hsintiger-news-radar-submission-nudge",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    );
    if (response.status !== 204) {
      await note("error", `github_status=${response.status}`);
      return;
    }
    await note("accepted", "dispatched");
  } catch (error) {
    await note("error", `network_error=${String(error).slice(0, 120)}`);
  }
}

function corsHeaders(request) {
  const origin = request.headers.get("Origin");
  const headers = {
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Authorization,Content-Type,Idempotency-Key",
    "Access-Control-Max-Age": "86400",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
  if (origin === ALLOWED_ORIGIN) headers["Access-Control-Allow-Origin"] = origin;
  return headers;
}

function reply(payload, status, headers) {
  return Response.json(payload, {
    status,
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}

async function currentRuntime(env) {
  const row = await env.DB.prepare(
    "SELECT mode,submission_processor,source,updated_at FROM automation_state WHERE id='runtime'",
  ).first();
  const mode = row?.mode || env.AUTOMATION_MODE || "paused";
  const submissionProcessor = row?.submission_processor || env.SUBMISSION_PROCESSOR_MODE || "paused";
  const metaPublishNowEnabled = env.ENABLE_META_PUBLISH_NOW === "true";
  const substackPublishNowEnabled = env.ENABLE_SUBSTACK_PUBLISH_NOW === "true";
  return {
    mode,
    submission_processor: submissionProcessor,
    source: row?.source || "worker_fallback",
    updated_at: row?.updated_at || null,
    meta_publish_now_enabled: metaPublishNowEnabled,
    meta_publish_now_ready: metaPublishNowEnabled && submissionProcessor === "live",
    substack_publish_now_enabled: substackPublishNowEnabled,
    substack_publish_now_ready: substackPublishNowEnabled && submissionProcessor === "live",
    substack_auto_publish: false,
  };
}

async function requireToken(request, expected, actor) {
  if (!expected) throw new HTTPError(503, `${actor} credential is not configured`, "auth_unavailable");
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) throw new HTTPError(401, "Authentication required", "unauthorized");
  if (!(await constantTimeEqual(auth.slice(7), expected))) {
    throw new HTTPError(401, "Invalid credential", "unauthorized");
  }
}

async function constantTimeEqual(actual, expected) {
  const encoder = new TextEncoder();
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const a = new Uint8Array(left);
  const b = new Uint8Array(right);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    diff |= (a[i] || 0) ^ (b[i] || 0);
  }
  return diff === 0;
}

async function bodyJson(request, maxBytes = 60_000) {
  const length = Number(request.headers.get("Content-Length") || 0);
  if (length > maxBytes) throw new HTTPError(413, "Request body is too large", "payload_too_large");
  let body;
  try {
    body = await request.json();
  } catch {
    throw new HTTPError(400, "Invalid JSON", "invalid_json");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new HTTPError(400, "JSON object required", "invalid_json");
  }
  return body;
}

function cleanString(value, name, max, required = false) {
  const text = typeof value === "string" ? value.trim() : "";
  if (required && !text) throw new HTTPError(400, `${name} is required`, "invalid_input");
  if (text.length > max) throw new HTTPError(400, `${name} exceeds ${max} characters`, "invalid_input");
  return text;
}

async function enforceOwnerRateLimit(env) {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM audit_events WHERE actor='owner' AND action='create_submission' AND datetime(created_at) >= datetime('now','-1 minute')",
  ).first();
  if (Number(row?.count || 0) >= OWNER_RATE_LIMIT_PER_MINUTE) {
    throw new HTTPError(429, "Too many submissions; retry in one minute", "rate_limited");
  }
}

async function decideLearningProposal(request, env, proposalId, cors) {
  const body = await bodyJson(request, 20_000);
  const decision = cleanString(body.decision, "decision", 20, true);
  if (!new Set(["approved", "rejected"]).has(decision)) {
    throw new HTTPError(400, "decision must be approved or rejected", "invalid_input");
  }
  const comment = cleanString(body.comment, "comment", 1000);
  const current = await env.DB.prepare(
    "SELECT id,status,decided_at FROM learning_proposals WHERE id=?",
  ).bind(proposalId).first();
  if (!current) throw new HTTPError(404, "learning proposal not found", "not_found");
  if (current.status === "applied" || current.status === "superseded") {
    throw new HTTPError(409, "proposal is already terminal", "invalid_transition");
  }
  if (current.status !== "proposed" && current.status !== decision) {
    throw new HTTPError(409, "proposal already has another owner decision", "invalid_transition");
  }
  const now = current.decided_at || new Date().toISOString();
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE learning_proposals
       SET status=?,decision_comment=?,decided_at=? WHERE id=?`,
    ).bind(decision, comment || null, now, proposalId),
    env.DB.prepare(
      `INSERT INTO audit_events(actor,action,subject_id,status,metadata_json,created_at)
       VALUES('owner','decide_learning_proposal',?,'accepted',?,?)`,
    ).bind(proposalId, JSON.stringify({ decision, comment }), new Date().toISOString()),
  ]);
  return reply(
    {
      ok: true,
      proposal: { id: proposalId, status: decision, decision_comment: comment || null, decided_at: now },
      execution: decision === "approved" ? "next_learning_review" : "none",
    },
    200,
    cors,
  );
}

async function listLearningDecisions(env, cors) {
  const { results } = await env.DB.prepare(
    `SELECT id,status,decision_comment,decided_at
     FROM learning_proposals
     WHERE status IN ('approved','rejected')
     ORDER BY decided_at,id`,
  ).all();
  return reply({ ok: true, decisions: results }, 200, cors);
}

async function createSubmission(request, env, cors) {
  const body = await bodyJson(request);
  const target = cleanString(body.target, "target", 20, true);
  const sourceType = cleanString(body.source_type, "source_type", 20, true);
  if (!TARGETS.has(target)) throw new HTTPError(400, "target must be meta or substack", "invalid_input");
  if (!SOURCE_TYPES.has(sourceType)) {
    throw new HTTPError(400, "source_type must be url, text, or youtube", "invalid_input");
  }
  const content = cleanString(body.content, "content", 50_000, true);
  const note = cleanString(body.note, "note", 500);
  let mode = cleanString(body.mode, "mode", 20) || (target === "substack" ? "draft" : "queue");
  if (target === "substack" && !new Set(["draft", "draft_priority", "publish_now"]).has(mode)) {
    throw new HTTPError(400, "Substack mode must be draft, draft_priority, or publish_now", "invalid_input");
  }
  if (target === "meta" && !META_MODES.has(mode)) {
    throw new HTTPError(400, "Meta mode must be publish_now or queue", "invalid_input");
  }
  if (target === "meta" && mode === "publish_now") {
    const runtime = await currentRuntime(env);
    if (!runtime.meta_publish_now_enabled) {
      throw new HTTPError(
        409,
        "Meta publish-now is locked until the setup-only canary is approved",
        "canary_required",
      );
    }
    if (!runtime.meta_publish_now_ready) {
      throw new HTTPError(
        409,
        "Meta publish-now is unavailable because the submission processor is not live",
        "processor_unavailable",
      );
    }
    if (sourceType === "text" && !note) {
      throw new HTTPError(
        400,
        "note is required as the editorial title for immediate text publishing",
        "editorial_title_required",
      );
    }
    if (sourceType === "text" && content.length < 80) {
      throw new HTTPError(
        400,
        "immediate text publishing requires at least 80 characters of source material",
        "source_too_short",
      );
    }
  }
  if (target === "substack" && mode === "publish_now") {
    const runtime = await currentRuntime(env);
    if (!runtime.substack_publish_now_enabled) {
      throw new HTTPError(409, "Substack publish-now is disabled", "publish_now_disabled");
    }
    if (!runtime.substack_publish_now_ready) {
      throw new HTTPError(
        409,
        "Substack publish-now is unavailable because the submission processor is not live",
        "processor_unavailable",
      );
    }
  }
  let platforms = target === "meta" ? body.platforms : [];
  if (!Array.isArray(platforms)) throw new HTTPError(400, "platforms must be an array", "invalid_input");
  platforms = [...new Set(platforms.map((value) => cleanString(value, "platform", 20)))];
  if (platforms.some((platform) => !PLATFORMS.has(platform))) {
    throw new HTTPError(400, "unknown Meta platform", "invalid_input");
  }
  if (target === "meta" && platforms.length === 0) platforms = ["threads"];
  const idempotencyKey = cleanString(
    request.headers.get("Idempotency-Key") || body.idempotency_key,
    "idempotency_key",
    80,
    true,
  );
  if (!/^[A-Za-z0-9_-]{8,80}$/.test(idempotencyKey)) {
    throw new HTTPError(400, "invalid idempotency_key", "invalid_input");
  }
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  try {
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO submissions(
          id,idempotency_key,target,source_type,content,note,platforms_json,
          requested_mode,status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)`,
      ).bind(
        id,
        idempotencyKey,
        target,
        sourceType,
        content,
        note,
        JSON.stringify(platforms),
        mode,
        "queued",
        now,
        now,
      ),
      env.DB.prepare(
        "INSERT INTO audit_events(actor,action,subject_id,status,metadata_json,created_at) VALUES('owner','create_submission',?,'accepted',?,?)",
      ).bind(id, JSON.stringify({ target, source_type: sourceType, mode, platforms }), now),
    ]);
  } catch (error) {
    if (String(error).includes("UNIQUE constraint failed")) {
      const existing = await env.DB.prepare(
        "SELECT id,target,status,created_at,updated_at FROM submissions WHERE idempotency_key=?",
      )
        .bind(idempotencyKey)
        .first();
      return reply({ ok: true, duplicate: true, submission: existing }, 200, cors);
    }
    throw error;
  }
  // Immediate hand-off; the */5 cron remains the fallback. Awaited (not
  // waitUntil) so the nudge is guaranteed to be attempted before the response,
  // and swallowed internally so it can never fail an accepted submission.
  await nudgeSubmissionPoller(env, id);
  return reply(
    { ok: true, submission: { id, target, status: "queued", mode, platforms, created_at: now } },
    202,
    cors,
  );
}

async function listSubmissions(url, env, cors) {
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit") || 25), 1), 100);
  const { results } = await env.DB.prepare(
    `SELECT id,target,source_type,note,platforms_json,requested_mode,status,
            workflow_run_url,error,external_post_id,result_url,published_at,
            created_at,updated_at
       FROM submissions ORDER BY created_at DESC LIMIT ?`,
  )
    .bind(limit)
    .all();
  return reply(
    {
      ok: true,
      submissions: results.map((row) => ({
        ...row,
        platforms: JSON.parse(row.platforms_json || "[]"),
        platforms_json: undefined,
      })),
    },
    200,
    cors,
  );
}

async function claimNextSubmission(env, cors) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const row = await env.DB.prepare(
      `SELECT * FROM submissions
        WHERE status='queued'
           OR (status='claimed' AND datetime(lease_until) < datetime('now'))
        ORDER BY created_at ASC LIMIT 1`,
    ).first();
    if (!row) return reply({ ok: true, submission: null }, 200, cors);
    const now = new Date().toISOString();
    const lease = new Date(Date.now() + 15 * 60_000).toISOString();
    const updated = await env.DB.prepare(
      `UPDATE submissions SET status='claimed',claimed_at=?,lease_until=?,updated_at=?
        WHERE id=? AND (status='queued' OR (status='claimed' AND datetime(lease_until) < datetime('now')))`,
    )
      .bind(now, lease, now, row.id)
      .run();
    if (Number(updated.meta?.changes || 0) === 1) {
      await audit(env, "service", "claim_submission", row.id, "claimed", { lease_until: lease });
      return reply(
        {
          ok: true,
          submission: {
            id: row.id,
            target: row.target,
            source_type: row.source_type,
            content: row.content,
            note: row.note,
            platforms: JSON.parse(row.platforms_json || "[]"),
            mode: row.requested_mode,
            lease_until: lease,
          },
        },
        200,
        cors,
      );
    }
  }
  throw new HTTPError(409, "Could not claim queued submission", "claim_conflict");
}

async function updateSubmissionStatus(request, env, id, cors) {
  const body = await bodyJson(request, 20_000);
  const status = cleanString(body.status, "status", 30, true);
  const allowed = new Set([
    "dispatched",
    "processing",
    "content_queued",
    "source_queued",
    "draft_created",
    "published",
    "partial",
    "quality_held",
    "failed",
    "rejected",
  ]);
  if (!allowed.has(status)) throw new HTTPError(400, "invalid status", "invalid_input");
  const existing = await env.DB.prepare(
    "SELECT target,requested_mode,status,updated_at FROM submissions WHERE id=?",
  ).bind(id).first();
  if (!existing) throw new HTTPError(404, "submission not found", "not_found");
  if (existing.status === status) {
    return reply({ ok: true, duplicate: true, id, status, updated_at: existing.updated_at }, 200, cors);
  }
  if (status === "draft_created" && existing.target !== "substack") {
    throw new HTTPError(409, "draft_created is only valid for Substack", "invalid_transition");
  }
  if (status === "published" && existing.target === "substack" && existing.requested_mode !== "publish_now") {
    throw new HTTPError(409, "published requires a Substack publish_now request", "invalid_transition");
  }
  if (status === "partial" && existing.target === "substack" && existing.requested_mode !== "publish_now") {
    throw new HTTPError(409, "partial requires a Substack publish_now request", "invalid_transition");
  }
  if (status === "quality_held" && existing.target !== "meta") {
    throw new HTTPError(409, "quality_held is only valid for Meta", "invalid_transition");
  }
  const transitions = {
    queued: new Set(["claimed", "rejected", "failed"]),
    claimed: new Set(["dispatched", "processing", "rejected", "failed"]),
    dispatched: new Set(["processing", "content_queued", "source_queued", "published", "partial", "quality_held", "draft_created", "failed"]),
    processing: new Set(["content_queued", "source_queued", "published", "partial", "quality_held", "draft_created", "failed"]),
    content_queued: new Set(["processing", "published", "partial", "quality_held", "failed"]),
    source_queued: new Set(["processing", "draft_created", "published", "partial", "failed"]),
    partial: new Set(["processing", "published", "quality_held", "failed"]),
    quality_held: new Set(["processing", "published", "partial", "failed"]),
  };
  if (!transitions[existing.status]?.has(status)) {
    throw new HTTPError(
      409,
      `invalid status transition: ${existing.status} -> ${status}`,
      "invalid_transition",
    );
  }
  const workflowRunUrl = cleanString(body.workflow_run_url, "workflow_run_url", 500);
  const error = cleanString(body.error, "error", 2000);
  const externalPostId = cleanString(body.external_post_id, "external_post_id", 200);
  const resultUrl = cleanString(body.result_url, "result_url", 1000);
  const publishedAt = cleanString(body.published_at, "published_at", 60);
  if (existing.target === "substack" && status === "published") {
    if (!externalPostId || !resultUrl || !publishedAt || !/^https:\/\//.test(resultUrl)) {
      throw new HTTPError(
        409,
        "Substack publish-now requires public post evidence",
        "publication_evidence_required",
      );
    }
  }
  if (existing.target === "substack" && status === "partial" && !externalPostId) {
    throw new HTTPError(409, "Substack partial requires a remote draft id", "draft_evidence_required");
  }
  const now = new Date().toISOString();
  const result = await env.DB.prepare(
    `UPDATE submissions
        SET status=?,workflow_run_url=?,error=?,external_post_id=?,result_url=?,
            published_at=?,lease_until=NULL,updated_at=?
      WHERE id=?`,
  )
    .bind(
      status,
      workflowRunUrl || null,
      error || null,
      externalPostId || null,
      resultUrl || null,
      publishedAt || null,
      now,
      id,
    )
    .run();
  if (Number(result.meta?.changes || 0) !== 1) throw new HTTPError(409, "status update conflict", "update_conflict");
  await audit(env, "service", "update_submission", id, status, {
    workflow_run_url: workflowRunUrl || null,
    has_error: Boolean(error),
  });
  return reply({ ok: true, id, status, updated_at: now }, 200, cors);
}

async function recordServiceEvent(request, env, cors) {
  const body = await bodyJson(request, 20_000);
  const action = cleanString(body.action, "action", 80, true);
  const subjectId = cleanString(body.subject_id, "subject_id", 100);
  const status = cleanString(body.status, "status", 40, true);
  await audit(env, "service", action, subjectId || null, status, body.metadata || {});
  return reply({ ok: true }, 202, cors);
}

function listField(body, name) {
  const value = body[name] ?? [];
  if (!Array.isArray(value)) throw new HTTPError(400, `${name} must be an array`, "invalid_input");
  return value;
}

function integer(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : fallback;
}

function nullableInteger(value) {
  if (value === null || value === undefined || value === "") return null;
  return integer(value, 0);
}

function jsonValue(value, max = 20_000) {
  const encoded = JSON.stringify(value ?? {});
  if (encoded.length > max) throw new HTTPError(400, "JSON field is too large", "invalid_input");
  return encoded;
}

async function syncOperationalData(request, env, cors) {
  const body = await bodyJson(request, 500_000);
  const groups = {
    automation: listField(body, "automation"),
    posts: listField(body, "posts"),
    substack_drafts: listField(body, "substack_drafts"),
    engagement: listField(body, "engagement"),
    quality: listField(body, "quality"),
    experiments: listField(body, "experiments"),
    knowledge: listField(body, "knowledge"),
    audience: listField(body, "audience"),
    health: listField(body, "health"),
    proposals: listField(body, "proposals"),
  };
  const total = Object.values(groups).reduce((sum, rows) => sum + rows.length, 0);
  if (total === 0) return reply({ ok: true, synced: 0 }, 200, cors);
  if (total > 100) throw new HTTPError(413, "A sync batch may contain at most 100 rows", "batch_too_large");

  const statements = [];
  const counts = {};
  for (const row of groups.automation) {
    const id = cleanString(row.id, "automation.id", 20, true);
    const mode = cleanString(row.mode, "automation.mode", 20, true);
    const processor = cleanString(
      row.submission_processor,
      "automation.submission_processor",
      20,
      true,
    );
    if (id !== "runtime" || !new Set(["paused", "recovery", "live"]).has(mode)) {
      throw new HTTPError(400, "invalid automation state", "invalid_input");
    }
    if (!new Set(["paused", "live"]).has(processor)) {
      throw new HTTPError(400, "invalid submission processor state", "invalid_input");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO automation_state(id,mode,submission_processor,source,detail,updated_at)
         VALUES(?,?,?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET mode=excluded.mode,
           submission_processor=excluded.submission_processor,source=excluded.source,
           detail=excluded.detail,updated_at=excluded.updated_at`,
      ).bind(
        id,
        mode,
        processor,
        cleanString(row.source, "automation.source", 80, true),
        cleanString(row.detail, "automation.detail", 1000),
        cleanString(row.updated_at, "automation.updated_at", 60, true),
      ),
    );
  }
  counts.automation = groups.automation.length;

  for (const row of groups.substack_drafts) {
    const kind = cleanString(row.editorial_kind, "substack.editorial_kind", 20, true);
    const status = cleanString(row.status, "substack.status", 20, true);
    const remoteDraftId = cleanString(row.remote_draft_id, "substack.remote_draft_id", 200);
    const remotePostId = cleanString(row.remote_post_id, "substack.remote_post_id", 200);
    const publicUrl = cleanString(row.public_url, "substack.public_url", 1000);
    const publishedAt = cleanString(row.published_at, "substack.published_at", 60);
    if (!new Set(["submission", "podcast", "company", "editorial"]).has(kind)) {
      throw new HTTPError(400, "invalid Substack editorial kind", "invalid_input");
    }
    if (!new Set(["published", "partial", "draft_created", "local_written", "unknown"]).has(status)) {
      throw new HTTPError(400, "invalid Substack draft status", "invalid_input");
    }
    if (status === "published" && (!remotePostId || !publicUrl || !publishedAt || !/^https:\/\//.test(publicUrl))) {
      throw new HTTPError(409, "Substack published sync requires public post evidence", "publication_evidence_required");
    }
    if (status === "partial" && !remoteDraftId) {
      throw new HTTPError(409, "Substack partial sync requires a remote draft id", "draft_evidence_required");
    }
    const updatedAt = cleanString(
      row.published_at || row.drafted_at || row.written_at || new Date().toISOString(),
      "substack.updated_at",
      60,
      true,
    );
    statements.push(
      env.DB.prepare(
        `INSERT INTO substack_drafts(
          id,source_id,submission_id,editorial_kind,source_type,source_title,
          source_url,remote_draft_id,remote_post_id,public_url,status,written_at,
          drafted_at,published_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET submission_id=excluded.submission_id,
          editorial_kind=excluded.editorial_kind,source_type=excluded.source_type,
          source_title=excluded.source_title,source_url=excluded.source_url,
          remote_draft_id=excluded.remote_draft_id,
          remote_post_id=excluded.remote_post_id,public_url=excluded.public_url,
          status=excluded.status,
          written_at=excluded.written_at,drafted_at=excluded.drafted_at,
          published_at=excluded.published_at,
          updated_at=excluded.updated_at`,
      ).bind(
        cleanString(row.id, "substack.id", 160, true),
        cleanString(row.source_id, "substack.source_id", 200, true),
        cleanString(row.submission_id, "substack.submission_id", 160),
        kind,
        cleanString(row.source_type, "substack.source_type", 40, true),
        cleanString(row.source_title, "substack.source_title", 500, true),
        cleanString(row.source_url, "substack.source_url", 2000),
        remoteDraftId,
        remotePostId,
        publicUrl,
        status,
        cleanString(row.written_at, "substack.written_at", 60),
        cleanString(row.drafted_at, "substack.drafted_at", 60),
        publishedAt,
        updatedAt,
      ),
    );
  }
  counts.substack_drafts = groups.substack_drafts.length;

  for (const row of groups.experiments) {
    const platform = cleanString(row.platform, "experiment.platform", 20, true);
    const experimentType = cleanString(
      row.experiment_type,
      "experiment.type",
      20,
      true,
    );
    const contentFormat = cleanString(row.content_format, "experiment.format", 20, true);
    const actualFormat = cleanString(row.actual_format, "experiment.actual_format", 20);
    if (!PLATFORMS.has(platform)) {
      throw new HTTPError(400, "unknown experiment platform", "invalid_input");
    }
    if (!new Set(["interest", "trust", "utility", "format"]).has(experimentType)) {
      throw new HTTPError(400, "unknown experiment type", "invalid_input");
    }
    if (!new Set(["feed", "carousel", "reel"]).has(contentFormat)) {
      throw new HTTPError(400, "invalid experiment format", "invalid_input");
    }
    if (actualFormat && !new Set(["feed", "carousel", "reel"]).has(actualFormat)) {
      throw new HTTPError(400, "invalid actual experiment format", "invalid_input");
    }
    const rawBaseline = row.baseline_primary_value;
    const baselineValue = rawBaseline === null || rawBaseline === undefined
      ? null
      : Number(rawBaseline);
    if (baselineValue !== null && !Number.isFinite(baselineValue)) {
      throw new HTTPError(400, "invalid experiment baseline", "invalid_input");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO recovery_experiments(
          id,draft_id,platform,experiment_type,hypothesis,baseline_followers,
          baseline_primary_metric,baseline_primary_value,baseline_captured_at,
          content_format,actual_format,actual_format_at,topic,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET experiment_type=excluded.experiment_type,
          hypothesis=excluded.hypothesis,baseline_followers=excluded.baseline_followers,
          baseline_primary_metric=excluded.baseline_primary_metric,
          baseline_primary_value=excluded.baseline_primary_value,
          baseline_captured_at=excluded.baseline_captured_at,
          content_format=excluded.content_format,actual_format=excluded.actual_format,
          actual_format_at=excluded.actual_format_at,topic=excluded.topic,
          updated_at=excluded.updated_at`,
      ).bind(
        cleanString(row.id, "experiment.id", 200, true),
        cleanString(row.draft_id, "experiment.draft_id", 160, true),
        platform,
        experimentType,
        cleanString(row.hypothesis, "experiment.hypothesis", 1500, true),
        nullableInteger(row.baseline_followers),
        cleanString(row.baseline_primary_metric, "experiment.baseline_metric", 40, true),
        baselineValue,
        cleanString(row.baseline_captured_at, "experiment.baseline_at", 60, true),
        contentFormat,
        actualFormat || null,
        cleanString(row.actual_format_at, "experiment.actual_format_at", 60) || null,
        cleanString(row.topic, "experiment.topic", 100) || null,
        cleanString(row.created_at, "experiment.created_at", 60, true),
        new Date().toISOString(),
      ),
    );
  }
  counts.experiments = groups.experiments.length;

  for (const row of groups.posts) {
    const platform = cleanString(row.platform, "platform", 20, true);
    if (!PLATFORMS.has(platform)) throw new HTTPError(400, "unknown post platform", "invalid_input");
    const status = cleanString(row.status, "status", 20, true);
    if (!new Set(["planned", "published", "failed", "deleted", "unknown"]).has(status)) {
      throw new HTTPError(400, "invalid post status", "invalid_input");
    }
    const format = cleanString(row.format, "format", 20) || "feed";
    if (!new Set(["feed", "carousel", "reel"]).has(format)) {
      throw new HTTPError(400, "invalid post format", "invalid_input");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO platform_posts(
          id,draft_id,submission_id,platform,format,platform_post_id,status,
          title,topic,source_url,posted_at,created_at,updated_at
        ) VALUES(?, ?, (SELECT id FROM submissions WHERE id=?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          draft_id=excluded.draft_id,submission_id=excluded.submission_id,
          format=excluded.format,platform_post_id=excluded.platform_post_id,
          status=excluded.status,
          title=excluded.title,topic=excluded.topic,source_url=excluded.source_url,
          posted_at=excluded.posted_at,updated_at=excluded.updated_at`,
      ).bind(
        cleanString(row.id, "post.id", 160, true),
        cleanString(row.draft_id, "draft_id", 160) || null,
        cleanString(row.submission_id, "submission_id", 160) || null,
        platform,
        format,
        cleanString(row.platform_post_id, "platform_post_id", 200) || null,
        status,
        cleanString(row.title, "title", 500) || null,
        cleanString(row.topic, "topic", 100) || null,
        cleanString(row.source_url, "source_url", 2000) || null,
        cleanString(row.posted_at, "posted_at", 60) || null,
        cleanString(row.created_at, "created_at", 60) || new Date().toISOString(),
        cleanString(row.updated_at, "updated_at", 60) || new Date().toISOString(),
      ),
    );
  }
  counts.posts = groups.posts.length;

  for (const row of groups.engagement) {
    const platform = cleanString(row.platform, "platform", 20, true);
    if (!PLATFORMS.has(platform)) throw new HTTPError(400, "unknown engagement platform", "invalid_input");
    statements.push(
      env.DB.prepare(
        `INSERT INTO engagement_snapshots(
          platform,platform_post_id,captured_at,post_age_hours,views,reach,likes,
          comments,shares,saves,replies,reposts,quotes,clicks,
          metric_status,raw_summary_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(platform,platform_post_id,captured_at) DO UPDATE SET
          post_age_hours=excluded.post_age_hours,views=excluded.views,reach=excluded.reach,
          likes=excluded.likes,comments=excluded.comments,shares=excluded.shares,
          saves=excluded.saves,replies=excluded.replies,reposts=excluded.reposts,
          quotes=excluded.quotes,clicks=excluded.clicks,
          metric_status=excluded.metric_status,
          raw_summary_json=excluded.raw_summary_json`,
      ).bind(
        platform,
        cleanString(row.platform_post_id, "platform_post_id", 200, true),
        cleanString(row.captured_at, "captured_at", 60, true),
        nullableInteger(row.post_age_hours),
        integer(row.views), integer(row.reach), integer(row.likes), integer(row.comments),
        integer(row.shares), integer(row.saves), integer(row.replies),
        integer(row.reposts), integer(row.quotes),
        integer(row.clicks),
        cleanString(row.metric_status, "metric_status", 60) || "unknown",
        jsonValue(row.raw_summary),
      ),
    );
  }
  counts.engagement = groups.engagement.length;

  for (const row of groups.quality) {
    const platform = cleanString(row.platform, "platform", 20, true);
    if (!PLATFORMS.has(platform)) throw new HTTPError(400, "unknown quality platform", "invalid_input");
    const candidates = integer(row.candidates);
    const evaluated = integer(row.evaluated);
    const passCount = integer(row.pass_count);
    const warnCount = integer(row.warn_count);
    const rewriteCount = integer(row.rewrite_count);
    const blockCount = integer(row.block_count);
    const publishReadyCount = integer(row.publish_ready_count);
    const legacyExcludedCount = integer(row.legacy_excluded_count);
    const coverage = Number(row.evidence_coverage);
    if (!Number.isFinite(coverage) || coverage < 0 || coverage > 1) {
      throw new HTTPError(400, "invalid quality evidence coverage", "invalid_input");
    }
    if (evaluated !== passCount + warnCount + rewriteCount + blockCount) {
      throw new HTTPError(400, "quality decision counts do not reconcile", "invalid_input");
    }
    if (publishReadyCount !== passCount + warnCount || evaluated > candidates) {
      throw new HTTPError(400, "invalid quality aggregate", "invalid_input");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO content_quality_snapshots(
          platform,captured_at,window_days,candidates,evaluated,evidence_coverage,
          pass_count,warn_count,rewrite_count,block_count,publish_ready_count,
          top_issue_codes_json,guard_version,legacy_excluded_count
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(platform,captured_at) DO UPDATE SET
          window_days=excluded.window_days,candidates=excluded.candidates,
          evaluated=excluded.evaluated,evidence_coverage=excluded.evidence_coverage,
          pass_count=excluded.pass_count,warn_count=excluded.warn_count,
          rewrite_count=excluded.rewrite_count,block_count=excluded.block_count,
          publish_ready_count=excluded.publish_ready_count,
          top_issue_codes_json=excluded.top_issue_codes_json,
          guard_version=excluded.guard_version,
          legacy_excluded_count=excluded.legacy_excluded_count`,
      ).bind(
        platform,
        cleanString(row.captured_at, "captured_at", 60, true),
        integer(row.window_days),
        candidates,
        evaluated,
        coverage,
        passCount,
        warnCount,
        rewriteCount,
        blockCount,
        publishReadyCount,
        jsonValue(row.top_issue_codes, 5000),
        cleanString(row.guard_version, "guard_version", 80, true),
        legacyExcludedCount,
      ),
    );
  }
  counts.quality = groups.quality.length;

  for (const row of groups.knowledge) {
    statements.push(
      env.DB.prepare(
        `INSERT INTO knowledge_items(
          id,source_type,source_url,title,topic,evidence_summary,status,
          first_seen_at,last_used_at,use_count
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          source_type=excluded.source_type,source_url=excluded.source_url,
          title=excluded.title,topic=excluded.topic,evidence_summary=excluded.evidence_summary,
          status=excluded.status,last_used_at=excluded.last_used_at,use_count=excluded.use_count`,
      ).bind(
        cleanString(row.id, "knowledge.id", 160, true),
        cleanString(row.source_type, "source_type", 40, true),
        cleanString(row.source_url, "source_url", 2000) || null,
        cleanString(row.title, "title", 1000, true),
        cleanString(row.topic, "topic", 100) || null,
        cleanString(row.evidence_summary, "evidence_summary", 2000) || null,
        cleanString(row.status, "status", 30) || "active",
        cleanString(row.first_seen_at, "first_seen_at", 60, true),
        cleanString(row.last_used_at, "last_used_at", 60) || null,
        integer(row.use_count),
      ),
    );
  }
  counts.knowledge = groups.knowledge.length;

  for (const row of groups.audience) {
    const platform = cleanString(row.platform, "platform", 20, true);
    if (!PLATFORMS.has(platform)) throw new HTTPError(400, "unknown audience platform", "invalid_input");
    statements.push(
      env.DB.prepare(
        `INSERT INTO audience_snapshots(
          platform,captured_at,followers,followers_delta_7d,source,metric_status,raw_summary_json
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(platform,captured_at) DO UPDATE SET
          followers=excluded.followers,followers_delta_7d=excluded.followers_delta_7d,
          source=excluded.source,metric_status=excluded.metric_status,
          raw_summary_json=excluded.raw_summary_json`,
      ).bind(
        platform,
        cleanString(row.captured_at, "captured_at", 60, true),
        nullableInteger(row.followers),
        row.followers_delta_7d === null || row.followers_delta_7d === undefined
          ? null : Math.round(Number(row.followers_delta_7d) || 0),
        cleanString(row.source, "source", 60) || "platform_api",
        cleanString(row.metric_status, "metric_status", 60) || "unknown",
        jsonValue(row.raw_summary),
      ),
    );
  }
  counts.audience = groups.audience.length;

  for (const row of groups.health) {
    const platform = cleanString(row.platform, "platform", 20, true);
    if (![...PLATFORMS, "system"].includes(platform)) {
      throw new HTTPError(400, "unknown health platform", "invalid_input");
    }
    const status = cleanString(row.status, "status", 20, true);
    if (!new Set(["healthy", "degraded", "unknown", "error"]).has(status)) {
      throw new HTTPError(400, "invalid health status", "invalid_input");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO data_health_snapshots(platform,metric,status,detail,captured_at)
         VALUES(?,?,?,?,?)
         ON CONFLICT(platform,metric,captured_at) DO UPDATE SET
           status=excluded.status,detail=excluded.detail`,
      ).bind(
        platform,
        cleanString(row.metric, "metric", 100, true),
        status,
        cleanString(row.detail, "detail", 2000),
        cleanString(row.captured_at, "captured_at", 60, true),
      ),
    );
  }
  counts.health = groups.health.length;

  for (const row of groups.proposals) {
    const status = cleanString(row.status, "status", 30, true);
    if (!new Set(["proposed", "approved", "rejected", "applied", "superseded"]).has(status)) {
      throw new HTTPError(400, "invalid proposal status", "invalid_input");
    }
    const proposalId = cleanString(row.id, "proposal.id", 160, true);
    const ownerDecision = cleanString(row.owner_decision, "owner_decision", 20);
    if (ownerDecision && !new Set(["approved", "rejected"]).has(ownerDecision)) {
      throw new HTTPError(400, "invalid owner decision", "invalid_input");
    }
    const existing = await env.DB.prepare(
      "SELECT status FROM learning_proposals WHERE id=?",
    ).bind(proposalId).first();
    if (status === "applied" && existing?.status !== "applied" && ownerDecision !== "approved") {
      throw new HTTPError(409, "applied proposal lacks owner approval", "invalid_transition");
    }
    if (status === "applied" && existing?.status === "proposed") {
      throw new HTTPError(409, "proposal must be approved before applied", "invalid_transition");
    }
    if (
      existing?.status === "rejected" && !new Set(["proposed", "rejected"]).has(status)
    ) {
      throw new HTTPError(409, "rejected proposal cannot transition", "invalid_transition");
    }
    if (existing?.status === "approved" && status === "rejected") {
      throw new HTTPError(409, "approved proposal cannot be rejected by service sync", "invalid_transition");
    }
    if (
      existing?.status === "superseded" && !new Set(["proposed", "superseded"]).has(status)
    ) {
      throw new HTTPError(409, "superseded proposal cannot transition", "invalid_transition");
    }
    statements.push(
      env.DB.prepare(
        `INSERT INTO learning_proposals(
          id,kind,status,summary,evidence_json,proposed_change_json,created_at,
          decision_comment,decided_at,applied_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          kind=excluded.kind,
          status=CASE
            WHEN learning_proposals.status='applied' THEN 'applied'
            WHEN learning_proposals.status='rejected' THEN 'rejected'
            WHEN learning_proposals.status='superseded' THEN 'superseded'
            WHEN learning_proposals.status='approved' AND excluded.status='applied' THEN 'applied'
            WHEN learning_proposals.status='approved' THEN 'approved'
            WHEN excluded.status='applied' THEN 'applied'
            ELSE excluded.status
          END,
          summary=excluded.summary,
          evidence_json=excluded.evidence_json,proposed_change_json=excluded.proposed_change_json,
          decision_comment=COALESCE(learning_proposals.decision_comment,excluded.decision_comment),
          decided_at=COALESCE(excluded.decided_at,learning_proposals.decided_at),
          applied_at=COALESCE(excluded.applied_at,learning_proposals.applied_at)`,
      ).bind(
        proposalId,
        cleanString(row.kind, "kind", 100, true),
        status,
        cleanString(row.summary, "summary", 2000, true),
        jsonValue(row.evidence),
        jsonValue(row.proposed_change),
        cleanString(row.created_at, "created_at", 60, true),
        cleanString(row.decision_comment, "decision_comment", 1000) || null,
        cleanString(row.decided_at, "decided_at", 60) || null,
        cleanString(row.applied_at, "applied_at", 60) || null,
      ),
    );
  }
  counts.proposals = groups.proposals.length;

  await env.DB.batch(statements);
  await audit(env, "service", "sync_operational_data", null, "accepted", counts);
  return reply({ ok: true, synced: total, counts }, 202, cors);
}

async function audit(env, actor, action, subjectId, status, metadata) {
  await env.DB.prepare(
    "INSERT INTO audit_events(actor,action,subject_id,status,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
  )
    .bind(
      actor,
      action,
      subjectId,
      status,
      JSON.stringify(metadata || {}),
      new Date().toISOString(),
    )
    .run();
}

async function dashboard(env, cors) {
  const [
    submissions, recentSubmissions, posts, recentPosts, substackDrafts,
    recentSubstackDrafts, engagement, engagementTrend,
    audience, quality, proposals, knowledgeTopics, knowledgeTotal, health,
    runtimeState, recoveryExperiments, events,
  ] = await env.DB.batch([
    env.DB.prepare("SELECT target,status,COUNT(*) AS count FROM submissions GROUP BY target,status"),
    env.DB.prepare(`SELECT id,target,source_type,note,platforms_json,requested_mode,status,
      workflow_run_url,error,external_post_id,result_url,published_at,created_at,updated_at
      FROM submissions ORDER BY created_at DESC LIMIT 25`),
    env.DB.prepare("SELECT platform,status,COUNT(*) AS count,MAX(posted_at) AS last_posted_at FROM platform_posts GROUP BY platform,status"),
    env.DB.prepare(`WITH ranked_engagement AS (
      SELECT *,ROW_NUMBER() OVER(
        PARTITION BY platform,platform_post_id ORDER BY captured_at DESC
      ) AS rn FROM engagement_snapshots
    )
    SELECT p.id,p.draft_id,p.submission_id,p.platform,p.format,p.platform_post_id,
      p.status,p.title,p.topic,p.source_url,p.posted_at,
      e.views,e.reach,e.clicks,e.likes,e.comments,e.shares,e.saves,
      e.replies,e.reposts,e.quotes,e.metric_status,e.captured_at AS metrics_captured_at
      FROM platform_posts p LEFT JOIN ranked_engagement e
        ON e.platform=p.platform AND e.platform_post_id=p.platform_post_id AND e.rn=1
      ORDER BY COALESCE(p.posted_at,p.created_at) DESC LIMIT 30`),
    env.DB.prepare(`SELECT editorial_kind,status,COUNT(*) AS count,
      MAX(COALESCE(published_at,drafted_at)) AS last_drafted_at FROM substack_drafts
      GROUP BY editorial_kind,status ORDER BY editorial_kind,status`),
    env.DB.prepare(`SELECT id,submission_id,editorial_kind,source_type,source_title,
      source_url,remote_draft_id,remote_post_id,public_url,status,written_at,
      drafted_at,published_at,updated_at FROM substack_drafts
      ORDER BY COALESCE(published_at,drafted_at,written_at,updated_at) DESC LIMIT 30`),
    env.DB.prepare(LATEST_ENGAGEMENT_SQL),
    env.DB.prepare(`WITH daily_post AS (
      SELECT platform,platform_post_id,substr(captured_at,1,10) AS day,MAX(captured_at) AS captured_at
      FROM engagement_snapshots
      WHERE datetime(captured_at) >= datetime('now','-30 day')
      GROUP BY platform,platform_post_id,substr(captured_at,1,10)
    )
    SELECT e.platform,d.day,SUM(e.views) AS views,SUM(e.reach) AS reach,
      SUM(e.clicks) AS clicks,
      SUM(e.likes+e.comments+e.shares+e.saves+e.replies+e.reposts+e.quotes+e.clicks) AS actions
    FROM daily_post d JOIN engagement_snapshots e
      ON e.platform=d.platform AND e.platform_post_id=d.platform_post_id AND e.captured_at=d.captured_at
    GROUP BY e.platform,d.day ORDER BY d.day`),
    env.DB.prepare(LATEST_AUDIENCE_SQL),
    env.DB.prepare(`WITH ranked AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY platform ORDER BY captured_at DESC) AS rn
      FROM content_quality_snapshots
    ) SELECT platform,captured_at,window_days,candidates,evaluated,evidence_coverage,
      pass_count,warn_count,rewrite_count,block_count,publish_ready_count,
      top_issue_codes_json,guard_version,legacy_excluded_count FROM ranked WHERE rn=1`),
    env.DB.prepare(`SELECT id,kind,status,summary,evidence_json,proposed_change_json,
      decision_comment,created_at,decided_at,applied_at
      FROM learning_proposals ORDER BY created_at DESC LIMIT 20`),
    env.DB.prepare(`SELECT COALESCE(topic,'unclassified') AS topic,COUNT(*) AS items,
      MAX(last_used_at) AS last_used_at,SUM(use_count) AS uses
      FROM knowledge_items WHERE status='active' GROUP BY COALESCE(topic,'unclassified')
      ORDER BY items DESC LIMIT 20`),
    env.DB.prepare("SELECT COUNT(*) AS items,SUM(use_count) AS uses,MAX(last_used_at) AS last_used_at FROM knowledge_items WHERE status='active'"),
    env.DB.prepare(`WITH ranked AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY platform,metric ORDER BY captured_at DESC) AS rn
      FROM data_health_snapshots
    ) SELECT platform,metric,status,detail,captured_at FROM ranked WHERE rn=1 ORDER BY platform,metric`),
    env.DB.prepare("SELECT mode,submission_processor,source,detail,updated_at FROM automation_state WHERE id='runtime'"),
    env.DB.prepare(`WITH ranked_posts AS (
      SELECT *,ROW_NUMBER() OVER(
        PARTITION BY draft_id,platform ORDER BY COALESCE(posted_at,updated_at) DESC
      ) AS rn FROM platform_posts
    ), ranked_engagement AS (
      SELECT *,ROW_NUMBER() OVER(
        PARTITION BY platform,platform_post_id ORDER BY post_age_hours DESC,captured_at DESC
      ) AS rn FROM engagement_snapshots
    ), ranked_audience AS (
      SELECT *,ROW_NUMBER() OVER(PARTITION BY platform ORDER BY captured_at DESC) AS rn
      FROM audience_snapshots
    )
    SELECT r.*,p.status AS publish_status,p.platform_post_id,p.posted_at,
      e.post_age_hours,e.views,e.reach,e.clicks,e.likes,e.comments,e.shares,
      e.saves,e.replies,e.reposts,e.quotes,e.metric_status,e.captured_at AS result_captured_at,
      a.followers AS current_followers,a.captured_at AS followers_captured_at
    FROM recovery_experiments r
    LEFT JOIN ranked_posts p ON p.draft_id=r.draft_id AND p.platform=r.platform AND p.rn=1
    LEFT JOIN ranked_engagement e ON e.platform=p.platform
      AND e.platform_post_id=p.platform_post_id AND e.rn=1
    LEFT JOIN ranked_audience a ON a.platform=r.platform AND a.rn=1
    ORDER BY r.created_at DESC LIMIT 50`),
    env.DB.prepare("SELECT actor,action,subject_id,status,created_at FROM audit_events ORDER BY id DESC LIMIT 50"),
  ]);
  const runtime = runtimeState.results[0] || {};
  let editorialContract = {};
  try {
    const detail = JSON.parse(runtime.detail || "{}");
    editorialContract = detail.substack || {};
  } catch (_error) {
    editorialContract = {};
  }
  return reply(
    {
      ok: true,
      version: API_VERSION,
      generated_at: new Date().toISOString(),
      automation: {
        ...(await currentRuntime(env)),
        source: runtime.source || "worker_fallback",
        updated_at: runtime.updated_at || null,
      },
      submissions: submissions.results,
      recent_submissions: recentSubmissions.results.map((row) => ({
        ...row,
        platforms: JSON.parse(row.platforms_json || "[]"),
        platforms_json: undefined,
      })),
      platforms: posts.results,
      recent_posts: recentPosts.results,
      substack: substackDrafts.results,
      recent_substack_drafts: recentSubstackDrafts.results,
      editorial_contract: editorialContract,
      engagement: engagement.results,
      engagement_trend: engagementTrend.results,
      audience: audience.results,
      content_quality: quality.results.map((row) => ({
        ...row,
        top_issue_codes: JSON.parse(row.top_issue_codes_json || "[]"),
        top_issue_codes_json: undefined,
      })),
      learning_proposals: proposals.results.map((row) => ({
        ...row,
        evidence: JSON.parse(row.evidence_json || "{}"),
        proposed_change: JSON.parse(row.proposed_change_json || "{}"),
        evidence_json: undefined,
        proposed_change_json: undefined,
      })),
      knowledge: {
        total: knowledgeTotal.results[0] || { items: 0, uses: 0, last_used_at: null },
        topics: knowledgeTopics.results,
      },
      data_health: health.results,
      recovery: {
        experiments: recoveryExperiments.results.map(recoveryExperimentView),
      },
      recent_events: events.results,
    },
    200,
    cors,
  );
}

function recoveryExperimentView(row) {
  const actions = integer(row.likes) + integer(row.comments) + integer(row.shares)
    + integer(row.saves) + integer(row.replies) + integer(row.reposts)
    + integer(row.quotes) + integer(row.clicks);
  const primaryMetric = row.baseline_primary_metric;
  const primaryValue = primaryMetric === "clicks"
    ? integer(row.clicks)
    : primaryMetric === "reach"
      ? integer(row.reach)
      : integer(row.views);
  const followerDelta = row.current_followers === null || row.current_followers === undefined
    || row.baseline_followers === null || row.baseline_followers === undefined
    ? null
    : Number(row.current_followers) - Number(row.baseline_followers);
  const bucket = row.post_age_hours === null || row.post_age_hours === undefined
    ? null
    : Number(row.post_age_hours);
  let status = "planned";
  let recommendationCode = "wait_for_publish";
  let recommendation = "等待真實 platform post ID；不要擴大頻率。";
  if (row.publish_status === "published" && row.platform_post_id) {
    status = bucket === null ? "published" : (bucket >= 168 ? "complete" : "measuring");
    if (bucket === null) {
      recommendationCode = "wait_for_1h";
      recommendation = "貼文已發布；等待 1h 平台回讀。";
    } else if (row.metric_status !== "ok") {
      recommendationCode = "fix_measurement";
      recommendation = "量測退化；先修資料，不把零值解讀為觀眾沒興趣。";
    } else if (bucket < 24) {
      recommendationCode = "continue_measuring";
      recommendation = "只有早期訊號；至少量到 24h，再判斷內容方向。";
    } else if (row.baseline_primary_value === null || row.baseline_primary_value === undefined) {
      const promising = (followerDelta !== null && followerDelta > 0) || primaryValue > 0 || actions > 0;
      if (bucket >= 168) {
        recommendationCode = promising ? "continue" : "stop_or_redesign";
        recommendation = promising
          ? "冷啟動已有非零訊號；保留此實驗，再累積至少 3 篇。"
          : "168h 仍無非零訊號；停止同格式，重新設計下一個實驗。";
      } else {
        recommendationCode = "collect_168h";
        recommendation = "舊資料不可當可靠基準；收滿 168h 與 follower delta。";
      }
    } else if (bucket >= 168) {
      const beatsBaseline = primaryValue >= Number(row.baseline_primary_value);
      const followerSafe = followerDelta === null || followerDelta >= 0;
      recommendationCode = beatsBaseline && followerSafe ? "continue" : "revise";
      recommendation = beatsBaseline && followerSafe
        ? "達到歷史中位基準且未傷害粉絲；保留方向，再累積樣本。"
        : "未達歷史中位基準或粉絲下降；降低頻率並改寫假設。";
    } else {
      recommendationCode = primaryValue >= Number(row.baseline_primary_value)
        ? "promising_collect_168h"
        : "collect_168h";
      recommendation = primaryValue >= Number(row.baseline_primary_value)
        ? "24h 已達基準；先收滿 168h，不提早加頻率。"
        : "24h 尚未達基準；等 168h 再決定停止或改寫。";
    }
  }
  return {
    ...row,
    status,
    actions,
    primary_value: primaryValue,
    follower_delta: followerDelta,
    recommendation_code: recommendationCode,
    recommendation,
  };
}
