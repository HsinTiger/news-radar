# substack_radar · Pipeline Block Diagram

> AI-readable single source of truth for the twice-daily Substack draft pipeline.
> Two representations below: (1) a Mermaid flowchart (renders in GitHub/IDE), and
> (2) a machine-readable stage I/O contract (YAML). Keep both in sync with the code.
> Entry point: `python substack_radar/compose.py {morning|evening} --harvest`.
> **Only STAGE 2 consumes LLM tokens; every other stage is deterministic (0 token).**

## Mermaid flowchart

```mermaid
flowchart TD
    T["⏰ launchd · morning 08:00 / evening 17:00<br/>python substack_radar/compose.py {mode} --harvest"]
    T --> S0

    S0["STAGE 0 · HARVEST  ⟨0 token⟩<br/>in: config.yaml (38 feeds) + substack_youtube_sources.yaml<br/>fn: RSS (feedparser+httpx+trafilatura) ; YouTube 字幕 (yt-dlp VTT→text)<br/>out: news_items(status=fetched) → news_radar.db"]
    S0 --> S1

    S1["STAGE 1 · SOURCE PICK (_pick_top_from_pool)  ⟨0 token⟩<br/>in: news_items (morning ≤3d / evening ≤7d) + .substack_used.json<br/>fn: 確定性評分 video+1.5 · feed+1.0 · 新鮮度衰減 · 數字密度×0.15 · 字數甜蜜區+0.5 → 排除已用 → top-1 → 標記used<br/>out: (id, title, clean_markdown, topic_category)<br/>override: --source-file / --news-id / --topic"]
    S1 --> S2

    S2["STAGE 2 · COMPOSE (compose_substack_article)  ★ 唯一 LLM 呼叫 · 吃掉全部 token<br/>in: 素材 + system(精簡soul + voice_anchor) + user(舉一反三步驟 + §13內文視覺標記指令 + JSON schema)<br/>fn: Claude CLI -p · minimal-context · WebSearch/WebFetch 關閉 · 只用離線素材<br/>out: SubstackDraft( title, subtitle, body_markdown[含3-6內文視覺標記], cover_image_prompt, hook_type, metaphor_domain, open_ending_form, reading_time )<br/>→ 成本/tokens 寫入 token_usage_daily"]
    S2 --> S3

    S3["STAGE 3 · AUTOFIX + AUDIT  ⟨0 token⟩<br/>in: SubstackDraft<br/>fn: 自動修正明確大陸用語(帶寬→頻寬…) + audit警告(破折號/對仗/填充詞/模糊用語/字數)<br/>out: 清理後 draft + warnings[]"]
    S3 --> S4

    S4["STAGE 4 · WRITE FILES  ⟨0 token⟩<br/>in: draft + warnings + source<br/>fn: Article_Substack.md(貼上版 + footer + 單一封面prompt) · Article_Full.md(metadata) · metadata.json · cover.png(PIL, 副標換行已修)<br/>out: data/substack_drafts/{date}/{mode}_{slug}/"]
    S4 --> S5
    S4 --> S6

    S5["STAGE 5 · MIRROR  ⟨0 token⟩<br/>in: 本地資料夾<br/>fn: 複製到 OneDrive<br/>out: …/substack/autogen/{date}/{mode}_{slug}/"]

    S6["STAGE 6 · PUSH DRAFT  ⟨0 token · opt-in SUBSTACK_AUTO_DRAFT=1⟩<br/>in: Article_Substack.md + cover.png + SUBSTACK_COOKIES_STRING<br/>fn: python-substack Api.post_draft (建立草稿, 不自動發佈)<br/>out: Substack 草稿 id / URL"]
    S6 --> S7

    S7["STAGE 7 · NOTIFY  ⟨0 token⟩<br/>fn: Gmail / macOS 通知 (標題 + 草稿URL + 警告)"]

    SH["共享依賴 (import from src/): llm_brain · db · fetcher · cleaner · schema · image_brain · cover_renderer · notify  +  news_radar.db"]
    SH -.shared.-> S0
    SH -.shared.-> S1
    SH -.shared.-> S2
    SH -.shared.-> S3
    SH -.shared.-> S4

    style S2 fill:#ffe0b2,stroke:#e65100,stroke-width:3px
    style SH fill:#eceff1,stroke:#607d8b,stroke-dasharray:4 3
```

