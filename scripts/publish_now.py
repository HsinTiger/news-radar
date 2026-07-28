#!/usr/bin/env python3
"""Governed immediate publish with durable per-platform evidence.

The workflow is intentionally fail-closed:

1. fetch owner input and persist one deterministic source row;
2. compose only the requested platforms;
3. persist draft/platform variants plus deterministic quality evidence;
4. allow one bounded rewrite, holding unresolved content for review;
5. publish only platform tuples that lack prior success evidence;
6. report ``published`` only when every requested platform is proven live.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from src import db as dbmod  # noqa: E402
from src.composer import compose_multi_platform, finalize_variant  # noqa: E402
from src.content_quality_guard import (  # noqa: E402
    check_platform_format,
    check_platform_style,
    check_quality,
    combine_visible_text,
    format_issues,
    has_blocking_issues,
    should_request_rewrite,
)
from src.cover_uploader import upload_cards  # noqa: E402
from src.publisher import (  # noqa: E402
    publish_fb_carousel,
    publish_ig_carousel,
    publish_threads_carousel,
)
from src.schema import (  # noqa: E402
    CarouselCards,
    Draft,
    DraftContent,
    MultiPlatformDraft,
    NewsItem,
    PlatformVariant,
    PublishResult,
    ScoreBreakdown,
)
from substack_radar.cards import build_cards, render_cards  # noqa: E402
from run_pipeline import (  # noqa: E402
    _deterministic_food_safety_carousel,
    _deterministic_food_safety_variant,
    _is_recovery_food_safety_investigation,
)


_PUB = {
    "ig": publish_ig_carousel,
    "threads": publish_threads_carousel,
    "fb": publish_fb_carousel,
}
_PLAT_KEY = {
    "fb": "fb",
    "facebook": "fb",
    "ig": "ig",
    "instagram": "ig",
    "threads": "threads",
}
_DB_PLATFORM = {"fb": "facebook", "ig": "instagram", "threads": "threads"}
_SHORT_PLATFORM = {value: key for key, value in _DB_PLATFORM.items()}
_ORDER = ("threads", "ig", "fb")
APPENDIX_VERSION = "1.0"

_TW_STOCK_TITLE_CUES = ("台股", "證交所", "加權指數", "櫃買")

STATUS_PUBLISHED = "published"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_QUALITY_HELD = "quality_held"
STATUS_SETUP_READY = "setup_ready"


def _topic_category_for_title(title: str) -> str:
    """Keep immediate-publish cards out of the generic ``other`` bucket."""

    if any(cue in (title or "") for cue in _TW_STOCK_TITLE_CUES):
        return "tw_stocks"
    return "other"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_result(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_platforms(raw: str) -> list[str]:
    platforms: list[str] = []
    invalid: list[str] = []
    for value in raw.split(","):
        value = value.strip().lower()
        if not value:
            continue
        canonical = _PLAT_KEY.get(value)
        if canonical is None:
            invalid.append(value)
        elif canonical not in platforms:
            platforms.append(canonical)
    if invalid:
        raise ValueError(f"unknown platforms: {','.join(invalid)}")
    if not platforms:
        raise ValueError("no valid platforms")
    return platforms


def fetch_article(url: str) -> tuple[str, str]:
    """Return ``(title, clean_text)`` for one public article URL."""
    import httpx
    import trafilatura

    response = httpx.get(
        url,
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NewsRadar/1.0)"},
    )
    response.raise_for_status()
    title, text = "", ""
    data = trafilatura.extract(
        response.text,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_tables=False,
    )
    if data:
        parsed = json.loads(data)
        title = (parsed.get("title") or "").strip()
        text = (parsed.get("text") or "").strip()
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.I | re.S)
        title = (
            match.group(1).strip() if match else url.rstrip("/").split("/")[-1]
        )[:80]
    return title, text


def _source_identity(args: argparse.Namespace) -> str:
    if args.submission_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", args.submission_id):
            raise ValueError("invalid submission_id")
        return f"submission:{args.submission_id}"
    if args.url:
        return f"url:{args.url.strip()}"
    if args.file:
        return f"file:{Path(args.file).resolve()}"
    return f"text:{args.title}\n{args.text}"


def _lineage_ids(args: argparse.Namespace) -> tuple[str, str]:
    identity = _source_identity(args)
    news_id = hashlib.sha1(f"publish_now:{identity}".encode()).hexdigest()
    draft_id = hashlib.sha1(f"{news_id}:immediate:v1".encode()).hexdigest()
    return news_id, draft_id


def _load_source(args: argparse.Namespace) -> tuple[str, str]:
    if args.url:
        from scripts.submit_source import YT_VIDEO_ID_RE, _extract_yt_transcript

        if YT_VIDEO_ID_RE.search(args.url):
            print(f"[publish_now] ▶️ YouTube → 抓逐字稿 {args.url}", flush=True)
            info = _extract_yt_transcript(args.url)
            if not info or len((info.get("transcript") or "").strip()) < 80:
                raise ValueError(
                    "YouTube 抓不到足夠逐字稿；請改貼全文或改由本機取得逐字稿"
                )
            return (info.get("title") or args.note or "YouTube").strip(), info[
                "transcript"
            ].strip()
        print(f"[publish_now] 🔗 fetching {args.url}", flush=True)
        return fetch_article(args.url)

    title = args.title.strip()
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    text = (text or "").strip()
    if not title or not text:
        raise ValueError("需要 --url，或 --title 搭配 --text/--file")
    return title, text


def _load_exact_bundle(raw: str, platforms: list[str]) -> MultiPlatformDraft:
    """Load owner-supplied copy without weakening any downstream quality gate."""

    try:
        payload = json.loads(raw)
        bundle = MultiPlatformDraft.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid exact_copy_json: {exc}") from exc
    requested = set(platforms)
    missing = [platform for platform in platforms if getattr(bundle, platform) is None]
    if missing:
        raise ValueError(f"exact_copy_json missing requested platforms: {','.join(missing)}")
    broadened = [
        platform
        for platform in _ORDER
        if platform not in requested and getattr(bundle, platform) is not None
    ]
    if broadened:
        raise ValueError(
            f"exact_copy_json contains unrequested platforms: {','.join(broadened)}"
        )
    return bundle


async def _compose_or_exact(
    args: argparse.Namespace,
    title: str,
    content: str,
    platforms: list[str],
) -> MultiPlatformDraft | None:
    if args.exact_copy_json:
        print("[publish_now] 🧾 using deterministic owner copy", flush=True)
        return _load_exact_bundle(args.exact_copy_json, platforms)
    return await compose_multi_platform(
        title,
        content,
        editorial_note=args.note,
        platforms=platforms,
    )


def _persist_source(
    conn,
    *,
    args: argparse.Namespace,
    news_id: str,
    title: str,
    text: str,
    platforms: list[str],
) -> None:
    tags = ["user_submission", "publish_now"]
    tags.extend(f"platform:{platform}" for platform in platforms)
    if args.submission_id:
        tags.append(f"control_submission:{args.submission_id}")
        tags.append(f"control_route:{args.submission_id}:{','.join(platforms)}")
    if args.url:
        tags.append(f"control_source_url:{args.url[:1800]}")
    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="video" if args.url and "youtu" in args.url else "article",
        # Synthetic URL keeps one control-plane submission isolated from an RSS row
        # for the same article while the original source remains in a governed tag.
        url=f"control:publish-now:{news_id}",
        title=title,
        published_at=_now(),
        fetched_at=_now(),
        clean_markdown=text,
        word_count=len(text.split()),
        tags=tags,
        status="fetched",
    )
    if dbmod.upsert_news(conn, item):
        return
    conn.execute(
        """
        UPDATE news_items
           SET title=?,clean_markdown=?,word_count=?,tags=?,status='fetched'
         WHERE id=?
        """,
        (title, text, len(text.split()), json.dumps(tags, ensure_ascii=False), news_id),
    )
    conn.commit()


def _legacy_content(variant: PlatformVariant, image_url: str | None) -> DraftContent:
    paragraphs = [part.strip() for part in (variant.body or "").split("\n\n") if part.strip()]

    def paragraph(index: int) -> str:
        return paragraphs[index] if index < len(paragraphs) else "(見 platform_drafts)"

    return DraftContent(
        title=variant.title.strip() or "(untitled)",
        hook=paragraph(0),
        framework=paragraph(1),
        validation=paragraph(2),
        macro_insight=paragraph(3),
        ending_question=" ".join(variant.hashtags or []) or "(見 platform_drafts)",
        hashtags=list(variant.hashtags or []),
        image_url=image_url,
    )


def _finalize_bundle(bundle, platforms: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    finalized: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for platform in platforms:
        raw_variant = getattr(bundle, platform, None)
        if raw_variant is None:
            problems.append(f"{platform}: missing variant")
            continue
        variant, full_text, within_limit = finalize_variant(raw_variant, platform)
        finalized[platform] = {
            "variant": variant,
            "title": variant.title,
            "full_text": full_text,
            "within_limit": bool(within_limit),
        }
        if not within_limit:
            problems.append(f"{platform}: finalize limit rejected")
    if bundle.carousel is None:
        problems.append("carousel: missing")
    return finalized, problems


def _apply_source_bounded_overrides(
    bundle,
    *,
    title: str,
    source_text: str,
    platforms: list[str],
):
    """Reuse production Recovery templates for exact owner-supplied evidence."""

    evidence = f"{title}\n{source_text}"
    source_label = "食藥署 owner submission" if "食藥署" in evidence else "owner submission"
    if not _is_recovery_food_safety_investigation(
        source_label=source_label,
        title=title,
        content=source_text,
    ):
        return bundle
    updates: dict[str, Any] = {
        "carousel": _deterministic_food_safety_carousel(),
    }
    for platform in platforms:
        variant = getattr(bundle, platform, None)
        if variant is not None:
            updates[platform] = _deterministic_food_safety_variant(
                variant,
                platform=platform,
            )
    return bundle.model_copy(update=updates)


def _persist_composition(
    conn,
    *,
    news_id: str,
    draft_id: str,
    bundle,
    finalized: dict[str, dict[str, Any]],
    platforms: list[str],
    status: str = "pending_review",
) -> None:
    canonical = "fb" if "fb" in finalized else platforms[0]
    item = finalized[canonical]
    draft = Draft(
        id=draft_id,
        news_id=news_id,
        persona_version="publish-now-v1",
        content=_legacy_content(item["variant"], bundle.image_url),
        full_text=item["full_text"],
        confidence_score=1.0,
        score_breakdown=ScoreBreakdown(
            data_density=1.0,
            strategic_signal=1.0,
            news_novelty=1.0,
            persona_fit=1.0,
        ),
        llm_provider="composer",
        llm_model="governed-immediate",
        generated_at=_now(),
        status=status,
    )
    dbmod.insert_draft(conn, draft)
    dbmod.set_carousel_json(conn, draft_id, bundle.carousel.model_dump_json())
    for platform in platforms:
        item = finalized[platform]
        variant = item["variant"]
        dbmod.upsert_platform_draft(
            conn,
            draft_id=draft_id,
            platform=_DB_PLATFORM[platform],
            title=variant.title,
            body=variant.body,
            hashtags=list(variant.hashtags or []),
            full_text=item["full_text"],
            char_count=variant.char_count,
            appendix_version=APPENDIX_VERSION,
            created_at=_now(),
        )


def _record_quality(
    conn,
    *,
    draft_id: str,
    news_id: str,
    title: str,
    source_text: str,
    carousel: CarouselCards | None,
    finalized: dict[str, dict[str, Any]],
    attempt: int,
) -> dict[str, list]:
    findings: dict[str, list] = {}
    for platform, item in finalized.items():
        visible_text, issues = _quality_issues(
            platform,
            item,
            title=title,
            source_text=source_text,
            carousel=carousel,
        )
        findings[platform] = issues
        dbmod.record_quality_evaluation(
            conn,
            draft_id=draft_id,
            news_id=news_id,
            platform=_DB_PLATFORM[platform],
            stage="compose",
            attempt=attempt,
            full_text=visible_text,
            issues=issues,
        )
    return findings


def _quality_problems(findings: dict[str, list], severity: str) -> list[str]:
    predicate = has_blocking_issues if severity == "block" else should_request_rewrite
    return [
        f"{platform}: {format_issues(issues)}"
        for platform, issues in findings.items()
        if predicate(issues)
    ]


def _quality_evidence(findings: dict[str, list]) -> dict[str, list[dict[str, str]]]:
    return {
        platform: [
            {
                "code": issue.code,
                "severity": issue.severity,
                "message": issue.message,
                "evidence": issue.evidence,
            }
            for issue in issues
        ]
        for platform, issues in findings.items()
    }


def _quality_issues(
    platform: str,
    item: dict[str, Any],
    *,
    title: str,
    source_text: str,
    carousel: CarouselCards | None,
) -> tuple[str, list]:
    """Apply the same evidence, native-style, and visible-card gates as Recovery."""

    visible_text = combine_visible_text(item["full_text"], carousel)
    issues = check_quality(
        visible_text,
        title=title,
        recovery=True,
        source_text=source_text,
    )
    issues.extend(
        check_platform_style(
            platform,
            item["full_text"],
            title=title,
            recovery=True,
        )
    )
    card_count = 0
    if carousel is not None:
        card_count = len(
            build_cards(
                title=item["title"] or "",
                subtitle="",
                carousel=carousel,
            )
        )
    issues.extend(
        check_platform_format(
            platform,
            carousel_card_count=card_count,
            recovery=True,
        )
    )
    return visible_text, issues


def _check_setup_quality(
    finalized: dict[str, dict[str, Any]],
    *,
    title: str,
    source_text: str,
    carousel: CarouselCards | None,
) -> tuple[dict[str, list], list[str]]:
    findings = {}
    for platform, item in finalized.items():
        _visible_text, issues = _quality_issues(
            platform,
            item,
            title=title,
            source_text=source_text,
            carousel=carousel,
        )
        findings[platform] = issues
    unresolved = _quality_problems(findings, "block")
    unresolved.extend(_quality_problems(findings, "rewrite"))
    return findings, unresolved


def _render_setup_previews(
    *,
    evidence_dir: Path,
    bundle,
    finalized: dict[str, dict[str, Any]],
    platforms: list[str],
) -> dict[str, list[str]]:
    previews: dict[str, list[str]] = {}
    for platform in platforms:
        item = finalized[platform]
        cards = build_cards(
            title=item["title"] or "",
            subtitle="",
            carousel=bundle.carousel,
        )
        if len(cards) < 2:
            raise ValueError(f"{platform}: build_cards <2")
        output_dir = evidence_dir / "cards" / platform
        paths = render_cards(
            cards=cards,
            topic_category=_topic_category_for_title(item["title"] or ""),
            aspect=platform,
            output_dir=output_dir,
        )
        if len(paths) < 2:
            raise ValueError(f"{platform}: render_cards <2")
        previews[platform] = [
            path.resolve().relative_to(evidence_dir.resolve()).as_posix()
            for path in paths
        ]
    return previews


async def run_setup_only(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Compose and render evidence without opening canonical state or Meta APIs."""
    platforms = _parse_platforms(args.platforms)
    title, text = _load_source(args)
    if len(text) < 80:
        raise ValueError("抓不到足夠內文；請改用全文輸入")
    content = (f"{args.note}\n\n" if args.note else "") + text
    bundle = await _compose_or_exact(args, title, content, platforms)
    if bundle is None:
        return 3, {
            "status": STATUS_FAILED,
            "reason": "compose_failed",
            "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
            "publish_invoked": False,
            "canonical_state_mutated": False,
        }
    source_evidence_text = f"{title}\n{text}"
    bundle = _apply_source_bounded_overrides(
        bundle,
        title=title,
        source_text=text,
        platforms=platforms,
    )
    finalized, structural = _finalize_bundle(bundle, platforms)
    findings: dict[str, list] = {}
    unresolved = structural
    if not unresolved:
        findings, unresolved = _check_setup_quality(
            finalized,
            title=title,
            source_text=source_evidence_text,
            carousel=bundle.carousel,
        )
    if unresolved and not args.exact_copy_json:
        print(
            "[publish_now] ✍️ setup-only quality guard 要求唯一一次重寫："
            + " || ".join(unresolved),
            flush=True,
        )
        rewrite_note = (
            f"{args.note}\n\nQUALITY REWRITE (one attempt only): Preserve source-backed "
            "facts and the core insight. Remove or attribute unsupported numeric claims; "
            "do not invent citations. Fix: "
            + " || ".join(unresolved)
        )
        retry = await compose_multi_platform(
            title,
            content,
            editorial_note=rewrite_note,
            platforms=platforms,
        )
        if retry is not None:
            retry = _apply_source_bounded_overrides(
                retry,
                title=title,
                source_text=text,
                platforms=platforms,
            )
            retry_finalized, retry_structural = _finalize_bundle(retry, platforms)
            if not retry_structural:
                retry_findings, retry_unresolved = _check_setup_quality(
                    retry_finalized,
                    title=title,
                    source_text=source_evidence_text,
                    carousel=retry.carousel,
                )
                bundle = retry
                finalized = retry_finalized
                findings = retry_findings
                unresolved = retry_unresolved
            else:
                unresolved = ["quality rewrite incomplete", *retry_structural]
        else:
            unresolved = ["quality rewrite composer failed", *unresolved]

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    preview_payload = {
        platform: {
            "title": item["title"],
            "full_text": item["full_text"],
            "within_limit": item["within_limit"],
        }
        for platform, item in finalized.items()
    }
    if unresolved:
        result = {
            "status": STATUS_QUALITY_HELD,
            "reason": "unresolved_quality_evidence",
            "issues": unresolved,
            "quality": _quality_evidence(findings),
            "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
            "previews": preview_payload,
            "publish_invoked": False,
            "canonical_state_mutated": False,
        }
        _write_result(str(evidence_dir / "setup_only_evidence.json"), result)
        return 4, result

    card_files = _render_setup_previews(
        evidence_dir=evidence_dir,
        bundle=bundle,
        finalized=finalized,
        platforms=platforms,
    )
    result = {
        "status": STATUS_SETUP_READY,
        "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
        "quality": _quality_evidence(findings),
        "previews": preview_payload,
        "card_files": card_files,
        "publish_invoked": False,
        "canonical_state_mutated": False,
    }
    _write_result(str(evidence_dir / "setup_only_evidence.json"), result)
    return 0, result


