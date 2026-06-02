/* News Radar · Dashboard App
 * Single-page application using sql.js + Chart.js
 * Reads SQLite DB from state branch via GitHub raw CDN
 */

// === Config ===
const CONFIG = {
  owner: 'HsinTiger',
  repo: 'news-radar',
  stateBranch: 'state',
  mainBranch: 'main',
  dbUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/state/data/01_harvest/news_radar.db`,
  dbTimestampUrl: () => `https://api.github.com/repos/HsinTiger/news-radar/contents/data/01_harvest/news_radar.db?ref=state`,
  lastRunUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/state/LAST_RUN.txt`,
  soulUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/main/config/news_radar_soul.md`,
  platformUrl: (p) => `https://raw.githubusercontent.com/HsinTiger/news-radar/main/config/platforms/${p}_v2.md`,
  changelogUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/main/CHANGELOG.md`,
  feedbackUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/state/data/05_reflect/feedback/latest.json`,
  engagementReportUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/state/data/05_reflect/feedback/engagement_report.json`,
  topicAdjustUrl: () => `https://raw.githubusercontent.com/HsinTiger/news-radar/state/data/05_reflect/feedback/topic_adjustments.json`,
  REFRESH_INTERVAL: 300000, // 5 min
};

// === State ===
let SQL = null;
let db = null;
let dbLastFetched = null;
let refreshTimer = null;
let charts = {};

// === Init ===
document.addEventListener('DOMContentLoaded', async () => {
  initNavigation();
  await initSQL();
  scheduleRefresh();
});

async function initSQL() {
  try {
    SQL = await initSqlJs({
      locateFile: file => `https://cdn.jsdelivr.net/npm/sql.js@1.11/dist/${file}`
    });
    await loadDB();
  } catch (err) {
    console.error('SQL.js init failed:', err);
    showError('SQL.js 載入失敗: ' + err.message);
    hideLoading();
  }
}

async function loadDB() {
  setDBStatus('loading', '正在下載資料庫…');
  try {
    const resp = await fetch(CONFIG.dbUrl(), {
      cache: 'no-cache',
      headers: { 'Accept': 'application/octet-stream' }
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const buf = await resp.arrayBuffer();
    const u8 = new Uint8Array(buf);

    if (db) db.close();
    db = new SQL.Database(u8);

    dbLastFetched = new Date();
    setDBStatus('online', '資料庫連線中');
    document.getElementById('last-update').textContent =
      '更新於 ' + dbLastFetched.toLocaleTimeString('zh-TW');
    hideLoading();
    refreshAll();
    await fetchLastRun();
  } catch (err) {
    console.error('DB load failed:', err);
    setDBStatus('offline', '下載失敗');
    showError('無法載入資料庫: ' + err.message);
    hideLoading();
  }
}

async function refreshDB() {
  showLoading('正在重新整理…');
  await loadDB();
}

function scheduleRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    loadDB();
  }, CONFIG.REFRESH_INTERVAL);
}

// === Navigation ===
function initNavigation() {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const page = item.dataset.page;
      navigateTo(page);
    });
  });
  // Handle hash routing
  window.addEventListener('hashchange', () => {
    const page = location.hash.slice(1) || 'home';
    showPage(page);
  });
  // Show initial page based on hash
  const initial = location.hash.slice(1) || 'home';
  showPage(initial);
}

function navigateTo(page) {
  location.hash = '#' + page;
}

function showPage(pageId) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${pageId}"]`)?.classList.add('active');
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const page = document.getElementById('page-' + pageId);
  if (page) page.classList.add('active');
  if (db) renderPage(pageId);
}

function renderPage(pageId) {
  switch (pageId) {
    case 'home': renderHome(); break;
    case 'queue': renderQueue(); break;
    case 'archive': renderArchive(); break;
    case 'dropped': renderDropped(); break;
    case 'persona': renderPersona(); break;
    case 'settings': renderSettings(); break;
    case 'analytics': renderAnalytics(); break;
    case 'source': renderSource(); break;
    case 'changelog': renderChangelog(); break;
  }
}

function refreshAll() {
  if (db && document.querySelector('.page.active')) {
    const active = location.hash.slice(1) || 'home';
    renderPage(active);
  }
  updateQueueBadge();
}

// === DB Queries ===
function q(sql, params = {}) {
  if (!db) return [];
  try {
    const stmt = db.prepare(sql);
    if (params && typeof params === 'object') {
      stmt.bind(params);
    }
    const results = [];
    while (stmt.step()) {
      results.push(stmt.getAsObject());
    }
    stmt.free();
    return results;
  } catch (err) {
    console.error('Query error:', sql, err.message);
    return [];
  }
}

function qOne(sql, params = {}) {
  const results = q(sql, params);
  return results.length > 0 ? results[0] : null;
}

// === Home Page ===
function renderHome() {
  const page = document.getElementById('page-home');
  const now = new Date();

  // Stats
  const totalPublished = qOne("SELECT COUNT(*) as c FROM publish_log WHERE success=1");
  const totalDrafts = qOne("SELECT COUNT(*) as c FROM drafts");
  const totalItems = qOne("SELECT COUNT(*) as c FROM news_items");
  const queuedCount = qOne("SELECT COUNT(*) as c FROM drafts WHERE queue_status='queued'");

  const lastPublish = qOne(`
    SELECT p.posted_at, p.platform, d.title
    FROM publish_log p
    JOIN drafts d ON d.id = p.draft_id
    WHERE p.success=1
    ORDER BY p.posted_at DESC LIMIT 1
  `);

  let lastPublishStr = '尚未發布';
  let lastPublishClass = '';
  if (lastPublish) {
    const lastTime = new Date(lastPublish.posted_at.replace(' ', 'T'));
    const hoursAgo = (now - lastTime) / 3600000;
    lastPublishStr = `${Math.round(hoursAgo)} 小時前 · ${lastPublish.platform} · ${(lastPublish.title || '').slice(0, 30)}`;
    lastPublishClass = hoursAgo > 3 ? 'danger' : hoursAgo > 1 ? 'alarm' : 'success';
  }

  // Engagement stats
  const eng = q(`
    SELECT e.platform, AVG(e.likes) as avg_likes, AVG(e.comments) as avg_comments,
           AVG(e.views) as avg_views, AVG(e.shares) as avg_shares,
           COUNT(*) as sample_count
    FROM engagement_stats e
    WHERE e.likes > 0 OR e.comments > 0
    GROUP BY e.platform
  `);

  // Recent publishes
  const recentPub = q(`
    SELECT p.posted_at, p.platform, p.success, d.title, d.id as draft_id
    FROM publish_log p
    JOIN drafts d ON d.id = p.draft_id
    ORDER BY p.posted_at DESC LIMIT 10
  `);

  // Recent harvest activity
  const recentItems = q(`
    SELECT title, published_at, feed_name, status
    FROM news_items
    ORDER BY fetched_at DESC LIMIT 10
  `);

  page.innerHTML = `
    <div class="page-header">
      <h2>儀表板</h2>
      <p>系統總覽與最近活動</p>
    </div>
    <div class="stats-grid">
      <div class="stat-card ${lastPublishClass}">
        <div class="stat-label">上次發布</div>
        <div class="stat-value">${lastPublish ? Math.round((now - new Date(lastPublish.posted_at.replace(' ', 'T'))) / 3600000) + 'h' : '—'}</div>
        <div class="stat-sub">${lastPublishStr}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">等待發布</div>
        <div class="stat-value">${queuedCount?.c || 0}</div>
        <div class="stat-sub">queue 中草稿</div>
      </div>
      <div class="stat-card success">
        <div class="stat-label">累計發布</div>
        <div class="stat-value">${totalPublished?.c || 0}</div>
        <div class="stat-sub">${totalDrafts?.c || 0} 篇草稿 / ${totalItems?.c || 0} 篇新聞</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">互動概覽</div>
        <div class="stat-value" style="font-size:1.2rem">${eng.map(e => `${e.platform.slice(0,4)}: ❤${Math.round(e.avg_likes||0)} 💬${Math.round(e.avg_comments||0)}`).join('<br>') || '尚無數據'}</div>
      </div>
    </div>

    <h3 style="margin-bottom:12px;color:var(--text-secondary);font-size:1rem">🕐 最近發布</h3>
    ${recentPub.length === 0 ?
      '<div class="empty-state"><div class="empty-icon">📭</div><p>尚無發布紀錄</p></div>' :
      `<div class="table-container"><table>
        <tr><th>時間</th><th>平台</th><th>標題</th><th>狀態</th></tr>
        ${recentPub.map(r => `<tr class="clickable" onclick="showDetail('${r.draft_id}')">
          <td style="white-space:nowrap">${relTime(r.posted_at)}</td>
          <td>${platLabel(r.platform)}</td>
          <td>${esc(r.title || '').slice(0, 50)}</td>
          <td>${r.success ? '✅' : '❌'}</td>
        </tr>`).join('')}
      </table></div>`
    }

    <h3 style="margin:20px 0 12px;color:var(--text-secondary);font-size:1rem">🌾 最近素材</h3>
    <div class="table-container"><table>
      <tr><th>時間</th><th>來源</th><th>標題</th><th>狀態</th></tr>
      ${recentItems.map(r => `<tr>
        <td style="white-space:nowrap">${relTime(r.published_at)}</td>
        <td>${esc(r.feed_name || '').slice(0, 20)}</td>
        <td>${esc(r.title || '').slice(0, 60)}</td>
        <td>${statusBadge(r.status)}</td>
      </tr>`).join('')}
    </table></div>
  `;
}