## Machine-readable stage contract

```yaml
pipeline: substack_radar
entrypoint: "python substack_radar/compose.py {morning|evening} --harvest"
schedule: { morning: "08:00", evening: "17:00", via: launchd }
token_stages: [stage_2_compose]   # everything else is deterministic / 0 token
stages:
  - id: 0_harvest
    impl: substack_radar/harvest_inspiration.py (+ src/fetcher.py, substack_radar/youtube_transcripts.py)
    token: 0
    input:  ["config.yaml feeds(38)", "substack_youtube_sources.yaml"]
    function: "RSS fetch+clean (trafilatura) ; YouTube transcripts (yt-dlp VTT→text)"
    output: ["news_items rows status=fetched in news_radar.db"]
  - id: 1_source_pick
    impl: "substack_radar/compose.py::_pick_top_from_pool"
    token: 0
    input:  ["news_items (morning≤3d / evening≤7d)", ".substack_used.json"]
    function: "deterministic score (video+1.5, inspiration-feed+1.0, freshness decay, signal-density*0.15, wordcount sweet-spot+0.5); exclude used; top-1; mark used"
    output: ["(id, title, clean_markdown, topic_category)"]
    overrides: ["--source-file", "--news-id", "--topic"]
  - id: 2_compose
    impl: "substack_radar/composer.py::compose_substack_article → src/llm_brain.py::call_for_json (Claude CLI)"
    token: ALL          # the entire token budget of the pipeline
    input:  ["picked source text", "system = trimmed soul + voice_anchor", "user = mode hint + 舉一反三 step + §13 inline-image marker spec + JSON schema"]
    function: "Claude CLI -p, minimal-context flags, WebSearch/WebFetch disabled, writes from supplied material only"
    output: ["SubstackDraft{title, subtitle, body_markdown(with 3-6 inline image markers), cover_image_prompt, hook_type, metaphor_domain_used, open_ending_form, reading_time_minutes}", "usage → token_usage_daily"]
  - id: 3_autofix_audit
    impl: "substack_radar/composer.py::autofix_mainland_terms + audit_substack_draft"
    token: 0
    input:  ["SubstackDraft"]
    function: "auto-replace unambiguous mainland terms; warn on dash/parallelism/filler/ambiguous-terms/wordcount"
    output: ["cleaned draft", "warnings[]"]
  - id: 4_write_files
    impl: "substack_radar/compose.py::write_* + render_substack_cover"
    token: 0
    input:  ["draft", "warnings", "source"]
    function: "Article_Substack.md(paste-ready + footer + ONE cover prompt); Article_Full.md(metadata); metadata.json; cover.png(PIL, wrapped subtitle)"
    output: ["data/substack_drafts/{date}/{mode}_{slug}/"]
  - id: 5_mirror
    impl: "substack_radar/compose.py::mirror_to_onedrive"
    token: 0
    input:  ["local draft folder"]
    function: "copy folder to OneDrive"
    output: [".../substack/autogen/{date}/{mode}_{slug}/"]
  - id: 6_push_draft
    impl: "substack_radar/compose.py::push_to_substack_draft (python-substack)"
    token: 0
    opt_in: "SUBSTACK_AUTO_DRAFT=1 + SUBSTACK_COOKIES_STRING + SUBSTACK_PUBLICATION_URL"
    input:  ["Article_Substack.md", "cover.png", "cookies"]
    function: "Api.post_draft — creates a DRAFT (never auto-publishes)"
    output: ["Substack draft id / URL"]
  - id: 7_notify
    impl: "src/notify.py"
    token: 0
    function: "Gmail / macOS notification (title + draft URL + audit warnings)"
shared_deps:
  note: "substack_radar imports these from src/ via repo-root on sys.path (NOT symlinked, to avoid double-import)"
  modules: [llm_brain, db, fetcher, cleaner, schema, image_brain, cover_renderer, notify]
  data: [news_radar.db]
naming_note: "package is 'substack_radar' NOT 'substack' — a local 'substack' package would shadow the pip python-substack library (from substack import Api) and break Stage 6."
```