async def _compose_governed(
    conn,
    *,
    args: argparse.Namespace,
    news_id: str,
    draft_id: str,
    title: str,
    content: str,
    source_text: str,
    platforms: list[str],
) -> tuple[Any | None, dict[str, dict[str, Any]], list[str]]:
    note = args.note
    bundle = await _compose_or_exact(args, title, content, platforms)
    if bundle is None:
        return None, {}, ["composer returned no draft"]
    bundle = _apply_source_bounded_overrides(
        bundle,
        title=title,
        source_text=source_text,
        platforms=platforms,
    )
    finalized, structural = _finalize_bundle(bundle, platforms)
    if structural:
        return bundle, finalized, structural
    _persist_composition(
        conn,
        news_id=news_id,
        draft_id=draft_id,
        bundle=bundle,
        finalized=finalized,
        platforms=platforms,
    )
    findings = _record_quality(
        conn,
        draft_id=draft_id,
        news_id=news_id,
        title=title,
        source_text=f"{title}\n{source_text}",
        carousel=bundle.carousel,
        finalized=finalized,
        attempt=1,
    )
    blocks = _quality_problems(findings, "block")
    if blocks:
        return bundle, finalized, blocks
    rewrites = _quality_problems(findings, "rewrite")
    if not rewrites:
        return bundle, finalized, []

    if args.exact_copy_json:
        return bundle, finalized, rewrites

    print(
        "[publish_now] ✍️ quality guard 要求唯一一次重寫：" + " || ".join(rewrites),
        flush=True,
    )
    rewrite_note = (
        f"{note}\n\nQUALITY REWRITE (one attempt only): Preserve source-backed facts "
        "and the core insight. Remove or attribute unsupported numeric claims; "
        "do not invent citations. Fix: " + " || ".join(rewrites)
    )
    retry = await compose_multi_platform(
        title,
        content,
        editorial_note=rewrite_note,
        platforms=platforms,
    )
    if retry is None:
        return bundle, finalized, ["quality rewrite composer failed", *rewrites]
    retry = _apply_source_bounded_overrides(
        retry,
        title=title,
        source_text=source_text,
        platforms=platforms,
    )
    retry_finalized, structural = _finalize_bundle(retry, platforms)
    if structural:
        return bundle, finalized, ["quality rewrite incomplete", *structural]
    _persist_composition(
        conn,
        news_id=news_id,
        draft_id=draft_id,
        bundle=retry,
        finalized=retry_finalized,
        platforms=platforms,
    )
    retry_findings = _record_quality(
        conn,
        draft_id=draft_id,
        news_id=news_id,
        title=title,
        source_text=f"{title}\n{source_text}",
        carousel=retry.carousel,
        finalized=retry_finalized,
        attempt=2,
    )
    unresolved = _quality_problems(retry_findings, "block")
    unresolved.extend(_quality_problems(retry_findings, "rewrite"))
    return retry, retry_finalized, unresolved


