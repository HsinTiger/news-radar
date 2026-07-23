(() => {
  "use strict";

  const API = "https://news-radar-submit.smartmmmoney.workers.dev";
  const TOKEN_KEY = "hsintiger_social_ops_owner_token";
  const PLATFORM_ORDER = ["facebook", "instagram", "threads"];
  const PLATFORM_META = {
    facebook: { label: "Facebook", short: "FB", color: "#5a8dee" },
    instagram: { label: "Instagram", short: "IG", color: "#d977d3" },
    threads: { label: "Threads", short: "TH", color: "#55d8e6" },
  };
  const STATUS_COPY = {
    queued: "等待 poller", claimed: "已領取", dispatched: "已派送",
    processing: "處理中", content_queued: "內容已入佇列", source_queued: "素材已入庫",
    draft_created: "草稿已建立", published: "已發布", partial: "部分平台已發布",
    quality_held: "品質待複核", failed: "失敗", rejected: "拒絕",
    planned: "規劃中", deleted: "已刪除", unknown: "未知",
    proposed: "待 owner 決策", approved: "已批准", applied: "已套用",
    superseded: "已取代",
  };
  const GOOD = new Set(["published", "draft_created", "healthy", "approved", "applied"]);
  const BAD = new Set(["failed", "rejected", "error"]);
  const PENDING = new Set(["queued", "claimed", "dispatched", "processing", "content_queued", "source_queued", "partial", "quality_held"]);
  let chart = null;
  let refreshTimer = null;

  const $ = (id) => document.getElementById(id);
  const token = () => sessionStorage.getItem(TOKEN_KEY) || "";
  const n = (value) => Number(value || 0);
  const fmt = (value) => new Intl.NumberFormat("zh-TW", { notation: n(value) >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n(value));
  const when = (value) => value ? new Date(value).toLocaleString("zh-TW", { month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit" }) : "未知";
  const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = content;
    return element;
  }

  function clear(element) { element.replaceChildren(); }
  function empty(element, message) { clear(element); element.appendChild(node("div", "empty", message)); }
  function badge(status) {
    const item = node("span", `badge ${GOOD.has(status) ? "good" : (BAD.has(status) ? "bad" : "warn")}`, STATUS_COPY[status] || status || "unknown");
    return item;
  }
  function showError(message) { const box=$("error"); box.hidden=!message; box.textContent=message || ""; }

  async function request(path, options = {}) {
    const headers = { Authorization:`Bearer ${token()}`, ...(options.headers || {}) };
    if (options.body) headers["Content-Type"] = "application/json";
    const response = await fetch(`${API}${path}`, { ...options, headers, cache:"no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    return data;
  }

  function setLocked(locked) {
    $("unlock-state").textContent = locked ? "尚未驗證" : "已驗證 · token 只存在本分頁";
    $("unlock-state").className = `unlock-state${locked ? "" : " good"}`;
    $("automation-mode").textContent = locked ? "LOCKED" : "LOADING";
    $("mode-dot").className = "state-dot paused";
  }

  async function unlock() {
    const candidate = $("owner-token").value.trim() || token();
    if (!candidate) { showError("請先貼上 owner token。"); return; }
    sessionStorage.setItem(TOKEN_KEY, candidate);
    try {
      await request("/api/submissions?limit=1");
      $("owner-token").value = "";
      setLocked(false);
      showError("");
      await load();
      startRefresh();
    } catch (error) {
      sessionStorage.removeItem(TOKEN_KEY);
      setLocked(true);
      showError(`驗證失敗：${error.message}`);
    }
  }

  async function load() {
    if (!token()) { setLocked(true); return; }
    $("refresh").disabled = true;
    try {
      const data = await request("/api/dashboard");
      render(data);
      showError("");
    } catch (error) {
      showError(`Dashboard 載入失敗：${error.message}`);
      if (/credential|auth|unauthorized/i.test(error.message)) {
        sessionStorage.removeItem(TOKEN_KEY);
        setLocked(true);
      }
    } finally { $("refresh").disabled = false; }
  }

  function render(data) {
    renderAutomation(data.automation || {});
    renderKpis(data);
    renderPlatforms(data);
    renderTrend(data.engagement_trend || []);
    renderHealth(data.data_health || []);
    renderSubmissions(data.recent_submissions || []);
    renderPosts(data.recent_posts || []);
    renderKnowledge(data.knowledge || {});
    renderProposals(data.learning_proposals || []);
    renderEvents(data.recent_events || []);
    $("asof").textContent = `更新 ${when(data.generated_at)}`;
    $("api-version").textContent = text(data.version, "API unknown");
  }

  function renderAutomation(automation) {
    const mode = automation.mode || "unknown";
    $("automation-mode").textContent = mode.toUpperCase();
    $("mode-dot").className = `state-dot ${mode === "live" ? "live" : (mode === "paused" ? "paused" : "error")}`;
  }

  function renderKpis(data) {
    const postCount = (data.platforms || []).reduce((sum, row) => sum + n(row.count), 0);
    const pending = (data.submissions || []).filter(row => PENDING.has(row.status)).reduce((sum, row) => sum + n(row.count), 0);
    const knowledge = data.knowledge && data.knowledge.total ? data.knowledge.total : {};
    const proposals = (data.learning_proposals || []).filter(row => row.status === "proposed").length;
    $("kpi-posts").textContent = fmt(postCount);
    $("kpi-posts-note").textContent = `${fmt((data.engagement || []).reduce((sum,row)=>sum+n(row.posts),0))} 篇有 engagement`;
    $("kpi-submissions").textContent = fmt(pending);
    $("kpi-submissions-note").textContent = pending ? "仍未到 terminal state" : "目前沒有積壓";
    $("kpi-knowledge").textContent = fmt(knowledge.items);
    $("kpi-knowledge-note").textContent = `${fmt(knowledge.uses)} 次內容採用`;
    $("kpi-proposals").textContent = fmt(proposals);
  }

  function renderPlatforms(data) {
    const host = $("platform-grid"); clear(host);
    const counts = data.platforms || [];
    const engagement = data.engagement || [];
    const audience = data.audience || [];
    const quality = data.content_quality || [];
    const health = data.data_health || [];
    PLATFORM_ORDER.forEach(platform => {
      const meta = PLATFORM_META[platform];
      const rows = counts.filter(row => row.platform === platform);
      const total = rows.reduce((sum,row)=>sum+n(row.count),0);
      const published = rows.filter(row=>row.status==="published").reduce((sum,row)=>sum+n(row.count),0);
      const lastPosted = rows.map(row=>row.last_posted_at).filter(Boolean).sort().at(-1);
      const eng = engagement.find(row=>row.platform===platform) || {};
      const aud = audience.find(row=>row.platform===platform) || {};
      const q = quality.find(row=>row.platform===platform) || {};
      const h = health.find(row=>row.platform===platform && row.metric==="latest_post_canary") ||
        health.find(row=>row.platform===platform && row.metric==="engagement_api");
      const card = node("article", "platform"); card.style.setProperty("--platform-color", meta.color);
      const head=node("div","platform-head"); const name=node("div","platform-name");
      name.append(node("span","platform-icon",meta.short),node("span","",meta.label));
      head.append(name,node("span","metric-state",h ? `${h.status.toUpperCase()} data` : "UNKNOWN data"));
      const metrics=node("div","platform-main");
      const nativeMetrics = platform === "facebook"
        ? [
          metric("平均 clicks",eng.avg_clicks===null||eng.avg_clicks===undefined?"UNKNOWN":fmt(eng.avg_clicks),`${fmt(eng.posts)} posts sampled`),
          metric("平均 actions",eng.avg_actions===null||eng.avg_actions===undefined?"UNKNOWN":fmt(eng.avg_actions),"Facebook native"),
        ]
        : platform === "instagram"
          ? [
            metric("平均 views",eng.avg_views===null||eng.avg_views===undefined?"UNKNOWN":fmt(eng.avg_views),`${fmt(eng.posts)} posts sampled`),
            metric("平均 reach",eng.avg_reach===null||eng.avg_reach===undefined?"UNKNOWN":fmt(eng.avg_reach),"Instagram native"),
          ]
          : [
            metric("平均 views",eng.avg_views===null||eng.avg_views===undefined?"UNKNOWN":fmt(eng.avg_views),`${fmt(eng.posts)} posts sampled`),
            metric("平均 actions",eng.avg_actions===null||eng.avg_actions===undefined?"UNKNOWN":fmt(eng.avg_actions),"Threads native"),
          ];
      const qualityRate = n(q.evaluated)>0
        ? `${(n(q.publish_ready_count)/n(q.evaluated)*100).toFixed(0)}%`
        : "UNKNOWN";
      metrics.append(
        metric("已發布",fmt(published),`${fmt(total)} total records`),
        ...nativeMetrics,
        metric("Followers",aud.followers===null||aud.followers===undefined?"UNKNOWN":fmt(aud.followers),aud.followers_delta_7d===null||aud.followers_delta_7d===undefined?"no 7d evidence":`${aud.followers_delta_7d>=0?"+":""}${fmt(aud.followers_delta_7d)} / 7d`),
        metric(
          "規則直通率",
          qualityRate,
          n(q.evaluated)>0
            ? `${fmt(q.rewrite_count)} rewrite · ${fmt(q.block_count)} block · coverage ${(n(q.evidence_coverage)*100).toFixed(0)}%`
            : "尚無品質 evidence",
        )
      );
      const foot=node("div","platform-foot"); foot.append(node("span","",`最後發布 ${when(lastPosted)}`),node("span","",`資料 ${when(eng.last_captured_at)}`));
      card.append(head,metrics,foot); host.appendChild(card);
    });
  }

  function metric(label,value,note){ const box=node("div","metric"); box.append(node("span","",label),node("strong","",value),node("small","",note)); return box; }

  function renderTrend(rows) {
    const emptyState = $("trend-empty");
    if (!rows.length || typeof Chart === "undefined") {
      emptyState.hidden = false;
      if (chart) { chart.destroy(); chart=null; }
      return;
    }
    emptyState.hidden = true;
    const days=[...new Set(rows.map(row=>row.day))].sort();
    const datasets=PLATFORM_ORDER.map(platform=>({
      label:PLATFORM_META[platform].label,
      data:days.map(day=>n((rows.find(row=>row.platform===platform&&row.day===day)||{}).actions)),
      borderColor:PLATFORM_META[platform].color,
      backgroundColor:`${PLATFORM_META[platform].color}22`,
      tension:.3,fill:false,borderWidth:2,pointRadius:2,
    }));
    if(chart) chart.destroy();
    chart=new Chart($("trend-chart"),{type:"line",data:{labels:days,datasets},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},plugins:{legend:{labels:{color:"#8fa6bd",usePointStyle:true,boxWidth:8}}},scales:{x:{ticks:{color:"#6f879e",maxTicksLimit:8},grid:{color:"rgba(32,58,90,.35)"}},y:{beginAtZero:true,ticks:{color:"#6f879e"},grid:{color:"rgba(32,58,90,.35)"}}}}});
  }

  function renderHealth(rows) {
    const host=$("health-list"); clear(host);
    if(!rows.length){empty(host,"UNKNOWN · 尚未同步 health snapshots");return;}
    rows.forEach(item=>{
      const severity=item.status==="healthy"?"good":(item.status==="error"?"bad":"warn");
      const row=node("div","row"); const dot=node("span",`health-dot ${severity}`);
      const main=node("div","row-main"); main.append(node("div","row-title",`${PLATFORM_META[item.platform]?.label||"System"} · ${item.metric}`),node("div","row-detail",item.detail||"沒有 detail evidence"),node("div","row-meta",`觀測 ${when(item.captured_at)}`));
      row.append(dot,main,badge(item.status)); host.appendChild(row);
    });
  }

  function renderSubmissions(rows) {
    const host=$("submission-list"); clear(host);
    if(!rows.length){empty(host,"尚無投稿紀錄");return;}
    rows.slice(0,12).forEach(item=>{
      const row=node("div","row"); const main=node("div","row-main");
      const route=item.target==="substack"?"Substack":`Meta · ${(item.platforms||[]).map(p=>PLATFORM_META[p]?.short||p).join("/")}`;
      main.append(node("div","row-title",item.note||route),node("div","row-meta",`${route} · ${item.source_type} · ${when(item.created_at)}`));
      if(item.error) main.append(node("div","row-detail",item.error));
      row.append(main,badge(item.status)); host.appendChild(row);
    });
  }

  function renderPosts(rows) {
    const host=$("post-list"); clear(host);
    if(!rows.length){empty(host,"尚未匯入平台發文紀錄");return;}
    rows.slice(0,12).forEach(item=>{
      const row=node("div","row"); const main=node("div","row-main");
      main.append(node("div","row-title",item.title||item.draft_id||item.platform_post_id||"(untitled)"),node("div","row-meta",`${PLATFORM_META[item.platform]?.label||item.platform} · ${item.topic||"unclassified"} · ${when(item.posted_at)}`));
      row.append(main,badge(item.status)); host.appendChild(row);
    });
  }

  function renderKnowledge(knowledge) {
    const host=$("topic-list"); clear(host); const rows=knowledge.topics||[];
    if(!rows.length){empty(host,"尚未匯入知識 metadata");return;}
    const max=Math.max(...rows.map(row=>n(row.items)),1);
    rows.slice(0,12).forEach(item=>{
      const row=node("div","topic"); const label=node("span","",item.topic||"unclassified");
      const bar=node("div","topic-bar"); const fill=node("div","topic-fill"); fill.style.width=`${Math.max(2,n(item.items)/max*100)}%`; bar.appendChild(fill);
      row.append(label,bar,node("span","topic-count",`${fmt(item.items)} · ${fmt(item.uses)} uses`)); host.appendChild(row);
    });
  }

  function proposalChangeText(item) {
    const change=item.proposed_change||{};
    const value=(input)=>input&&typeof input==="object"?JSON.stringify(input):text(input);
    return change.field
      ? `${change.field}: ${value(change.current_value)} → ${value(change.proposed_value)}`
      : "尚無具體 change payload";
  }

  function proposalEvidenceText(item) {
    const evidence=item.evidence||{}; const metrics=evidence.metrics||{};
    const parts=[`信心 ${text(evidence.confidence,"UNKNOWN")}`];
    if(metrics.total_samples!==undefined) parts.push(`學習樣本 ${fmt(metrics.total_samples)}`);
    if(metrics.raw_delta!==undefined) parts.push(`raw Δ ${n(metrics.raw_delta).toFixed(3)}`);
    const platformLikes=[
      ["FB",metrics.view_fb_avg_likes_30d],
      ["IG",metrics.view_ig_avg_likes_30d],
      ["TH",metrics.view_th_avg_likes_30d],
    ];
    if(platformLikes.some(([,value])=>value!==undefined&&value!==null)){
      parts.push(`30d avg likes ${platformLikes.map(([label,value])=>`${label} ${text(value)}`).join(" / ")}`);
    }
    if(metrics.current&&metrics.baseline){
      parts.push(
        `本期 ${fmt(metrics.current.posts)} 篇／coverage ${(n(metrics.current.metric_coverage)*100).toFixed(0)}%`,
        `median action ${text(metrics.baseline.median_action_score)} → ${text(metrics.current.median_action_score)}`,
      );
      if(metrics.score_ratio!==undefined) parts.push(`ratio ${n(metrics.score_ratio).toFixed(2)}`);
    }
    return parts.join(" · ");
  }

  function renderProposals(rows) {
    const host=$("proposal-list"); clear(host);
    if(!rows.length){empty(host,"尚無學習提案");return;}
    rows.slice(0,12).forEach(item=>{
      const row=node("div","row"); const main=node("div","row-main");
      main.append(
        node("div","row-title",item.summary||item.kind),
        node("div","row-detail",proposalChangeText(item)),
        node("div","row-detail",proposalEvidenceText(item)),
        node("div","row-meta",`${item.kind} · ${when(item.created_at)}`),
      );
      const side=node("div","proposal-side"); side.appendChild(badge(item.status));
      if(item.status==="proposed"){
        const actions=node("div","proposal-actions");
        const approve=node("button","proposal-approve","批准下輪套用");
        const reject=node("button","proposal-reject","拒絕");
        approve.addEventListener("click",()=>decideProposal(item,"approved",approve,reject));
        reject.addEventListener("click",()=>decideProposal(item,"rejected",approve,reject));
        actions.append(approve,reject); side.appendChild(actions);
      } else if(item.decision_comment) {
        side.appendChild(node("small","muted",item.decision_comment));
      }
      row.append(main,side); host.appendChild(row);
    });
  }

  async function decideProposal(item,decision,...buttons){
    const verb=decision==="approved"?"批准並於下一次 learning review 套用":"拒絕";
    if(!window.confirm(
      `確定${verb}這筆提案？\n${item.summary||item.id}\n${proposalChangeText(item)}\n${proposalEvidenceText(item)}`
    )) return;
    buttons.forEach(button=>button.disabled=true);
    try{
      await request(`/api/learning-proposals/${encodeURIComponent(item.id)}/decision`,{
        method:"POST",body:JSON.stringify({decision}),
      });
      showError(""); await load();
    }catch(error){
      showError(`提案決策失敗：${error.message}`);
      buttons.forEach(button=>button.disabled=false);
    }
  }

  function renderEvents(rows) {
    const host=$("event-list"); clear(host);
    if(!rows.length){empty(host,"尚無稽核事件");return;}
    rows.slice(0,25).forEach(item=>{
      const row=node("div","audit-row"); row.append(node("span","muted",when(item.created_at)),node("span","",item.actor),node("span","",`${item.action}${item.subject_id?` · ${item.subject_id}`:""}`),node("span","muted",item.status)); host.appendChild(row);
    });
  }

  function startRefresh(){ clearInterval(refreshTimer); refreshTimer=setInterval(load,60000); }
  $("unlock").addEventListener("click",unlock);
  $("owner-token").addEventListener("keydown",event=>{if(event.key==="Enter")unlock();});
  $("refresh").addEventListener("click",load);
  $("lock").addEventListener("click",()=>{sessionStorage.removeItem(TOKEN_KEY);clearInterval(refreshTimer);setLocked(true);showError("");});

  if(token()){setLocked(false);load();startRefresh();}else setLocked(true);
})();
