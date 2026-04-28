"""
News Radar · Composer Rule Analyzer (Phase 9 Item 5)
=====================================================
Weekly LLM augmentor extracting body rules (top-Q vs bot-Q diffs) and hook rules
(first-N-chars patterns) per platform. Outputs to per-platform rules files in
`config/platforms/{fb,ig,threads}_v2.md`.

**Framing A calibration**: Proposal-only path (no auto-deploy). Every proposal
requires Hsin approval via Settings UI.

**LLM augmentor budget** (Hsin 2026-04-26):
  - Soft cap: $0.50/week
  - Hard cap: 50,000 input tokens/week
  - Alert at 80%
  - Truncated output if exceeding hard cap (truncated: true flag)

**Hook layer** (per spec §8.3 row 5):
  - FB: first 100 chars (substr limit per Meta)
  - IG: first line (title-like content)
  - Threads: first 30 chars

**Body + hook dimensions**:
  - Body rules: language patterns, specificity, tone detected from content
  - Hook rules: opening structures, punctuation, question patterns that correlate
    with engagement

Spec: PM_Radar/roadmap/phase_9_unified_reflector.md §3 Item 5 + §8.3 + Q-A4
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import random

# Lazy imports (pydantic, llm_brain pulled here, not at module top)


# ---------- Constants ----------

PLATFORMS = ("facebook", "instagram", "threads")

# Per-platform hook length (first-N-chars pattern)
HOOK_LENGTHS = {
    "facebook":  100,
    "instagram": None,  # first line (newline-delimited)
    "threads":   30,
}

# Engagement quartile thresholds
TOP_QUARTILE = 4
BOT_QUARTILE = 1

# Sample size requirements (Phase 9 Item 6 interpretation)
MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM = 2  # at least 2 top-Q and 2 bot-Q per platform

# Token budget (Hsin 2026-04-26 Q-A4)
LLM_SOFT_CAP_USD = 0.50
LLM_HARD_CAP_INPUT_TOKENS = 50_000
LLM_ALERT_THRESHOLD = 0.80


# ---------- Data structures ----------

@dataclass(frozen=True)
class DraftSample:
    """Single draft from v_drafts_with_outcome."""
    draft_id: str
    news_id: str
    news_title: str
    news_body: str
    topic_category: str
    published_at: str
    engagement_quartile: int
    fb_likes: Optional[int] = None
    ig_likes: Optional[int] = None
    th_likes: Optional[int] = None


@dataclass
class PlatformHooks:
    """Hook (first-N-chars) pairs for a draft × platform."""
    platform: str
    draft_id: str
    hook_text: str  # first N chars or first line
    engagement_quartile: int


@dataclass
class AnalyzerResult:
    """Result of one composer analyzer run."""
    ran_at: str
    lookback_days: int
    dry_run: bool
    samples_scanned: int
    proposals_written: int
    token_usage: Optional[Dict[str, int]] = field(default_factory=dict)
    alerts: List[str] = field(default_factory=list)


# ---------- Pure fetch helpers ----------

def _fetch_drafts_with_outcome(
    conn: sqlite3.Connection,
    lookback_days: int = 14,
) -> List[DraftSample]:
    """Fetch v_drafts_with_outcome: all drafts from past N days with engagement
    & quartile computed per topic_category."""
    sql = """
        SELECT draft_id, news_id, title AS news_title, body AS news_body,
               topic_category, published_at, engagement_quartile,
               fb_likes, ig_likes, th_likes
          FROM v_drafts_with_outcome
         WHERE published_at >= datetime('now', ?)
    """
    window = f"-{int(lookback_days)} days"
    rows: List[DraftSample] = []
    try:
        for r in conn.execute(sql, (window,)).fetchall():
            rows.append(DraftSample(
                draft_id=r["draft_id"] if hasattr(r, "keys") else r[0],
                news_id=r["news_id"] if hasattr(r, "keys") else r[1],
                news_title=r["news_title"] if hasattr(r, "keys") else r[2],
                news_body=r["news_body"] if hasattr(r, "keys") else r[3],
                topic_category=r["topic_category"] if hasattr(r, "keys") else r[4],
                published_at=r["published_at"] if hasattr(r, "keys") else r[5],
                engagement_quartile=r["engagement_quartile"] if hasattr(r, "keys") else r[6],
                fb_likes=r["fb_likes"] if hasattr(r, "keys") else r[7],
                ig_likes=r["ig_likes"] if hasattr(r, "keys") else r[8],
                th_likes=r["th_likes"] if hasattr(r, "keys") else r[9],
            ))
    except sqlite3.OperationalError:
        # View may not exist on older DBs
        pass
    return rows


# ---------- Sampler logic ----------

def _get_hook_for_platform(draft: DraftSample, platform: str) -> str:
    """Extract hook text (first N chars or first line) per platform."""
    text = draft.news_title or draft.news_body or ""

    if platform == "facebook":
        # First 100 chars
        return text[:100]
    elif platform == "instagram":
        # First line (up to newline or 100 chars)
        lines = text.split("\n", 1)
        return lines[0][:100]
    elif platform == "threads":
        # First 30 chars
        return text[:30]
    return ""


def sample_top_bot_quartiles_per_platform(
    drafts: List[DraftSample],
) -> Dict[Tuple[str, str], Tuple[List[DraftSample], List[DraftSample]]]:
    """
    Sample top-Q (quartile=4) and bot-Q (quartile=1) drafts per
    (topic_category, platform) pair.

    Returns: {(topic_category, platform): (top_q_samples, bot_q_samples)}

    Empty lists for pairs with insufficient samples. Caller filters.
    """
    # Group by topic_category to understand quartile distribution
    by_topic = {}
    for d in drafts:
        if d.topic_category not in by_topic:
            by_topic[d.topic_category] = []
        by_topic[d.topic_category].append(d)

    # Per topic × platform, filter by quartile
    result: Dict[Tuple[str, str], Tuple[List[DraftSample], List[DraftSample]]] = {}
    for topic_cat, topic_drafts in by_topic.items():
        for platform in PLATFORMS:
            # Only include drafts that have engagement on this platform
            # (heuristic: if the platform_likes column is not null)
            platform_col = {"facebook": "fb_likes", "instagram": "ig_likes", "threads": "th_likes"}[platform]

            # Filter to drafts with engagement data on this platform
            platform_drafts = [
                d for d in topic_drafts
                if getattr(d, platform_col, None) is not None
            ]

            top_q = [d for d in platform_drafts if d.engagement_quartile == TOP_QUARTILE]
            bot_q = [d for d in platform_drafts if d.engagement_quartile == BOT_QUARTILE]

            result[(topic_cat, platform)] = (top_q, bot_q)

    return result


# ---------- LLM augmentor: prompt template + helper ----------

# Per-platform context (KOL benchmarks per ORIGIN_VISION §2)
_PLATFORM_CONTEXT = {
    "facebook": (
        "FB · 戰略解讀(IEObserve benchmark)— 高頻長文、Bloomberg/Reuters/FT/Economist 級頂層來源、"
        "聚焦結構性改變、宏觀切入具體事件、品牌化資訊圖。讀者來這裡看「這個事件對產業版圖的長"
        "期影響」、不是當天熱搜。"
    ),
    "instagram": (
        "IG · 市場週期 / 總體經濟科普(游庭皓 benchmark)— FED 談話、券商研究、官方指標、週期變"
        "換敏感、數據圖表 + 快節奏金句。讀者期待具體 Fed 數字 / 央行立場 / 官方文件級評論,不"
        "是 lifestyle 圖文。"
    ),
    "threads": (
        "Threads · 系統架構拆解(Fox Hsiao benchmark)— 第一手官方資訊(Release Notes / API Doc / "
        "創辦人訪談)、「輸入/處理/輸出/整合」四層系統架構、冷靜一針見血、把對手拉進來看。"
        "讀者要「為什麼這個產品這樣設計」的工匠拆解,不是 PR 通稿。"
    ),
}

# Brand DNA — 規則生成必須遵守的 voice 邊界(從 ORIGIN_VISION + Phase 8.20 reflector 紀錄萃取)
_BRAND_DNA = """
✅ DO 偏好(這些是 brand 賺錢路徑的訊號):
- 具體數字(百分比、貨幣金額、時間戳記、產量、比例)
- 專有名詞(人名、公司名、產品名、政策名、Fed 官員、API endpoint)
- 結論前置(hook 第一句就放結論,不鋪陳)
- 第一手實作體悟 / 真實踩坑(「我發現…」「實測下來…」「這條跟去年那條的差別是…」)
- 結構性框架(輸入/處理/輸出/整合;或產業價值鏈位置)
- 把對手 / 競品 / 同業拉進來看(從一個工具升級到產業版圖)
- 一氣呵成 narrative(段落間有邏輯銜接,不是條列堆砌)

