"""
News Radar · Inline image engines (2026-08-01)
===============================================

內文插圖的生圖後端。**只服務內文 🖼 標記**；封面不走這裡——封面已經有自己的
SOP（``substack_radar/compose.py::render_substack_cover`` → route 3 角色合成
``character_cover`` → route 2 字體海報 ``promise_cover``），那條路是確定性的
Pillow 合成，不需要也不應該被生圖模型取代。

為什麼是「本機 CLI」而不是雲端 API
----------------------------------
Hsin 的 Mac 上已經有 ``codex``（OpenAI Codex CLI，內建 ``$imagegen`` skill，
底層 gpt-image-2）與 ``agy``（Antigravity CLI，內建 image tool）。走這兩支的
好處：

  1. **不需要新增任何 API key**——兩者都用本機既有登入 / 訂閱額度。
  2. 不是新帳單，是已經在付的訂閱。
  3. 與現有架構天然吻合：compose 跑在 Mac launchd；雲端 runner 沒裝這些 CLI，
     ``resolve_engine()`` 直接回 None → 內文標記原封保留，草稿照樣產出。
     這與 ``llm_brain._agy_available()`` 的既有 fallback pattern 同形。

額度分流（2026-08-01 決策）
--------------------------
``agy`` 的額度與 **寫稿的 composer 共用同一份 Google AI Pro 配額**
（見 ``src/llm_brain.py:341-347``；agy 每次請求光內部 system prompt 就吃
23-25k token）。寫稿是主餐，插圖是配菜——配菜不該把主餐的額度吃光。
因此 **預設引擎是 codex**（走 ChatGPT 訂閱，另一個池子），``agy`` 保留為
可切換的備選。``SUBSTACK_IMAGE_MAX_PER_RUN`` 再加一道每篇張數硬上限。

誠實邊界（重要）
----------------
生圖模型**畫不出可信的數據圖表**：它會捏造座標軸數字、把中文字畫成亂碼。
本 repo 的 composer prompt 自己寫著「絕不可自己掰一個數字或日期」
（``substack_radar/composer.py:687-693``）。所以這裡只負責**場景圖／概念示意圖**
（🖼 標記的 Path C prompt 本來就是 "B&W documentary / side profile / 1960s LIFE"
這類無數字的構圖）。真正帶數字的數據圖表應該走確定性繪製，不走這裡。

環境變數
--------
``SUBSTACK_IMAGE_ENGINE``      ``off``(預設) | ``codex`` | ``agy``
``SUBSTACK_IMAGE_MAX_PER_RUN`` 每次 compose 最多生幾張（預設 3）
``SUBSTACK_IMAGE_TIMEOUT_S``   單張逾時秒數（預設 180）
``CODEX_BIN`` / ``AGY_BIN``    CLI 路徑（預設 ``~/.local/bin/<name>``）
``CODEX_HOME``                 codex 出圖目錄的 parent（預設 ``~/.codex``）
``SUBSTACK_IMAGE_CMD_CODEX``   覆寫 codex 指令樣板（見下方 probe 說明）
``SUBSTACK_IMAGE_CMD_AGY``     覆寫 agy 指令樣板

指令樣板為什麼可覆寫
--------------------
這兩支 CLI 的生圖旗標會隨版本變動，而 CI 容器裡沒有它們、無法實測。
``scripts/probe_image_engines.py`` 讓 Hsin 在 Mac 上跑一次，把**當下真實可用**
的指令釘死並寫進 ``.env``。在 probe 確認之前，預設樣板只是有依據的起點，
不是保證——這是刻意的，本 repo 的規矩是 verify，不是 vouch
（見 ``docs/System_Architecture.md`` §7.3）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
from pathlib import Path
from typing import List, Optional, Tuple

OFF = "off"
CODEX = "codex"
AGY = "agy"

# 預設指令樣板。``{prompt}`` / ``{out}`` / ``{w}`` / ``{h}`` / ``{outdir}`` 會被替換。
# 兩者都採「把明確像素與輸出路徑寫進 prompt」的策略，因為兩支 CLI 對長寬比
# 簡寫（"16:9"、"ar 4:5"）的遵從都不穩定——agy 預設會退回 1024×1024 方圖。
_DEFAULT_CMD = {
    CODEX: (
        '{bin} exec --skip-git-repo-check '
        '"Use the imagegen skill to generate exactly one image. '
        'Size must be exactly {w}x{h} pixels. '
        'Save it to {out} and do not write any other file. '
        'Prompt: {prompt}"'
    ),
    AGY: (
        '{bin} --print '
        '"Generate exactly one image and save it to {out}. '
        'The canvas must be exactly {w}x{h} pixels — do not use the default '
        'square canvas, and do not rely on aspect-ratio shorthand. '
        'Prompt: {prompt}"'
    ),
}

_DEFAULT_BIN = {
    CODEX: "~/.local/bin/codex",
    AGY: "~/.local/bin/agy",
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def engine_name() -> str:
    """目前設定的引擎名（小寫）。未設 / 不認得 → ``off``。"""
    raw = (os.getenv("SUBSTACK_IMAGE_ENGINE") or OFF).strip().lower()
    return raw if raw in (CODEX, AGY) else OFF


def max_images_per_run() -> int:
    """每次 compose 的生圖張數硬上限（保護訂閱額度）。"""
    try:
        n = int(os.getenv("SUBSTACK_IMAGE_MAX_PER_RUN", "3"))
    except ValueError:
        return 3
    return max(0, n)


def _timeout_s() -> float:
    try:
        return float(os.getenv("SUBSTACK_IMAGE_TIMEOUT_S", "180"))
    except ValueError:
        return 180.0


def _bin_path(engine: str) -> str:
    env_key = f"{engine.upper()}_BIN"
    return os.path.expanduser(os.getenv(env_key, _DEFAULT_BIN[engine]))


def _cmd_template(engine: str) -> str:
    override = os.getenv(f"SUBSTACK_IMAGE_CMD_{engine.upper()}")
    return override if (override and override.strip()) else _DEFAULT_CMD[engine]


def resolve_engine() -> Optional[str]:
    """回傳可用的引擎名，或 None。

    None 的三種成因都不是錯誤，都是「這台機器不生圖」的正常狀態：
    未設定 / 設成 off / CLI 不在這台機器上（例如雲端 runner）。
    """
    name = engine_name()
    if name == OFF:
        return None
    if not os.path.exists(_bin_path(name)):
        return None
    return name


def engine_status() -> dict:
    """給 probe 與 log 用的可讀狀態，不觸發任何生圖。"""
    name = engine_name()
    return {
        "configured": name,
        "bin": _bin_path(name) if name != OFF else None,
        "bin_exists": os.path.exists(_bin_path(name)) if name != OFF else False,
        "resolved": resolve_engine(),
        "max_per_run": max_images_per_run(),
        "timeout_s": _timeout_s(),
    }


# --------------------------------------------------------------------------
# Post-condition — 這是本模組的重點
# --------------------------------------------------------------------------

def verify_image(path: Path, size: Tuple[int, int]) -> Optional[Path]:
    """檢查 ``path`` 真的是一張可開啟的圖，必要時修正尺寸。回 Path 或 None。

    這是 scoped-vdd 要求的 post-condition：subprocess 的 exit code 0
    **不算**成功。CLI agent 很常「回報做完了」但沒真的寫檔、或寫了一個
    0 byte 檔、或存成非圖片格式。只有這個函式回非 None 才叫成功。
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
        from PIL import Image

        with Image.open(path) as im:
            im.verify()  # 格式健全性；verify 後該 handle 不能再用
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.size != size:
                # 尺寸不符不算失敗——兩支 CLI 都有「不遵從指定尺寸」的已知行為，
                # 這裡直接中心裁切+縮放到規格，比丟掉一張好圖划算。
                im = _center_crop_to_aspect(im, size).resize(size, Image.LANCZOS)
            im.save(path, "PNG", optimize=True)
        return path if path.exists() and path.stat().st_size > 0 else None
    except Exception:
        return None


