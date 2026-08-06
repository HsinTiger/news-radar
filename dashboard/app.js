import {
  DEFAULT_EDITORIAL_CONTRACT,
  PLATFORM_ORDER,
  TOKEN_KEY,
  buildSubmissionPayload,
  buildTrendSeries,
  deriveAttention,
  ensureExpectedSchedulerHealth,
  forgetToken,
  isPendingStatus,
  normalizeEditorialContract,
  platformSnapshot,
  readStoredToken,
  rememberToken,
  summarizeRecentContent,
} from "./ops-core.mjs";

const API = "https://news-radar-submit.smartmmmoney.workers.dev";
const WORKFLOWS_API = "https://api.github.com/repos/HsinTiger/news-radar/actions/runs?per_page=30";
const PLATFORM_META = {
  facebook: {label: "Facebook", short: "FB", color: "#82a9ff"},
  instagram: {label: "Instagram", short: "IG", color: "#df8acb"},
  threads: {label: "Threads", short: "TH", color: "#d8d6cf"},
};
const VIEW_COPY = {
  overview: ["TODAY'S CONTROL ROOM", "營運總覽"],
  substack: ["EDITORIAL OPERATIONS", "Substack"],
  meta: ["META OPERATIONS", "Meta 三平台"],
  health: ["EVIDENCE & HEALTH", "資料健康"],
  submit: ["OWNER INBOX", "新增投稿"],
};
const STATUS_COPY = {
  queued: "等待處理", claimed: "已領取", dispatched: "已派送", processing: "處理中",
  content_queued: "內容已入列", source_queued: "素材已入列", draft_created: "遠端草稿已建立",
  local_written: "本地文章已寫成", published: "已發布", partial: "部分完成",
  quality_held: "品質閘門暫停", failed: "失敗", rejected: "已拒絕", unknown: "未知",
  healthy: "正常", degraded: "降級", error: "錯誤", success: "通過", cancelled: "已取消",
};
const HEALTH_COPY = {
  daily_publish_cadence: "每日發布節奏", latest_post_canary: "最新貼文回讀",
  engagement_api: "互動數據 API", scheduler_delivery: "GitHub 排程送達",
  scheduler_watchdog_dispatch: "排程 watchdog 派送", scheduler_watchdog_delivery: "watchdog 送達",
  substack_draft_worker: "Substack 草稿 worker", substack_legacy_backlog: "Substack 舊佇列",
};
const GOOD_STATUS = new Set(["healthy", "success", "published", "draft_created", "local_written", "complete"]);
const BAD_STATUS = new Set(["error", "failed", "failure", "rejected", "cancelled"]);

const state = {
  publicHealth: null,
  dashboard: null,
  workflows: [],
  currentView: "overview",
  trendMetric: "actions",
  privateError: "",
  refreshTimer: null,
};

const $ = (id) => document.getElementById(id);
const storage = () => ({local: window.localStorage, session: window.sessionStorage});
const currentToken = () => readStoredToken(storage());
const svgNode = (name, attributes = {}) => {
  const item = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, String(value)));
  return item;
};
const node = (name, className, text) => {
  const item = document.createElement(name);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};
const clear = (item) => item.replaceChildren();

class ApiError extends Error {
  constructor(message, status = 0, code = "request_error") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function number(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value, options = {}) {
  const parsed = number(value);
  if (parsed === null) return "未知";
  return new Intl.NumberFormat("zh-TW", {
    notation: Math.abs(parsed) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
    ...options,
  }).format(parsed);
}

function when(value, fallback = "時間未知") {
  if (!value) return fallback;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return fallback;
  return date.toLocaleString("zh-TW", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
}

function age(value) {
  if (!value) return "時間未知";
  const elapsed = Date.now() - Date.parse(value);
  if (!Number.isFinite(elapsed) || elapsed < 0) return when(value);
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 2) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} 小時前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function newest(values) {
  return values.filter(Boolean).sort().at(-1) || null;
}

function statusClass(value) {
  if (GOOD_STATUS.has(value)) return "good";
  if (BAD_STATUS.has(value)) return "bad";
  return "warn";
}

function statusChip(value, override) {
  return node("span", `status-chip ${statusClass(value)}`, override || STATUS_COPY[value] || value || "未知");
}

function showGlobalError(message = "") {
  $("global-error").hidden = !message;
  $("global-error").textContent = message;
}

function showAuthNotice(message = "") {
  $("auth-notice").hidden = !message;
  $("auth-notice").textContent = message;
}

async function publicJson(url) {
  const response = await fetch(url, {headers: {Accept: "application/json"}, cache: "no-store"});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.message || `HTTP ${response.status}`, response.status);
  return body;
}