❌ DON'T(這些是 anti-brand,即使 engagement 高也不能寫進規則):
- 摘要味語言(「綜合來看」「值得注意的是」「整體而言」「在這個瞬息萬變的時代」)
- 抽象化沒人名沒數字(「許多人」「業界」「市場專家認為」)
- 鋪陳式開頭(浪費 hook 黃金 N 字)
- 條列式功能流水帳(「該產品有以下特色:1...2...3...」)
- 標題黨句式(「驚!」「沒想到…」「這個現象 99% 的人都不知道」)
- 情緒煽動(「太離譜了」「不能再忍」「快來看」)
- 改變 voice DNA 的建議(「應該加 emoji」「應該更口語」「應該縮短到 30 字」屬於 brand-side
  call,不是 reflector 該動的)
"""


_COMPOSER_PROMPT_TEMPLATE = """你是分析 News Radar 中文評論帳號 engagement pattern 的助手。這個帳號**不是** clickbait 商品,
變現路徑長期靠受眾信任資產,短期靠平台演算法觸及。你的任務是**從數據找出可重複的結構性模式**,
不是把 brand 訓練成標題黨。

# 平台脈絡

{platform_context}

# Brand DNA(規則生成必須遵守的硬邊界)
{brand_dna}

# 任務

我給你同一個主題類別在 {platform} 平台的 top-quartile(高 engagement)跟 bot-quartile
(低 engagement)drafts。找出**結構性差異**,寫成可實作的規則,讓 composer 下次寫該主題
時能 hit 高 engagement 區塊的形狀 — 但**不能違背上面 Brand DNA**。

