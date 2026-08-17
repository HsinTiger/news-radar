"""Push an already-written article as a reader-ready Substack draft.

This lane deliberately owns no image-prompt logic.  The caller supplies the
article; the helper removes legacy authoring instructions, appends the public
brand footer and native subscribe widget, and creates a draft without
publishing it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from substack import Api  # noqa: E402
from substack.post import Post  # noqa: E402
from substack_radar.audience import (  # noqa: E402
    DEFAULT_SUBSTACK_AUDIENCE,
    validate_substack_audience,
)
from substack_radar.compose import BRAND_TAGLINE, PUBLIC_CADENCE  # noqa: E402
from substack_radar.composer import (  # noqa: E402
    assert_reader_ready_markdown,
    strip_generated_footer,
    strip_production_instructions,
)


def _make_para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _make_bq(text: str) -> dict:
    return {"type": "blockquote", "content": [_make_para(text)]}


def _make_hr() -> dict:
    return {"type": "horizontal_rule"}


def _make_subscribe_widget() -> dict:
    """Native Substack subscribe block, not an editor-only placeholder."""
    return {
        "type": "blockquote",
        "content": [
            {
                "type": "ctaCaption",
                "content": [
                    {"type": "text", "text": "點此訂閱 → 不錯過下一篇拆解。"}
                ],
            }
        ],
        "attrs": {"url": "%%checkout_url%%", "text": "Subscribe", "language": "en"},
    }


def _make_footer_blockquote() -> dict:
    """跟 compose.build_footer_block 共用同一組常數。

    這支原本自己寫死了第三種說法（「每天兩篇對談延伸 · 每週一篇公司深拆」
    ＋「你可以直接回信」），兩者都是已退役的文案，於是同一份刊物在不同路徑
    上對讀者講了不一樣的節奏。改動節奏只該改 compose 裡的常數。"""
    tagline = BRAND_TAGLINE.strip("「」")
    return _make_bq(
        f"{tagline}\n\n"
        f"📅 {PUBLIC_CADENCE}\n"
        "💬 有想法？留言區聊聊。"
    )


def _assert_reader_ready_body(body: dict) -> None:
    """Last line of defense immediately before the remote mutation."""
    raw = json.dumps(body, ensure_ascii=False)
    assert_reader_ready_markdown(raw)
    if BRAND_TAGLINE.strip("「」") not in raw:
        raise ValueError("reader-ready gate rejected body without brand footer")
    if PUBLIC_CADENCE not in raw:
        raise ValueError("reader-ready gate rejected body without cadence promise")


def push_pasted_draft(
    *,
    title: str,
    subtitle: str,
    body_md: str,
    audience: str = DEFAULT_SUBSTACK_AUDIENCE,
) -> tuple[int, str]:
    """Create, but never publish, a reader-ready Substack draft."""
    for argname, value in (
        ("title", title),
        ("subtitle", subtitle),
        ("body_md", body_md),
    ):
        if not (value or "").strip():
            raise ValueError(f"push_pasted_draft: {argname!r} is empty")

    audience = validate_substack_audience(audience)
    clean_body = strip_generated_footer(strip_production_instructions(body_md))
    assert_reader_ready_markdown(clean_body)
    if not clean_body:
        raise ValueError("push_pasted_draft: body is empty after reader-ready cleanup")

    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not cookies or not pub_url:
        raise RuntimeError(
            "SUBSTACK_COOKIES_STRING / SUBSTACK_PUBLICATION_URL not set in env"
        )

    api = Api(cookies_string=cookies, publication_url=pub_url)
    post = Post(
        title=title,
        subtitle=subtitle,
        user_id=api.get_user_id(),
        audience=audience,
    )
    post.from_markdown(clean_body, api=api)
    draft_dict = post.get_draft()
    body = json.loads(draft_dict["draft_body"])
    nodes: list[dict] = body["content"]
    nodes.extend((_make_hr(), _make_footer_blockquote(), _make_subscribe_widget()))
    body["content"] = nodes
    _assert_reader_ready_body(body)

    draft_dict["draft_body"] = json.dumps(body, ensure_ascii=False)
    result = api.post_draft(draft_dict)
    draft_id = result.get("id") if isinstance(result, dict) else None
    if not draft_id:
        raise RuntimeError(f"post_draft returned no id: {result!r}")

    url = f"{pub_url.rstrip('/')}/publish/post/{draft_id}"
    print(f"[push_pasted_draft] ✅ reader-ready draft created id={draft_id}")
    print(f"  title: {title}")
    print(f"  URL:   {url}")
    print(f"  nodes: {len(nodes)}")
    return draft_id, url


def _cli() -> None:
    """Read title/subtitle/body_md/audience JSON from stdin and create a draft."""
    spec = json.loads(sys.stdin.read())
    draft_id, url = push_pasted_draft(**spec)
    print(json.dumps({"draft_id": draft_id, "url": url}, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