def _load_reusable(
    conn,
    draft_id: str,
    platforms: list[str],
) -> tuple[CarouselCards, dict[str, dict[str, Any]]] | None:
    draft = conn.execute(
        "SELECT status,queue_status,carousel_json FROM drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    if draft is None or draft["status"] not in ("approved", "auto_approved", "published"):
        return None
    if not draft["carousel_json"]:
        return None
    rows = conn.execute(
        "SELECT platform,title,full_text FROM platform_drafts WHERE draft_id=?",
        (draft_id,),
    ).fetchall()
    by_platform = {
        _SHORT_PLATFORM[row["platform"]]: {
            "title": row["title"] or "",
            "full_text": row["full_text"],
        }
        for row in rows
        if row["platform"] in _SHORT_PLATFORM
    }
    if any(platform not in by_platform for platform in platforms):
        return None
    try:
        carousel = CarouselCards.model_validate_json(draft["carousel_json"])
    except Exception:  # noqa: BLE001
        return None
    return carousel, by_platform


async def _publish_platform(
    platform: str,
    cover_title: str,
    carousel: CarouselCards,
    caption: str,
    draft_id: str,
) -> tuple[bool, str, object]:
    cards = build_cards(title=cover_title or "", subtitle="", carousel=carousel)
    if len(cards) < 2:
        return False, "build_cards <2", None
    output_dir = Path(tempfile.mkdtemp(prefix=f"pn_{platform}_"))
    paths = render_cards(
        cards=cards,
        topic_category=_topic_category_for_title(cover_title),
        aspect=platform,
        output_dir=output_dir,
    )
    slug = re.sub(r"[^A-Za-z0-9_]", "", f"{draft_id[:20]}_{platform}")[:40]
    urls = upload_cards(paths, slug)
    if len(urls) < 2:
        return False, f"card upload failed ({len(urls)})", None
    result = await _PUB[platform](urls, caption)
    return bool(result.get("success")), str(result.get("error") or "")[:200], result.get("id")


async def _publish_pending(
    conn,
    *,
    news_id: str,
    draft_id: str,
    platforms: list[str],
    carousel: CarouselCards,
    finalized: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, dict[str, Any]]]:
    outcomes: dict[str, dict[str, Any]] = {}
    for platform in (value for value in _ORDER if value in platforms):
        db_platform = _DB_PLATFORM[platform]
        if dbmod.has_successful_publish(conn, draft_id, db_platform):
            outcomes[db_platform] = {"status": "already_published", "success": True}
            continue
        item = finalized[platform]
        try:
            ok, error, post_id = await _publish_platform(
                platform,
                item["title"],
                carousel,
                item["full_text"],
                draft_id,
            )
        except Exception as exc:  # noqa: BLE001 — one platform must not hide another
            ok, error, post_id = False, f"exception: {exc!r}"[:200], None
        dbmod.log_publish(
            conn,
            PublishResult(
                draft_id=draft_id,
                platform=db_platform,
                platform_post_id=str(post_id) if post_id is not None else None,
                posted_at=_now(),
                success=ok,
                error_message=error or None,
            ),
        )
        outcomes[db_platform] = {
            "status": "published" if ok else "failed",
            "success": ok,
            "platform_post_id": str(post_id) if post_id is not None else None,
            "error": error or None,
        }
        print(
            f"{'✅' if ok else '❌'} [{platform}] id={post_id}"
            + ("" if ok else f" err={error}"),
            flush=True,
        )

    intended = {_DB_PLATFORM[platform] for platform in platforms}
    succeeded = {
        platform
        for platform in intended
        if dbmod.has_successful_publish(conn, draft_id, platform)
    }
    if not dbmod.pending_publish_platforms(conn, draft_id):
        dbmod.mark_queue_published(conn, draft_id)
        return STATUS_PUBLISHED, outcomes
    dbmod.enqueue_draft(conn, draft_id, publish_at=_now())
    if succeeded:
        dbmod.update_status(conn, news_id, "publish_partial")
        return STATUS_PARTIAL, outcomes
    dbmod.update_status(conn, news_id, "publish_failed")
    return STATUS_FAILED, outcomes