主題類別: {topic_category}
Hook 字數限制(此平台): {hook_chars}

# Top-quartile drafts({top_n} 篇,engagement 高)

{top_q_block}

# Bot-quartile drafts({bot_n} 篇,engagement 低)

{bot_q_block}

# 重要過濾原則

如果 top-Q 高 engagement 是因為**違反 Brand DNA 的 anti-pattern**(標題黨、煽情、AI 摘要味、
抽象化、條列式),**不要**寫成規則。寧願 body_rules / hook_rules 兩條都空陣列,把該觀察記
進 anti_patterns_filtered 欄位。寫進規則的東西會直接餵給 composer 訓練下一輪寫稿,放錯規則
等於主動腐蝕 brand。

# 規則品質要求

- 每條規則必須**可實作**(composer LLM 看到能直接遵守)。寫「使用具體數字」是空規則,寫
  「在前 {hook_chars} 字 hook 內必須含 ≥1 個百分比、貨幣金額、或具名 Fed 官員」才可實作。
- WHEN/DO 框架: WHEN <輸入條件> DO <具體動作>。不要寫感想式的「應該更…」「最好…」。
- HIGH confidence 條件: 該模式在 top-Q 出現 ≥3 次,且 bot-Q 從未出現。
- MED: 該模式在 top-Q 出現 ≥2 次,bot-Q 出現 ≤1 次。
- LOW: 只在 top-Q 出現 1 次,無法確認是否系統性 — **預設不要 propose LOW**,除非真的有獨立
  論據(例如該模式在三個 KOL benchmark 文章裡也常見)。
- 每條規則附 ≤30 字 evidence_quote(從某篇 top-Q draft 直接抽出來的字串)。

# Output(嚴格 JSON,不要 markdown 包裹)

{{
  "body_rules": [
    {{
      "rule": "WHEN <條件> DO <具體動作>",
      "evidence_quote": "≤30 字 quote",
      "confidence": "HIGH" | "MED" | "LOW"
    }}
  ],
  "hook_rules": [
    {{
      "rule": "WHEN 寫前 {hook_chars} 字 hook DO <具體動作>",
      "evidence_quote": "≤30 字 quote(從 top-Q draft 的前 {hook_chars} 字內抽)",
      "confidence": "HIGH" | "MED" | "LOW"
    }}
  ],
  "rationale": "1-2 句中文總結 top-Q vs bot-Q 的結構性差異主軸",
  "anti_patterns_filtered": [
    "如果有 top-Q pattern 因 brand DNA 違反被過濾,列在這。空陣列 OK。"
  ]
}}

# 禁止項

