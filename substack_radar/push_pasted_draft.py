"""News Radar · ad-hoc Substack draft push helper
================================================================

**When to use this**: Hsin pastes a fully-written article body to PM (in chat)
and asks to push it as a new Substack draft. The standard `substack_radar/compose.py`
pipeline is overkill (and inappropriate) for this case because:
  - Body is already written by Hsin / PM, no LLM compose loop needed
  - No source-file → outline → draft → audit flow
  - But the brand discipline (tagline, footer, 3-version cover prompts) STILL applies

**Why this file exists (root cause, 2026-05-22 retro)**: Before this helper,
each ad-hoc push spawned a one-off `/tmp/push_*.py` script that handcrafted a
minimal footer and skipped cover prompts. 4 drafts (197965158 / 197966296 /
198003885 / 198011799) shipped with missing brand tagline + missing 3-version
cover prompt block. Bug was bypass of `substack_radar/compose.py`, not a bug
inside compose.py itself. This helper guarantees every ad-hoc push runs the
SAME footer + cover-prompt path as the official compose pipeline.

**Discipline guaranteed by this helper**:
  1. Brand tagline appended (substack_compose.BRAND_TAGLINE)
  2. Cadence promise appended (每天兩篇對談延伸 · 每週一篇公司深拆)
  3. Subscribe widget injected
  4. 3-version cover prompt block appended (場景 / 概念 / 抽象, cold-print v0.2.2)
  5. **Hard assertion** before push: body MUST contain tagline + all 3 cover prompts

Usage::

    from tools.push_pasted_draft import push_pasted_draft

    draft_id, url = push_pasted_draft(
        title="改答案的人考更好。但他們以為自己改壞了。",
        subtitle="Kruger 2005 數了 1561 張...",
        body_md="你又一次坐在考卷前。...",
        scene_prompt="場景式 · 鉛筆橡皮擦停在...",
        concept_prompt="概念式 · 50.75% 的扁平柱狀圖...",
        abstract_prompt="抽象式 (T01) · 「對一半。」hero...",
    )
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make repo importable when this is run directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

# Share the one source of truth for brand tagline / footer / cover prompts
from substack_radar.compose import BRAND_TAGLINE  # noqa: E402
from src.image_brain import (  # noqa: E402
    BRAND_AESTHETIC_VERSION,
    HERO_TEXT_KEYPHRASES,
    build_cover_prompt_block,
)

from substack import Api  # noqa: E402
from substack.post import Post  # noqa: E402


# ---------------------------------------------------------------------------
# Footer / subscribe node builders (ProseMirror JSON — what Substack stores)
# ---------------------------------------------------------------------------

def _make_para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _make_bq(text: str) -> dict:
    return {"type": "blockquote", "content": [_make_para(text)]}


def _make_h2(text: str) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [{"type": "text", "text": text}],
    }


def _make_hr() -> dict:
    return {"type": "horizontal_rule"}


def _make_subscribe_widget() -> dict:
    """Native Substack subscribe button blockquote. Matches the shape produced
    by post.subscribe_with_caption() when called on a normal-shaped Post.
    We build it manually because subscribe_with_caption is brittle when
    post.body has been manipulated."""
    return {
        "type": "blockquote",
        "content": [{
            "type": "ctaCaption",
            "content": [{"type": "text", "text": "點此訂閱 → 不錯過下一篇拆解。"}],
        }],
        "attrs": {"url": "%%checkout_url%%", "text": "Subscribe", "language": "en"},
    }


def _make_footer_blockquote() -> dict:
    """Brand tagline + cadence promise.

    Mirrors tools/substack_compose.build_footer_block() but in ProseMirror
    JSON form (compose.py emits markdown; here we operate post-from_markdown
    so we work in JSON)."""
    # Strip the leading / trailing 「」 from BRAND_TAGLINE — we wrap in
    # blockquote which already provides visual quoting.
    tagline = BRAND_TAGLINE.strip("「」")
    return _make_bq(
        f"{tagline}\n\n"
        "📅 每天兩篇對談延伸 · 每週一篇公司深拆\n"
        "✉️ 你可以直接回信，告訴我哪個判斷值得再追"
    )


# ---------------------------------------------------------------------------
# Cover prompt section (3 blockquotes in JSON, derived from image_brain text)
# ---------------------------------------------------------------------------

def _build_cover_prompt_nodes(
    *, scene_prompt: str, concept_prompt: str, abstract_prompt: str,
    title: str = "", subtitle: str = "",
) -> list[dict]:
    """Return ProseMirror nodes for the cover prompt authoring section.

    Routes through image_brain.build_cover_prompt_block to inherit the
    aesthetic_tail (so we don't fork the cold-print spec into 2 places).
    Then we strip the markdown wrapper and embed each version as its own
    blockquote node — gives Hsin clean copy-paste in the editor.
    """
    # Re-route to image_brain so any future aesthetic bump is single-source.
    # We call with explicit 3 prompts so it's pure-formatting (no fan-out).
    md_block = build_cover_prompt_block(
        cover_image_prompt="",  # unused when all 3 explicit prompts given
        title=title,
        subtitle=subtitle,
        scene_prompt=scene_prompt,
        concept_prompt=concept_prompt,
        abstract_prompt=abstract_prompt,
    )

    # Extract aesthetic_tail from the rendered block (it appears 3x — once per
    # version). We'll re-render each blockquote ourselves to control structure.
    # Sentinel: the tail starts with " — Style: COLD-PRINT EDITORIAL".
    tail_marker = " — Style: COLD-PRINT EDITORIAL"
    if tail_marker not in md_block:
        raise RuntimeError(
            "image_brain.build_cover_prompt_block aesthetic_tail not found — "
            "tail spec may have changed. Update _build_cover_prompt_nodes "
            "in push_pasted_draft.py to match."
        )
    tail = tail_marker + md_block.split(tail_marker, 1)[1]
    # Cut tail at the first "\n\n" that ends one version's blockquote
    tail = tail.split("\n\n")[0]

    return [
        _make_hr(),
        _make_h2("📸 封面圖 Prompt · 發文前請刪除"),
        _make_para(
            "PM 替你寫好的 3 版本封面 prompt（全套 "
            f"{BRAND_AESTHETIC_VERSION} 美學）。挑 1 個（或全試）→ 丟 "
            "ChatGPT image / NanoBanana / Midjourney → 拿圖回來換掉 cover.png "
            "再 publish。發文前把整段刪掉。"
        ),
        _make_bq(f"場景式（documentary photo / scene）\n\n{scene_prompt.strip()}{tail}"),
        _make_bq(f"概念式（visual metaphor / infographic）\n\n{concept_prompt.strip()}{tail}"),
        _make_bq(f"抽象式（T01 typography-only）\n\n{abstract_prompt.strip()}{tail}"),
    ]


# ---------------------------------------------------------------------------
# Brand-discipline assertions
# ---------------------------------------------------------------------------

class BrandDisciplineError(RuntimeError):
    """Raised when assembled body fails the pre-push brand check.

    The whole point of this helper is to make this error impossible to skip.
    Do NOT add a try/except around the assertion call in the push flow —
    if the body fails, fix the body, don't suppress the error.
    """


def _assert_hero_text_in_prompt(prompt_text: str, version_label: str) -> list[str]:
    """Check a single cover prompt against visual_brand_system.md.

    Per Hsin 2026-05-16 alignment check + repaved §10.1, all 3 versions
    (scene/concept/abstract) must encode hero text occupying 40-60% of canvas,
    ≤6 字 preferred. We grep for the keyphrases that any compliant prompt
    must contain (case-insensitive on English, exact on Chinese).
    """
    problems = []
    low = prompt_text.lower()
    # "hero text" or "hero" (English brand vocab) — accepts e.g. "hero text",
    # "hero typography", "hero phrase"
    if "hero" not in low:
        problems.append(
            f"{version_label}: missing 'hero' / 'hero text' keyword "
            "(每版本必須有 hero text 設計、不能只是純照片或純圖表)"
        )
    # ≤N 字 hero length constraint — accept any digit 1-8 with optional ≤
    # (e.g. "≤6 字", "6 字", "≤4 字" all OK; semantically tighter constraints
    # like ≤4 are still compliant with §10.2 #1 ≤8 ceiling)
    import re as _re
    if not _re.search(r'[≤<]?[1-8]\s*字', prompt_text):
        problems.append(
            f"{version_label}: missing ≤N字 hero length constraint "
            "(prompt 必須明示 hero text 長度上限、N 為 1-8 整數)"
        )
    # Canvas area dominance — accept 40-60% / 40%-60% / 40-50% range marker
    if "40-60%" not in prompt_text and "40%-60%" not in prompt_text and \
       "300-360px" not in prompt_text and "180-200px" not in prompt_text:
        problems.append(
            f"{version_label}: missing 40-60% area or 300-360px / 180-200px "
            "hero size encoding (prompt 必須明示 hero 占面積或實際 px 大小)"
        )
    return problems


def _assert_brand_discipline(body_json: dict, cover_prompts: dict[str, str]) -> None:
    """Hard-fail if body fails brand checks. Now enforces 3-version 大字 rule.

    Why hard-fail: the bug we're guarding against (4 drafts shipped without
    brand tagline + cover prompts; then 5 drafts shipped with 3-version
    prompts that violated 大字 rule on 2/3 versions) only happened because
    nothing checked. Cheap automated check >> human review every time.

    2026-05-16 update: per Hsin's alignment check on §10.1 + §10.2:
      - 3 versions stay (NOT 1)  — corrected agent self-attribution
      - All 3 versions MUST encode 大字 rule (hero text + ≤6字 + 40-60% area)
    """
    raw = json.dumps(body_json, ensure_ascii=False)
    tagline_core = BRAND_TAGLINE.strip("「」")
    problems: list[str] = []
    if tagline_core not in raw:
        problems.append(f"brand tagline missing (expected substring: {tagline_core!r})")
    for label in ("場景式", "概念式", "抽象式"):
        if label not in raw:
            problems.append(f"cover prompt version missing: {label}")
    if "📸 封面圖 Prompt" not in raw:
        problems.append("📸 封面圖 Prompt heading missing")
    if "📅 每天兩篇對談延伸" not in raw:
        problems.append("cadence promise missing")
    if BRAND_AESTHETIC_VERSION not in raw:
        problems.append(
            f"aesthetic version stamp missing (expected {BRAND_AESTHETIC_VERSION}) "
            "— cover prompts may have used a stale aesthetic_tail"
        )
    # 大字 enforcement per §10.2 #1 — check each version's prompt body
    for label, key in [("場景式", "scene"), ("概念式", "concept"), ("抽象式", "abstract")]:
        prompt = cover_prompts.get(key, "")
        problems.extend(_assert_hero_text_in_prompt(prompt, label))

    if problems:
        raise BrandDisciplineError(
            "Brand discipline assertion failed before push:\n  - "
            + "\n  - ".join(problems)
            + "\n\nDo NOT remove this check. Fix the prompt instead. "
            "See config/visual_brand_system.md for context."
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def push_pasted_draft(
    *,
    title: str,
    subtitle: str,
    body_md: str,
    scene_prompt: str,
    concept_prompt: str,
    abstract_prompt: str,
    audience: str = "everyone",
) -> tuple[int, str]:
    """Push a Hsin-pasted / PM-written body as a new Substack draft, with full
    brand discipline guaranteed.

    Args:
        title: Substack draft title (≤25 字 per brand discipline; not enforced
            here, audit upstream).
        subtitle: Substack draft subtitle.
        body_md: Article body in markdown form (without footer / without cover
            prompt section — this helper appends those).
        scene_prompt: Cover version A — documentary / scene-based prompt.
        concept_prompt: Cover version B — visual metaphor / infographic prompt.
        abstract_prompt: Cover version C — T01 typography-only prompt.
        audience: Substack audience setting (default "everyone").

    Returns:
        (draft_id, draft_url) tuple. URL is the editor URL Hsin can open.

    Raises:
        BrandDisciplineError: if assembled body fails brand check before push.
            Do NOT catch this — fix the body and re-run.
        ValueError: if any required string arg is empty.
    """
    # Input validation
    for argname, val in [
        ("title", title), ("subtitle", subtitle), ("body_md", body_md),
        ("scene_prompt", scene_prompt), ("concept_prompt", concept_prompt),
        ("abstract_prompt", abstract_prompt),
    ]:
        if not (val or "").strip():
            raise ValueError(f"push_pasted_draft: {argname!r} is empty")

    # Connect to Substack
    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not cookies or not pub_url:
        raise RuntimeError(
            "SUBSTACK_COOKIES_STRING / SUBSTACK_PUBLICATION_URL not set in env"
        )
    api = Api(cookies_string=cookies, publication_url=pub_url)
    user_id = api.get_user_id()

    # Compile body markdown → ProseMirror JSON via python-substack
    post = Post(title=title, subtitle=subtitle, user_id=user_id, audience=audience)
    post.from_markdown(body_md, api=api)
    draft_dict = post.get_draft()
    body = json.loads(draft_dict["draft_body"])
    nodes: list[dict] = body["content"]

    # 1) Append brand footer + subscribe widget
    nodes.append(_make_hr())
    nodes.append(_make_footer_blockquote())
    nodes.append(_make_subscribe_widget())

    # 2) Append cover prompt authoring section (HR + H2 + intro + 3 blockquotes)
    nodes.extend(_build_cover_prompt_nodes(
        scene_prompt=scene_prompt,
        concept_prompt=concept_prompt,
        abstract_prompt=abstract_prompt,
        title=title,
        subtitle=subtitle,
    ))

    body["content"] = nodes

    # 3) **Brand discipline assertion** — last line of defense.
    _assert_brand_discipline(body, {
        "scene": scene_prompt,
        "concept": concept_prompt,
        "abstract": abstract_prompt,
    })

    # 4) Push
    draft_dict["draft_body"] = json.dumps(body, ensure_ascii=False)
    result = api.post_draft(draft_dict)
    draft_id = result.get("id")
    if not draft_id:
        raise RuntimeError(f"post_draft returned no id: {result!r}")

    url = f"{pub_url.rstrip('/')}/publish/post/{draft_id}"
    print(f"[push_pasted_draft] ✅ draft created id={draft_id}")
    print(f"  title: {title}")
    print(f"  URL:   {url}")
    print(f"  nodes: {len(nodes)}")
    return draft_id, url


# ---------------------------------------------------------------------------
# CLI for one-shot use (mostly for manual smoke tests)
# ---------------------------------------------------------------------------

def _cli() -> None:
    """Minimal CLI: read a JSON spec from stdin, push, print result.

    The JSON shape mirrors push_pasted_draft kwargs::

        {
          "title": "...",
          "subtitle": "...",
          "body_md": "...",
          "scene_prompt": "...",
          "concept_prompt": "...",
          "abstract_prompt": "...",
          "markers": {"H2 text fragment": "marker text", ...}
        }
    """
    spec = json.loads(sys.stdin.read())
    draft_id, url = push_pasted_draft(**spec)
    print(json.dumps({"draft_id": draft_id, "url": url}, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
