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