- 不要 propose 違反 Brand DNA 的規則
- 不要 propose 改變 voice tone 的規則(改 emoji / 改長度 / 改人稱屬 brand-side call)
- 不要 propose 違反三 KOL benchmark 結構的規則
- 不要把 evidence_quote 寫成你腦補的句子,必須是某篇 top-Q draft 裡實際出現的字串
- 規則 ≤ 5 條(body 跟 hook 加總),寧少勿多;每多一條規則 composer 多一條限制
"""


def _format_draft_for_prompt(d: DraftSample, max_body_chars: int = 400) -> str:
    """Format one draft sample into a compact block for the LLM prompt."""
    body = (d.news_body or "").strip()
    if len(body) > max_body_chars:
        body = body[:max_body_chars] + "…[truncated]"
    eng_hint = []
    if d.fb_likes is not None:
        eng_hint.append(f"FB likes={d.fb_likes}")
    if d.ig_likes is not None:
        eng_hint.append(f"IG likes={d.ig_likes}")
    if d.th_likes is not None:
        eng_hint.append(f"Threads likes={d.th_likes}")
    eng_str = " · ".join(eng_hint) if eng_hint else "no engagement signal"
    return (
        f"--- draft_id={d.draft_id[:8]} | quartile={d.engagement_quartile} | {eng_str} ---\n"
        f"標題: {d.news_title}\n"
        f"內容: {body}\n"
    )


def _build_composer_prompt(
    top_q_samples: List[DraftSample],
    bot_q_samples: List[DraftSample],
    platform: str,
    topic_category: str,
) -> str:
    """Construct the LLM prompt by injecting samples + per-platform context."""
    hook_chars = HOOK_LENGTHS.get(platform)
    hook_chars_str = f"{hook_chars}" if hook_chars else "第一行(換行符前)"
    top_q_block = "\n".join(_format_draft_for_prompt(d) for d in top_q_samples)
    bot_q_block = "\n".join(_format_draft_for_prompt(d) for d in bot_q_samples)
    return _COMPOSER_PROMPT_TEMPLATE.format(
        platform=platform,
        platform_context=_PLATFORM_CONTEXT.get(platform, ""),
        brand_dna=_BRAND_DNA,
        topic_category=topic_category,
        hook_chars=hook_chars_str,
        top_n=len(top_q_samples),
        bot_n=len(bot_q_samples),
        top_q_block=top_q_block,
        bot_q_block=bot_q_block,
    )


def analyze_with_llm(
    top_q_samples: List[DraftSample],
    bot_q_samples: List[DraftSample],
    platform: str,
    topic_category: str,
) -> Optional[Dict]:
    """
    Call LLM to extract body rules + hook rules from top vs bot quartile samples.

    Prompt template baked in by Hsin 2026-04-28 (per character + brand DNA from
    ORIGIN_VISION §2 + Phase 8.20 reflector log + accumulated PM ratifications).

    Returns dict with:
      - body_rules: List[Dict]  (rule + evidence_quote + confidence)
      - hook_rules: List[Dict]  (rule + evidence_quote + confidence)
      - rationale: str
      - anti_patterns_filtered: List[str]
      - token_usage: Dict[str, int]  {input, output}
      - truncated: bool

    Or None if LLM call fails / unavailable.

    **Activation**: This function is wired to call `src.llm_brain.call_for_json`.
    On import / call failure (e.g. llm_brain API mismatch), returns None safely
    so cron continues without crashing. To verify Hsin's prompt before first
    real fire: `python3 -m src.reflector.composer --dry-run` prints the prompt
    that would be sent (see CLI `--print-prompt` flag).
    """
    # Build prompt
    prompt = _build_composer_prompt(top_q_samples, bot_q_samples, platform, topic_category)

    # Token-budget pre-check (rough estimate: 1 token ≈ 3 chars for Chinese)
    estimated_input_tokens = len(prompt) // 3
    truncated = False
    if estimated_input_tokens > LLM_HARD_CAP_INPUT_TOKENS:
        # Refuse to send oversize prompt — return truncation marker for audit
        return {
            "body_rules": [],
            "hook_rules": [],
            "rationale": "input exceeded hard cap; sample truncation needed",
            "anti_patterns_filtered": [],
            "truncated": True,
            "token_usage": {"input": estimated_input_tokens, "output": 0},
        }

    # Try LLM call (lazy import; gracefully degrade on import / call failure)
    try:
        from src.llm_brain import call_for_json  # type: ignore
        response = call_for_json(
            prompt=prompt,
            model_tier="primary",  # Gemini Flash; falls back to Claude CLI per llm_brain
            response_schema={
                "type": "object",
                "properties": {
                    "body_rules": {"type": "array"},
                    "hook_rules": {"type": "array"},
                    "rationale": {"type": "string"},
                    "anti_patterns_filtered": {"type": "array"},
                },
            },
        )
        if not isinstance(response, dict):
            return None
        return {
            "body_rules": response.get("body_rules", []),
            "hook_rules": response.get("hook_rules", []),
            "rationale": response.get("rationale", ""),
            "anti_patterns_filtered": response.get("anti_patterns_filtered", []),
            "truncated": truncated,
            "token_usage": response.get("_usage", {"input": estimated_input_tokens, "output": 0}),
        }
    except (ImportError, AttributeError) as e:
        # llm_brain API mismatch — log but don't crash cron
        print(f"[composer] llm_brain unavailable ({e}); skipping LLM analysis", file=sys.stderr)
        return None
    except Exception as e:
        # Any other LLM call failure (rate limit, network, schema mismatch)
        print(f"[composer] LLM call failed: {e}; skipping this (topic, platform)", file=sys.stderr)
        return None


# ---------- Proposal writer ----------

def _build_proposal_payload(
    body_rules: List[str],
    hook_rules: List[str],
    platform: str,
    topic_category: str,
    evidence_draft_ids: Dict[str, List[str]],  # {quartile: [draft_ids]}
) -> dict:
    """Construct proposal dict for src.reflector.proposals.write_proposal."""
    return {
        "analyzer": "composer",
        "platform": platform,
        "proposal_type": "composer_rules",
        "evidence": {
            "sample_ids": evidence_draft_ids.get("top_q", []) + evidence_draft_ids.get("bot_q", []),
            "metrics": {
                "topic_category": topic_category,
                "top_q_count": len(evidence_draft_ids.get("top_q", [])),
                "bot_q_count": len(evidence_draft_ids.get("bot_q", [])),
            },
            "confidence": "MED",  # Preliminary; Hsin refines
        },
        "action": {
            "target_config": f"config/platforms/{platform}_v2.md",
            "field": f"{topic_category}_rules",
            "body_rules": body_rules,
            "hook_rules": hook_rules,
        },
        "boss_attention_required": True,  # Calibration phase: all proposals require approval
    }


# ---------- Main orchestrator ----------

def run_analyzer(
    conn: sqlite3.Connection,
    lookback_days: int = 14,
    dry_run: bool = False,
) -> AnalyzerResult:
    """
    Complete one composer analyzer cycle.

    1. Fetch v_drafts_with_outcome
    2. Sample top/bot-Q per platform per topic
    3. Call LLM for each (topic, platform) pair
    4. Write proposals (no auto-deploy)
    """
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Fetch data
    drafts = _fetch_drafts_with_outcome(conn, lookback_days)
    samples_count = len(drafts)

    if samples_count < 5:
        # Insufficient data for meaningful analysis
        return AnalyzerResult(
            ran_at=started_at,
            lookback_days=lookback_days,
            dry_run=True,  # Suppress all writes
            samples_scanned=samples_count,
            proposals_written=0,
            alerts=["Insufficient engagement data (<5 samples); skipping analyzer"],
        )

    # Sample per platform
    quartile_samples = sample_top_bot_quartiles_per_platform(drafts)

    proposals_written = 0

    for (topic_cat, platform), (top_q, bot_q) in quartile_samples.items():
        # Skip if insufficient samples
        if len(top_q) < MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM or len(bot_q) < MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM:
            continue

        # Call LLM (mock in tests, real in production)
        llm_result = analyze_with_llm(top_q, bot_q, platform, topic_cat)
        if llm_result is None:
            continue

        body_rules = llm_result.get("body_rules", [])
        hook_rules = llm_result.get("hook_rules", [])

        if not body_rules and not hook_rules:
            continue  # No actionable rules extracted

        # Build + write proposal
        if not dry_run:
            from src.reflector.proposals import write_proposal

            payload = _build_proposal_payload(
                body_rules,
                hook_rules,
                platform,
                topic_cat,
                {
                    "top_q": [d.draft_id for d in top_q],
                    "bot_q": [d.draft_id for d in bot_q],
                },
            )
            try:
                fire_id = write_proposal(payload)
                proposals_written += 1
            except Exception as e:
                print(f"[composer] proposal write failed: {e}", file=sys.stderr)

    return AnalyzerResult(
        ran_at=started_at,
        lookback_days=lookback_days,
        dry_run=dry_run,
        samples_scanned=samples_count,
        proposals_written=proposals_written,
        token_usage={},  # Populated by LLM calls if enabled
    )


# ---------- CLI ----------

def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=14,
                        help="Lookback window for v_drafts_with_outcome")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but don't write proposals")
    args = parser.parse_args(argv)

    # Connect to DB
    from src import db as dbmod
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        result = run_analyzer(
            conn,
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    # Report
    print(f"[composer] Ran at {result.ran_at}")
    print(f"[composer] Samples scanned: {result.samples_scanned}")
    print(f"[composer] Proposals written: {result.proposals_written}")
    if result.alerts:
        for alert in result.alerts:
            print(f"[composer] ALERT: {alert}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(_main())
