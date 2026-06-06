"""
News Radar · Manual Submit API Server (FastAPI)
=================================================
A lightweight REST API server that accepts article/Twitter/YouTube/image
submissions from the frontend, processes them through the existing pipeline,
and optionally triggers immediate compose/publish.

Architecture:
    runs via launchd on the Mac (localhost:8765 by default)
    → validates + stores submission
    → inserts into news_items DB (reuses existing submit_source.py logic)
    → optionally triggers compose via run_pipeline.py subprocess

Usage (dev):
    cd ~/news_radar && uvicorn scripts.manual_submit_server:app --reload --port 8765

Usage (production via launchd):
    see com.hsin.news-radar.manual-submit-server.plist (created next)

Frontend talks to: http://localhost:8765  (or remote via GitHub PAT → workflow_dispatch)

Endpoints:
    POST /api/submit          — generic: {type, content, platforms?, note?, schedule?}
    POST /api/submit/image    — multipart image upload
    GET  /api/status          — system health + queue depth
    GET  /api/history         — recent submissions
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
sys.path.insert(0, str(_REPO_ROOT))

# Attempt .env load (best-effort — dotenv may not be loaded at import time)
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass

# Import existing submission logic
from scripts.submit_source import (
    process_url,
    process_text,
    process_youtube,
    process_image_base64,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="News Radar · Manual Submitter API",
    version="1.0.0",
    description="Receive manual content submissions for the News Radar pipeline",
)

# CORS — allow the GitHub Pages dashboard + any local dev origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:5500",
        "https://hsintiger.github.io",
        "https://news-radar-dashboard.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SubmitRequest(BaseModel):
    type: str = Field(..., pattern=r"^(url|text|youtube|image)$")
    content: str = Field(..., min_length=1, max_length=100_000)
    platforms: Optional[List[str]] = None
    note: Optional[str] = ""
    schedule: Optional[str] = Field("next", pattern=r"^(now|next)$")

class SubmitResponse(BaseModel):
    status: str  # "created" | "already_exists" | "error"
    id: Optional[str] = None
    title: Optional[str] = None
    word_count: Optional[int] = None
    message: Optional[str] = None

class StatusResponse(BaseModel):
    status: str  # "ok" | "error"
    version: str = "1.0.0"
    server_time: str
    db_path: Optional[str] = None
    db_size_mb: Optional[float] = None
    queue_stats: Optional[Dict[str, int]] = None
    substack_auto_draft: bool = False

class HistoryItem(BaseModel):
    type: str
    content_preview: str
    status: str
    submitted_at: str
    id: Optional[str] = None
    title: Optional[str] = None
    word_count: Optional[int] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db_path() -> Optional[Path]:
    """Resolve the news_radar SQLite DB path."""
    candidates = [
        _REPO_ROOT / "data" / "01_harvest" / "news_radar.db",
        _REPO_ROOT / "data" / "news_radar.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _get_queue_stats(db_path: Path) -> Optional[Dict[str, int]]:
    """Query the publish queue for a status breakdown."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, COUNT(*) FROM publish_queue GROUP BY status"
        ).fetchall()
        conn.close()
        return {r[0]: r[1] for r in row}
    except Exception:
        return None