def _center_crop_to_aspect(img, target_size: Tuple[int, int]):
    tw, th = target_size
    target_ratio = tw / th
    w, h = img.size
    src_ratio = w / h
    if abs(src_ratio - target_ratio) < 1e-3:
        return img
    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = int(w / target_ratio)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _snapshot(dirs: List[Path]) -> set:
    seen = set()
    for d in dirs:
        if d.is_dir():
            for p in d.rglob("*"):
                if p.is_file():
                    seen.add(p)
    return seen


def _harvest_dirs(engine: str, out_path: Path) -> List[Path]:
    """CLI 可能不聽話、把圖寫到自己的預設目錄。這裡列出要回收的候選目錄。"""
    dirs = [out_path.parent]
    if engine == CODEX:
        codex_home = Path(os.path.expanduser(os.getenv("CODEX_HOME", "~/.codex")))
        dirs.append(codex_home / "generated_images")
    return dirs


_IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _claim_new_image(
    engine: str, out_path: Path, before: set, started_at: float
) -> Optional[Path]:
    """out_path 沒出現時的回收路徑：找 CLI 在這次呼叫期間新產出的圖，搬過來。"""
    candidates = []
    for d in _harvest_dirs(engine, out_path):
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if (
                p.is_file()
                and p not in before
                and p.suffix.lower() in _IMG_SUFFIXES
                and p.stat().st_mtime >= started_at - 1
            ):
                candidates.append(p)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        newest.replace(out_path)
        return out_path
    except Exception:
        try:
            out_path.write_bytes(newest.read_bytes())
            return out_path
        except Exception:
            return None