// === Queue Page ===
function renderQueue() {
  const page = document.getElementById('page-queue');
  const items = q(`
    SELECT d.id, d.title, d.confidence_score, d.queue_status, d.status as draft_status,
           d.publish_at, d.generated_at,
           n.published_at as news_published_at, n.feed_name, n.topic_category, n.weighted_score
    FROM drafts d
    JOIN news_items n ON d.news_id = n.id
    WHERE d.queue_status IS NOT NULL
    ORDER BY d.publish_at DESC, d.generated_at DESC
    LIMIT 50
  `);

  const queued = items.filter(i => i.queue_status === 'queued');
  const published = items.filter(i => i.queue_status === 'published');
  const failed = items.filter(i => i.queue_status === 'failed');
  const stale = items.filter(i => i.queue_status === 'stale');

  document.getElementById('queue-badge').textContent = queued.length;

  page.innerHTML = `
    <div class="page-header">
      <h2>發布佇列</h2>
      <p>等待中 ${queued.length} · 已發布 ${published.length} · 失敗 ${failed.length} · 過期 ${stale.length}</p>
    </div>
    <div class="filters">
      <select id="queue-filter" onchange="renderQueueFiltered()">
        <option value="all">全部</option>
        <option value="queued" selected>等待中</option>
        <option value="published">已發布</option>
        <option value="failed">失敗</option>
        <option value="stale">過期</option>
      </select>
    </div>
    <div id="queue-table" class="table-container">
      ${buildQueueTable(queued)}
    </div>
  `;
  document.getElementById('queue-filter').value = 'queued';
}

function renderQueueFiltered() {
  const filter = document.getElementById('queue-filter').value;
  const items = q(`
    SELECT d.id, d.title, d.confidence_score, d.queue_status, d.status as draft_status,
           d.publish_at, d.generated_at,
           n.published_at as news_published_at, n.feed_name, n.topic_category, n.weighted_score
    FROM drafts d
    JOIN news_items n ON d.news_id = n.id
    WHERE d.queue_status IS NOT NULL
    ORDER BY d.publish_at DESC
    LIMIT 100
  `);
  const filtered = filter === 'all' ? items : items.filter(i => i.queue_status === filter);
  document.getElementById('queue-table').innerHTML = buildQueueTable(filtered);
}

function buildQueueTable(items) {
  if (items.length === 0) {
    return '<div class="empty-state"><div class="empty-icon">📋</div><p>無符合項目</p></div>';
  }
  return `<table>
    <tr>
      <th>狀態</th><th>分數</th><th>主題</th><th>標題</th><th>來源</th><th>時間</th>
    </tr>
    ${items.map(r => `<tr class="clickable" onclick="showDetail('${r.id}')">
      <td>${queueStatusBadge(r.queue_status)}</td>
      <td>${scoreBadge(r.confidence_score)}</td>
      <td>${topicBadge(r.topic_category)}</td>
      <td>${esc(r.title || '').slice(0, 60)}</td>
      <td>${esc(r.feed_name || '').slice(0, 15)}</td>
      <td style="white-space:nowrap">${r.publish_at ? relTime(r.publish_at) : relTime(r.generated_at)}</td>
    </tr>`).join('')}
  </table>`;
}

// === Archive Page ===
function renderArchive() {
  const page = document.getElementById('page-archive');
  const items = q(`
    SELECT p.id as log_id, p.platform, p.posted_at, p.success, p.platform_post_id,
           d.id as draft_id, d.title, d.confidence_score,
           e.likes, e.comments, e.shares, e.views, e.reach
    FROM publish_log p
    JOIN drafts d ON d.id = p.draft_id
    LEFT JOIN engagement_stats_latest e ON e.draft_id = d.id AND e.platform = p.platform
    WHERE p.success = 1
    ORDER BY p.posted_at DESC
    LIMIT 100
  `);

  // Group by draft_id
  const grouped = {};
  items.forEach(r => {
    if (!grouped[r.draft_id]) {
      grouped[r.draft_id] = { ...r, platforms: [] };
    }
    grouped[r.draft_id].platforms.push({
      platform: r.platform,
      likes: r.likes, comments: r.comments, shares: r.shares,
      views: r.views, platform_post_id: r.platform_post_id
    });
  });
  const drafts = Object.values(grouped);

  page.innerHTML = `
    <div class="page-header">
      <h2>歷史存檔</h2>
      <p>最近 ${drafts.length} 篇已發布貼文</p>
    </div>
    ${drafts.length === 0
      ? '<div class="empty-state"><div class="empty-icon">📚</div><p>尚無已發布內容</p></div>'
      : `<div class="card-grid">${drafts.map(d => `
        <div class="card" onclick="showDetail('${d.draft_id}')">
          <div class="card-title">${esc(d.title || '無標題').slice(0, 80)}</div>
          <div class="card-meta">
            <span class="badge badge-green">${(d.confidence_score || 0).toFixed(2)}</span>
            <span style="color:var(--text-muted);font-size:.85rem">${relTime(d.posted_at)}</span>
          </div>
          <div class="eng">
            ${d.platforms.map(p => `
              <span>${platLabel(p.platform)}
                ${p.likes ? `❤️${p.likes}` : ''}
                ${p.comments ? `💬${p.comments}` : ''}
                ${p.views ? `👁️${p.views}` : ''}
              </span>
            `).join('')}
          </div>
        </div>
      `).join('')}</div>`
    }
  `;
}

// === Dropped Page ===
function renderDropped() {
  const page = document.getElementById('page-dropped');
  const items = q(`
    SELECT id, title, published_at, feed_name, status, drop_reason,
           topic_category, weighted_score
    FROM news_items
    WHERE status IN ('dropped') AND drop_reason IS NOT NULL
    ORDER BY published_at DESC
    LIMIT 100
  `);

  page.innerHTML = `
    <div class="page-header">
      <h2>被擋掉的新聞</h2>
      <p>最近 ${items.length} 筆未通過篩選的素材</p>
    </div>
    ${items.length === 0
      ? '<div class="empty-state"><div class="empty-icon">🗑️</div><p>無被擋掉的素材</p></div>'
      : `<div class="filters">
        <select id="drop-filter" onchange="renderDroppedFiltered()">
          <option value="all">全部原因</option>
          ${[...new Set(items.map(i => i.drop_reason))].map(r =>
            `<option value="${esc(r)}">${esc(r)}</option>`
          ).join('')}
        </select>
      </div>
      <div id="dropped-table" class="table-container"><table>
        <tr><th>時間</th><th>來源</th><th>標題</th><th>原因</th><th>主題</th></tr>
        ${items.map(r => `<tr>
          <td style="white-space:nowrap">${relTime(r.published_at)}</td>
          <td>${esc(r.feed_name || '').slice(0, 15)}</td>
          <td>${esc(r.title || '').slice(0, 60)}</td>
          <td><span class="badge badge-red">${esc(r.drop_reason || '').slice(0, 30)}</span></td>
          <td>${topicBadge(r.topic_category)}</td>
        </tr>`).join('')}
      </table></div>`
    }
  `;
}

function renderDroppedFiltered() {
  const filter = document.getElementById('drop-filter').value;
  const items = q(`SELECT * FROM news_items WHERE status='dropped' ORDER BY published_at DESC LIMIT 200`);
  const filtered = filter === 'all' ? items : items.filter(i => i.drop_reason === filter);
  document.getElementById('dropped-table').innerHTML = filtered.length === 0
    ? '<div class="empty-state"><p>無符合項目</p></div>'
    : `<table><tr><th>時間</th><th>來源</th><th>標題</th><th>原因</th><th>主題</th></tr>
      ${filtered.map(r => `<tr>
        <td style="white-space:nowrap">${relTime(r.published_at)}</td>
        <td>${esc(r.feed_name || '').slice(0, 15)}</td>
        <td>${esc(r.title || '').slice(0, 60)}</td>
        <td><span class="badge badge-red">${esc(r.drop_reason || '').slice(0, 30)}</span></td>
        <td>${topicBadge(r.topic_category)}</td>
      </tr>`).join('')}</table>`;
}

