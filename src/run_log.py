"""寫稿紀錄（2026-10-18 信哥要的「工作回放」）。

兩個 CSV，Excel 直接打得開，在 logs/writing/：
  llm_calls.csv  每一次叫模型：時間、哪篇稿（run）、哪一步（stage）、誰（provider/model）、
                 成功與否、花幾秒、輸入／輸出 token（拿得到才有；agy 不回報 token）。
  articles.csv   每一篇稿的總結：專欄、標題、總耗時、寫手、稽核、審稿幾次、退稿幾次、是否通過。
寫紀錄失敗絕不影響寫稿。
"""
from __future__ import annotations

import contextvars
import csv
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(os.getenv("WRITING_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs" / "writing")))
CALL_FIELDS = ["time", "run", "stage", "provider", "model", "ok", "seconds", "input_tokens", "output_tokens", "note"]
ARTICLE_FIELDS = ["date", "time", "run", "column", "title", "minutes", "writer", "auditor",
                  "review_rounds", "rewrites", "passed", "draft_id", "remaining_issues"]

_stage: contextvars.ContextVar[str] = contextvars.ContextVar("writing_stage", default="")


def current_run() -> str:
    return os.getenv("NEWS_RADAR_RUN", "")


@contextmanager
def stage(name: str):
    """標記接下來的模型呼叫屬於哪一步（審稿、退稿重寫、FB 版…）。"""
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


# 紀錄會上傳 GitHub：錯誤訊息裡萬一夾帶金鑰／cookie，寫檔前先遮掉。
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|nvapi-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[A-Za-z0-9]{20,}"
                     r"|ghp_[A-Za-z0-9]{20,}|[A-Fa-f0-9]{32}\.[A-Za-z0-9]{8,}|Bearer\s+\S+|substack\.sid=[^;\s]+)")


def redact(text: str) -> str:
    return _SECRET.sub("[已遮罩]", text or "")


def _append(filename: str, fields: list[str], row: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / filename
        new = not path.exists()
        with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass


def log_call(*, provider: str, model: str | None, ok: bool, seconds: float,
             input_tokens: int | None = None, output_tokens: int | None = None,
             note: str = "", stage_name: str | None = None) -> None:
    _append("llm_calls.csv", CALL_FIELDS, {
        "time": datetime.now().isoformat(timespec="seconds"),
        "run": current_run(),
        "stage": stage_name or _stage.get() or "",
        "provider": provider, "model": model or "",
        "ok": "Y" if ok else "N", "seconds": round(seconds, 1),
        "input_tokens": input_tokens if input_tokens else "",
        "output_tokens": output_tokens if output_tokens else "",
        "note": redact((note or "").replace("\n", " "))[:160],
    })


def log_article(**row) -> None:
    now = datetime.now()
    _append("articles.csv", ARTICLE_FIELDS,
            {"date": now.date().isoformat(), "time": now.strftime("%H:%M"), "run": current_run(), **row})


class Timer:
    def __init__(self):
        self.t0 = time.monotonic()

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.t0
