#!/usr/bin/env python3
"""Drain user-submitted Substack sources into drafts (local, token-free).

The Substack manual-submit frontend (GitHub Pages) → substack-submit.yml (cloud)
writes a row into the Release-state DB with feed_name='user_substack'. The Mac's
worker pulls that verified state into the local DB. THIS script
is the final link: it finds those submissions and composes a Substack draft for
each via substack_radar/compose.py (Claude/Gemini CLI — free, high quality).

Already-drafted ids are tracked in data/substack_drafts/.substack_submissions.json
so each submission is composed exactly once.

Usage:
    python scripts/drain_substack.py            # compose all new submissions
    python scripts/drain_substack.py --dry-run  # list candidates, compose nothing
    python scripts/drain_substack.py --mark <id> # mark an id done without composing
    python scripts/drain_substack.py --only-current-control  # current website/API submissions
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from substack_radar.draft_receipts import (
    DEFAULT_RECEIPTS_PATH,
    reconcile_remote_receipts,
)

# NEWS_RADAR_DB 覆寫：讓「立即」快速通道（drain_substack_fast.sh）能指向一份剛從
# canonical Release 拉下來的暫存 DB，完全不碰主 DB，避免和每小時的 compose_hourly.sh 互踩。
DB = Path(os.environ.get("NEWS_RADAR_DB") or (REPO / "data" / "01_harvest" / "news_radar.db"))
DONE_FILE = REPO / "data" / "substack_drafts" / ".substack_submissions.json"
COMPOSE = REPO / "substack_radar" / "compose.py"
ENRICH = REPO / "scripts" / "enrich_youtube_sources.py"
BUNDLE_DIR = REPO / "data" / "source_bundles"
PY = REPO / ".venv" / "bin" / "python"
RECEIPTS_FILE = DEFAULT_RECEIPTS_PATH
REMOTE_DRAFT_EVIDENCE_PENDING = 6
SUBSTACK_PUBLISH_UNPROVEN = 7
REMOTE_PUBLICATION_EVIDENCE_PENDING = 8

# YouTube 種子偵測：submit 進來的 url 欄位 + 內文裡的 youtube 連結都算。
_YT_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/[^\s)\]]+|youtu\.be/[^\s)\]]+)", re.I)


def _load_done() -> set:
    if DONE_FILE.exists():
        try:
            return set(json.loads(DONE_FILE.read_text()).get("done", []))
        except Exception:
            return set()
    return set()


def _save_done(done: set):
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DONE_FILE.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False, indent=2))


def _candidate_tags(raw: str | None) -> set[str]:
    try:
        values = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def _candidates(
    only_immediate: bool = False,
    only_current_control: bool = False,
) -> list:
    if not DB.exists():
        print(f"[drain] DB not found: {DB}")
        return []
    conn = sqlite3.connect(str(DB))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
        if {"substack_drafted_at", "substack_published_at"} <= columns:
            remote_filter = (
                " AND (substack_drafted_at IS NULL "
                "OR (INSTR(COALESCE(tags,''),'\"publish_now\"') > 0 "
                "AND substack_published_at IS NULL)) "
            )
        elif "substack_drafted_at" in columns:
            remote_filter = " AND substack_drafted_at IS NULL "
        else:
            remote_filter = " "
        rows = conn.execute(
            "SELECT id, title, word_count, url, clean_markdown, COALESCE(tags,'') FROM news_items "
            "WHERE feed_name='user_substack' "
            "  AND clean_markdown IS NOT NULL AND LENGTH(TRIM(clean_markdown)) > 0 "
            + remote_filter +
            "ORDER BY fetched_at ASC"
        ).fetchall()
    finally:
        conn.close()
    tagged = [(row, _candidate_tags(row[5])) for row in rows]
    if only_immediate:
        tagged = [(row, tags) for row, tags in tagged if "immediate" in tags]
    if only_current_control:
        tagged = [
            (row, tags)
            for row, tags in tagged
            if any(tag.startswith("control_submission:") for tag in tags)
        ]
    # The loaded five-minute worker serves all current control-plane submissions.
    # Priority remains meaningful: explicit immediate requests go first, while old
    # rows without control_submission lineage are never admitted to this lane.
    tagged.sort(key=lambda item: 0 if "immediate" in item[1] else 1)
    # Include parsed tags so the caller can pass the explicit publish-now mode.
    return [(*row[:5], tags) for row, tags in tagged]


def _yt_seeds(url, body) -> list:
    """這筆 submission 的 YouTube 種子（url 欄位 + 內文連結，去重、去尾標點）。"""
    seeds = []
    for cand in ([url] if url else []) + _YT_RE.findall(body or ""):
        s = (cand or "").strip().rstrip(".,)]。）")
        if _YT_RE.match(s) and s not in seeds:
            seeds.append(s)
    return seeds


def _enrich(rid, title, seeds):
    """跑 enrich_youtube_sources.py 建深度素材包（含無字幕 Whisper + 自動找書面報告）。
    回傳素材包路徑；任何失敗都回 None，讓 compose 照舊用原始素材，不阻斷出稿。"""
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    out = BUNDLE_DIR / f"submit_{rid[:12]}.md"
    print(f"[drain] 🎥 {len(seeds)} 個 YouTube 種子 → 建深度素材包（Whisper+書面報告，可能數分鐘）…")
    try:
        r = subprocess.run(
            [str(PY), str(ENRICH), *seeds, "--topic", title or "", "--whisper", "--out", str(out)],
            cwd=str(REPO), timeout=3600,
        )
    except Exception as e:
        print(f"[drain]   ⚠️ enrich 例外：{e}；用原始素材續寫")
        return None
    if r.returncode == 0 and out.exists():
        print(f"[drain]   ✅ 素材包 {out.name}")
        return out
    print(f"[drain]   ⚠️ enrich 失敗 (rc={r.returncode})；用原始素材續寫")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark", type=str, help="mark this id as done without composing")
    ap.add_argument("--no-enrich", action="store_true",
                    help="跳過 YouTube 深度素材包 enrichment（純用原始 submission 內文）")
    lane = ap.add_mutually_exclusive_group()
    lane.add_argument("--only-immediate", action="store_true",
                      help="只處理被標 immediate 的投稿（相容舊版手動 canary）")
    lane.add_argument(
        "--only-current-control",
        action="store_true",
        help=(
            "只處理帶 control_submission lineage 的目前投稿；priority 先處理，"
            "排除無 lineage 的歷史 backlog"
        ),
    )
    args = ap.parse_args()

    done = _load_done()

    if args.mark:
        done.add(args.mark)
        _save_done(done)
        print(f"[drain] marked done: {args.mark}")
        return 0

    try:
        receipt_ids, reconciled = reconcile_remote_receipts(
            DB,
            path=RECEIPTS_FILE,
        )
    except Exception as exc:
        print(f"[drain] 🛑 remote-draft receipt reconciliation failed: {exc}")
        return 2
    if reconciled:
        print(f"[drain] reconciled {reconciled} remote-draft receipt(s) into SQLite")
    if receipt_ids:
        print(
            f"[drain] protecting {len(receipt_ids)} source(s) with pending remote receipts"
        )

    rows = _candidates(
        only_immediate=args.only_immediate,
        only_current_control=args.only_current_control,
    )
    pending = [
        row
        for row in rows
        if (row[0] not in done or "publish_now" in row[5])
        and row[0] not in receipt_ids
    ]
    scope = (
        " (immediate only)"
        if args.only_immediate
        else " (current control only)" if args.only_current_control else ""
    )
    print(f"[drain] {len(rows)} user_substack item(s){scope}, {len(pending)} pending compose")
    if (args.only_immediate or args.only_current_control) and not pending:
        return 0  # 快速通道沒事就安靜結束（每 5 分鐘跑一次，不洗 log）
    for rid, title, wc, url, body, tags in pending:
        tag = "  🎥yt" if (not args.no_enrich and _yt_seeds(url, body)) else ""
        print(f"  · {rid[:12]}  {wc:>6}w  {title[:50]}{tag}")

    if args.dry_run:
        print("[drain] dry-run — composed nothing.")
        return 0

    composed = 0
    evidence_pending = 0
    for rid, title, wc, url, body, tags in pending:
        print(f"[drain] composing {rid[:12]} …")
        cmd = [
            str(PY),
            "-u",
            str(COMPOSE),
            "morning",
            "--news-id",
            rid,
            "--require-substack-draft",
        ]
        if "publish_now" in tags:
            cmd.append("--publish-now")
        if not args.no_enrich:
            seeds = _yt_seeds(url, body)
            if seeds:
                bundle = _enrich(rid, title, seeds)
                if bundle:
                    cmd += ["--bundle", str(bundle)]
        r = subprocess.run(cmd, cwd=str(REPO))
        if r.returncode in (
            0,
            REMOTE_DRAFT_EVIDENCE_PENDING,
            REMOTE_PUBLICATION_EVIDENCE_PENDING,
        ):
            done.add(rid)
            _save_done(done)          # persist after each success (crash-safe)
            if r.returncode == 0:
                composed += 1
            else:
                evidence_pending += 1
                print(
                    f"[drain] ⚠️ remote Substack evidence exists for {rid[:12]}; "
                    "receipt will reconcile canonical state next run"
                )
        else:
            print(f"[drain] ⚠️ compose failed for {rid[:12]} (rc={r.returncode}); will retry next run")
    print(
        f"[drain] done. composed {composed}/{len(pending)}; "
        f"evidence_pending={evidence_pending}."
    )
    return 0 if composed == len(pending) else 1


if __name__ == "__main__":
    sys.exit(main())