// === Persona Page ===
async function renderPersona() {
  const page = document.getElementById('page-persona');
  page.innerHTML = `<div class="page-header"><h2>寫作風格</h2><p>正在載入風格指南…</p></div><div class="loader" style="margin:48px auto"></div>`;

  try {
    const [soul, fb, ig, threads] = await Promise.all([
      fetch(CONFIG.soulUrl()).then(r => r.ok ? r.text() : '載入失敗'),
      fetch(CONFIG.platformUrl('fb')).then(r => r.ok ? r.text() : '載入失敗'),
      fetch(CONFIG.platformUrl('ig')).then(r => r.ok ? r.text() : '載入失敗'),
      fetch(CONFIG.platformUrl('threads')).then(r => r.ok ? r.text() : '載入失敗'),
    ]);

    page.innerHTML = `
      <div class="page-header">
        <h2>寫作風格</h2>
        <p>三平台共用靈魂 + 各平台撰文指南</p>
      </div>
      <div class="persona-section">
        <h3>📡 核心靈魂 (news_radar_soul.md)</h3>
        <pre>${esc(soul.slice(0, 2000))}${soul.length > 2000 ? '\n\n...(略)' : ''}</pre>
      </div>
      <div style="display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">
        <div class="persona-section">
          <h3>📘 FB / IEObserve 風格</h3>
          <pre>${esc(fb.slice(0, 1500))}${fb.length > 1500 ? '\n...(略)' : ''}</pre>
        </div>
        <div class="persona-section">
          <h3>🧵 Threads / 游庭皓 風格</h3>
          <pre>${esc(threads.slice(0, 1500))}${threads.length > 1500 ? '\n...(略)' : ''}</pre>
        </div>
        <div class="persona-section">
          <h3>📸 IG 風格</h3>
          <pre>${esc(ig.slice(0, 1500))}${ig.length > 1500 ? '\n...(略)' : ''}</pre>
        </div>
      </div>
    `;
  } catch (err) {
    page.innerHTML = `<div class="page-header"><h2>寫作風格</h2></div>
      <div class="error-banner">⚠️ 載入風格指南失敗: ${esc(err.message)}</div>`;
  }
}

// === Settings Page ===
function renderSettings() {
  const page = document.getElementById('page-settings');

  const topicWeights = q(`SELECT * FROM topic_weights ORDER BY weight DESC`);
  const reflectionEvents = q(`SELECT * FROM reflection_events ORDER BY ran_at DESC LIMIT 10`);
  const tokenUsage = q(`SELECT * FROM token_usage_daily ORDER BY date DESC LIMIT 14`);
  const queueCounts = q(`SELECT COALESCE(queue_status,'null') as qs, COUNT(*) as c FROM drafts GROUP BY qs`);

  page.innerHTML = `
    <div class="page-header">
      <h2>設定</h2>
      <p>系統參數與狀態（唯讀）</p>
    </div>

    <h3 style="margin-bottom:12px;color:var(--text-secondary)">📊 Queue 狀態分布</h3>
    <div class="stats-grid">
      ${queueCounts.map(r => `<div class="stat-card"><div class="stat-label">${esc(r.qs === 'null' ? '無狀態' : r.qs)}</div><div class="stat-value">${r.c}</div></div>`).join('')}
    </div>

    <h3 style="margin:20px 0 12px;color:var(--text-secondary)">🎯 主題權重</h3>
    <div class="table-container"><table>
      <tr><th>主題</th><th>權重</th><th>樣本數</th><th>更新時間</th><th>原因</th></tr>
      ${topicWeights.map(r => `<tr>
        <td>${topicBadge(r.category_id)}</td>
        <td><span class="${r.weight >= 1.2 ? 'badge badge-green' : r.weight >= 0.8 ? 'badge badge-yellow' : 'badge badge-red'}">${(r.weight || 1).toFixed(2)}</span></td>
        <td>${r.sample_count || 0}</td>
        <td style="white-space:nowrap;font-size:.85rem">${r.last_updated_at ? relTime(r.last_updated_at) : '—'}</td>
        <td style="font-size:.85rem">${esc(r.update_reason || '')}</td>
      </tr>`).join('')}
    </table></div>

    <h3 style="margin:20px 0 12px;color:var(--text-secondary)">💸 Token 用量（近 14 天）</h3>
    ${tokenUsage.length === 0
      ? '<p style="color:var(--text-muted)">尚無數據</p>'
      : `<div class="table-container"><table>
        <tr><th>日期</th><th>Provider</th><th>模型</th><th>Input</th><th>Output</th><th>成本</th><th>呼叫次數</th></tr>
        ${tokenUsage.map(r => `<tr>
          <td>${esc(r.date || '')}</td>
          <td>${esc(r.provider || '')}</td>
          <td style="font-size:.85rem">${esc(r.model || '').slice(0, 25)}</td>
          <td>${(r.total_input || 0).toLocaleString()}</td>
          <td>${(r.total_output || 0).toLocaleString()}</td>
          <td>$${(r.total_cost_usd || 0).toFixed(4)}</td>
          <td>${r.call_count || 0}</td>
        </tr>`).join('')}
      </table></div>`
    }

    <h3 style="margin:20px 0 12px;color:var(--text-secondary)">🔄 最近 Reflection 事件</h3>
    ${reflectionEvents.length === 0
      ? '<p style="color:var(--text-muted)">尚無事件</p>'
      : `<div class="table-container"><table>
        <tr><th>時間</th><th>樣本數</th><th>狀態</th><th>原因</th></tr>
        ${reflectionEvents.map(r => `<tr>
          <td style="white-space:nowrap">${relTime(r.ran_at)}</td>
          <td>${r.samples_used || 0}</td>
          <td>${statusBadge(r.status)}</td>
          <td style="font-size:.85rem;color:var(--text-secondary)">${esc((r.rationale || '').slice(0, 80))}</td>
        </tr>`).join('')}
      </table></div>`
    }
  `;
}

// === Detail Modal ===
let currentModalTab = 'platforms';
let modalDraftId = null;
let modalData = {};

async function showDetail(draftId) {
  if (!draftId) return;
  modalDraftId = draftId;

  // Fetch draft + news_item data
  const draft = qOne(`SELECT * FROM drafts WHERE id = ?`, {1: draftId});
  if (!draft) { showError('找不到此草稿'); return; }
  const news = qOne(`SELECT * FROM news_items WHERE id = ?`, {1: draft.news_id});
  const platformDrafts = q(`SELECT * FROM platform_drafts WHERE draft_id = ?`, {1: draftId});
  const publishLog = q(`
    SELECT * FROM publish_log WHERE draft_id = ? ORDER BY posted_at DESC
  `, {1: draftId});
  const engagement = q(`
    SELECT * FROM engagement_stats WHERE draft_id = ? ORDER BY fetched_at DESC
  `, {1: draftId});

  modalData = { draft, news, platformDrafts, publishLog, engagement };

  showDetailModal();
}

function showDetailModal() {
  const { draft, news, platformDrafts, publishLog, engagement } = modalData;

  const overlay = document.getElementById('detail-modal') || createDetailModal();
  overlay.classList.add('active');

  document.getElementById('modal-title').textContent = draft.title || '無標題';
  document.getElementById('modal-score').textContent = (draft.confidence_score || 0).toFixed(2);

  // Build score breakdown display
  let scoreDetails = '';
  try {
    const sb = JSON.parse(draft.score_breakdown || '{}');
    scoreDetails = Object.entries(sb).map(([k, v]) =>
      `<span style="margin:0 8px">${esc(k)}: ${typeof v === 'number' ? v.toFixed(2) : v}</span>`
    ).join('');
  } catch (e) {}

  document.getElementById('modal-score-detail').innerHTML = scoreDetails;

  // Populate tabs
  switchTab('modal-tab-platforms');
}