async function request(path, {token = currentToken(), ...options} = {}) {
  const headers = {Accept: "application/json", ...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API}${path}`, {...options, headers, cache: "no-store"});
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new ApiError("授權未通過或已過期；本機 token 已保留，請重新驗證。", 401, body.code || "unauthorized");
  }
  if (!response.ok) throw new ApiError(body.message || body.error || `HTTP ${response.status}`, response.status, body.code);
  return body;
}

function normalizeWorkflows(payload) {
  return (payload.workflow_runs || []).map((run) => ({
    id: run.id,
    workflowName: run.name || run.display_title || "GitHub workflow",
    conclusion: run.conclusion || run.status || "unknown",
    status: run.status || "unknown",
    createdAt: run.created_at || null,
    updatedAt: run.updated_at || null,
    url: run.html_url || null,
    event: run.event || null,
  }));
}

async function loadPublicData() {
  const [healthResult, workflowResult] = await Promise.allSettled([
    publicJson(`${API}/health`),
    publicJson(WORKFLOWS_API),
  ]);
  if (healthResult.status === "fulfilled") state.publicHealth = healthResult.value;
  if (workflowResult.status === "fulfilled") state.workflows = normalizeWorkflows(workflowResult.value);
  renderRuntime();
  renderWorkflows();
  renderAttention();
  if (healthResult.status === "rejected") {
    showGlobalError(`目前無法讀取公開 runtime：${healthResult.reason.message}`);
  } else {
    showGlobalError("");
  }
}

async function loadPrivateData(token = currentToken(), {quiet = false} = {}) {
  if (!token) {
    state.dashboard = null;
    state.privateError = "";
    renderPrivateViews();
    updateAuthUi();
    return false;
  }
  try {
    state.dashboard = await request("/api/dashboard", {token});
    state.privateError = "";
    showAuthNotice("");
    renderPrivateViews();
    updateAuthUi(true);
    return true;
  } catch (error) {
    state.dashboard = null;
    state.privateError = error.message;
    renderPrivateViews();
    updateAuthUi(false);
    if (!quiet || error.status === 401) showAuthNotice(error.message);
    return false;
  }
}

async function refreshAll() {
  const refresh = $("refresh-all");
  refresh.disabled = true;
  refresh.textContent = "…";
  await Promise.all([loadPublicData(), loadPrivateData(currentToken(), {quiet: true})]);
  refresh.disabled = false;
  refresh.textContent = "↻";
}

function updateAuthUi(verified = Boolean(state.dashboard)) {
  const hasToken = Boolean(currentToken());
  $("open-auth").textContent = verified ? "已解鎖 · 管理" : (hasToken ? "重新驗證" : "解鎖營運資料");
  $("forget-token").hidden = !hasToken;
  $("remember-token").checked = Boolean(localStorage.getItem(TOKEN_KEY));
  $("submit-button").disabled = !verified;
  $("submit-button").textContent = verified ? submissionButtonCopy() : "先解鎖再投稿";
  const connection = $("connection-pill");
  if (verified) {
    connection.dataset.state = "good";
    $("connection-copy").textContent = "營運資料已解鎖";
  } else if (state.publicHealth?.ok) {
    connection.dataset.state = hasToken ? "warn" : "good";
    $("connection-copy").textContent = hasToken ? "公開系統正常 · 授權待驗證" : "公開系統正常";
  } else {
    connection.dataset.state = "bad";
    $("connection-copy").textContent = "公開系統狀態未知";
  }
}

function showView(view, {push = true} = {}) {
  if (!VIEW_COPY[view]) view = "overview";
  state.currentView = view;
  document.querySelectorAll("[data-view]").forEach((section) => {
    const active = section.dataset.view === view;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  });
  document.querySelectorAll("[data-nav]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.nav === view);
    if (button.dataset.nav === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("view-kicker").textContent = VIEW_COPY[view][0];
  $("view-title").textContent = VIEW_COPY[view][1];
  if (push) {
    const url = new URL(window.location.href);
    url.searchParams.set("view", view);
    history.pushState({view}, "", url);
  }
  window.scrollTo({top: 0, behavior: "smooth"});
}

function renderRuntime() {
  const runtime = state.dashboard?.automation || state.publicHealth?.automation || {};
  const mode = runtime.mode || "unknown";
  const label = {live: "正式運行", recovery: "恢復模式", paused: "已暫停", unknown: "狀態未知"}[mode] || mode;
  $("runtime-mode").textContent = label;
  $("runtime-updated").textContent = runtime.updated_at ? `更新於 ${age(runtime.updated_at)}` : "尚無持久化時間證據";
  $("runtime-processor").textContent = runtime.submission_processor === "live" ? "運行中" : (runtime.submission_processor || "未知");
  $("runtime-meta").textContent = runtime.meta_publish_now_ready === true ? "可用" : "不可用";
  $("runtime-substack").textContent = runtime.substack_auto_publish === true ? "開啟" : "關閉（僅草稿）";
  const orb = $("runtime-orb");
  orb.className = `runtime-orb ${mode === "live" ? "good" : (mode === "recovery" ? "warn" : "bad")}`;
  updateAuthUi();
}

function renderAttention() {
  const list = $("attention-list");
  const attentionDashboard = state.dashboard ? {
    ...state.dashboard,
    data_health: ensureExpectedSchedulerHealth(state.dashboard.data_health || []),
  } : {};
  const items = deriveAttention({dashboard: attentionDashboard, workflows: state.workflows});
  clear(list);
  if (!items.length) {
    const empty = node("div", "locked-placeholder", state.dashboard
      ? "目前沒有 workflow 失敗、資料降級或待處理投稿。"
      : "公開 workflow 沒有可見異常；解鎖後才能檢查資料健康與投稿佇列。");
    list.append(empty);
  } else {
    items.slice(0, 6).forEach((item) => {
      const row = node(item.url ? "a" : "div", "attention-item");
      row.dataset.severity = item.severity;
      if (item.url) { row.href = item.url; row.target = "_blank"; row.rel = "noreferrer"; }
      row.append(node("span", "attention-dot"));
      const content = node("div");
      content.append(node("strong", "", item.title), node("p", "", item.detail));
      row.append(content, node("time", "", age(item.observedAt)));
      list.append(row);
    });
  }
  const critical = items.filter((item) => item.severity === "critical").length;
  $("attention-summary").textContent = items.length
    ? `目前有 ${items.length} 項需要辨識，其中 ${critical} 項屬於執行或驗證失敗。請依證據逐項處理，不把單一 workflow 失敗擴大解讀成全系統中斷。`
    : (state.dashboard ? "目前沒有需要立即處理的訊號；仍應以平台回讀證據確認真正送達。" : "公開系統已讀取。解鎖後會補上草稿、數據健康與投稿佇列。 ");
}

function renderDraftPreview() {
  const box = $("overview-drafts");
  clear(box);
  const rows = state.dashboard?.recent_substack_drafts || [];
  if (!state.dashboard) return box.append(node("div", "locked-placeholder", "解鎖後顯示遠端草稿證據。"));
  if (!rows.length) return box.append(node("div", "locked-placeholder", "尚未同步到 Substack 草稿 metadata。這代表未知，不代表零草稿。"));
  rows.slice(0, 4).forEach((row) => {
    const item = node("div", "compact-row");
    const content = node("div");
    content.append(node("strong", "", row.source_title || "未命名草稿"), node("small", "", `${editorialKind(row.editorial_kind)} · ${STATUS_COPY[row.status] || row.status}`));
    item.append(content, node("time", "", age(row.drafted_at || row.written_at || row.updated_at)));
    box.append(item);
  });
}

function renderMetaPreview() {
  const box = $("overview-meta");
  clear(box);
  if (!state.dashboard) return box.append(node("div", "locked-placeholder", "解鎖後顯示 Facebook、Instagram、Threads。"));
  PLATFORM_ORDER.forEach((platform) => {
    const snapshot = platformSnapshot(platform, state.dashboard);
    const item = node("div", "mini-platform");
    item.append(node("span", "", PLATFORM_META[platform].label), node("strong", "", fmt(snapshot.followers)), node("small", "", `7 日 ${deltaCopy(snapshot.followersDelta7d)}`));
    box.append(item);
  });
}

function editorialKind(value) {
  return {podcast: "Podcast", company: "公司分析", submission: "Owner 投稿", editorial: "編輯稿"}[value] || value || "未知類型";
}

function contractScheduleCard(label, time, title, copy) {
  const card = node("article", "schedule-card");
  card.append(node("span", "day-chip", label), node("strong", "", time), node("h3", "", title), node("p", "", copy));
  return card;
}

function renderSubstack() {
  const contract = normalizeEditorialContract(state.dashboard?.editorial_contract || DEFAULT_EDITORIAL_CONTRACT);
  const schedule = $("substack-schedule");
  clear(schedule);
  schedule.append(
    contractScheduleCard("每日", contract.podcast.local_time || "12:00", `Podcast 延伸文 × ${contract.podcast.drafts || 2}`, `候選限最近 ${contract.podcast.candidate_window_days || 7} 天；兩篇集中同批完成，每篇 ${rangeCopy(contract.podcast.target_chars)} 字。`),
    contractScheduleCard("週日", (contract.company.local_time || "Sun 09:00").replace("Sun ", ""), `公司深度文 × ${contract.company.drafts || 1}`, `${contract.company.pick_and_compose ? "選題與寫作合併在同一輪" : "先選題後寫作"}；以週刊深度完成，每篇 ${rangeCopy(contract.company.target_chars)} 字。`),
  );
  const writer = $("writer-contract");
  clear(writer);
  [
    ["定位", contract.writer.positioning],
    ["Podcast 方法", contract.writer.podcast_method],
    ["證據邊界", contract.writer.evidence_boundary],
    ["第二視角", contract.writer.source_strategy],
    ["收尾方式", contract.writer.ending],
  ].forEach(([label, copy]) => {
    const row = node("div", "principle-row");
    row.append(node("strong", "", label), node("p", "", copy || "未定義"));
    writer.append(row);
  });

  const body = $("substack-draft-rows");
  clear(body);
  const rows = state.dashboard?.recent_substack_drafts || [];
  if (!state.dashboard || !rows.length) {
    const tr = node("tr");
    const td = node("td", "table-empty", !state.dashboard ? "解鎖後顯示草稿 metadata；文章全文不會進入儀表板。" : "尚無已同步的草稿 metadata；這不等於沒有本地文章。 ");
    td.colSpan = 5;
    tr.append(td);
    body.append(tr);
  } else {
    rows.forEach((row) => {
      const tr = node("tr");
      const title = node("td", "table-title");
      title.append(node("strong", "", row.source_title || "未命名"));
      if (row.source_url) {
        const link = node("a", "", row.source_url);
        link.href = row.source_url; link.target = "_blank"; link.rel = "noreferrer";
        title.append(link);
      }
      const proof = row.remote_draft_id ? `draft ID · ${row.remote_draft_id}` : "尚無遠端 draft ID";
      tr.append(title, node("td", "", editorialKind(row.editorial_kind)), node("td"), node("td", "", when(row.drafted_at || row.written_at || row.updated_at)), node("td", "", proof));
      tr.children[2].append(statusChip(row.status));
      body.append(tr);
    });
  }
  const latest = newest(rows.map((row) => row.drafted_at || row.written_at || row.updated_at));
  $("substack-data-stamp").textContent = latest ? `最新 ${age(latest)}` : "尚無同步證據";
}

function rangeCopy(value) {
  return Array.isArray(value) && value.length === 2 ? `${fmt(value[0])}–${fmt(value[1])}` : "2,800–4,200";
}

function deltaCopy(value) {
  const parsed = number(value);
  if (parsed === null) return "未知";
  return `${parsed > 0 ? "+" : ""}${fmt(parsed)}`;
}

function renderMeta() {
  const grid = $("platform-grid");
  clear(grid);
  if (!state.dashboard) {
    grid.append(node("div", "locked-placeholder wide", "解鎖後顯示平台原生指標與資料時間。"));
  } else {
    PLATFORM_ORDER.forEach((platform) => grid.append(platformCard(platform, platformSnapshot(platform, state.dashboard))));
  }
  const contentSummary = summarizeRecentContent(state.dashboard?.recent_posts || []);
  renderMetaInsights(contentSummary);
  renderTrend();
  renderRecentPosts(contentSummary);
  const stamps = [
    ...(state.dashboard?.engagement || []).map((row) => row.last_captured_at),
    ...(state.dashboard?.audience || []).map((row) => row.captured_at),
  ];
  const latest = newest(stamps);
  $("meta-data-stamp").textContent = latest ? `最新數據 ${age(latest)}` : "尚無數據時間";
}

function renderMetaInsights(summary) {
  const box = $("meta-insights");
  clear(box);
  if (!state.dashboard) {
    box.append(node("div", "locked-placeholder wide", "解鎖後整理近期發文數、平台回讀覆蓋與可行動訊號。"));
    return;
  }
  const {insights} = summary;
  const cards = [
    {
      label: "近期內容",
      value: fmt(insights.totalContent),
      copy: `${fmt(insights.totalPlatformPosts)} 筆平台發布紀錄`,
    },
    {
      label: "數據回讀覆蓋",
      value: insights.coverageRate === null ? "未知" : `${fmt(insights.coverageRate)}%`,
      copy: `${fmt(insights.measuredPlatformPosts)} / ${fmt(insights.totalPlatformPosts)} 筆已有 metrics`,
    },
    {
      label: "近期互動較高",
      value: insights.topActions === null ? "未知" : fmt(insights.topActions),
      copy: insights.topTitle || "尚無可比較的回讀樣本",
      caution: "只比較已回讀的互動動作，不等同跨平台觸及成效。",
    },
    {
      label: "最新數據證據",
      value: insights.latestMetricsAt ? age(insights.latestMetricsAt) : "未接上",
      copy: insights.latestMetricsAt ? when(insights.latestMetricsAt) : "目前沒有 metrics captured_at",
    },
  ];
  cards.forEach((card) => {
    const item = node("article", "insight-card");
    item.append(node("span", "", card.label), node("strong", "", card.value), node("p", "", card.copy));
    if (card.caution) item.append(node("small", "", card.caution));
    box.append(item);
  });
}

function platformCard(platform, snapshot) {
  const meta = PLATFORM_META[platform];
  const card = node("article", "platform-card");
  card.style.setProperty("--platform-color", meta.color);
  const head = node("div", "platform-head");
  const name = node("div", "platform-name");
  name.append(node("span", "platform-badge", meta.short), node("span", "", meta.label));
  head.append(name, node("span", "platform-freshness", snapshot.audienceCapturedAt ? age(snapshot.audienceCapturedAt) : "受眾未接上"));
  const main = node("div", "platform-main");
  main.append(node("span", "", "追蹤者"), node("strong", "", fmt(snapshot.followers)), node("small", "", `7 日變化 ${deltaCopy(snapshot.followersDelta7d)}`));
  const facts = node("div", "platform-facts");
  [
    [snapshot.medianPrimaryLabel, fmt(snapshot.medianPrimary)],
    ["品質可發布率", snapshot.qualityRate === null ? "未知" : `${fmt(snapshot.qualityRate)}%`],
    ["已發布紀錄", fmt(snapshot.published)],
    ["零互動樣本", snapshot.zeroActionRate === null ? "未知" : `${fmt(snapshot.zeroActionRate)}%`],
  ].forEach(([label, value]) => {
    const fact = node("div", "platform-fact");
    fact.append(node("span", "", label), node("strong", "", value));
    facts.append(fact);
  });
  const foot = node("p", "platform-foot", snapshot.engagementCapturedAt
    ? `互動樣本 ${fmt(snapshot.sampledPosts)} 篇 · ${age(snapshot.engagementCapturedAt)}`
    : "互動 analytics 尚未接上或尚無有效 snapshot。 ");
  card.append(head, main, facts, foot);
  return card;
}

function renderTrend() {
  const box = $("trend-chart");
  clear(box);
  const rows = state.dashboard?.engagement_trend || [];
  if (!state.dashboard || !rows.length) return box.append(node("div", "locked-placeholder", state.dashboard ? "尚無 30 日趨勢資料。" : "解鎖後顯示；缺少日期會保留為斷點。"));
  const days = [...new Set(rows.map((row) => row.day).filter(Boolean))].sort().slice(-30);
  if (!days.length) return box.append(node("div", "locked-placeholder", "趨勢資料沒有有效日期。"));
  const series = buildTrendSeries(rows, state.trendMetric, days);
  const values = Object.values(series).flat().filter((value) => value !== null);
  if (!values.length) return box.append(node("div", "locked-placeholder", "這個指標目前沒有數值；未把缺值補成零。"));

  const width = 900, height = 220, left = 42, right = 14, top = 12, bottom = 28;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const max = Math.max(...values, 1);
  const svg = svgNode("svg", {class: "chart-svg", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Meta 三平台 30 日趨勢"});
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = top + (plotHeight * tick / 4);
    svg.append(svgNode("line", {x1: left, y1: y, x2: width - right, y2: y, class: "chart-grid"}));
    const label = svgNode("text", {x: left - 8, y: y + 3, "text-anchor": "end", class: "chart-label"});
    label.textContent = fmt(max * (1 - tick / 4));
    svg.append(label);
  }
  const dateIndexes = [...new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])];
  dateIndexes.forEach((index) => {
    const x = days.length === 1 ? left : left + plotWidth * index / (days.length - 1);
    const label = svgNode("text", {x, y: height - 6, "text-anchor": index === 0 ? "start" : (index === days.length - 1 ? "end" : "middle"), class: "chart-label"});
    label.textContent = days[index].slice(5).replace("-", "/");
    svg.append(label);
  });
  PLATFORM_ORDER.forEach((platform) => {
    const color = PLATFORM_META[platform].color;
    const points = series[platform];
    const segments = [];
    let current = [];
    points.forEach((value, index) => {
      if (value === null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      const x = days.length === 1 ? left : left + plotWidth * index / (days.length - 1);
      const y = top + plotHeight * (1 - value / max);
      current.push([x, y]);
    });
    if (current.length) segments.push(current);
    segments.forEach((segment) => {
      const path = segment.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
      svg.append(svgNode("path", {d: path, class: "chart-line", stroke: color}));
      segment.forEach(([x, y]) => svg.append(svgNode("circle", {cx: x, cy: y, r: 3.4, fill: color, class: "chart-dot"})));
    });
  });
  const legend = node("div", "chart-legend");
  PLATFORM_ORDER.forEach((platform) => {
    const item = node("span", "", PLATFORM_META[platform].label);
    item.style.color = PLATFORM_META[platform].color;
    item.prepend(node("i"));
    legend.append(item);
  });
  box.append(svg, legend);
}

function renderRecentPosts(summary) {
  const box = $("recent-posts");
  clear(box);
  if (!state.dashboard || !summary.items.length) return box.append(node("div", "locked-placeholder", state.dashboard ? "尚無近期貼文 metadata。" : "解鎖後顯示最近發布內容。"));
  summary.items.slice(0, 12).forEach((content) => {
    const item = node("article", "content-card");
    const header = node("div", "content-head");
    const title = node("div", "content-title");
    title.append(node("strong", "", content.title), node("small", "", `${content.format || "格式未知"} · 發布於 ${when(content.postedAt)}`));
    header.append(title);
    if (content.sourceUrl) {
      const link = node("a", "source-link", "查看來源 ↗");
      link.href = content.sourceUrl;
      link.target = "_blank";
      link.rel = "noreferrer";
      header.append(link);
    }
    const platforms = node("div", "content-platforms");
    content.platforms.forEach((row) => {
      const platform = PLATFORM_META[row.platform] || {label: row.platform || "未知", short: "?", color: "#aaa89e"};
      const platformRow = node("div", "content-platform-row");
      platformRow.style.setProperty("--platform-color", platform.color);
      const name = node("div", "content-platform-name");
      name.append(node("span", "platform-badge", platform.short), node("strong", "", platform.label));
      const native = node("div", "content-native-metric");
      native.append(node("span", "", row.primaryLabel), node("strong", "", fmt(row.primaryValue)));
      const actions = node("div", "content-native-metric");
      actions.append(node("span", "", "互動"), node("strong", "", fmt(row.actions)));
      const proof = node("div", "content-proof");
      proof.append(statusChip(row.status || "published"), node("small", "", row.metrics_captured_at ? `數據 ${age(row.metrics_captured_at)}` : "尚未回讀數據"));
      platformRow.append(name, native, actions, proof);
      platforms.append(platformRow);
    });
    const footer = node("div", "content-foot");
    const total = content.totalActions === null ? "互動未知" : `已回讀互動合計 ${fmt(content.totalActions)}`;
    footer.append(
      node("span", "", `${content.platforms.length} 個平台 · ${content.measuredPlatforms}/${content.platforms.length} 已回讀`),
      node("strong", "", total),
    );
    item.append(header, platforms, footer);
    box.append(item);
  });
}

function dataFreshness(row) {
  const timestamp = row.captured_at;
  const hours = timestamp ? (Date.now() - Date.parse(timestamp)) / 3600000 : Infinity;
  if (!timestamp) return {label: "UNKNOWN", className: "warn"};
  if (hours > 30 && row.status === "healthy") return {label: "STALE", className: "warn"};
  return {label: STATUS_COPY[row.status] || String(row.status || "unknown").toUpperCase(), className: statusClass(row.status)};
}

function renderHealth() {
  const box = $("health-list");
  clear(box);
  const rows = state.dashboard ? ensureExpectedSchedulerHealth(state.dashboard.data_health || []) : [];
  if (!state.dashboard || !rows.length) {
    box.append(node("div", "locked-placeholder", state.dashboard ? "尚無 data health snapshot；不能解讀成全部正常。" : "解鎖後逐項顯示 collector、sync 與 cadence。"));
  } else {
    rows.forEach((row) => {
      const item = node("div", "health-row");
      const content = node("div");
      const name = node("div", "health-name");
      name.append(node("strong", "", `${row.platform || "system"} · ${HEALTH_COPY[row.metric] || row.metric}`));
      content.append(name, node("p", "", row.detail || "沒有補充 detail"));
      const meta = node("div", "health-meta");
      const freshness = dataFreshness(row);
      meta.append(node("span", `status-chip ${freshness.className}`, freshness.label), node("time", "", age(row.captured_at)));
      item.append(content, meta);
      box.append(item);
    });
  }
  const latest = newest(rows.map((row) => row.captured_at));
  $("health-data-stamp").textContent = latest ? `最新 ${age(latest)}` : "尚無健康快照";
}

function renderWorkflows() {
  const box = $("workflow-list");
  clear(box);
  if (!state.workflows.length) return box.append(node("div", "locked-placeholder", "GitHub workflow 公開資料目前無法讀取。"));
  const seen = new Set();
  state.workflows.filter((run) => {
    if (seen.has(run.workflowName)) return false;
    seen.add(run.workflowName);
    return true;
  }).slice(0, 10).forEach((run) => {
    const item = node(run.url ? "a" : "div", "workflow-row");
    if (run.url) { item.href = run.url; item.target = "_blank"; item.rel = "noreferrer"; }
    const content = node("div");
    content.append(node("strong", "", run.workflowName), node("p", "", `${run.event || "事件未知"} · ${run.status}`));
    const meta = node("div", "health-meta");
    meta.append(statusChip(run.conclusion), node("time", "", age(run.updatedAt)));
    item.append(content, meta);
    box.append(item);
  });
}

function renderHistory() {
  const box = $("submission-history");
  clear(box);
  const rows = state.dashboard?.recent_submissions || [];
  if (!state.dashboard || !rows.length) return box.append(node("div", "locked-placeholder", state.dashboard ? "還沒有投稿紀錄。" : "解鎖後顯示投稿進度。"));
  rows.slice(0, 15).forEach((row) => {
    const item = node("article", "history-row");
    const head = node("div", "history-head");
    head.append(node("strong", "", row.note || (row.target === "substack" ? "Substack 素材" : "Meta 素材")), node("time", "", age(row.created_at)));
    item.append(head, node("p", "", `${row.target === "substack" ? "Substack" : "Meta"} · ${row.source_type} · ${row.requested_mode}`), statusChip(row.status));
    if (row.error) item.append(node("p", "", row.error));
    box.append(item);
  });
}

function renderPrivateViews() {
  renderRuntime();
  renderAttention();
  renderDraftPreview();
  renderMetaPreview();
  renderSubstack();
  renderMeta();
  renderHealth();
  renderHistory();
  updateSubmissionUi();
}

function selected(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || "";
}

function submissionButtonCopy() {
  if (selected("target") === "substack") return "建立 Substack 優先草稿";
  return selected("meta-mode") === "publish_now" ? "立即發布到 Meta" : "排入 Meta 佇列";
}

function updateSubmissionUi() {
  const target = selected("target") || "substack";
  const source = selected("source") || "url";
  const meta = target === "meta";
  $("meta-options").hidden = !meta;
  $("source-line-field").hidden = source === "text";
  $("source-text-field").hidden = source !== "text";
  $("source-line-label").textContent = source === "youtube" ? "YouTube 網址" : "來源網址";
  $("source-line").placeholder = source === "youtube" ? "https://youtube.com/watch?v=…" : "https://…";
  const ready = (state.dashboard?.automation || state.publicHealth?.automation || {}).meta_publish_now_ready === true;
  const publishInput = document.querySelector('input[name="meta-mode"][value="publish_now"]');
  publishInput.disabled = !ready;
  if (!ready && publishInput.checked) document.querySelector('input[name="meta-mode"][value="queue"]').checked = true;
  $("publish-now-help").textContent = ready
    ? "Production runtime 顯示立即發布可用；送出後仍須等待三平台 post ID 回讀。"
    : "Production runtime 未顯示 ready，因此只開放排入佇列。";
  $("submission-route").textContent = target === "substack"
    ? "系統會建立 Substack 優先草稿，交由你最後審稿與發布。"
    : (selected("meta-mode") === "publish_now"
      ? "這會要求 production runtime 立即發布；平台送達仍以 post ID 為準。"
      : "素材會先進入 Meta 佇列，不代表已經發布。 ");
  $("submit-button").disabled = !state.dashboard;
  $("submit-button").textContent = state.dashboard ? submissionButtonCopy() : "先解鎖再投稿";
}

function formMessage(message, kind = "") {
  const box = $("form-message");
  box.hidden = !message;
  box.className = `form-message ${kind}`;
  box.textContent = message;
}

async function submitMaterial(event) {
  event.preventDefault();
  if (!state.dashboard || !currentToken()) return formMessage("請先解鎖營運資料。", "bad");
  const target = selected("target");
  const sourceType = selected("source");
  const content = (sourceType === "text" ? $("source-text").value : $("source-line").value).trim();
  const note = $("submission-note").value.trim();
  const platforms = [...document.querySelectorAll('input[name="platform"]:checked')].map((input) => input.value);
  const metaMode = selected("meta-mode") || "queue";
  if (!content) return formMessage("請提供網址、YouTube 或原始文字。", "bad");
  if (target === "meta" && !platforms.length) return formMessage("Meta 至少要選一個平台。", "bad");
  if (target === "meta" && metaMode === "publish_now" && sourceType === "text" && !note) return formMessage("立即發布純文字時，處理說明會作為編輯標題，不能留白。", "bad");
  if (target === "meta" && metaMode === "publish_now" && sourceType === "text" && content.length < 80) return formMessage("立即發布純文字至少需要 80 字素材。", "bad");

  const button = $("submit-button");
  button.disabled = true;
  button.textContent = "正在送出…";
  formMessage("正在建立可追蹤的投稿紀錄。", "");
  try {
    const payload = buildSubmissionPayload({target, sourceType, content, note, platforms, metaMode});
    const idempotency = window.crypto?.randomUUID?.().replaceAll("-", "") || `owner${Date.now()}${Math.random().toString(16).slice(2)}`;
    const result = await request("/api/submissions", {
      method: "POST",
      headers: {"Idempotency-Key": `owner_${idempotency}`.slice(0, 80)},
      body: JSON.stringify(payload),
    });
    const row = result.submission || {};
    formMessage(`已建立投稿 ${row.id || "（ID 未回傳）"}\n目前狀態：${STATUS_COPY[row.status] || row.status || "未知"}`, "good");
    $("source-line").value = "";
    $("source-text").value = "";
    $("submission-note").value = "";
    await loadPrivateData(currentToken(), {quiet: true});
  } catch (error) {
    if (error.status === 401) showAuthNotice(error.message);
    formMessage(`投稿未建立：${error.message}`, "bad");
  } finally {
    updateSubmissionUi();
  }
}

async function unlock(event) {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const candidate = $("owner-token").value.trim() || currentToken();
  if (!candidate) {
    $("auth-status").textContent = "請輸入 owner token。";
    return;
  }
  const button = $("unlock-button");
  button.disabled = true;
  button.textContent = "驗證中…";
  $("auth-status").textContent = "";
  const valid = await loadPrivateData(candidate, {quiet: true});
  if (valid) {
    rememberToken(candidate, {remember: $("remember-token").checked, ...storage()});
    $("owner-token").value = "";
    $("auth-dialog").close();
    showAuthNotice("");
    updateAuthUi(true);
    startPolling();
  } else {
    $("auth-status").textContent = state.privateError || "驗證失敗；既有 token 未被刪除。";
  }
  button.disabled = false;
  button.textContent = "驗證並解鎖";
}

function explicitForget() {
  forgetToken(storage());
  state.dashboard = null;
  state.privateError = "";
  clearInterval(state.refreshTimer);
  $("auth-dialog").close();
  showAuthNotice("已依你的明確操作鎖定這台裝置；其他電腦的授權不受影響。 ");
  renderPrivateViews();
  updateAuthUi(false);
}

function startPolling() {
  clearInterval(state.refreshTimer);
  if (!currentToken()) return;
  state.refreshTimer = setInterval(() => loadPrivateData(currentToken(), {quiet: true}), 60000);
}

function bindEvents() {
  document.querySelectorAll("[data-nav]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.nav)));
  document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.jump)));
  $("refresh-all").addEventListener("click", refreshAll);
  $("refresh-history").addEventListener("click", () => loadPrivateData(currentToken(), {quiet: false}));
  $("open-auth").addEventListener("click", () => {
    $("auth-status").textContent = "";
    $("auth-dialog").showModal();
    setTimeout(() => $("owner-token").focus(), 0);
  });
  $("auth-form").addEventListener("submit", unlock);
  $("forget-token").addEventListener("click", explicitForget);
  $("submission-form").addEventListener("submit", submitMaterial);
  document.querySelectorAll('input[name="target"], input[name="source"], input[name="meta-mode"]').forEach((input) => input.addEventListener("change", updateSubmissionUi));
  $("metric-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-metric]");
    if (!button) return;
    state.trendMetric = button.dataset.metric;
    document.querySelectorAll("[data-metric]").forEach((item) => item.classList.toggle("is-active", item === button));
    renderTrend();
  });
  window.addEventListener("popstate", () => showView(new URL(location.href).searchParams.get("view") || "overview", {push: false}));
}

async function boot() {
  bindEvents();
  const requestedView = new URL(location.href).searchParams.get("view") || "overview";
  showView(requestedView, {push: false});
  renderPrivateViews();
  await loadPublicData();
  if (currentToken()) {
    await loadPrivateData(currentToken(), {quiet: true});
    startPolling();
  } else {
    updateAuthUi(false);
  }
}

boot();
