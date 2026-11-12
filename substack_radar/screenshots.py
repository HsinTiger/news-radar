"""投稿截圖 → 文字（2026-11-13）。

主編有時沒辦法複製文字，只能截圖。投稿頁把圖存在雲端，素材開頭留一個標記
`[[SCREENSHOTS:<投稿編號>:<張數>]]`；寫稿前在這裡用 owner token 把圖取回，
用 macOS 內建的文字辨識（scripts/ocr.swift，免費、離線）換成文字。
辨識失敗就把標記拿掉、回報 0 字，由 compose 的「素材沒抓到」那一關通知主編。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

_MARK = re.compile(r"\[\[SCREENSHOTS:([A-Za-z0-9_-]{8,80}):(\d+)\]\]\n*")
_REPO = Path(__file__).resolve().parent.parent
API = os.getenv("SOCIAL_OPS_API_URL") or "https://news-radar-submit.smartmmmoney.workers.dev"


def _fetch(submission_id: str) -> list[dict]:
    req = urllib.request.Request(
        f"{API.rstrip('/')}/api/submissions/{submission_id}/images",
        headers={"Authorization": f"Bearer {os.environ['SOCIAL_OPS_OWNER_TOKEN']}",
                 "User-Agent": "news-radar-compose"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8")).get("images") or []


def _ocr(paths: list[str]) -> str:
    out = subprocess.run(["swift", str(_REPO / "scripts" / "ocr.swift"), *paths],
                         capture_output=True, text=True, timeout=600)
    return out.stdout.strip()


def resolve(body: str, *, fetch=_fetch, ocr=_ocr) -> tuple[str, int]:
    """把截圖標記換成辨識出來的文字。回 (新素材, 辨識出的字數)；沒有標記回 (原文, -1)。"""
    m = _MARK.search(body or "")
    if not m:
        return body or "", -1
    text = ""
    try:
        images = fetch(m.group(1))
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i, img in enumerate(images):
                path = Path(tmp) / f"shot{i}.jpg"
                path.write_bytes(base64.b64decode(img["data_b64"]))
                paths.append(str(path))
            text = ocr(paths) if paths else ""
    except Exception as exc:
        print(f"[Screenshots] ⚠️ 截圖取回或辨識失敗：{str(exc)[:120]}")
    block = f"# 截圖素材（{m.group(2)} 張，文字由系統辨識，可能有錯字）\n\n{text}\n\n" if text else ""
    print(f"[Screenshots] 截圖 {m.group(2)} 張 → 辨識出 {len(text)} 字")
    return _MARK.sub(block, body, count=1), len(text)