function createDetailModal() {
  const div = document.createElement('div');
  div.id = 'detail-modal';
  div.className = 'modal-overlay';
  div.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h3 id="modal-title">—</h3>
        <button class="modal-close" onclick="closeDetail()">✕</button>
      </div>
      <div style="margin-bottom:12px">
        <span class="badge badge-blue" id="modal-score">0.00</span>
        <span id="modal-score-detail" style="color:var(--text-muted);font-size:.85rem"></span>
      </div>
      <div class="modal-tabs">
        <button class="modal-tab active" data-tab="platforms" onclick="switchTab('modal-tab-platforms')">三平台內容</button>
        <button class="modal-tab" data-tab="news" onclick="switchTab('modal-tab-news')">原始新聞</button>
        <button class="modal-tab" data-tab="publish" onclick="switchTab('modal-tab-publish')">發布紀錄</button>
        <button class="modal-tab" data-tab="engagement" onclick="switchTab('modal-tab-engagement')">互動數據</button>
      </div>
      <div id="modal-tab-platforms" class="modal-tab-content"></div>
      <div id="modal-tab-news" class="modal-tab-content" style="display:none"></div>
      <div id="modal-tab-publish" class="modal-tab-content" style="display:none"></div>
      <div id="modal-tab-engagement" class="modal-tab-content" style="display:none"></div>
    </div>
  `;
  document.body.appendChild(div);
  div.addEventListener('click', (e) => {
    if (e.target === div) closeDetail();
  });
  return div;
}

function closeDetail() {
  const overlay = document.getElementById('detail-modal');
  if (overlay) overlay.classList.remove('active');
  modalDraftId = null;
}

function switchTab(tabId) {
  const { draft, news, platformDrafts, publishLog, engagement } = modalData;

  // Update tab buttons
  document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.modal-tab[data-tab="${tabId.replace('modal-tab-', '')}"]`)?.classList.add('active');

  // Show target tab content
  document.querySelectorAll('.modal-tab-content').forEach(c => c.style.display = 'none');
  const tabContent = document.getElementById(tabId);
  if (!tabContent) return;
  tabContent.style.display = 'block';

  // Populate based on tab
  const platformLabels = { 'facebook': '📘 FB', 'instagram': '📸 IG', 'threads': '🧵 Threads' };
  const platformDbName = { 'fb': 'facebook', 'ig': 'instagram', 'threads': 'threads' };

  if (tabId === 'modal-tab-platforms') {
    tabContent.innerHTML = platformDrafts.length === 0
      ? '<p style="color:var(--text-muted)">無平台變體</p>'
      : platformDrafts.map(pd => `
        <div class="modal-section">
          <h4>${platformLabels[pd.platform] || pd.platform} (${pd.char_count || 0} 字)</h4>
          <pre style="background:var(--bg-card);padding:12px;border-radius:var(--radius-sm);font-size:.88rem;line-height:1.6;white-space:pre-wrap;color:var(--text-secondary)">${esc(pd.full_text || pd.body || '')}</pre>
          ${pd.hashtags ? `<div style="margin-top:8px;color:var(--text-muted);font-size:.85rem">🏷️ ${esc(pd.hashtags)}</div>` : ''}
        </div>
      `).join('');
  }
  else if (tabId === 'modal-tab-news') {
    tabContent.innerHTML = news ? `
      <div class="modal-section">
        <h4>${esc(news.title || '')}</h4>
        <p style="margin-bottom:8px;color:var(--text-secondary);font-size:.85rem">
          ${esc(news.feed_name || '')} · ${relTime(news.published_at)}
          ${news.url ? ` · <a href="${esc(news.url)}" target="_blank" style="color:var(--accent)">🔗 原文</a>` : ''}
        </p>
        ${news.og_image_url ? `<img src="${esc(news.og_image_url)}" style="max-width:100%;max-height:200px;border-radius:var(--radius-sm);margin-bottom:8px">` : ''}
        <pre style="background:var(--bg-card);padding:12px;border-radius:var(--radius-sm);font-size:.85rem;line-height:1.6;white-space:pre-wrap;color:var(--text-secondary);max-height:300px;overflow-y:auto">${esc((news.clean_markdown || '').slice(0, 1000))}${(news.clean_markdown || '').length > 1000 ? '\n...(略)' : ''}</pre>
        <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
          <span class="badge badge-blue">${esc(news.source_type || 'article')}</span>
          ${news.topic_category ? topicBadge(news.topic_category) : ''}
          <span class="badge badge-gray">${esc(news.status || '')}</span>
        </div>
      </div>
    ` : '<p style="color:var(--text-muted)">無原始新聞資料</p>';
  }
  else if (tabId === 'modal-tab-publish') {
    tabContent.innerHTML = publishLog.length === 0
      ? '<p style="color:var(--text-muted)">無發布紀錄</p>'
      : `<div class="table-container"><table>
        <tr><th>時間</th><th>平台</th><th>結果</th><th>訊息</th></tr>
        ${publishLog.map(r => `<tr>
          <td style="white-space:nowrap">${relTime(r.posted_at)}</td>
          <td>${platLabel(r.platform)}</td>
          <td>${r.success ? '✅ 成功' : '❌ 失敗'}</td>
          <td style="font-size:.85rem;color:var(--text-secondary)">${esc(r.error_message || '').slice(0, 60)}</td>
        </tr>`).join('')}
      </table></div>`;
  }
  else if (tabId === 'modal-tab-engagement') {
    tabContent.innerHTML = engagement.length === 0
      ? '<p style="color:var(--text-muted)">尚無互動數據（發布後 1-24 小時會自動抓取）</p>'
      : `<div class="table-container"><table>
        <tr><th>時間</th><th>平台</th><th>❤️</th><th>💬</th><th>🔄</th><th>👁️</th></tr>
        ${engagement.map(r => `<tr>
          <td style="white-space:nowrap;font-size:.85rem">${relTime(r.fetched_at)}</td>
          <td>${platLabel(r.platform)}</td>
          <td>${r.likes || 0}</td>
          <td>${r.comments || r.replies || 0}</td>
          <td>${r.shares || r.reposts || 0}</td>
          <td>${r.views || 0}</td>
        </tr>`).join('')}
      </table></div>`;
  }
}

