#!/usr/bin/env python3
"""
News Radar · 內文生圖引擎 probe（2026-08-01）
=============================================

**在 Hsin 的 Mac 上跑一次**，把 ``codex`` / ``agy`` 兩支 CLI 當下真實可用的
生圖行為釘死，再據此填 ``.env``。

為什麼需要這支：這兩支 CLI 的生圖旗標會隨版本變動，而 CI 容器與雲端 runner
上都沒有它們，寫 code 的當下無法實測。本 repo 的規矩是 verify 不是 vouch
（``docs/System_Architecture.md`` §7.3：log 說 ✅ 不等於狀態真的對），所以
``src/image_engines.py`` 的預設指令樣板只是有依據的起點，不是保證。這支
probe 就是把「起點」變成「實測結果」的那一步。

用法
----
    # 兩個都測（不會動到任何草稿，只寫 /tmp）
    python3 scripts/probe_image_engines.py

    # 只測其中一個
    python3 scripts/probe_image_engines.py --engine codex

    # 自帶指令樣板試打（樣板可用 {bin} {prompt} {out} {w} {h} {outdir}）
    python3 scripts/probe_image_engines.py --engine agy --cmd '{bin} --print "…{out}…"'

輸出
----
每個引擎一段報告：CLI 是否存在、實際耗時、有沒有真的產出可開啟的圖、
出圖尺寸是否符合要求。最後印出建議貼進 ``.env`` 的設定行。

這支**只做讀取與 /tmp 寫入**，不碰 DB、不碰 state branch、不碰草稿目錄。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src import image_engines  # noqa: E402

# 刻意選一個「無數字、無文字」的構圖：生圖模型畫不出可信的數據圖表，
# probe 要測的是引擎會不會出圖，不是它會不會畫圖表。
PROBE_PROMPT = (
    "A black-and-white documentary photograph, 1960s LIFE magazine style: "
    "the back of a lone analyst standing in front of a wall of paper charts "
    "in a quiet office, side profile, no visible facial features, "
    "grainy film, high contrast, no text, no watermark"
)
PROBE_SIZE = (1024, 576)


async def probe_one(engine: str, cmd_override: str | None) -> dict:
    out_path = Path(f"/tmp/news_radar_probe_{engine}.png")
    if out_path.exists():
        out_path.unlink()

    os.environ["SUBSTACK_IMAGE_ENGINE"] = engine
    if cmd_override:
        os.environ[f"SUBSTACK_IMAGE_CMD_{engine.upper()}"] = cmd_override

    status = image_engines.engine_status()
    result = {
        "engine": engine,
        "bin": status["bin"],
        "bin_exists": status["bin_exists"],
        "cmd": image_engines._cmd_template(engine),
        "ok": False,
        "elapsed_s": None,
        "size": None,
        "out": str(out_path),
    }

    if not status["bin_exists"]:
        result["note"] = f"CLI 不在 {status['bin']}（用 {engine.upper()}_BIN 指定路徑）"
        return result

    t0 = time.time()
    got = await image_engines.generate(
        prompt=PROBE_PROMPT, out_path=out_path, size=PROBE_SIZE
    )
    result["elapsed_s"] = round(time.time() - t0, 1)

    if got is None:
        result["note"] = "CLI 有跑，但沒產出可開啟的圖 → 需要調整指令樣板（--cmd）"
        return result

    from PIL import Image

    with Image.open(got) as im:
        result["size"] = list(im.size)
    result["ok"] = True
    result["note"] = "成功"
    return result


def render(results: list) -> None:
    print("\n" + "=" * 66)
    print("內文生圖引擎 probe 結果")
    print("=" * 66)
    for r in results:
        flag = "✅" if r["ok"] else ("⚠️ " if r["bin_exists"] else "—")
        print(f"\n{flag} {r['engine']}")
        print(f"   CLI        : {r['bin']}  (exists={r['bin_exists']})")
        print(f"   指令樣板   : {r['cmd']}")
        if r["elapsed_s"] is not None:
            print(f"   耗時       : {r['elapsed_s']}s")
        if r["size"]:
            want = list(PROBE_SIZE)
            tag = "符合" if r["size"] == want else f"已裁切修正自 {r['size']}"
            print(f"   出圖尺寸   : {want} ({tag})")
            print(f"   檔案       : {r['out']}  ← 打開看一眼再決定要不要用")
        print(f"   結論       : {r['note']}")

    winners = [r for r in results if r["ok"]]
    print("\n" + "-" * 66)
    if not winners:
        print("沒有任何引擎通過。內文 🖼 標記會原樣保留給手動生圖（草稿不受影響）。")
        print("下一步：用 --cmd 試不同指令樣板，或先確認 CLI 路徑。")
        return

    # codex 優先：agy 與寫稿 composer 共用同一份 Google AI Pro 額度
    # （src/llm_brain.py:341-347），插圖不該把寫稿的額度吃掉。
    pick = next((r for r in winners if r["engine"] == "codex"), winners[0])
    print("建議貼進 .env：\n")
    print(f"SUBSTACK_IMAGE_ENGINE={pick['engine']}")
    print("SUBSTACK_IMAGE_MAX_PER_RUN=3")
    cmd_key = f"SUBSTACK_IMAGE_CMD_{pick['engine'].upper()}"
    if os.getenv(cmd_key):
        print(f"{cmd_key}={os.environ[cmd_key]}")
    if pick["engine"] == "agy":
        print("\n注意：agy 與寫稿 composer 共用同一份 Google AI Pro 額度。")
        print("      張數上限請保守，避免把寫稿額度吃光。")


def main() -> int:
    ap = argparse.ArgumentParser(description="probe 本機生圖 CLI（codex / agy）")
    ap.add_argument("--engine", choices=["codex", "agy"], help="只測這一個")
    ap.add_argument("--cmd", help="覆寫指令樣板試打")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    engines = [args.engine] if args.engine else ["codex", "agy"]
    results = [asyncio.run(probe_one(e, args.cmd)) for e in engines]
    render(results)
    return 0 if any(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