async def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    platforms = _parse_platforms(args.platforms)
    news_id, draft_id = _lineage_ids(args)
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        reusable = _load_reusable(conn, draft_id, platforms)
        if reusable is not None:
            carousel, finalized = reusable
            print("[publish_now] ♻️ reuse canonical draft; retry only missing platform tuples")
        else:
            title, text = _load_source(args)
            print(f"[publish_now] 📄 title={title!r} text={len(text)}字", flush=True)
            if len(text) < 80:
                raise ValueError("抓不到足夠內文；請改用全文輸入")
            _persist_source(
                conn,
                args=args,
                news_id=news_id,
                title=title,
                text=text,
                platforms=platforms,
            )
            content = (f"{args.note}\n\n" if args.note else "") + text
            print(
                f"[publish_now] ✍️ composing requested platforms={','.join(platforms)}",
                flush=True,
            )
            bundle, finalized, unresolved = await _compose_governed(
                conn,
                args=args,
                news_id=news_id,
                draft_id=draft_id,
                title=title,
                content=content,
                source_text=text,
                platforms=platforms,
            )
            draft_exists = conn.execute(
                "SELECT 1 FROM drafts WHERE id=?", (draft_id,)
            ).fetchone()
            if (
                bundle is None
                or bundle.carousel is None
                or len(finalized) != len(platforms)
                or draft_exists is None
            ):
                dbmod.update_status(conn, news_id, "compose_failed")
                return 3, {
                    "status": STATUS_FAILED,
                    "reason": "compose_failed",
                    "issues": unresolved,
                    "news_id": news_id,
                    "draft_id": draft_id,
                    "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
                }
            if unresolved:
                conn.execute(
                    "UPDATE drafts SET status='pending_review',queue_status=NULL WHERE id=?",
                    (draft_id,),
                )
                conn.commit()
                dbmod.update_status(conn, news_id, "quality_held")
                return 4, {
                    "status": STATUS_QUALITY_HELD,
                    "reason": "unresolved_quality_evidence",
                    "issues": unresolved,
                    "news_id": news_id,
                    "draft_id": draft_id,
                    "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
                }
            conn.execute("UPDATE drafts SET status='auto_approved' WHERE id=?", (draft_id,))
            conn.commit()
            dbmod.enqueue_draft(conn, draft_id, publish_at=_now())
            dbmod.update_status(conn, news_id, "queued")
            carousel = bundle.carousel

        status, outcomes = await _publish_pending(
            conn,
            news_id=news_id,
            draft_id=draft_id,
            platforms=platforms,
            carousel=carousel,
            finalized=finalized,
        )
        return (0 if status == STATUS_PUBLISHED else 1), {
            "status": status,
            "news_id": news_id,
            "draft_id": draft_id,
            "submission_id": args.submission_id or None,
            "selected_platforms": [_DB_PLATFORM[p] for p in platforms],
            "platforms": outcomes,
        }
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Immediate governed carousel publish (url or title+text)"
    )
    parser.add_argument("--url", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--platforms", default="fb,ig,threads")
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--exact-copy-json",
        default="",
        help="owner-supplied MultiPlatformDraft JSON; still subject to all quality gates",
    )
    parser.add_argument("--submission-id", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="compose, quality-check, and render previews without DB or Meta I/O",
    )
    parser.add_argument("--evidence-dir", default="logs/publish-now-canary")
    return parser


async def main() -> int:
    args = _parser().parse_args()
    try:
        if args.setup_only:
            exit_code, result = await run_setup_only(args)
        else:
            exit_code, result = await run(args)
    except (OSError, ValueError) as exc:
        exit_code = 2
        result = {"status": STATUS_FAILED, "reason": str(exc)[:500]}
        print(f"[publish_now] ❌ {exc}", flush=True)
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        result = {"status": STATUS_FAILED, "reason": f"unexpected: {exc!r}"[:500]}
        print(f"[publish_now] ❌ unexpected: {exc!r}", flush=True)
    _write_result(args.result_json, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
