"""
Substack 草稿知識庫產生器
==========================
把 data/substack_drafts/<YYYY-MM-DD>/<mode>_<slug>/ 底下所有過去寫過的草稿，
解析成一份可瀏覽 + LLM 可讀的「軌跡」索引，讓未來 compose 跑之前可以先掃過
「我們已經寫過什麼」，也讓人類可以在 wiki/index.html 用瀏覽器逛。

設計：
  - **雙來源解析**：優先讀 metadata.json（結構化、乾淨）；缺 metadata.json 的
    舊資料夾（只有 Article_Substack.md）退回 regex 解析標題/副標。
  - **零外部依賴**：只用標準函式庫（json / re / pathlib），CI 不需要額外 pip install。
  - **冪等 + 無破壞**：只讀 data/substack_drafts/，只寫 wiki/ 底下三個輸出檔。
    data/substack_drafts/ 是 gitignored 的本機目錄，wiki/ 底下的產出才是進 git 的
    可提交成品，所以 CI 不依賴來源檔案存在。
  - **related 連結**：同 metaphor_domain（排除 'none'）或同 topic_category 的
    其它草稿 slug，讓 corpus.md / index.html 可以做交叉連結。

用法：
    python3 scripts/build_substack_wiki.py
    # 或用 repo 的 venv：
    .venv/bin/python scripts/build_substack_wiki.py

輸出：
    wiki/data.json    — 全部記錄的陣列（LLM 可讀的索引本體）
    wiki/index.html   — 單檔 SPA，純前端 fetch ./data.json 渲染
    wiki/corpus.md     — 依 metaphor_domain / 月份分組的標題＋副標一覽表
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = REPO_ROOT / "data" / "substack_drafts"
WIKI_DIR = REPO_ROOT / "wiki"

EXCERPT_LEN = 300

# 跟 src/cover_renderer.py 的 TOPIC_CHIP_LABELS 同步維護的中文標籤，
# 沒收錄的 key 會 fallback 成原始英文字串（不會炸）。
TOPIC_LABELS: dict[str, str] = {
    "ai_model": "AI 模型",
    "ai_agent": "AI Agent",
    "ai_application": "AI 應用",
    "supply_chain": "產業鏈",
    "earnings": "財報",
    "tw_stocks": "台股",
    "us_stocks": "美股",
    "tech_product_launch": "科技新品",
    "policy_geopolitics": "政策",
    "other": "其它",
}

MODE_LABELS: dict[str, str] = {
    "morning": "晨報",
    "evening": "晚報",
    "podcast": "Podcast",
}

# Article_Full.md 的結構化中繼資料行，用 regex 抽。順序在不同檔案間會變動
# （例如 podcast 模式多一行 generated_by），所以每個欄位獨立用 re.search
# 掃整份檔頭，不假設行序。
RE_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
RE_SUBTITLE_FULL = re.compile(r"^\*\*Subtitle:\*\*\s*(.+?)\s*$", re.MULTILINE)
RE_SUBTITLE_PUB = re.compile(r"^\*(.+?)\*\s*$", re.MULTILINE)
RE_MODE_LINE = re.compile(
    r"^\*\*Mode:\*\*\s*`([^`]*)`\s*\|\s*\*\*Hook:\*\*\s*`([^`]*)`\s*\|\s*\*\*Open-ending:\*\*\s*`([^`]*)`",
    re.MULTILINE,
)
RE_METAPHOR_LINE = re.compile(
    r"^\*\*Metaphor domain:\*\*\s*`([^`]*)`\s*\|\s*\*\*Estimated reading time:\*\*\s*(\d+)",
    re.MULTILINE,
)
RE_SOURCE_BLOCK = re.compile(r"```\s*\n(.*?)\n```", re.DOTALL)
RE_SOURCE_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
RE_SOURCE_TOPIC = re.compile(r"^topic_category:\s*(.+?)\s*$", re.MULTILINE)
RE_BODY_MARKER = re.compile(r"^##\s*本文\s*$", re.MULTILINE)

# slug 版本後綴：_v2 / _v3_clean / _v2_3500 / _v4_taiwan_terms 之類，
# 拿掉之後得到「基底 slug」，同一篇文章的不同修訂版可以群組在一起看。
RE_VERSION_SUFFIX = re.compile(r"(_v\d+)(_[a-zA-Z0-9]+)*$")


def strip_version_suffix(slug: str) -> str:
    """拿掉 _v2 / _v3_clean / _v2_3500 等版本後綴，回傳基底 slug。"""
    return RE_VERSION_SUFFIX.sub("", slug)


def count_cjk_aware_words(text: str) -> int:
    """中英混合字數估算：CJK 每字算一個詞，非 CJK 用空白切詞。
    跟 audit_warnings 裡常見的「中文字數」概念對齊（看過的草稿都是中文為主）。
    """
    if not text:
        return 0
    cjk = re.findall(r"[一-鿿㐀-䶿]", text)
    non_cjk = re.sub(r"[一-鿿㐀-䶿]", " ", text)
    words = [w for w in non_cjk.split() if w]
    return len(cjk) + len(words)


def make_excerpt(body: str, length: int = EXCERPT_LEN) -> str:
    """從正文取前 ~length 字當摘要，去除多餘空白/小節符號，不截斷半個句子太難看
    就算了（這是摘要不是正文，截斷是預期行為）。"""
    if not body:
        return ""
    cleaned = re.sub(r"▉", "", body)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= length:
        return cleaned
    return cleaned[:length].rstrip() + "…"


def extract_body_from_full(full_text: str) -> str:
    """取 Article_Full.md 裡 '## 本文' 標記之後的全部內容當正文。"""
    m = RE_BODY_MARKER.search(full_text)
    if not m:
        return ""
    return full_text[m.end():].strip()


def extract_body_from_pub(pub_text: str) -> str:
    """Article_Substack.md 沒有 '## 本文' 標記，整篇扣掉標題/副標行就是正文。"""
    lines = pub_text.splitlines()
    body_lines = []
    skipped_title = False
    skipped_subtitle = False
    for line in lines:
        if not skipped_title and line.strip().startswith("# "):
            skipped_title = True
            continue
        if not skipped_subtitle and line.strip().startswith("*") and line.strip().endswith("*"):
            skipped_subtitle = True
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def parse_metadata_json(meta_path: Path) -> Optional[dict[str, Any]]:
    try:
        with meta_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def parse_from_full_md(full_text: str) -> dict[str, Any]:
    """metadata.json 不存在時，從 Article_Full.md 用 regex 重建等價欄位。"""
    out: dict[str, Any] = {}
    if m := RE_TITLE.search(full_text):
        out["title"] = m.group(1).strip()
    if m := RE_SUBTITLE_FULL.search(full_text):
        out["subtitle"] = m.group(1).strip()
    if m := RE_MODE_LINE.search(full_text):
        out["mode"] = m.group(1).strip()
        out["hook_type"] = m.group(2).strip()
        out["open_ending_form"] = m.group(3).strip()
    if m := RE_METAPHOR_LINE.search(full_text):
        out["metaphor_domain_used"] = m.group(1).strip()
        out["reading_time_minutes"] = int(m.group(2))
    if m := RE_SOURCE_BLOCK.search(full_text):
        block = m.group(1)
        source: dict[str, Any] = {}
        if sm := RE_SOURCE_TITLE.search(block):
            source["title"] = sm.group(1).strip()
        if tm := RE_SOURCE_TOPIC.search(block):
            source["topic_category"] = tm.group(1).strip()
        if source:
            out["source"] = source
    return out


def parse_from_pub_md(pub_text: str) -> dict[str, Any]:
    """7 份既無 metadata.json 也無 Article_Full.md 的早期 ad-hoc/v3/v4 草稿，
    只剩 Article_Substack.md（純發布版：標題 + 斜體副標 + 正文），從這份重建
    最低限度的欄位。其它結構化欄位（hook/metaphor/topic...）這批資料本來就沒有，
    留空比硬造假資料誠實。"""
    out: dict[str, Any] = {}
    if m := RE_TITLE.search(pub_text):
        out["title"] = m.group(1).strip()
    if m := RE_SUBTITLE_PUB.search(pub_text):
        out["subtitle"] = m.group(1).strip()
    return out


def build_record(folder: Path) -> Optional[dict[str, Any]]:
    """解析單一草稿資料夾，回傳一筆 wiki record；資料夾沒有任何可讀檔案才回傳 None。"""
    date_str = folder.parent.name
    folder_name = folder.name
    mode_match = re.match(r"^([a-zA-Z]+\d*)_(.+)$", folder_name)
    mode = mode_match.group(1) if mode_match else folder_name
    # podcast/morning/evening/adhocN 的 mode 前綴；adhoc3..6 沒有結構化中繼資料，
    # 但仍視為獨立 mode 顯示，不偽裝成 morning/evening。

    pub_path = folder / "Article_Substack.md"
    full_path = folder / "Article_Full.md"
    meta_path = folder / "metadata.json"

    pub_text = pub_path.read_text(encoding="utf-8") if pub_path.exists() else ""
    full_text = full_path.read_text(encoding="utf-8") if full_path.exists() else ""

    if not pub_text and not full_text:
        return None  # 完全沒有可讀內容的資料夾，跳過（目前資料集裡沒有遇到）

    meta = parse_metadata_json(meta_path) if meta_path.exists() else None
    if meta is None:
        meta = parse_from_full_md(full_text) if full_text else {}
        if not meta.get("title") or not meta.get("subtitle"):
            fallback = parse_from_pub_md(pub_text) if pub_text else {}
            for k, v in fallback.items():
                meta.setdefault(k, v)

    title = meta.get("title") or folder_name
    subtitle = meta.get("subtitle") or ""
    hook = meta.get("hook_type") or ""
    open_ending = meta.get("open_ending_form") or ""
    metaphor_domain = meta.get("metaphor_domain_used") or "none"
    reading_time = meta.get("reading_time_minutes")
    source = meta.get("source") or {}
    source_title = source.get("title") or ""
    topic_category = source.get("topic_category") or ""

    body = extract_body_from_full(full_text) if full_text else extract_body_from_pub(pub_text)
    if not body and pub_text:
        body = extract_body_from_pub(pub_text)

    word_count = count_cjk_aware_words(body)
    excerpt = make_excerpt(body)
    has_cover = (folder / "cover.png").exists()
    base_slug = strip_version_suffix(folder_name)

    return {
        "date": date_str,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "title": title,
        "subtitle": subtitle,
        "hook": hook,
        "open_ending": open_ending,
        "metaphor_domain": metaphor_domain,
        "reading_time": reading_time,
        "source_title": source_title,
        "topic_category": topic_category,
        "topic_label": TOPIC_LABELS.get(topic_category, topic_category or "未分類"),
        "slug": folder_name,
        "base_slug": base_slug,
        "body_excerpt": excerpt,
        "word_count": word_count,
        "has_cover": has_cover,
        "related": [],  # 第二輪填入
    }


def attach_related(records: list[dict[str, Any]]) -> None:
    """同 metaphor_domain（排除 'none'）或同 topic_category 的其它 slug 互相連結。
    上限各 6 筆，避免熱門 domain（例如 contrarian_markets 12 篇）撐爆單筆 related 清單。
    """
    by_metaphor: dict[str, list[str]] = defaultdict(list)
    by_topic: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r["metaphor_domain"] and r["metaphor_domain"] != "none":
            by_metaphor[r["metaphor_domain"]].append(r["slug"])
        if r["topic_category"]:
            by_topic[r["topic_category"]].append(r["slug"])

    for r in records:
        related: list[str] = []
        if r["metaphor_domain"] and r["metaphor_domain"] != "none":
            related += [s for s in by_metaphor[r["metaphor_domain"]] if s != r["slug"]]
        if r["topic_category"]:
            related += [
                s
                for s in by_topic[r["topic_category"]]
                if s != r["slug"] and s not in related
            ]
        r["related"] = related[:6]


def collect_records() -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    records: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []

    if not DRAFTS_DIR.exists():
        return records, failures

    date_dirs = sorted(p for p in DRAFTS_DIR.iterdir() if p.is_dir())
    for date_dir in date_dirs:
        draft_dirs = sorted(p for p in date_dir.iterdir() if p.is_dir())
        for draft_dir in draft_dirs:
            try:
                record = build_record(draft_dir)
                if record is None:
                    failures.append((str(draft_dir), "no readable content (no Article_Substack.md / Article_Full.md)"))
                    continue
                records.append(record)
            except Exception as exc:  # noqa: BLE001 — 想看到完整失敗清單，不要讓單筆例外中斷整批
                failures.append((str(draft_dir), f"{type(exc).__name__}: {exc}"))

    records.sort(key=lambda r: (r["date"], r["mode"], r["slug"]))
    attach_related(records)
    return records, failures


def write_data_json(records: list[dict[str, Any]]) -> Path:
    out_path = WIKI_DIR / "data.json"
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def render_corpus_md(records: list[dict[str, Any]]) -> str:
    by_metaphor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_metaphor[r["metaphor_domain"] or "none"].append(r)
        month = r["date"][:7] if len(r["date"]) >= 7 else "unknown"
        by_month[month].append(r)

    lines: list[str] = []
    lines.append("# Substack 草稿全集地圖（corpus map）")
    lines.append("")
    lines.append(
        f"自動產生，共 {len(records)} 篇草稿。給未來 compose 掃一遍「已經寫過什麼」，"
        "避免題材/隱喻重複，也方便找可以互相連結的舊文。"
    )
    lines.append("")
    lines.append("來源：`data/substack_drafts/`（本機目錄，gitignored）。")
    lines.append("重新產生：`python3 scripts/build_substack_wiki.py`")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 依隱喻領域分組 (metaphor_domain)")
    lines.append("")
    metaphor_order = sorted(by_metaphor.keys(), key=lambda k: (-len(by_metaphor[k]), k))
    for domain in metaphor_order:
        items = by_metaphor[domain]
        label = "（無特定隱喻）" if domain == "none" else f"`{domain}`"
        lines.append(f"### {label} — {len(items)} 篇")
        lines.append("")
        for r in items:
            sub = f" — {r['subtitle']}" if r["subtitle"] else ""
            lines.append(
                f"- **{r['title']}**{sub}  \n"
                f"  `{r['date']} · {r['mode_label']} · {r['topic_label']}` · slug: `{r['slug']}`"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 依月份分組")
    lines.append("")
    for month in sorted(by_month.keys()):
        items = by_month[month]
        lines.append(f"### {month} — {len(items)} 篇")
        lines.append("")
        for r in items:
            sub = f" — {r['subtitle']}" if r["subtitle"] else ""
            lines.append(
                f"- `{r['date']}` **{r['title']}**{sub}  \n"
                f"  `{r['mode_label']} · {r['topic_label']} · metaphor={r['metaphor_domain']}` · slug: `{r['slug']}`"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_corpus_md(records: list[dict[str, Any]]) -> Path:
    out_path = WIKI_DIR / "corpus.md"
    out_path.write_text(render_corpus_md(records), encoding="utf-8")
    return out_path


INDEX_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📚 Substack 草稿知識庫 · News Radar</title>
<style>
  :root {
    --paper:#F2EEE5; --ink:#141414; --sienna:#C84A32; --stone:#8A8378;
    --paper-2:#EAE4D6; --line:#D8D1C2; --ink-soft:#3a3a3a;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--paper); color:var(--ink);
    font-family:'Noto Serif TC', 'Source Han Serif TC', Georgia, serif;
    line-height:1.7; -webkit-font-smoothing:antialiased;
  }
  .mono { font-family:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }
  a { color:var(--sienna); text-decoration:none; }
  a:hover { text-decoration:underline; }
  header.site {
    border-bottom:1px solid var(--line); padding:28px 20px 20px;
    position:sticky; top:0; background:var(--paper); z-index:50;
  }
  header.site .wrap { max-width:1080px; margin:0 auto; }
  .nav { font-size:13px; margin-bottom:10px; }
  .nav a { margin-right:16px; color:var(--stone); }
  .nav a.current { color:var(--sienna); font-weight:700; }
  h1.title { font-size:28px; margin:0 0 6px; letter-spacing:.01em; }
  p.tagline { color:var(--stone); font-size:14px; margin:0 0 18px; font-family:'JetBrains Mono', ui-monospace, monospace; }
  .stats-row { display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:var(--stone); margin-bottom:16px; }
  .stats-row b { color:var(--ink); }
  .controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .controls input[type=search] {
    flex:1; min-width:200px; padding:10px 14px; border:1px solid var(--line); border-radius:8px;
    background:#fff; color:var(--ink); font-size:14px; font-family:inherit;
  }
  .controls select {
    padding:9px 10px; border:1px solid var(--line); border-radius:8px; background:#fff;
    color:var(--ink); font-size:13px; font-family:'JetBrains Mono', ui-monospace, monospace;
  }
  .controls button.clear {
    padding:9px 14px; border:1px solid var(--line); border-radius:8px; background:var(--paper-2);
    color:var(--stone); font-size:13px; cursor:pointer; font-family:inherit;
  }
  .controls button.clear:hover { color:var(--sienna); border-color:var(--sienna); }
  main { max-width:1080px; margin:0 auto; padding:24px 20px 80px; }
  .result-count { font-size:13px; color:var(--stone); margin-bottom:14px; font-family:'JetBrains Mono', ui-monospace, monospace; }
  .grid { display:grid; gap:14px; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); }
  .card {
    background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px;
    cursor:pointer; transition:border-color .12s, transform .12s;
  }
  .card:hover { border-color:var(--sienna); transform:translateY(-1px); }
  .card .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
  .chip {
    font-size:11px; padding:2px 8px; border-radius:20px; font-family:'JetBrains Mono', ui-monospace, monospace;
    background:var(--paper-2); color:var(--stone); border:1px solid var(--line); white-space:nowrap;
  }
  .chip.mode { color:var(--sienna); border-color:var(--sienna); background:rgba(200,74,50,.08); }
  .card h3 { font-size:16px; margin:0 0 6px; line-height:1.5; }
  .card p.sub { font-size:13px; color:var(--ink-soft); margin:0 0 8px; font-style:italic; }
  .card .excerpt { font-size:12.5px; color:var(--stone); line-height:1.6; }
  .card .meta-line { font-size:11px; color:var(--stone); margin-top:8px; font-family:'JetBrains Mono', ui-monospace, monospace; }
  .empty-state { text-align:center; padding:60px 20px; color:var(--stone); }
  .empty-state .ei { font-size:36px; margin-bottom:10px; }
  /* Modal */
  .modal-overlay {
    position:fixed; inset:0; background:rgba(20,20,20,.55); z-index:500; display:none;
    align-items:center; justify-content:center; padding:16px;
  }
  .modal-overlay.active { display:flex; }
  .modal {
    background:var(--paper); border:1px solid var(--line); border-radius:12px; max-width:760px;
    width:100%; max-height:86vh; overflow-y:auto; padding:28px;
  }
  .modal-close {
    float:right; background:none; border:none; color:var(--stone); font-size:22px; cursor:pointer;
    line-height:1; margin-left:10px;
  }
  .modal-close:hover { color:var(--sienna); }
  .modal h2 { font-size:22px; margin:0 0 8px; }
  .modal p.sub { font-size:15px; font-style:italic; color:var(--ink-soft); margin:0 0 14px; }
  .modal .chips { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
  .modal .excerpt-block {
    background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; font-size:14.5px;
    color:var(--ink-soft); margin-bottom:18px; white-space:pre-wrap;
  }
  .modal .related h4 { font-size:13px; color:var(--stone); margin:0 0 8px; font-family:'JetBrains Mono', ui-monospace, monospace; text-transform:uppercase; letter-spacing:.05em; }
  .modal .related ul { list-style:none; margin:0; padding:0; }
  .modal .related li { padding:6px 0; border-bottom:1px solid var(--line); font-size:13.5px; }
  .modal .related li:last-child { border-bottom:none; }
  .modal .related a.related-link { color:var(--ink); cursor:pointer; }
  .modal .related a.related-link:hover { color:var(--sienna); }
  footer.site { text-align:center; padding:30px 20px 60px; color:var(--stone); font-size:12px; font-family:'JetBrains Mono', ui-monospace, monospace; }
  @media (max-width:640px) {
    .grid { grid-template-columns:1fr; }
    .controls select { flex:1; }
    h1.title { font-size:22px; }
  }
</style>
</head>
<body>
<header class="site">
  <div class="wrap">
    <div class="nav">
      <a href="../">🏠 Dashboard</a>
      <a href="../meta-submit/">📱 Meta 三平台</a>
      <a href="../substack-submit/">📝 Substack 草稿提交</a>
      <a href="./" class="current">📚 草稿知識庫</a>
    </div>
    <h1 class="title">Substack 草稿知識庫</h1>
    <p class="tagline mono">過去寫過的每一篇 — 留下軌跡，不要重複</p>
    <div class="stats-row" id="stats-row"></div>
    <div class="controls">
      <input type="search" id="search" placeholder="搜尋標題／副標／摘要…">
      <select id="filter-month"><option value="">全部月份</option></select>
      <select id="filter-mode"><option value="">全部模式</option></select>
      <select id="filter-metaphor"><option value="">全部隱喻領域</option></select>
      <select id="filter-hook"><option value="">全部 Hook 類型</option></select>
      <button class="clear" id="clear-filters">清除篩選</button>
    </div>
  </div>
</header>
<main>
  <div class="result-count mono" id="result-count"></div>
  <div class="grid" id="grid"></div>
  <div class="empty-state" id="empty-state" style="display:none;">
    <div class="ei">🔍</div>
    <div>沒有符合篩選條件的草稿</div>
  </div>
</main>
<footer class="site">
  News Radar · Substack 草稿知識庫 · 自動產生自 scripts/build_substack_wiki.py
</footer>

<div class="modal-overlay" id="modal-overlay">
  <div class="modal" id="modal-content"></div>
</div>

<script>
let ALL = [];
let BY_SLUG = {};

const $ = (sel) => document.querySelector(sel);
const grid = $('#grid');
const resultCount = $('#result-count');
const emptyState = $('#empty-state');
const statsRow = $('#stats-row');
const searchInput = $('#search');
const filterMonth = $('#filter-month');
const filterMode = $('#filter-mode');
const filterMetaphor = $('#filter-metaphor');
const filterHook = $('#filter-hook');
const modalOverlay = $('#modal-overlay');
const modalContent = $('#modal-content');

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[c]));
}

function populateSelect(sel, values, formatter) {
  const sorted = [...new Set(values)].filter(Boolean).sort();
  for (const v of sorted) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = formatter ? formatter(v) : v;
    sel.appendChild(opt);
  }
}

function cardHtml(r) {
  const chips = [
    `<span class="chip mode">${esc(r.mode_label || r.mode)}</span>`,
    `<span class="chip">${esc(r.date)}</span>`,
  ];
  if (r.metaphor_domain && r.metaphor_domain !== 'none') {
    chips.push(`<span class="chip">隱喻: ${esc(r.metaphor_domain)}</span>`);
  }
  if (r.topic_label) {
    chips.push(`<span class="chip">${esc(r.topic_label)}</span>`);
  }
  return `
    <div class="card" data-slug="${esc(r.slug)}">
      <div class="chips">${chips.join('')}</div>
      <h3>${esc(r.title)}</h3>
      ${r.subtitle ? `<p class="sub">${esc(r.subtitle)}</p>` : ''}
      <div class="excerpt">${esc(r.body_excerpt)}</div>
      <div class="meta-line">${esc(r.hook || '—')} · ${r.reading_time ? r.reading_time + ' min' : '—'} · ${r.word_count || 0} 字${r.has_cover ? ' · 🖼️' : ''}</div>
    </div>
  `;
}

function render(records) {
  if (!records.length) {
    grid.innerHTML = '';
    emptyState.style.display = 'block';
    resultCount.textContent = '';
    return;
  }
  emptyState.style.display = 'none';
  resultCount.textContent = `共 ${records.length} 篇`;
  grid.innerHTML = records.map(cardHtml).join('');
  grid.querySelectorAll('.card').forEach((el) => {
    el.addEventListener('click', () => openModal(el.dataset.slug));
  });
}

function applyFilters() {
  const q = searchInput.value.trim().toLowerCase();
  const month = filterMonth.value;
  const mode = filterMode.value;
  const metaphor = filterMetaphor.value;
  const hook = filterHook.value;

  const filtered = ALL.filter((r) => {
    if (month && !r.date.startsWith(month)) return false;
    if (mode && r.mode !== mode) return false;
    if (metaphor && r.metaphor_domain !== metaphor) return false;
    if (hook && r.hook !== hook) return false;
    if (q) {
      const hay = `${r.title} ${r.subtitle} ${r.body_excerpt}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  render(filtered);
}

function openModal(slug) {
  const r = BY_SLUG[slug];
  if (!r) return;
  const relatedHtml = (r.related || []).map((s) => {
    const rel = BY_SLUG[s];
    if (!rel) return '';
    return `<li><a class="related-link" data-slug="${esc(s)}">${esc(rel.title)}</a> <span class="mono" style="color:var(--stone); font-size:11px;">(${esc(rel.date)} · ${esc(rel.mode_label)})</span></li>`;
  }).join('');

  modalContent.innerHTML = `
    <button class="modal-close" id="modal-close">&times;</button>
    <div class="chips">
      <span class="chip mode">${esc(r.mode_label || r.mode)}</span>
      <span class="chip">${esc(r.date)}</span>
      <span class="chip">${esc(r.hook || '—')}</span>
      <span class="chip">${esc(r.open_ending || '—')}</span>
      ${r.metaphor_domain && r.metaphor_domain !== 'none' ? `<span class="chip">隱喻: ${esc(r.metaphor_domain)}</span>` : ''}
      ${r.topic_label ? `<span class="chip">${esc(r.topic_label)}</span>` : ''}
    </div>
    <h2>${esc(r.title)}</h2>
    ${r.subtitle ? `<p class="sub">${esc(r.subtitle)}</p>` : ''}
    <div class="excerpt-block">${esc(r.body_excerpt)}</div>
    <div class="meta-line mono" style="margin-bottom:18px; color:var(--stone); font-size:12px;">
      slug: ${esc(r.slug)} · ${r.reading_time ? r.reading_time + ' min read' : ''} · ${r.word_count || 0} 字${r.source_title ? ' · 來源: ' + esc(r.source_title) : ''}
    </div>
    ${relatedHtml ? `<div class="related"><h4>相關草稿</h4><ul>${relatedHtml}</ul></div>` : ''}
  `;
  modalOverlay.classList.add('active');
  $('#modal-close').addEventListener('click', closeModal);
  modalContent.querySelectorAll('.related-link').forEach((el) => {
    el.addEventListener('click', () => openModal(el.dataset.slug));
  });
}

function closeModal() {
  modalOverlay.classList.remove('active');
}

modalOverlay.addEventListener('click', (e) => {
  if (e.target === modalOverlay) closeModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
});

searchInput.addEventListener('input', applyFilters);
filterMonth.addEventListener('change', applyFilters);
filterMode.addEventListener('change', applyFilters);
filterMetaphor.addEventListener('change', applyFilters);
filterHook.addEventListener('change', applyFilters);
$('#clear-filters').addEventListener('click', () => {
  searchInput.value = '';
  filterMonth.value = '';
  filterMode.value = '';
  filterMetaphor.value = '';
  filterHook.value = '';
  applyFilters();
});

async function init() {
  try {
    const res = await fetch('./data.json');
    ALL = await res.json();
  } catch (err) {
    grid.innerHTML = `<div class="empty-state"><div class="ei">⚠️</div><div>無法載入 data.json：${esc(err)}</div></div>`;
    return;
  }
  BY_SLUG = Object.fromEntries(ALL.map((r) => [r.slug, r]));

  const months = ALL.map((r) => r.date.slice(0, 7));
  populateSelect(filterMonth, months);
  populateSelect(filterMode, ALL.map((r) => r.mode));
  populateSelect(filterMetaphor, ALL.map((r) => r.metaphor_domain).filter((m) => m && m !== 'none'));
  populateSelect(filterHook, ALL.map((r) => r.hook));

  const modeCounts = {};
  for (const r of ALL) modeCounts[r.mode_label || r.mode] = (modeCounts[r.mode_label || r.mode] || 0) + 1;
  statsRow.innerHTML = `<span><b>${ALL.length}</b> 篇草稿</span>` + Object.entries(modeCounts)
    .map(([k, v]) => `<span><b>${v}</b> ${esc(k)}</span>`).join('');

  render(ALL);
}

init();
</script>
</body>
</html>
"""


def write_index_html() -> Path:
    out_path = WIKI_DIR / "index.html"
    out_path.write_text(INDEX_HTML_TEMPLATE, encoding="utf-8")
    return out_path


def main() -> int:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    records, failures = collect_records()

    data_path = write_data_json(records)
    corpus_path = write_corpus_md(records)
    index_path = write_index_html()

    print(f"[build_substack_wiki] indexed {len(records)} drafts from {DRAFTS_DIR}")
    print(f"[build_substack_wiki] wrote {data_path}")
    print(f"[build_substack_wiki] wrote {corpus_path}")
    print(f"[build_substack_wiki] wrote {index_path}")

    if failures:
        print(f"[build_substack_wiki] {len(failures)} folder(s) failed to parse:")
        for path, reason in failures:
            print(f"  - {path}: {reason}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