// === Utility ===
function esc(s) {
  if (!s) return '';
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function relTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso.replace(' ', 'T'));
  if (isNaN(t.getTime())) return iso;
  const now = new Date();
  const diff = now - t;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '剛剛';
  if (mins < 60) return `${mins} 分鐘前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小時前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return t.toLocaleDateString('zh-TW');
}

function platLabel(p) {
  const labels = { 'facebook': '📘', 'instagram': '📸', 'threads': '🧵', 'fb': '📘', 'ig': '📸' };
  return labels[p] || p;
}

function statusBadge(s) {
  const colors = {
    'fetched': 'badge badge-gray',
    'scored': 'badge badge-blue',
    'drafted': 'badge badge-purple',
    'queued': 'badge badge-yellow',
    'published': 'badge badge-green',
    'dropped': 'badge badge-red',
    'completed': 'badge badge-green',
  };
  return `<span class="${colors[s] || 'badge badge-gray'}">${esc(s || '')}</span>`;
}

function queueStatusBadge(s) {
  const map = {
    'queued': '<span class="badge badge-yellow">📋 等待</span>',
    'published': '<span class="badge badge-green">✅ 已發</span>',
    'failed': '<span class="badge badge-red">❌ 失敗</span>',
    'stale': '<span class="badge badge-gray">⏳ 過期</span>',
  };
  return map[s] || `<span class="badge badge-gray">${esc(s || '—')}</span>`;
}

function scoreBadge(score) {
  if (score == null) return '<span class="badge badge-gray">—</span>';
  const cls = score >= 0.8 ? 'badge badge-green' : score >= 0.65 ? 'badge badge-yellow' : 'badge badge-red';
  return `<span class="${cls}">${score.toFixed(2)}</span>`;
}

function topicBadge(t) {
  if (!t) return '';
  const colors = {
    'ai_model': 'badge badge-green',
    'ai_agent': 'badge badge-blue',
    'ai_tooling': 'badge badge-purple',
  };
  const cls = colors[t] || 'badge badge-gray';
  return `<span class="${cls}">${esc(t)}</span>`;
}

function updateQueueBadge() {
  const count = qOne("SELECT COUNT(*) as c FROM drafts WHERE queue_status='queued'");
  const badge = document.getElementById('queue-badge');
  if (badge) badge.textContent = count?.c || 0;
}

function setDBStatus(state, text) {
  const statusEl = document.getElementById('db-status');
  if (!statusEl) return;
  const dot = statusEl.querySelector('.status-dot');
  if (dot) { dot.className = 'status-dot ' + state; }
  const label = statusEl.querySelector('span:last-child');
  if (label) label.textContent = text;
}

function showError(msg) {
  const banner = document.getElementById('error-banner');
  if (!banner) return;
  document.getElementById('error-text').textContent = msg;
  banner.style.display = 'flex';
  setTimeout(() => banner.style.display = 'none', 8000);
}

function showLoading(msg) {
  const overlay = document.getElementById('loading-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  const p = overlay.querySelector('p');
  if (p && msg) p.textContent = msg;
}

function hideLoading() {
  const overlay = document.getElementById('loading-overlay');
  if (overlay) overlay.classList.add('hidden');
}

async function fetchLastRun() {
  try {
    const resp = await fetch(CONFIG.lastRunUrl());
    if (resp.ok) {
      const text = await resp.text();
      document.getElementById('last-update').textContent =
        '最近 pipeline: ' + (text.match(/last_run_utc:\s*(.+)/)?.[1] || '').slice(0, 19) || '—';
    }
  } catch (e) {}
}

// === Toast Notification System ===
function showToast(message, type = 'info') {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none';
    document.body.appendChild(container);
  }

  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  const bgColors = { success: '#065f46', error: '#5c1a1a', info: '#1a3a5c' };
  const borderColors = { success: '#34d399', error: '#f87171', info: '#60a5fa' };

  const toast = document.createElement('div');
  toast.style.cssText = [
    'display:flex;align-items:center;gap:8px;padding:12px 16px',
    'border-radius:8px;background:' + (bgColors[type] || bgColors.info),
    'border:1px solid ' + (borderColors[type] || borderColors.info),
    'color:#e8eaed;font-size:.9rem;box-shadow:0 4px 12px rgba(0,0,0,.3)',
    'pointer-events:auto;transition:all .3s ease;transform:translateX(100%);opacity:0',
    'max-width:400px;word-break:break-word'
  ].join(';');
  toast.innerHTML = '<span>' + (icons[type] || 'ℹ️') + '</span><span>' + esc(message) + '</span>';
  container.appendChild(toast);

  // Trigger slide-in on next frame
  requestAnimationFrame(function () {
    toast.style.transform = 'translateX(0)';
    toast.style.opacity = '1';
  });

  setTimeout(function () {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(function () { toast.remove(); }, 300);
  }, 3000);
}

// === Analytics Page ===
function renderAnalytics() {
  var page = document.getElementById('page-analytics');

  // Destroy existing charts before re-render
  ['engagementTrend', 'topicRadar', 'lifecycle'].forEach(function (k) {
    if (charts[k]) { charts[k].destroy(); delete charts[k]; }
  });

  if (!db) {
    page.innerHTML = '<div class="page-header"><h2>分析</h2><p>請等待資料庫載入</p></div>';
    return;
  }

  page.innerHTML = [
    '<div class="page-header">',
    '  <h2>分析</h2>',
    '  <p>互動趨勢、主題表現、貼文生命週期</p>',
    '</div>',
    '<div class="card">',
    '  <h3 style="margin-bottom:12px;color:var(--text-secondary)">📈 互動率趨勢 (各平台)</h3>',
    '  <div class="chart-container"><canvas id="chart-engagement-trend"></canvas></div>',
    '</div>',
    '<div style="display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:16px">',
    '  <div class="card">',
    '    <h3 style="margin-bottom:12px;color:var(--text-secondary)">🎯 主題表現</h3>',
    '    <div class="chart-container"><canvas id="chart-topic-radar"></canvas></div>',
    '  </div>',
    '  <div class="card">',
    '    <h3 style="margin-bottom:12px;color:var(--text-secondary)">🔄 貼文生命週期</h3>',
    '    <div class="chart-container"><canvas id="chart-lifecycle"></canvas></div>',
    '  </div>',
    '</div>'
  ].join('\n');

  // Init charts after DOM paint
  setTimeout(function () {
    renderEngagementTrend();
    renderTopicRadar();
    renderLifecycleChart();
  }, 50);
}

function renderEngagementTrend() {
  var canvas = document.getElementById('chart-engagement-trend');
  if (!canvas) return;

  var data = q([
    'SELECT DATE(p.posted_at) as date, p.platform,',
    '       AVG(COALESCE(e.likes,0) + COALESCE(e.comments,0) + COALESCE(e.shares,0)) as avg_engagement,',
    '       COUNT(DISTINCT p.draft_id) as post_count',
    'FROM publish_log p',
    'LEFT JOIN engagement_stats_latest e ON e.draft_id = p.draft_id AND e.platform = p.platform',
    'WHERE p.success = 1 AND p.posted_at >= DATE("now", "-30 days")',
    'GROUP BY DATE(p.posted_at), p.platform',
    'ORDER BY date'
  ].join(' '));

  if (data.length === 0) {
    var ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  // Build date list sorted
  var dateSet = {};
  data.forEach(function (d) { dateSet[d.date] = true; });
  var dates = Object.keys(dateSet).sort();

  var platformMap = { facebook: 'FB', instagram: 'IG', threads: 'Threads' };
  var platformColors = { facebook: '#1877F2', instagram: '#E4405F', threads: '#000000' };
  var datasets = [];

  Object.keys(platformMap).forEach(function (dbPlat) {
    var label = platformMap[dbPlat];
    var platData = dates.map(function (date) {
      var row = null;
      for (var i = 0; i < data.length; i++) {
        if (data[i].date === date && data[i].platform === dbPlat) { row = data[i]; break; }
      }
      return (row && row.post_count > 0) ? +((row.avg_engagement || 0) / row.post_count).toFixed(2) : null;
    });
    datasets.push({
      label: label,
      data: platData,
      borderColor: platformColors[dbPlat] || '#60a5fa',
      backgroundColor: platformColors[dbPlat] || '#60a5fa',
      tension: 0.3,
      spanGaps: false,
      pointRadius: 3,
      pointHoverRadius: 5,
      fill: false
    });
  });

  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  try {
    charts.engagementTrend = new Chart(ctx, {
      type: 'line',
      data: { labels: dates, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#9aa0a6', font: { size: 12 } } },
          tooltip: {
            backgroundColor: '#232734', titleColor: '#e8eaed',
            bodyColor: '#9aa0a6', borderColor: '#303446', borderWidth: 1
          }
        },
        scales: {
          x: {
            ticks: { color: '#5f6368', maxTicksLimit: 10 },
            grid: { color: '#303446' }
          },
          y: {
            beginAtZero: true,
            ticks: { color: '#5f6368' },
            grid: { color: '#303446' },
            title: { display: true, text: '平均互動 (讚+留言+分享)', color: '#9aa0a6' }
          }
        }
      }
    });
  } catch (e) {
    console.error('Engagement trend chart error:', e);
  }
}

function renderTopicRadar() {
  var canvas = document.getElementById('chart-topic-radar');
  if (!canvas) return;

  var topics = q([
    'SELECT topic_category, AVG(weighted_score) as avg_score, COUNT(*) as count',
    'FROM news_items',
    'WHERE topic_category IS NOT NULL AND topic_category != ""',
    'GROUP BY topic_category',
    'ORDER BY avg_score DESC',
    'LIMIT 8'
  ].join(' '));

  if (topics.length === 0) {
    var ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  try {
    charts.topicRadar = new Chart(ctx, {
      type: 'radar',
      data: {
        labels: topics.map(function (t) { return t.topic_category; }),
        datasets: [{
          label: '平均加權分數',
          data: topics.map(function (t) { return +((t.avg_score || 0)).toFixed(2); }),
          backgroundColor: 'rgba(96, 165, 250, 0.2)',
          borderColor: '#60a5fa',
          pointBackgroundColor: '#60a5fa',
          pointBorderColor: '#fff',
          pointRadius: 4,
          borderWidth: 2
        }, {
          label: '樣本數',
          data: topics.map(function (t) { return t.count || 0; }),
          backgroundColor: 'rgba(52, 211, 153, 0.2)',
          borderColor: '#34d399',
          pointBackgroundColor: '#34d399',
          pointBorderColor: '#fff',
          pointRadius: 4,
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { color: '#9aa0a6', font: { size: 11 } } },
          tooltip: {
            backgroundColor: '#232734', titleColor: '#e8eaed',
            bodyColor: '#9aa0a6', borderColor: '#303446', borderWidth: 1
          }
        },
        scales: {
          r: {
            angleLines: { color: '#303446' },
            grid: { color: '#303446' },
            pointLabels: { color: '#9aa0a6', font: { size: 11 } },
            ticks: { color: '#5f6368', backdropColor: 'transparent', font: { size: 10 } }
          }
        }
      }
    });
  } catch (e) {
    console.error('Topic radar chart error:', e);
  }
}

function renderLifecycleChart() {
  var canvas = document.getElementById('chart-lifecycle');
  if (!canvas) return;

  var data = q([
    'SELECT ',
    '  CASE ',
    '    WHEN (julianday(e.fetched_at) - julianday(p.posted_at)) * 24 < 1 THEN "1h以内"',
    '    WHEN (julianday(e.fetched_at) - julianday(p.posted_at)) * 24 < 24 THEN "1-24h"',
    '    ELSE "24-168h"',
    '  END as bucket,',
    '  COUNT(*) as count',
    'FROM engagement_stats e',
    'JOIN publish_log p ON p.draft_id = e.draft_id AND p.platform = e.platform',
    'WHERE p.success = 1 AND e.fetched_at IS NOT NULL AND p.posted_at IS NOT NULL',
    'GROUP BY bucket',
    'ORDER BY bucket'
  ].join(' '));

  var bucketOrder = ['1h以内', '1-24h', '24-168h'];
  var bucketLabels = { '1h以内': '發布後 1h 內', '1-24h': '1h ~ 24h', '24-168h': '24h ~ 168h' };
  var bucketColors = { '1h以内': '#34d399', '1-24h': '#fbbf24', '24-168h': '#f87171' };

  var labels = bucketOrder.map(function (b) { return bucketLabels[b] || b; });
  var values = bucketOrder.map(function (b) {
    for (var i = 0; i < data.length; i++) {
      if (data[i].bucket === b) return data[i].count;
    }
    return 0;
  });
  var colors = bucketOrder.map(function (b) { return bucketColors[b] || '#60a5fa'; });

  if (values.every(function (v) { return v === 0; })) {
    var ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  try {
    charts.lifecycle = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: '互動數',
          data: values,
          backgroundColor: colors.map(function (c) { return c + '33'; }),
          borderColor: colors,
          borderWidth: 2,
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#232734', titleColor: '#e8eaed',
            bodyColor: '#9aa0a6', borderColor: '#303446', borderWidth: 1
          }
        },
        scales: {
          x: {
            ticks: { color: '#5f6368' },
            grid: { display: false }
          },
          y: {
            beginAtZero: true,
            ticks: { color: '#5f6368', precision: 0 },
            grid: { color: '#303446' }
          }
        }
      }
    });
  } catch (e) {
    console.error('Lifecycle chart error:', e);
  }
}

// === Source Submission Page ===
var SOURCE_STORAGE_KEY = 'news_radar_pending_sources';

function renderSource() {
  var page = document.getElementById('page-source');

  page.innerHTML = [
    '<div class="page-header">',
    '  <h2>提交來源</h2>',
    '  <p>提交新的新聞來源 URL 供系統收錄與分析</p>',
    '</div>',
    '<div class="card" style="margin-bottom:20px">',
    '  <div style="margin-bottom:16px">',
    '    <label style="display:block;margin-bottom:6px;font-weight:600;color:var(--text-secondary)">URL / 貼文連結</label>',
    '    <textarea id="source-urls" rows="4" placeholder="每行一個 URL，支援：文章連結、RSS feed、社群貼文..."',
    '      style="width:100%;padding:10px 12px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:.9rem;resize:vertical;font-family:inherit"></textarea>',
    '  </div>',
    '  <div style="margin-bottom:16px">',
    '    <label style="display:block;margin-bottom:6px;font-weight:600;color:var(--text-secondary)">目標平台</label>',
    '    <div style="display:flex;gap:16px;flex-wrap:wrap">',
    '      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text-secondary)">',
    '        <input type="checkbox" value="facebook" onchange="toggleAllPlatforms()" style="accent-color:var(--accent)"> FB',
    '      </label>',
    '      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text-secondary)">',
    '        <input type="checkbox" value="instagram" onchange="toggleAllPlatforms()" style="accent-color:var(--accent)"> IG',
    '      </label>',
    '      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text-secondary)">',
    '        <input type="checkbox" value="threads" onchange="toggleAllPlatforms()" style="accent-color:var(--accent)"> Threads',
    '      </label>',
    '      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:var(--text-muted);border-left:1px solid var(--border);padding-left:16px">',
    '        <input type="checkbox" value="all" onchange="toggleAllPlatforms()" style="accent-color:var(--accent)"> 全部',
    '      </label>',
    '    </div>',
    '  </div>',
    '  <div style="margin-bottom:16px">',
    '    <label style="display:block;margin-bottom:6px;font-weight:600;color:var(--text-secondary)">備註 <span style="font-weight:400;color:var(--text-muted)">(選填)</span></label>',
    '    <input id="source-notes" type="text" placeholder="補充說明、分類建議..."',
    '      style="width:100%;padding:8px 12px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:.9rem;font-family:inherit">',
    '  </div>',
    '  <button onclick="submitSource()" style="padding:8px 20px;border-radius:var(--radius-sm);border:none;background:var(--accent);color:#fff;font-weight:600;font-size:.9rem;cursor:pointer">📤 提交</button>',
    '</div>',
    '<h3 style="margin-bottom:12px;color:var(--text-secondary);font-size:1rem" id="pending-sources-count">📋 待處理來源 (' + loadPendingSources().length + ')</h3>',
    '<div id="pending-sources-list"></div>'
  ].join('\n');

  renderPendingSources();
}

function loadPendingSources() {
  try {
    var raw = localStorage.getItem(SOURCE_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function savePendingSources(sources) {
  try {
    localStorage.setItem(SOURCE_STORAGE_KEY, JSON.stringify(sources));
  } catch (e) {
    showToast('儲存失敗，localStorage 可能已滿', 'error');
  }
}

function renderPendingSources() {
  var container = document.getElementById('pending-sources-list');
  if (!container) return;

  var sources = loadPendingSources();

  if (sources.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="empty-icon">📤</div><p>尚無待處理的來源</p></div>';
    return;
  }

  var platformLabels = { facebook: '📘FB', instagram: '📸IG', threads: '🧵Threads' };

  container.innerHTML = '<div class="table-container"><table>' +
    '<tr><th>時間</th><th>URL</th><th>目標平台</th><th>備註</th><th></th></tr>' +
    sources.map(function (s) {
      var platHtml = (s.platforms || []).map(function (p) {
        return platformLabels[p] || p;
      }).join(' ') || '<span style="color:var(--text-muted)">未指定</span>';
      return '<tr>' +
        '<td style="white-space:nowrap;font-size:.85rem">' + relTime(s.submittedAt) + '</td>' +
        '<td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(s.url) + '">' +
          '<a href="' + esc(s.url) + '" target="_blank" style="color:var(--accent);text-decoration:none">' + esc(s.url.slice(0, 60)) + '</a>' +
        '</td>' +
        '<td>' + platHtml + '</td>' +
        '<td style="color:var(--text-muted);font-size:.85rem;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
          (s.notes ? esc(s.notes) : '<span style="color:var(--text-muted)">—</span>') +
        '</td>' +
        '<td><button onclick="deleteSource(\'' + esc(s.id) + '\')" style="background:none;border:1px solid var(--red);color:var(--red);border-radius:var(--radius-sm);padding:4px 10px;cursor:pointer;font-size:.8rem">刪除</button></td>' +
      '</tr>';
    }).join('') +
    '</table></div>';
}

function submitSource() {
  var textarea = document.getElementById('source-urls');
  var notesInput = document.getElementById('source-notes');

  if (!textarea) return;

  var raw = textarea.value.trim();
  if (!raw) {
    showToast('請輸入至少一個 URL', 'error');
    textarea.focus();
    return;
  }

  var urls = raw.split('\n').map(function (u) { return u.trim(); }).filter(function (u) { return u.length > 0; });

  // Validate URLs
  for (var i = 0; i < urls.length; i++) {
    try { new URL(urls[i]); } catch (e) {
      showToast('無效的 URL: ' + urls[i].slice(0, 40), 'error');
      return;
    }
  }

  // Get selected platforms
  var platCheckboxes = document.querySelectorAll('#page-source input[type="checkbox"]:not([value="all"])');
  var selectedPlatforms = [];
  platCheckboxes.forEach(function (cb) {
    if (cb.checked) selectedPlatforms.push(cb.value);
  });

  var notes = notesInput ? notesInput.value.trim() : '';

  // Save to localStorage
  var sources = loadPendingSources();
  var now = new Date().toISOString();

  urls.forEach(function (url) {
    sources.push({
      id: 'src_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
      url: url,
      platforms: selectedPlatforms.slice(),
      notes: notes,
      submittedAt: now
    });
  });

  savePendingSources(sources);

  // Clear form
  textarea.value = '';
  if (notesInput) notesInput.value = '';
  document.querySelectorAll('#page-source input[type="checkbox"]').forEach(function (cb) { cb.checked = false; });

  showToast('已提交 ' + urls.length + ' 個來源', 'success');

  renderPendingSources();
  var h3 = document.getElementById('pending-sources-count');
  if (h3) h3.textContent = '📋 待處理來源 (' + loadPendingSources().length + ')';
}

function deleteSource(id) {
  if (!id) return;
  var sources = loadPendingSources();
  var filtered = [];
  for (var i = 0; i < sources.length; i++) {
    if (sources[i].id !== id) filtered.push(sources[i]);
  }
  if (filtered.length === sources.length) return;

  savePendingSources(filtered);
  showToast('已刪除來源', 'info');
  renderPendingSources();

  var h3 = document.getElementById('pending-sources-count');
  if (h3) h3.textContent = '📋 待處理來源 (' + loadPendingSources().length + ')';
}

function toggleAllPlatforms() {
  var page = document.getElementById('page-source');
  if (!page) return;
  var allCb = page.querySelector('input[type="checkbox"][value="all"]');
  var platCbs = page.querySelectorAll('input[type="checkbox"]:not([value="all"])');

  if (!allCb) return;

  if (allCb.checked) {
    platCbs.forEach(function (cb) { cb.checked = true; });
  }

  // Sync "全部" checkbox: if every individual is checked, check "全部"
  var allChecked = true;
  platCbs.forEach(function (cb) { if (!cb.checked) allChecked = false; });
  allCb.checked = allChecked;
}

// === Changelog Page ===
async function renderChangelog() {
  var page = document.getElementById('page-changelog');
  page.innerHTML = [
    '<div class="page-header">',
    '  <h2>更新日誌</h2>',
    '  <p>正在載入變更紀錄…</p>',
    '</div>',
    '<div class="loader" style="margin:48px auto"></div>'
  ].join('\n');

  try {
    var resp = await fetch(CONFIG.changelogUrl(), { cache: 'no-cache' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    var md = await resp.text();

    page.innerHTML = [
      '<div class="page-header">',
      '  <h2>更新日誌</h2>',
      '  <p>專案變更紀錄 <a href="' + esc(CONFIG.changelogUrl()) + '" target="_blank" style="color:var(--accent);font-size:.85rem">查看原始檔 ↗</a></p>',
      '</div>',
      '<div class="persona-section changelog-content">',
      simpleMarkdownToHtml(md),
      '</div>'
    ].join('\n');
  } catch (err) {
    page.innerHTML = [
      '<div class="page-header">',
      '  <h2>更新日誌</h2>',
      '  <p>載入失敗</p>',
      '</div>',
      '<div class="error-banner">⚠️ 無法載入 CHANGELOG.md: ' + esc(err.message) + '</div>'
    ].join('\n');
  }
}

function simpleMarkdownToHtml(md) {
  if (!md) return '<p style="color:var(--text-muted)">無內容</p>';

  var html = esc(md);

  // Code blocks (```...```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
    return '<pre style="background:var(--bg-primary);padding:12px;border-radius:var(--radius-sm);overflow-x:auto;font-size:.85rem;line-height:1.5;margin:8px 0"><code>' + code.trim() + '</code></pre>';
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-primary);padding:2px 6px;border-radius:3px;font-size:.85rem;color:var(--accent)">$1</code>');

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4 style="margin:16px 0 8px;color:var(--text-primary);font-size:1rem">$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3 style="margin:20px 0 10px;color:var(--text-primary);font-size:1.05rem">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 style="margin:24px 0 12px;color:var(--text-primary);font-size:1.2rem">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 style="margin:24px 0 12px;color:var(--text-primary);font-size:1.4rem">$1</h1>');

  // Links [text](url)
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:var(--accent);text-decoration:none">$1</a>');

  // Bold and italic
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // Horizontal rules
  html = html.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:16px 0">');

  // Blockquotes
  html = html.replace(/^> (.+)$/gm, '<blockquote style="border-left:3px solid var(--accent);padding:4px 12px;margin:8px 0;color:var(--text-secondary)">$1</blockquote>');

  // Unordered list items
  html = html.replace(/^[\s]*[-*+] (.+)$/gm, '<li style="margin:4px 0;color:var(--text-secondary)">$1</li>');

  // Wrap consecutive <li> in <ul>
  html = html.replace(/((?:<li[^>]*>.*?<\/li>\s*)+)/g, '<ul style="padding-left:20px;margin:8px 0">$1</ul>');

  // Wrap remaining non-tag lines in paragraphs
  var lines = html.split('\n');
  var wrapped = [];
  var blockTags = { h1: true, h2: true, h3: true, h4: true, pre: true, ul: true, li: true, blockquote: true, hr: true };
  var inBlock = false;
  var closeMap = { pre: '</pre>', ul: '</ul>', blockquote: '</blockquote>' };

  for (var i = 0; i < lines.length; i++) {
    var trimmed = lines[i].trim();
    // Detect entering block
    var openMatch = trimmed.match(/^<(h[1-4]|pre|ul|blockquote|hr)/);
    if (openMatch) {
      wrapped.push(lines[i]);
      var tag = openMatch[1];
      if (closeMap[tag]) inBlock = tag;
      continue;
    }
    // Detect leaving block
    if (inBlock) {
      wrapped.push(lines[i]);
      if (trimmed.indexOf(closeMap[inBlock]) >= 0) inBlock = false;
      continue;
    }
    // Skip empty lines and block-level fragments
    if (!trimmed || trimmed.indexOf('<li') === 0) {
      wrapped.push('');
      continue;
    }
    wrapped.push('<p style="margin:8px 0;line-height:1.7;color:var(--text-secondary)">' + trimmed + '</p>');
  }

  return wrapped.join('\n');
}

// ====================================================================
// Toast 通知系統
// ====================================================================
function showToast(message, type) {
  type = type || 'info';
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  const bgColor = type === 'success' ? '#065f46' : type === 'error' ? '#5c1a1a' : '#1a3a5c';
  const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
  toast.style.cssText = 'background:' + bgColor + ';color:#e8eaed;padding:10px 16px;border-radius:var(--radius-sm);font-size:.9rem;pointer-events:auto;animation:slideIn .3s ease;box-shadow:0 2px 8px rgba(0,0,0,.3)';
  toast.innerHTML = icon + ' ' + message;
  container.appendChild(toast);
  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity .3s';
    setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
  }, 3000);
}

// Add keyframes for toast animation
var style = document.createElement('style');
style.textContent = '@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }';
document.head.appendChild(style);

// ====================================================================
// Analytics Page — Chart.js 圖表分析
// ====================================================================
function renderAnalytics() {
  var page = document.getElementById('page-analytics');
  // Destroy existing charts
  Object.keys(charts).forEach(function(k) { if (charts[k]) { charts[k].destroy(); delete charts[k]; } });
  page.innerHTML = '<div class="page-header"><h2>📊 互動分析</h2><p>基於 Meta API 數據的視覺化分析</p></div>' +
    '<div class="stats-grid" id="analytics-stats">' +
    '<div class="stat-card"><div class="stat-label">分析資料庫</div><div class="stat-value" style="font-size:1rem">等待資料載入…</div></div></div>' +
    '<div id="analytics-charts"><div class="empty-state"><div class="empty-icon">📊</div><p>需要更多數據才能產生圖表</p><p style="font-size:.85rem">至少須有 5 篇以上貼文且有互動數據</p></div></div>';

  if (!db) return;

  // Fetch data: daily trends & topic performance
  var dailyTrends = q('SELECT DATE(e.fetched_at) as day, e.platform, COUNT(DISTINCT e.draft_id) as post_count, SUM(e.likes) as total_likes, SUM(e.comments) as total_comments, MAX(e.reach) as total_reach, MAX(e.views) as total_views FROM engagement_stats e WHERE e.fetched_at >= datetime("now", "-30 days", "localtime") GROUP BY DATE(e.fetched_at), e.platform ORDER BY day ASC');
  var topicPerf = q('SELECT n.topic_category, e.platform, AVG(e.likes) as avg_likes, AVG(e.comments) as avg_comments, COUNT(*) as post_count FROM engagement_stats e JOIN drafts d ON d.id = e.draft_id JOIN news_items n ON n.id = d.news_id WHERE n.topic_category IS NOT NULL AND e.fetched_at >= datetime("now", "-30 days", "localtime") GROUP BY n.topic_category, e.platform HAVING COUNT(*) >= 2');

  // Update stats
  var totalEngagements = 0;
  dailyTrends.forEach(function(r) { totalEngagements += (r.total_likes||0) + (r.total_comments||0); });
  document.getElementById('analytics-stats').innerHTML =
    '<div class="stat-card"><div class="stat-label">分析期</div><div class="stat-value" style="font-size:1rem">過去 30 天</div><div class="stat-sub">' + dailyTrends.length + ' 筆日均數據</div></div>' +
    '<div class="stat-card"><div class="stat-label">總互動</div><div class="stat-value">' + totalEngagements + '</div></div>' +
    '<div class="stat-card"><div class="stat-label">主題</div><div class="stat-value">' + topicPerf.length + '</div><div class="stat-sub">主題-平台組合</div></div>';

  if (dailyTrends.length === 0) return;

  var chartsDiv = document.getElementById('analytics-charts');
  chartsDiv.innerHTML = '';

  // Chart 1: Engagement Rate Trend
  var chart1Div = document.createElement('div');
  chart1Div.style.cssText = 'background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:16px';
  chart1Div.innerHTML = '<h4 style="margin-bottom:12px;color:var(--text-secondary)">日均互動趨勢</h4><div class="chart-container" style="height:250px"><canvas id="chart-trend"></canvas></div>';
  chartsDiv.appendChild(chart1Div);

  var days = [];
  var fbData = [], igData = [], thData = [];
  var platformMap = {};
  dailyTrends.forEach(function(r) {
    if (!platformMap[r.day]) platformMap[r.day] = {};
    platformMap[r.day][r.platform] = (r.total_likes||0) + (r.total_comments||0);
  });
  Object.keys(platformMap).sort().forEach(function(d) {
    days.push(d.slice(5)); // MM-DD
    fbData.push(platformMap[d].facebook || 0);
    igData.push(platformMap[d].instagram || 0);
    thData.push(platformMap[d].threads || 0);
  });

  var ctx1 = document.getElementById('chart-trend').getContext('2d');
  charts['trend'] = new Chart(ctx1, {
    type: 'line',
    data: {
      labels: days,
      datasets: [
        { label: 'FB', data: fbData, borderColor: '#1877f2', backgroundColor: '#1877f233', fill: true, tension: 0.3 },
        { label: 'IG', data: igData, borderColor: '#e4405f', backgroundColor: '#e4405f33', fill: true, tension: 0.3 },
        { label: 'Threads', data: thData, borderColor: '#000000', backgroundColor: '#00000033', fill: true, tension: 0.3 }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#9aa0a6' } } }, scales: { x: { ticks: { color: '#5f6368' } }, y: { ticks: { color: '#5f6368' }, beginAtZero: true } } }
  });

  // Chart 2: Topic Radar
  if (topicPerf.length > 3) {
    var chart2Div = document.createElement('div');
    chart2Div.style.cssText = 'background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:16px';
    chart2Div.innerHTML = '<h4 style="margin-bottom:12px;color:var(--text-secondary)">主題表現雷達 (by avg_likes)</h4><div class="chart-container" style="height:280px"><canvas id="chart-radar"></canvas></div>';
    chartsDiv.appendChild(chart2Div);

    var topics = [], topicValues = [];
    var topicSeen = {};
    topicPerf.forEach(function(r) {
      if (!topicSeen[r.topic_category]) {
        topicSeen[r.topic_category] = true;
        topics.push(r.topic_category);
        topicValues.push(r.avg_likes || 0);
      }
    });

    var ctx2 = document.getElementById('chart-radar').getContext('2d');
    charts['radar'] = new Chart(ctx2, {
      type: 'radar',
      data: { labels: topics, datasets: [{ label: 'avg_likes', data: topicValues, borderColor: '#60a5fa', backgroundColor: '#60a5fa33' }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { r: { ticks: { color: '#5f6368', backdropColor: 'transparent' }, grid: { color: '#303446' } } } }
    });
  }

  showToast('圖表已更新', 'info');
}

// ====================================================================
// Source 提交頁 — 用戶貼上來源 URL，選擇平台
// ====================================================================
function renderSource() {
  var page = document.getElementById('page-source');

  // Load from localStorage
  var pendingSources = JSON.parse(localStorage.getItem('newsRadar_sources') || '[]');

  var platformOptions = [
    { id: 'fb', label: '📘 Facebook' },
    { id: 'ig', label: '📸 Instagram' },
    { id: 'threads', label: '🧵 Threads' },
  ];

  var html = '<div class="page-header"><h2>📤 提交來源</h2><p>貼上你想發布的文章 URL，選擇平台</p></div>';

  // Submission form
  html += '<div class="persona-section" style="margin-bottom:20px">';
  html += '<h3>新增來源</h3>';
  html += '<label style="display:block;margin-bottom:6px;color:var(--text-secondary);font-size:.9rem">文章 URL</label>';
  html += '<textarea id="source-url" style="width:100%;min-height:60px;padding:8px 12px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-card);color:var(--text-primary);font-size:.9rem;font-family:inherit;resize:vertical" placeholder="https://example.com/article 或 https://... 每行一個連結"></textarea>';
  html += '<div style="margin:12px 0"><span style="color:var(--text-secondary);font-size:.9rem">發布到：</span>';
  platformOptions.forEach(function(p) {
    html += '<label style="display:inline-flex;align-items:center;gap:4px;margin:0 12px 0 0;cursor:pointer;color:var(--text-secondary);font-size:.9rem">';
    html += '<input type="checkbox" class="source-platform" value="' + p.id + '" checked>';
    html += p.label + '</label>';
  });
  html += '</div>';
  html += '<label style="display:block;margin-bottom:6px;color:var(--text-secondary);font-size:.9rem">備註 (選填)</label>';
  html += '<input id="source-note" type="text" style="width:100%;padding:8px 12px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--bg-card);color:var(--text-primary);font-size:.9rem" placeholder="例如：這篇的觀點很特別，適合 Threads 辛辣風格">';
  html += '<button onclick="submitSource()" style="margin-top:12px;padding:10px 24px;border-radius:var(--radius-sm);border:none;background:var(--accent);color:#fff;font-size:.9rem;font-weight:600;cursor:pointer">📤 提交</button>';
  html += '</div>';

  // Pending sources list
  html += '<div class="persona-section">';
  html += '<h3>待處理來源 (' + pendingSources.length + ')</h3>';
  if (pendingSources.length === 0) {
    html += '<div class="empty-state"><div class="empty-icon">📭</div><p>尚無待處理來源</p><p style="font-size:.85rem">貼上 URL 後提交，下次 pipeline 會自動處理</p></div>';
  } else {
    html += '<div class="table-container"><table><tr><th>時間</th><th>URL</th><th>平台</th><th>備註</th><th></th></tr>';
    pendingSources.forEach(function(s, i) {
      var platforms = (s.platforms || []).map(function(p) { return {fb:'📘',ig:'📸',threads:'🧵'}[p] || p; }).join(' ');
      html += '<tr><td style="white-space:nowrap">' + s.time + '</td><td style="max-width:250px;overflow:hidden;text-overflow:ellipsis"><a href="' + s.url + '" target="_blank" style="color:var(--accent)">' + (s.url.length > 50 ? s.url.slice(0,50)+'…' : s.url) + '</a></td>';
      html += '<td>' + platforms + '</td><td style="color:var(--text-muted);font-size:.85rem">' + (s.note || '—').slice(0,30) + '</td>';
      html += '<td><button onclick="removeSource(' + i + ')" style="background:none;border:1px solid var(--border);border-radius:var(--radius-sm);padding:4px 8px;color:var(--red);cursor:pointer;font-size:.8rem">刪除</button></td></tr>';
    });
    html += '</table></div>';
  }
  html += '</div>';

  page.innerHTML = html;
}

function submitSource() {
  var url = document.getElementById('source-url').value.trim();
  if (!url) { showToast('請輸入文章 URL', 'error'); return; }

  var checkboxes = document.querySelectorAll('.source-platform:checked');
  if (checkboxes.length === 0) { showToast('請選擇至少一個平台', 'error'); return; }

  var platforms = [];
  checkboxes.forEach(function(cb) { platforms.push(cb.value); });

  var note = document.getElementById('source-note').value.trim();

  var entry = {
    url: url,
    platforms: platforms,
    note: note,
    time: new Date().toLocaleString('zh-TW'),
    id: 'src_' + Date.now()
  };

  var pending = JSON.parse(localStorage.getItem('newsRadar_sources') || '[]');
  pending.unshift(entry);
  localStorage.setItem('newsRadar_sources', JSON.stringify(pending));

  showToast('✅ 已接收，下一輪 pipeline 會處理', 'success');
  document.getElementById('source-url').value = '';
  document.getElementById('source-note').value = '';
  renderSource();
}

function removeSource(index) {
  var pending = JSON.parse(localStorage.getItem('newsRadar_sources') || '[]');
  pending.splice(index, 1);
  localStorage.setItem('newsRadar_sources', JSON.stringify(pending));
  renderSource();
  showToast('已移除', 'info');
}

// ====================================================================
// Changelog 頁 — 從 GitHub raw 讀取 CHANGELOG.md
// ====================================================================
function renderChangelog() {
  var page = document.getElementById('page-changelog');
  page.innerHTML = '<div class="page-header"><h2>📝 更新日誌</h2><p>系統重大更動記錄</p></div><div class="loader" style="margin:48px auto"></div>';

  fetch(CONFIG.changelogUrl())
    .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function(md) {
      var lines = md.split('\n');
      var html = '';
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.indexOf('# ') === 0) html += '<h2 style="margin:20px 0 8px;font-size:1.2rem">' + line.slice(2) + '</h2>';
        else if (line.indexOf('## ') === 0) html += '<h3 style="margin:16px 0 6px;font-size:1rem;color:var(--accent)">' + line.slice(3) + '</h3>';
        else if (line.indexOf('### ') === 0) html += '<h4 style="margin:12px 0 4px;font-size:.95rem;color:var(--text-secondary)">' + line.slice(4) + '</h4>';
        else if (line.indexOf('- ') === 0) html += '<li style="margin:4px 0;color:var(--text-secondary);font-size:.9rem">' + line.slice(2) + '</li>';
        else if (line.indexOf('  ') === 0) html += '<span style="color:var(--text-muted);font-size:.85rem;margin-left:16px">' + line.trim() + '</span><br>';
        else if (line.trim()) html += '<p style="margin:8px 0;color:var(--text-secondary);line-height:1.7">' + line + '</p>';
      }
      page.innerHTML = '<div class="page-header"><h2>📝 更新日誌</h2><p>系統重大更動記錄</p></div><div class="persona-section">' + html + '</div>';
    })
    .catch(function(err) {
      page.innerHTML = '<div class="page-header"><h2>📝 更新日誌</h2></div><div class="empty-state"><div class="empty-icon">📝</div><p>尚無更新紀錄</p><p style="font-size:.85rem">' + err.message + '</p></div>';
    });
}