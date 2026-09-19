"""讓 agy 的模型鏈自動跟上最新一代，而不是寫死版本號。

2026-09-19：agy 已經上架 Gemini 3.8 Flash，`.env` 的寫手鏈還停在 3.7 Flash。每次
新模型出來都要人發現、人改 `.env`——這種事會被忘記，而且忘記時不會有任何錯誤。

做法：每次組鏈時問 `agy models` 現在有哪些模型，挑出 Gemini 最新一代、推理強度
最高的一檔，排到鏈首。原本設定的鏈照舊接在後面當後備。

判斷規則：
  1. 版本號大的勝（3.8 > 3.7 > 3.1）。
  2. 同版本時 Pro 勝 Flash。
  3. 只挑最高一檔推理強度（High／Thinking），Medium／Low 永遠不自動選。

`agy models` 查不到（離線、agy 壞掉）時沿用上次快取；連快取都沒有就完全照設定
的鏈跑。這個模組只會讓鏈變新，不會讓寫稿因為查不到模型清單而停擺。

關掉：AGY_AUTO_LATEST=0。單次指定：AGY_MODEL="..." 仍然永遠排第一。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / ".agy_models_cache.json"
_CACHE_TTL_S = int(os.getenv("AGY_MODELS_CACHE_TTL_S", str(6 * 3600)))

_GEMINI = re.compile(r"^Gemini\s+(\d+(?:\.\d+)*)\s+(Pro|Flash)\s+\((High|Thinking)\)$", re.I)


def auto_latest_enabled() -> bool:
    return os.getenv("AGY_AUTO_LATEST", "1").strip().lower() not in ("0", "false", "no", "off")


def _parse(stdout: str) -> list[str]:
    """`agy models` 每行是 `id<TAB>顯示名稱`。鏈用的是顯示名稱。"""
    names = []
    for line in (stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip():
            names.append(parts[1].strip())
    return names


def _read_cache() -> tuple[float, list[str]]:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return float(data.get("fetched_at", 0)), [str(m) for m in data.get("models", [])]
    except Exception:
        return 0.0, []


def _write_cache(models: list[str]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"fetched_at": time.time(), "models": models}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def available_models(*, refresh: bool = False) -> list[str]:
    """目前 agy 上架的模型顯示名稱。查不到回上次快取，再不行回空清單。"""
    fetched_at, cached = _read_cache()
    if cached and not refresh and time.time() - fetched_at < _CACHE_TTL_S:
        return cached
    if not os.path.exists(AGY_BIN):
        return cached
    try:
        proc = subprocess.run(
            [AGY_BIN, "models"], capture_output=True, text=True, cwd="/tmp", timeout=60,
        )
        models = _parse(proc.stdout) if proc.returncode == 0 else []
    except Exception:
        models = []
    if models:
        _write_cache(models)
        return models
    return cached


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def newest_gemini(models: list[str]) -> str | None:
    """Gemini 最新一代的最高推理強度。看不懂的名稱一律略過，不猜。"""
    best, best_key = None, None
    for name in models:
        m = _GEMINI.match(name.strip())
        if not m:
            continue
        key = (_version_key(m.group(1)), 1 if m.group(2).lower() == "pro" else 0)
        if best_key is None or key > best_key:
            best, best_key = name.strip(), key
    return best


def with_latest(chain: list[str], models: list[str] | None = None) -> list[str]:
    """把最新的 Gemini 排到鏈首，其餘順序不動。

    已經下架的模型（清單查得到、但裡面沒有它）會被拿掉，免得每篇都白白重試。
    清單查不到時不動設定的鏈——寧可多試一個死模型，也不要因為查詢失敗把鏈清空。"""
    chain = [c.strip() for c in chain if c and c.strip()]
    if not auto_latest_enabled():
        return chain
    if models is None:
        models = available_models()
    if not models:
        return chain
    listed = set(models)
    kept = [c for c in chain if c in listed]
    latest = newest_gemini(models)
    if latest:
        kept = [latest] + [c for c in kept if c != latest]
    return kept or chain