def _trigger_immediate_publish(url: str, platforms: List[str], note: str = "") -> Dict[str, Any]:
    """Trigger the publish_now pipeline via subprocess (local) or GitHub dispatch.

    Local path: run `python scripts/publish_now.py --url <url>` directly.
    Falls back to returning instructions if not possible.
    """
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                str(_HERE / "publish_now.py"),
                "--url", url,
                "--platforms", ",".join(platforms),
            ]
            + (["--note", note] if note else []),
            capture_output=True, text=True, timeout=120, env=env,
        )
        stdout = result.stdout.strip() or ""
        stderr = result.stderr.strip() or ""
        ok = result.returncode == 0
        return {
            "triggered": ok,
            "exit_code": result.returncode,
            "stdout": stdout[-500:],
            "stderr": stderr[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"triggered": False, "error": "timeout after 120s"}
    except FileNotFoundError:
        return {"triggered": False, "error": "publish_now.py not found"}
    except Exception as exc:
        return {"triggered": False, "error": str(exc)}


# ===========================================================================
# Routes
# ===========================================================================

@app.get("/")
async def root():
    return {"service": "news-radar-manual-submit", "status": "running"}


@app.post("/api/submit", response_model=SubmitResponse)
async def submit(req: SubmitRequest):
    """Unified submission endpoint for all 4 source types.

    The request body is JSON with:
        type: "url" | "text" | "youtube" | "image"
        content: the actual payload (URL, text body, YouTube URL, or base64 image data)
        platforms: optional (default: ["fb", "ig", "threads"])
        note: optional editorial note
        schedule: "next" (default) | "now" — whether to queue or publish immediately
    """
    platforms = req.platforms or ["fb", "ig", "threads"]
    schedule = req.schedule or "next"

    try:
        if req.type == "url":
            result = process_url(req.content, req.note, platforms)
        elif req.type == "text":
            result = process_text(req.content, req.note, platforms)
        elif req.type == "youtube":
            result = process_youtube(req.content, req.note, platforms)
        elif req.type == "image":
            result = process_image_base64(
                req.content,
                filename=f"manual_{datetime.now():%Y%m%d%H%M%S}.jpg",
                caption=req.note or "",
                platforms=platforms,
            )
        else:
            raise HTTPException(400, f"Unknown type: {req.type}")

        publish_result = None
        if schedule == "now" and req.type in ("url", "youtube") and result.get("status") == "created":
            source_url = result.get("id") or req.content
            publish_result = _trigger_immediate_publish(
                url=source_url if req.type == "url" else req.content,
                platforms=platforms,
                note=req.note,
            )

        return SubmitResponse(
            status=result.get("status", "error"),
            id=result.get("id"),
            title=result.get("title"),
            word_count=result.get("word_count"),
            message=json.dumps(publish_result) if publish_result else None,
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/submit/image", response_model=SubmitResponse)
async def submit_image(
    file: UploadFile = File(...),
    note: str = Form(""),
    platforms: str = Form("fb,ig,threads"),
    schedule: str = Form("next"),
):
    """Image upload via multipart form (for mobile/screenshot upload).

    Accepts: JPG, PNG, HEIC/HEIF
    Returns the processed result.
    """
    allowed_types = {"image/jpeg", "image/png", "image/heic", "image/heif"}
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(400, f"Unsupported content type: {file.content_type}")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large — max 20 MB")

    import base64
    b64_data = base64.b64encode(contents).decode("ascii")
    ext = "jpg"
    if file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext in ("heic", "heif", "png"):
            pass
        else:
            ext = "jpg"
    data_url = f"data:image/{ext};base64,{b64_data}"

    plat_list = [p.strip() for p in platforms.split(",")]

    try:
        result = process_image_base64(
            data_url,
            filename=file.filename or f"upload_{datetime.now():%Y%m%d%H%M%S}.{ext}",
            caption=note or "",
            platforms=plat_list,
        )
        return SubmitResponse(
            status=result.get("status", "error"),
            id=result.get("id"),
            title=result.get("title") or file.filename,
            message=f"Image saved ({result.get('size_bytes', 0)} bytes)",
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/api/status", response_model=StatusResponse)
async def status():
    """Return system health: DB presence, queue depth, Substack auto-draft flag."""
    db_path = _get_db_path()
    db_size_mb = None
    queue_stats = None

    if db_path:
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
        queue_stats = _get_queue_stats(db_path)

    substack_auto_draft = os.getenv("SUBSTACK_AUTO_DRAFT", "0") == "1"

    return StatusResponse(
        status="ok",
        db_path=str(db_path) if db_path else None,
        db_size_mb=db_size_mb,
        queue_stats=queue_stats,
        substack_auto_draft=substack_auto_draft,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/history", response_model=List[HistoryItem])
async def history(limit: int = 20):
    """Return recent submission history from the processed records."""
    submissions_file = _REPO_ROOT / "data" / "submissions" / "processed.json"
    if not submissions_file.exists():
        return []

    try:
        records = json.loads(submissions_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []

    items = []
    for r in records[-limit:]:
        rtype = r.get("type", "unknown")
        content = str(r.get("url", r.get("title", r.get("filename", ""))))
        items.append(HistoryItem(
            type=rtype,
            content_preview=content[:80],
            status=r.get("status", "processed"),
            submitted_at=r.get("processed_at", r.get("submittedAt", "")),
        ))

    # Reverse so newest first
    items.reverse()
    return items


@app.get("/api/health")
async def health():
    """Minimal health check for load balancer / launchd KeepAlive."""
    return {"ok": True}


# ===========================================================================
# Main (for direct `python manual_submit_server.py`)
# ===========================================================================

def main():
    """Run the FastAPI server via uvicorn (dev mode).

    For production, use:
        uvicorn scripts.manual_submit_server:app --host 127.0.0.1 --port 8765
    """
    import uvicorn
    port = int(os.getenv("MANUAL_SUBMIT_PORT", "8765"))
    uvicorn.run(
        "scripts.manual_submit_server:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
