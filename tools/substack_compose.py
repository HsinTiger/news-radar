"""
News Radar · Substack Compose CLI (Phase 1)
============================================

Daily 2-draft pipeline 的 driver。每天兩班：
- 09:00 morning（type A 深度新聞）— 從 news_radar 24h 高分 pool 抽 1
- 18:00 evening（type B 獨立選題）— 從 config/substack_evening_topics.yaml 抽 1

每次跑出 1 篇 1500 字長文草稿 + 封面圖 + 全套 metadata report，
同時寫到 **兩個位置**讓 Hsin 從家裡／公司都看得到：

    Path A (本地 repo)：~/news_radar/data/substack_drafts/<date>/<mode>_<slug>/
    Path B (OneDrive)：~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/
                       文件/antigravity_workspace/substack/autogen/<date>/<mode>_<slug>/

每個資料夾長這樣：

    Article_Substack.md   # 貼到 Substack 的純淨版（標題＋副標＋本文）
    Article_Full.md       # 完整 metadata（含 hook type、metaphor domain、audit warnings、原料來源）
    cover.png             # 1456×816 Substack hero
    cover_prompt.txt      # 視覺架構師的提示詞（給 nano-banana 之類的 AI 繪圖工具用）
    chart_prompt.txt      # (可選) 機制解構示意圖提示詞
    metadata.json         # 機讀格式

可選步驟（環境變數開關，**opt-in 模式**）：
    SUBSTACK_AUTO_DRAFT=1 — 啟用透過 python-substack 自動建立 Substack 草稿。
                           預設關閉（=0 或未設）走純 OneDrive paste 流程。
    SUBSTACK_COOKIES_STRING — 從 Chrome DevTools 抓的 cookie header 字串
                              （登入 Substack 後 → F12 → Network → 任一 request
                                → Headers → Request Headers → Cookie 整段複製）。
                              通常 2-4 週過期，失效時 CLI 會明確報錯，重抓即可。
    SUBSTACK_PUBLICATION_URL — 你的 Substack URL，例如 https://hsin73.substack.com
    SUBSTACK_AUDIENCE — 草稿目標讀者（everyone / only_paid / founding / only_free）
                        預設 everyone。

LLM backend 架構（2026-05-12 重構）：
    SUBSTACK_COMPOSER_BACKEND=claude_cli  # 預設、推薦
        - 文章寫作 + research 都走 Claude CLI（Max 訂閱、含 web tools）
        - Gemini 不再 fallback（除非顯式設成 "default"）
    SUBSTACK_COMPOSER_BACKEND=default
        - 維持舊行為：Claude 主、Gemini 備援。**只在 Claude CLI 出問題時用**

    SUBSTACK_AI_COVER=1  # 預設 0（關）
        - 啟用 Gemini 生成 Moleskine 風格封面底圖（visual_soul.md aesthetic）
        - 失敗自動退回 photo-overlay / 合成噪點 base，不會 block 整個流程
        - 模型：gemini-2.5-flash-image-preview（可用 SUBSTACK_IMAGE_MODEL override）

    Gemini 在新架構中**唯一**的角色：image generation。文字／JSON 路徑全走 Claude CLI。

注意：python-substack 是社群非官方 wrapper，Substack 沒有公開 Write API。
若哪天失效或你想退出，OneDrive autogen/ 的 Article_Substack.md 永遠是手動 paste 後備。

用法：
    python tools/substack_compose.py morning            # 自動依環境變數決定要不要 push draft
    python tools/substack_compose.py morning --no-draft # 只寫檔，不 push draft
    python tools/substack_compose.py evening
    python tools/substack_compose.py morning --news-id <hash>  # 指定特定新聞
    python tools/substack_compose.py evening --topic "為什麼 AI 訓練成本指數下降而商業價值卻指數上升"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make src/ importable when running as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env from repo root so GEMINI_API_KEY / SUBSTACK_* are visible.
# Why this is here (not in src/llm_brain): the rest of the codebase loads
# .env at the run_pipeline.py entry. Our CLI is a separate entry point,
# so we own loading .env here. dotenv is optional — if missing, fall back
# to whatever the shell already exported.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass

from src.substack_composer import (  # noqa: E402
    SubstackDraft,
    audit_substack_draft,
    compose_substack_article,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LOCAL_BASE = _REPO_ROOT / "data" / "substack_drafts"

# OneDrive mount on Hsin's Mac. If running on a machine without this path
# (e.g. CI / sandbox), the OneDrive write is skipped with a warning.
ONEDRIVE_BASE = Path(
    "/Users/hsin/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/"
    "文件/antigravity_workspace/substack/autogen"
)

EVENING_TOPICS_PATH = _REPO_ROOT / "config" / "substack_evening_topics.yaml"
METAPHOR_HISTORY_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".metaphor_history.json"

NEWS_DB_PATH = _REPO_ROOT / "data" / "01_harvest" / "news_radar.db"


# ---------------------------------------------------------------------------
# Slug helpers — Chinese-safe
# ---------------------------------------------------------------------------

def _slug_from_title(title: str, max_len: int = 40) -> str:
    """Filesystem-safe slug. Strips emoji + control chars, keeps CJK."""
    cleaned = unicodedata.normalize("NFKC", title)
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned[:max_len] or f"draft_{datetime.now():%H%M%S}"


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------

def pick_morning_news(news_id: Optional[str] = None) -> Optional[Tuple[str, str, str]]:
    """Return (id, title, content_summary, topic_category) for type-A morning slot.

    Selection:
      - if news_id passed: that specific row
      - else: highest weighted_score in last 48h
    """
    if not NEWS_DB_PATH.exists():
        print(f"[ERROR] News DB not found at {NEWS_DB_PATH}")
        return None

    conn = sqlite3.connect(str(NEWS_DB_PATH))
    try:
        if news_id:
            row = conn.execute(
                "SELECT id, title, clean_markdown, topic_category "
                "FROM news_items WHERE id = ?",
                (news_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, title, clean_markdown, topic_category
                FROM news_items
                WHERE published_at >= datetime('now', '-2 days')
                  AND COALESCE(weighted_score, 0) > 0
                  AND status NOT IN ('dropped', 'filtered')
                ORDER BY COALESCE(weighted_score, 0) DESC, published_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        # Column order: id, title, clean_markdown (body), topic_category
        return tuple(row)  # type: ignore[return-value]
    finally:
        conn.close()


def pick_evening_topic(override_topic: Optional[str] = None) -> Tuple[str, str, str]:
    """Return (title, content_seed, topic_category) for type-B evening slot.

    File format (`config/substack_evening_topics.yaml`):

        topics:
          - title: "為什麼 AI 訓練成本指數下降而商業價值卻指數上升"
            seed: |
              論文出處：...
              核心觀察：...
              想挑戰的常識：...
            topic_category: ai_model

    Cycles through topics in order, marking used ones (cursor file).
    Fallback: if topic file missing, returns a placeholder asking user to add topics.
    """
    if override_topic:
        return (
            override_topic,
            (
                f"使用者指定的選題：{override_topic}\n"
                f"請按 substack_soul.md 的方法論：先抓一個常識→打破它→提供更高解析度的"
                f"思維模型→留一個開放結尾。"
            ),
            "other",
        )

    if not EVENING_TOPICS_PATH.exists():
        return (
            "TODO: 補充 evening 選題池",
            (
                f"請於 {EVENING_TOPICS_PATH} 補上 evening 選題（書／Podcast／概念）。"
                f"目前先用一個泛用的『高 agency 工作者該如何分配注意力』作為占位主題。"
            ),
            "other",
        )

    # 簡易 YAML 解析（避免引入 pyyaml 額外依賴；topic 結構固定）
    text = EVENING_TOPICS_PATH.read_text(encoding="utf-8")
    topics = _parse_yaml_topics(text)
    if not topics:
        return (
            "TODO: 補充 evening 選題池",
            "evening_topics.yaml 為空，請補充。",
            "other",
        )

    # Round-robin via cursor file
    cursor_path = EVENING_TOPICS_PATH.with_suffix(".cursor")
    cursor = 0
    if cursor_path.exists():
        try:
            cursor = int(cursor_path.read_text().strip())
        except ValueError:
            cursor = 0
    pick = topics[cursor % len(topics)]
    cursor_path.write_text(str((cursor + 1) % len(topics)))
    return (
        pick.get("title", ""),
        pick.get("seed", ""),
        pick.get("topic_category", "other"),
    )


def _parse_yaml_topics(text: str) -> List[Dict[str, str]]:
    """Minimal YAML topic-list parser (avoid pyyaml dep).

    Supports format:

        topics:
          - title: "..."
            seed: |
              line1
              line2
            topic_category: ai_model
    """
    topics: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    seed_lines: Optional[List[str]] = None
    seed_indent: Optional[int] = None

    for raw in text.splitlines():
        # Blank lines: inside a seed block they become empty seed lines;
        # outside they are skipped.
        if not raw.strip():
            if seed_lines is not None:
                seed_lines.append("")
            continue
        # Full-line comments only outside seed blocks; comments inside a
        # multi-line string are content, not metadata.
        if seed_lines is None and raw.lstrip().startswith("#"):
            continue
        if raw.strip() == "topics:":
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)

        # If we're collecting a seed block and this line is INDENTED deeper
        # than the `seed: |` declaration → it's part of the seed body.
        if seed_lines is not None and seed_indent is not None and indent > seed_indent:
            # Preserve relative indentation by stripping only the base
            # indent (seed_indent + 2 spaces for the YAML block-scalar
            # convention; if line has less, just lstrip).
            content = raw[seed_indent + 2 :] if len(raw) > seed_indent + 2 else stripped
            seed_lines.append(content)
            continue

        # Otherwise: indentation dropped → finalize seed block.
        if seed_lines is not None and seed_indent is not None and indent <= seed_indent:
            if current is not None:
                current["seed"] = "\n".join(seed_lines).rstrip()
            seed_lines = None
            seed_indent = None

        if stripped.startswith("- "):
            if current:
                topics.append(current)
            current = {}
            stripped = stripped[2:]
        if ":" in stripped and current is not None:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "|":
                seed_lines = []
                seed_indent = indent
                continue
            current[key] = val.strip("\"'")
    if seed_lines is not None and current is not None:
        current["seed"] = "\n".join(seed_lines).rstrip()
    if current:
        topics.append(current)
    return topics


# ---------------------------------------------------------------------------
# Metaphor history (avoid repeating the same domain)
# ---------------------------------------------------------------------------

def load_recent_metaphor_domains(limit: int = 7) -> List[str]:
    if not METAPHOR_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(METAPHOR_HISTORY_PATH.read_text(encoding="utf-8"))
        return data.get("recent", [])[-limit:]
    except Exception:
        return []


def append_metaphor_domain(domain: str, limit: int = 30) -> None:
    METAPHOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"recent": []}
    if METAPHOR_HISTORY_PATH.exists():
        try:
            data = json.loads(METAPHOR_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {"recent": []}
    data["recent"].append(domain)
    data["recent"] = data["recent"][-limit:]
    METAPHOR_HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Cover rendering — reuse cover_renderer with new "substack" spec
# ---------------------------------------------------------------------------

async def maybe_generate_ai_cover(
    *, cover_image_prompt: str, output_dir: Path
) -> Optional[Path]:
    """Optional: generate AI Moleskine cover via Gemini image gen.

    Gated by SUBSTACK_AI_COVER=1 in env. Returns the generated PNG path,
    or None if disabled / failed. On None, render_substack_cover falls
    back to the photo-overlay path (existing behavior).

    Why this lives in tools/ and not src/cover_pipeline.py: keeps the
    Substack-specific opt-in completely out of the social-publish flow.
    """
    try:
        from src.image_brain import generate_cover_image, is_ai_cover_enabled
    except Exception as exc:
        print(f"[Cover] ⚠️ image_brain unavailable: {exc}")
        return None
    if not is_ai_cover_enabled():
        return None
    target = output_dir / "ai_cover_raw.png"
    return await generate_cover_image(
        prompt=cover_image_prompt,
        out_path=target,
        size=(1456, 816),
    )


def render_substack_cover(
    *,
    title: str,
    subtitle: str,
    topic_category: str,
    output_dir: Path,
    source_image_path: Optional[Path] = None,
) -> Optional[Path]:
    """Render the 1456×816 Substack hero cover. Returns saved PNG path or None.

    If ``source_image_path`` is None, fall back to the synthetic noise base
    (existing behavior). To use AI-generated covers, the caller should run
    ``maybe_generate_ai_cover`` first and pass the returned path here as
    ``source_image_path``.
    """
    try:
        from PIL import Image
        from src.cover_renderer import CoverInput, render_cover
    except Exception as exc:
        print(f"[Cover] ⚠️ Cannot import cover_renderer: {exc}")
        return None

    # If no source image, synthesize a flat fallback (cover_pipeline does this for
    # social posts; we replicate the simplest variant here to avoid coupling).
    img_path = source_image_path
    if img_path is None or not img_path.exists():
        # Synthesize a neutral 1456×816 base (deep navy with noise).
        synth = output_dir / "_synth_bg.png"
        synth.parent.mkdir(parents=True, exist_ok=True)
        try:
            import random

            random.seed(42)
            base = Image.new("RGB", (1456, 816), (12, 16, 30))
            px = base.load()
            for y in range(0, 816, 4):
                for x in range(0, 1456, 4):
                    n = random.randint(-6, 6)
                    px[x, y] = (12 + n, 16 + n, 30 + n)
            base.save(synth, "PNG")
            img_path = synth
        except Exception as exc:
            print(f"[Cover] ⚠️ Fallback synth failed: {exc}")
            return None

    inp = CoverInput(
        image_path=img_path,
        title=title,
        subtitle=subtitle,
        topic_category=topic_category or "other",
        brand_name="主力爸爸我錯了 · Substack",
        date_str=date.today().isoformat(),
    )
    try:
        out = render_cover(inp, "substack", output_dir=output_dir)  # type: ignore[arg-type]
        # Rename to canonical cover.png; remove intermediates so the
        # final folder only contains user-facing artifacts.
        canonical = output_dir / "cover.png"
        if out != canonical:
            shutil.copyfile(out, canonical)
            try:
                out.unlink()
            except OSError:
                pass
        # Clean the synth background if it was created by us.
        synth = output_dir / "_synth_bg.png"
        if synth.exists():
            try:
                synth.unlink()
            except OSError:
                pass
        return canonical
    except Exception as exc:
        print(f"[Cover] ⚠️ render_cover failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Substack draft push via python-substack (Phase 1 semi-automation)
# ---------------------------------------------------------------------------
# Why this instead of email-to-draft: Substack has no email-to-draft feature
# nor a public Write API. The community-maintained ma2za/python-substack
# library (v0.1.18, 2026-03-07) reverse-engineers Substack's internal post
# endpoints using cookie auth. Risk: unofficial, cookies expire 2-4 weeks,
# Substack could break/detect it. We treat this as opt-in (SUBSTACK_AUTO_DRAFT=1).
# The OneDrive paste path is the always-available fallback.

def push_to_substack_draft(
    *,
    article_md_path: Path,
    title: str,
    subtitle: str,
    cover_path: Optional[Path] = None,
) -> bool:
    """Create a Substack draft via python-substack. Returns True iff draft created.

    Skipped (returns False) when:
      - SUBSTACK_AUTO_DRAFT is not "1"
      - python-substack not installed
      - Required env vars not set
      - Any API/auth failure (logged, doesn't raise)

    Required env vars (when SUBSTACK_AUTO_DRAFT=1):
      - SUBSTACK_COOKIES_STRING : full cookie header string copied from Chrome
                                  DevTools after logging in to substack.com.
                                  See README setup for the click-by-click guide.
      - SUBSTACK_PUBLICATION_URL : e.g. https://hsin73.substack.com

    Optional env vars:
      - SUBSTACK_AUDIENCE : "everyone" (default) | "only_paid" | "founding" | "only_free"
    """
    if os.getenv("SUBSTACK_AUTO_DRAFT") != "1":
        print(
            "[Substack] ℹ️ SUBSTACK_AUTO_DRAFT != '1'; skipping draft push. "
            "Open Article_Substack.md and paste manually."
        )
        return False

    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not (cookies and pub_url):
        print(
            "[Substack] ⚠️ SUBSTACK_AUTO_DRAFT=1 but SUBSTACK_COOKIES_STRING or "
            "SUBSTACK_PUBLICATION_URL missing; skipping. Article on disk."
        )
        return False

    try:
        from substack import Api  # type: ignore
        from substack.post import Post  # type: ignore
    except ImportError as exc:
        print(
            f"[Substack] ⚠️ python-substack not installed ({exc}); "
            "run `pip install python-substack`. Skipping draft push."
        )
        return False

    audience = os.getenv("SUBSTACK_AUDIENCE", "everyone")
    body_md = article_md_path.read_text(encoding="utf-8")

    # Article_Substack.md starts with "# <title>\n\n*<subtitle>*\n\n<body>".
    # python-substack's Post object takes title/subtitle as separate fields,
    # so we strip them out of the markdown before from_markdown() ingests it.
    # Without this, the draft would have the title duplicated as an H1 at the top.
    body_md = _strip_title_subtitle_lines(body_md, title=title, subtitle=subtitle)

    try:
        api = Api(cookies_string=cookies, publication_url=pub_url)
        user_id = api.get_user_id()
        post = Post(
            title=title,
            subtitle=subtitle,
            user_id=user_id,
            audience=audience,
        )
        post.from_markdown(body_md, api=api)

        # Optional: upload cover and prepend as captionedImage.
        # We deliberately do NOT auto-publish — only create draft. Hsin
        # eyeballs the cover placement in the Substack editor and Publish
        # manually.
        if cover_path and cover_path.exists():
            try:
                image = api.get_image(str(cover_path))
                # Insert cover at index 0 (top of body)
                post.add({"type": "captionedImage", "src": image.get("url")})
            except Exception as exc:
                print(f"[Substack] ⚠️ Cover upload failed (continuing without): {exc}")

        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id") if isinstance(draft, dict) else None
        print(
            f"[Substack] ✅ Draft created. id={draft_id!s} "
            f"audience={audience} cover={'yes' if cover_path else 'no'}"
        )
        return True
    except Exception as exc:
        print(
            f"[Substack] ❌ Draft push failed: {type(exc).__name__}: {exc}\n"
            f"    Common causes:\n"
            f"    - Cookie expired (re-copy from Chrome DevTools)\n"
            f"    - SUBSTACK_PUBLICATION_URL wrong (must be your-name.substack.com)\n"
            f"    - python-substack version mismatch with Substack backend\n"
            f"    Article still saved on disk; paste manually from OneDrive."
        )
        return False


def _strip_title_subtitle_lines(md: str, *, title: str, subtitle: str) -> str:
    """Strip the `# title` and `*subtitle*` lines we wrote in
    write_article_substack_md so python-substack's from_markdown doesn't
    duplicate them as body content."""
    lines = md.splitlines()
    out: List[str] = []
    skipped_title = False
    skipped_subtitle = False
    for line in lines:
        s = line.strip()
        if not skipped_title and s == f"# {title}":
            skipped_title = True
            continue
        if not skipped_subtitle and s == f"*{subtitle}*":
            skipped_subtitle = True
            continue
        # Skip the blank lines right after the metadata pair
        if (skipped_title or skipped_subtitle) and not out and s == "":
            continue
        out.append(line)
    return "\n".join(out).lstrip()


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_article_substack_md(out_dir: Path, draft: SubstackDraft) -> Path:
    """The PASTE-READY version. Goes straight into Substack editor."""
    path = out_dir / "Article_Substack.md"
    md = (
        f"# {draft.title}\n\n"
        f"*{draft.subtitle}*\n\n"
        f"{draft.body_markdown.strip()}\n"
    )
    path.write_text(md, encoding="utf-8")
    return path


def write_article_full_md(
    out_dir: Path,
    draft: SubstackDraft,
    *,
    mode: str,
    source: Dict[str, Any],
    audit_warnings: List[str],
) -> Path:
    """The FULL metadata version. For Hsin's review."""
    path = out_dir / "Article_Full.md"
    warning_block = (
        "\n".join(f"- ⚠️ {w}" for w in audit_warnings)
        if audit_warnings
        else "- ✅ 沒有命中黑名單"
    )
    md = f"""# {draft.title}

**Subtitle:** {draft.subtitle}

**Mode:** `{mode}`  |  **Hook:** `{draft.hook_type}`  |  **Open-ending:** `{draft.open_ending_form}`
**Metaphor domain:** `{draft.metaphor_domain_used}`  |  **Estimated reading time:** {draft.reading_time_minutes} min

---

## 原料來源 (Source)

```
title: {source.get("title")}
topic_category: {source.get("topic_category")}
id: {source.get("id", "n/a")}
```

## Audit warnings (自我體檢)

{warning_block}

---

## 本文

{draft.body_markdown.strip()}

---

## 視覺架構師指引

### 封面圖 prompt

> {draft.cover_image_prompt}

### 機制解構示意圖 prompt

{draft.chart_prompt or "(本篇未提供)"}
"""
    path.write_text(md, encoding="utf-8")
    return path


def write_prompts_and_metadata(
    out_dir: Path,
    draft: SubstackDraft,
    mode: str,
    source: Dict[str, Any],
    audit_warnings: List[str],
) -> None:
    (out_dir / "cover_prompt.txt").write_text(draft.cover_image_prompt, encoding="utf-8")
    if draft.chart_prompt:
        (out_dir / "chart_prompt.txt").write_text(draft.chart_prompt, encoding="utf-8")
    metadata = {
        "title": draft.title,
        "subtitle": draft.subtitle,
        "mode": mode,
        "hook_type": draft.hook_type,
        "open_ending_form": draft.open_ending_form,
        "metaphor_domain_used": draft.metaphor_domain_used,
        "reading_time_minutes": draft.reading_time_minutes,
        "source": source,
        "audit_warnings": audit_warnings,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mirror_to_onedrive(local_dir: Path, mirror_dir: Path) -> bool:
    """Copy entire draft folder to OneDrive autogen path. Skip if OneDrive
    not mounted (CI / sandbox)."""
    if not ONEDRIVE_BASE.exists() and not mirror_dir.parent.parent.exists():
        print(f"[OneDrive] ℹ️ Base not mounted at {ONEDRIVE_BASE}; skipping mirror.")
        return False
    try:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        for child in local_dir.iterdir():
            target = mirror_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
        print(f"[OneDrive] ✅ Mirrored to {mirror_dir}")
        return True
    except Exception as exc:
        print(f"[OneDrive] ❌ Mirror failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    today = date.today().isoformat()
    mode: str = args.mode

    # 1) Pick source
    if mode == "morning":
        pick = pick_morning_news(news_id=args.news_id)
        if pick is None:
            print("[ERROR] No suitable morning news found in last 48h.")
            return 2
        news_id, raw_title, raw_content, topic_category = pick
        source = {
            "id": news_id,
            "title": raw_title,
            "topic_category": topic_category,
        }
    else:
        raw_title, raw_content, topic_category = pick_evening_topic(args.topic)
        source = {
            "title": raw_title,
            "topic_category": topic_category,
        }
    print(f"[Source] mode={mode} title={raw_title!r} topic={topic_category}")

    # 2) Compose
    recent_domains = load_recent_metaphor_domains()
    print(f"[Compose] recent_metaphor_domains={recent_domains}")
    draft = await compose_substack_article(
        title=raw_title,
        content=raw_content or "",
        mode=mode,  # type: ignore[arg-type]
        topic_category=topic_category,
        editorial_note=args.editorial_note or "",
        recent_metaphor_domains=recent_domains,
    )
    if draft is None:
        print("[ERROR] LLM total failure. Aborting.")
        return 3
    print(f"[Compose] ✅ title={draft.title!r}")

    # 3) Audit
    warnings = audit_substack_draft(draft)
    if warnings:
        print(f"[Audit] ⚠️ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("[Audit] ✅ Clean")

    # 4) Output paths
    slug = _slug_from_title(draft.title)
    folder_name = f"{mode}_{slug}"
    local_dir = LOCAL_BASE / today / folder_name
    local_dir.mkdir(parents=True, exist_ok=True)
    mirror_dir = ONEDRIVE_BASE / today / folder_name

    # 5) Write files
    article_md = write_article_substack_md(local_dir, draft)
    write_article_full_md(local_dir, draft, mode=mode, source=source, audit_warnings=warnings)
    write_prompts_and_metadata(local_dir, draft, mode, source, warnings)

    # 5a) Optional: Gemini AI cover image (opt-in via SUBSTACK_AI_COVER=1)
    ai_cover = await maybe_generate_ai_cover(
        cover_image_prompt=draft.cover_image_prompt,
        output_dir=local_dir,
    )
    if ai_cover:
        print(f"[Cover] ✅ AI-generated base image: {ai_cover.name}")

    # 5b) Composite branded cover (text/chip/brand-bar on top of AI image or
    # synthetic fallback if AI gen skipped/failed).
    cover_path = render_substack_cover(
        title=draft.title,
        subtitle=draft.subtitle,
        topic_category=topic_category or "other",
        output_dir=local_dir,
        source_image_path=ai_cover,
    )
    print(f"[Files] wrote {local_dir}")

    # 6) Mirror to OneDrive
    mirror_to_onedrive(local_dir, mirror_dir)

    # 7) Optional Substack draft push (opt-in via SUBSTACK_AUTO_DRAFT=1)
    if not args.no_draft:
        push_to_substack_draft(
            article_md_path=article_md,
            title=draft.title,
            subtitle=draft.subtitle,
            cover_path=cover_path,
        )

    # 8) Update metaphor history
    append_metaphor_domain(draft.metaphor_domain_used)

    print(f"\n✨ Draft ready:")
    print(f"    Local:    {local_dir}")
    print(f"    OneDrive: {mirror_dir}")
    print(f"    Open Article_Substack.md → paste into Substack editor.")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a daily Substack draft (morning type-A / evening type-B)."
    )
    p.add_argument("mode", choices=["morning", "evening"], help="Which slot to compose")
    p.add_argument("--news-id", default=None, help="(morning) override: specific news_items.id")
    p.add_argument("--topic", default=None, help="(evening) override: free-text topic")
    p.add_argument("--editorial-note", default="", help="Editor's mandate to the writer")
    p.add_argument(
        "--no-draft",
        action="store_true",
        help="Skip python-substack draft push (still writes files to disk + OneDrive)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(run(args)))
