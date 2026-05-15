#!/usr/bin/env python3
"""
tools/substack_to_meta.py · 2026-05-15

把已 push 的 Substack article 注入 News Radar pipeline 當 news_item、
讓既有 composer + Cloud publisher 跑出 FB / IG / Threads 三平台變體。

Workflow:
    Hsin (Cowork): 「發 X 那篇到三平台」
        ↓
    PM agent 跑這個 script:
        python tools/substack_to_meta.py <article-md-path-or-fuzzy>
        ↓
    INSERT news_items row（status='scored'、weighted_score=1.5、跳過 scorer）
        ↓
    bash scripts/compose_hourly.sh （或等 launchctl 下次 fire）
        ↓
    Pipeline: composer 讀 news_item → 三平台變體 → enqueue
        ↓
    Cloud workflow (publish_queue.yml): 發 FB / IG / Threads

Usage:
    python tools/substack_to_meta.py data/substack_drafts/2026-05-13/adhoc6_如果我是配角/Article_Substack.md
    python tools/substack_to_meta.py 配角會富                  # fuzzy title
    python tools/substack_to_meta.py 主角會死                  # fuzzy title
    python tools/substack_to_meta.py 動詞名詞 --topic-category ai_application

Options:
    --substack-url URL      Public Substack post URL (optional, for source_url)
    --topic-category CAT    Override auto-guessed topic_category
    --dry-run               Parse + print plan, don't INSERT

Design notes:
- Reuse 既有 pipeline、零新 cron / 零新 spec
- status='scored' 跳過 scorer (Substack 文章本來就是 Hsin 親選、不需重評)
- weighted_score=1.5 確保在 RESCUE_PUBLISH_THRESHOLD (0.65) 跟
  AUTO_PUBLISH_THRESHOLD (0.7) 之上、立刻被 picker 選中
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "01_harvest" / "news_radar.db"
SUBSTACK_DRAFTS = REPO_ROOT / "data" / "substack_drafts"


# --------------------------------------------------------------------------
# Locate article
# --------------------------------------------------------------------------

def find_article(arg: str) -> Path:
    """Resolve article path from explicit path or fuzzy folder name."""
    p = Path(arg)
    if p.exists() and p.suffix == ".md":
        return p

    # fuzzy: search substack_drafts/**/<arg in folder name>/Article_Substack.md
    matches = list(SUBSTACK_DRAFTS.rglob(f"*{arg}*/Article_Substack.md"))
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"❌ Multiple matches for '{arg}':")
        for m in matches:
            print(f"   {m.relative_to(REPO_ROOT)}")
        print("→ 用更精確的字串或直接給完整路徑")
        sys.exit(1)
    else:
        print(f"❌ No article found for '{arg}'")
        print(f"   searched: {SUBSTACK_DRAFTS}")
        sys.exit(1)


# --------------------------------------------------------------------------
# Parse Article_Substack.md
# --------------------------------------------------------------------------

def parse_article(md_path: Path) -> dict:
    """Extract title, subtitle, clean body (strip footer + cover prompts)."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Title: first '# ' line
    title = md_path.stem
    for line in lines[:5]:
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            break

    # Subtitle: first *italic* line (single * each side, not bold **)
    subtitle = ""
    for line in lines[1:8]:
        s = line.strip()
        if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
            subtitle = s.strip("*").strip()
            break

    # Body: cut at footer markers
    body = text
    for marker in (
        "\n---\n\n> **「我專門拆解",         # tagline footer
        "\n## 📸 封面圖 Prompt",              # cover prompt block
        "\n## 📸 封面圖",                      # variant
    ):
        if marker in body:
            body = body.split(marker)[0]

    # Strip title + subtitle lines from body
    body_lines = body.split("\n")
    out_lines = []
    skipped_title = False
    skipped_subtitle = False
    for line in body_lines:
        s = line.strip()
        if not skipped_title and s == f"# {title}":
            skipped_title = True
            continue
        if not skipped_subtitle and subtitle and s == f"*{subtitle}*":
            skipped_subtitle = True
            continue
        out_lines.append(line)
    clean_body = "\n".join(out_lines).strip()

    # word_count: count CJK characters (matches existing pipeline convention)
    cjk_count = sum(1 for c in clean_body if "一" <= c <= "鿿")

    return {
        "title": title,
        "subtitle": subtitle,
        "body": clean_body,
        "word_count": cjk_count,
    }


# --------------------------------------------------------------------------
# Topic category guess
# --------------------------------------------------------------------------

