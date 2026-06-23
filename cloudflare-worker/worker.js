/**
 * News Radar · Submit Proxy Worker (2026-06-23)
 * =============================================
 * 讓公開的提交頁（hsintiger.github.io）不必再放 GitHub PAT。
 * PAT 存成這個 Worker 的加密 secret（env.GITHUB_PAT），頁面只送「內容」過來，
 * Worker 拿那把藏起來的 PAT 去觸發 GitHub workflow / 上傳圖片 / 讀紀錄。
 *
 * 安全閘門：只接受來自 ALLOWED_ORIGIN 的請求（瀏覽器強制帶 Origin）。最壞情況
 * （有人猜到 Worker 網址 + 偽造 Origin）= 觸發一次提交，無法讀寫你其他東西，可隨時
 * 改 Worker secret / 停用 Worker 撤銷。
 *
 * 部署：
 *   cd cloudflare-worker && npx wrangler login && npx wrangler deploy
 *   npx wrangler secret put GITHUB_PAT     # 貼上一把 fine-grained PAT（只授權 news-radar）
 */
const REPO = "HsinTiger/news-radar";
const ALLOWED_ORIGIN = "https://hsintiger.github.io";
const GH = "https://api.github.com";

export default {
  async fetch(req, env) {
    const cors = {
      "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });

    const origin = req.headers.get("Origin") || "";
    if (origin && origin !== ALLOWED_ORIGIN)
      return json({ error: "forbidden origin" }, 403, cors);

    const pat = env.GITHUB_PAT;
    if (!pat) return json({ error: "worker 未設定 GITHUB_PAT secret" }, 500, cors);
    const gh = {
      "Authorization": "Bearer " + pat,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "news-radar-submit-worker",
    };
    const url = new URL(req.url);

    try {
      // 讀紀錄（GET ?action=history&wf=submit-source.yml）
      if (req.method === "GET" && url.searchParams.get("action") === "history") {
        const wf = url.searchParams.get("wf") || "submit-source.yml";
        const r = await fetch(`${GH}/repos/${REPO}/actions/workflows/${wf}/runs?per_page=15`, { headers: gh });
        return json(await r.json(), r.status, cors);
      }
      if (req.method === "POST") {
        const b = await req.json();
        // 上傳圖片：頁面已 downscale 成 base64
        if (b.action === "image") {
          const r = await fetch(`${GH}/repos/${REPO}/contents/${b.path}`, {
            method: "PUT", headers: gh,
            body: JSON.stringify({ message: "upload " + b.path, content: b.b64, branch: "main" }),
          });
          return json({ ok: r.ok, status: r.status, path: b.path, text: r.ok ? "" : await r.text() }, r.ok ? 200 : 502, cors);
        }
        // 提交來源 → 觸發 workflow（Meta=submit-source.yml；Substack=substack-submit.yml）。
        // 頁面可帶 wf + inputs；沒帶就用 Meta 預設 + 扁平欄位。
        if (b.action === "submit") {
          const wf = b.wf || "submit-source.yml";
          const inputs = b.inputs || { source_type: b.source_type, content: b.content, platforms: b.platforms || "", note: b.note || "" };
          const r = await fetch(`${GH}/repos/${REPO}/actions/workflows/${wf}/dispatches`, {
            method: "POST", headers: gh,
            body: JSON.stringify({ ref: "main", inputs }),
          });
          return json({ ok: r.status === 204, status: r.status, text: r.status === 204 ? "" : await r.text() }, r.status === 204 ? 200 : 502, cors);
        }
        // 立即發送 → 觸發 full_pipeline.yml force_publish
        if (b.action === "publish_now") {
          const r = await fetch(`${GH}/repos/${REPO}/actions/workflows/full_pipeline.yml/dispatches`, {
            method: "POST", headers: gh,
            body: JSON.stringify({ ref: "main", inputs: { force_publish: "true" } }),
          });
          return json({ ok: r.status === 204, status: r.status, text: r.status === 204 ? "" : await r.text() }, r.status === 204 ? 200 : 502, cors);
        }
      }
      return json({ error: "unknown action" }, 400, cors);
    } catch (e) {
      return json({ error: String(e) }, 500, cors);
    }
  },
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), { status, headers: { ...cors, "Content-Type": "application/json" } });
}