async def generate(
    *,
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (1024, 576),
) -> Optional[Path]:
    """用本機 CLI 生一張圖。成功回 Path，任何失敗一律回 None（絕不 raise）。

    絕不 raise 是刻意的：插圖是加分項，不能讓一篇本來寫成功的草稿因為
    生圖失敗而整篇失敗（見 ``compose.py`` 2026-06-03 evening fix 的同款理由）。
    """
    engine = resolve_engine()
    if engine is None:
        return None
    prompt = (prompt or "").strip()
    if not prompt:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()  # 舊檔會讓 post-condition 誤判成功

    w, h = size
    cmd = _cmd_template(engine).format(
        bin=shlex.quote(_bin_path(engine)),
        prompt=prompt.replace('"', "'").replace("\n", " "),
        out=shlex.quote(str(out_path)),
        outdir=shlex.quote(str(out_path.parent)),
        w=w,
        h=h,
    )

    before = _snapshot(_harvest_dirs(engine, out_path))
    started_at = time.time()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=_timeout_s())
        except asyncio.TimeoutError:
            proc.kill()
            print(f"[image_engines] ⚠️ {engine} 逾時 {_timeout_s():.0f}s，放棄這張。")
            return None
    except Exception as exc:
        print(f"[image_engines] ⚠️ {engine} 啟動失敗: {type(exc).__name__}: {exc}")
        return None

    # 注意：這裡刻意 **不** 用 returncode 當成功判準。CLI agent 回 0 但沒寫檔
    # 是常態；唯一算數的是下面 verify_image 的結果。returncode 只拿來寫 log。
    if not out_path.exists():
        _claim_new_image(engine, out_path, before, started_at)

    verified = verify_image(out_path, size)
    if verified is None:
        tail = (err or b"").decode("utf-8", "replace").strip()[-200:]
        print(
            f"[image_engines] ⚠️ {engine} 未產出可用圖 "
            f"(rc={proc.returncode}){(' | ' + tail) if tail else ''}"
        )
        return None
    return verified


def write_manifest(*, output_dir: Path, entries: List[dict]) -> Optional[Path]:
    """把「哪張圖是照哪個 prompt 生的」落成機讀檔。

    Hsin 的原始需求：「Prompt 你當然也是可以稍微留一下，那我至少就可以知道
    說你這個圖是依照什麼 prompt 生出來」。markdown 裡留 HTML comment 給人看，
    這份 JSON 給程式看（之後要回溯哪個 prompt 生得好時用得上）。
    """
    if not entries:
        return None
    path = output_dir / "inline_images.json"
    payload = {
        "engine": resolve_engine(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "images": entries,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