# Heuristic keyword → topic_category. 跟 topic_taxonomy 對齊、不確定走 'other'
TOPIC_KEYWORDS = {
    "ai_application": [
        "anthropic", "openai", "gpt", "claude", "llm", "rag", "agent",
        "mcp", "prompt", "ai 應用", "推理"
    ],
    "ai_model": [
        "cerebras", "nvidia", "晶片", "wafer", "h100", "b200", "tsmc",
        "晶圓", "半導體"
    ],
    "policy_geopolitics": [
        "cfius", "g42", "uae", "中美", "制裁", "外資", "監管", "spitzer",
        "mifid", "sec"
    ],
}


def guess_topic_category(title: str, body: str) -> str:
    text = (title + " " + body[:1000]).lower()
    for cat, keywords in TOPIC_KEYWORDS.items():
        if any(k in text for k in keywords):
            return cat
    return "other"


# --------------------------------------------------------------------------
# DB insert
# --------------------------------------------------------------------------

def make_news_item_id(source_url: str, title: str) -> str:
    """16-char sha256 prefix of (url + title)."""
    return hashlib.sha256((source_url + title).encode("utf-8")).hexdigest()[:16]


def insert_news_item(article: dict, source_url: str, topic: str, dry_run: bool):
    item_id = make_news_item_id(source_url, article["title"])
    now_iso = datetime.now(timezone.utc).isoformat()

    if dry_run:
        print(f"\n🔎 DRY RUN · 不會 INSERT")
        print(f"   id (would be): {item_id}")
        print(f"   title:          {article['title']}")
        print(f"   topic_category: {topic}")
        print(f"   weighted_score: 1.5")
        print(f"   status:         scored (skip scorer)")
        print(f"   url:            {source_url}")
        print(f"   word_count:     {article['word_count']}")
        return item_id

    with sqlite3.connect(str(DB_PATH)) as conn:
        existing = conn.execute(
            "SELECT id, status FROM news_items WHERE id = ?", (item_id,)
        ).fetchone()
        if existing:
            print(f"⚠️  news_item {item_id} 已存在（status={existing[1]}）、不重複 insert")
            print(f"   如果需要重新觸發、手動改 status='scored' + weighted_score=1.5")
            return item_id

        conn.execute(
            """
            INSERT INTO news_items (
                id, feed_name, feed_tier, source_type, url, title,
                published_at, fetched_at, language, clean_markdown,
                word_count, status, topic_category, topic_confidence,
                topic_rationale, weighted_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                "substack_self",
                "primary",
                "substack",
                source_url,
                article["title"],
                now_iso,
                now_iso,
                "zh-TW",
                article["body"],
                article["word_count"],
                "scored",            # 跳過 scorer
                topic,
                0.95,                # high confidence (manual dispatch)
                f"manual substack→meta dispatch: {article['title']}",
                1.5,                 # force above thresholds
            ),
        )
        conn.commit()
    print(f"✅ inserted news_item id={item_id}")
    return item_id


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Insert published Substack article as news_item → "
            "pipeline composes FB/IG/Threads → Cloud publishes."
        )
    )
    parser.add_argument(
        "article",
        help="Path to Article_Substack.md, OR fuzzy folder substring (e.g. '配角會富')",
    )
    parser.add_argument(
        "--substack-url",
        default="",
        help="Public Substack post URL (optional, used as source_url)",
    )
    parser.add_argument(
        "--topic-category",
        default=None,
        help="Override topic_category (default: auto-guess from title+body)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + print plan, do not INSERT",
    )
    args = parser.parse_args()

    md_path = find_article(args.article)
    print(f"📄 found article: {md_path.relative_to(REPO_ROOT)}")

    article = parse_article(md_path)
    print(f"   title:      {article['title']}")
    print(f"   subtitle:   {article['subtitle'][:80]}{'...' if len(article['subtitle']) > 80 else ''}")
    print(f"   word_count: {article['word_count']}")

    topic = args.topic_category or guess_topic_category(article["title"], article["body"])
    print(f"   topic:      {topic}{' (auto-guessed)' if not args.topic_category else ' (override)'}")

    source_url = args.substack_url or f"https://hsin73.substack.com/p/{md_path.parent.name}"
    print(f"   source_url: {source_url}")

    item_id = insert_news_item(article, source_url, topic, args.dry_run)

    if args.dry_run:
        return

    print()
    print(f"🚀 ready. Next steps:")
    print(f"   bash scripts/compose_hourly.sh    # 立即 compose")
    print(f"   或等下次 launchctl 整點觸發")
    print(f"   compose 完進 publish queue → Cloud workflow 發 FB/IG/Threads")


if __name__ == "__main__":
    main()
