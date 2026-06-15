#!/usr/bin/env python3
"""HTML/CSS motion-graphics reel engine.

文章重點 → 多個視覺模板場景（hook/number/quote/list/vs/trend/outro）→ 各場景用
無頭 Chrome 逐幀渲染 HTML 動畫 → 台灣配音逐段對齊 → ffmpeg 合成直幅 reel。

對外：
  build_scenes(carousel: dict, title, issue) -> list[scene]
  await make_html_reel(scenes, output_path, voice) -> Path | None
"""
from __future__ import annotations
import json, os, re, random, subprocess
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parent.parent
HTML_DIR = REPO / "reels_html"
REELS_DIR = REPO / "data" / "reels"
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
NODE = os.environ.get("NODE", "node")

from scripts.reels_maker import _gen_voice, _get_audio_duration  # reuse TTS


def _short(s: str, n: int) -> str:
    s = re.sub(r'\[.*?\]|\(.*?\)|[*#=]', '', (s or '')).strip()
    return s if len(s) <= n else s[:n]


def parse_stat(s: Optional[str]):
    """'$2.1兆' -> {prefix:'$', value:2.1, decimals:1, suffix:'兆'}；非數字回 None。"""
    if not s:
        return None
    m = re.match(r'\s*([^\d\-]*)(-?[\d,\.]+)(.*)', s)
    if not m:
        return None
    num = m.group(2).replace(',', '')
    try:
        val = float(num)
    except ValueError:
        return None
    return {"prefix": m.group(1).strip(), "value": val,
            "decimals": 1 if '.' in num else 0, "suffix": m.group(3).strip()}


def build_scenes(c: dict, title: str, issue: str) -> List[dict]:
    """依 carousel 欄位自動選模板。c 可含 insight_statement/insight_support/
    stat_number/stat_caption/takeaways。"""
    c = c or {}
    scenes: List[dict] = []
    scenes.append({"template": "hook", "kicker": "NEWS RADAR",
                   "headline": _short(title, 18), "voice": title})
    st = parse_stat(c.get("stat_number"))
    if st:
        scenes.append({"template": "number",
                       "kicker": _short(c.get("stat_caption") or "關鍵數字", 12),
                       **st, "sub": _short(c.get("stat_caption") or "", 16),
                       "voice": c.get("stat_caption") or c.get("stat_number")})
    ins = c.get("insight_statement")
    if ins:
        scenes.append({"template": "quote", "quote": _short(ins, 44),
                       "attribution": "", "voice": ins})
    tk = [t for t in (c.get("takeaways") or []) if t][:3]
    if tk:
        scenes.append({"template": "list", "heading": "帶走重點",
                       "items": [_short(t, 22) for t in tk], "voice": "、".join(tk)})
    scenes.append({"template": "outro",
                   "punch": _short(tk[0] if tk else (ins or title), 20),
                   "handle": "@主力爸爸我錯了",
                   "voice": "追蹤主力爸爸我錯了，每天掌握科技與市場。"})
    n = len(scenes)
    for i, s in enumerate(scenes):
        s["issue"] = issue
        s["page"] = f"{i+1:02d} / {n:02d}"
        # 限制每段旁白 ≤30 字（≈6s）→ 控制總幀數，CI 逐幀截圖才跑得完
        s["voice"] = _short(s.get("voice", ""), 30)
    return scenes


def _ff(*args):
    subprocess.run([FFMPEG, "-y", *args], check=True, capture_output=True)


async def make_html_reel(scenes: List[dict], output_path: Optional[Path] = None,
                         voice: str = "zh-TW-HsiaoChenNeural", fps: int = 24,
                         music: Optional[Path] = None) -> Optional[Path]:
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    work = REELS_DIR / f"html_{random.randint(1000,9999)}"
    seqdir = work / "seq"
    seqdir.mkdir(parents=True, exist_ok=True)
    gidx = 0
    audio_segs: List[Path] = []

    for i, sc in enumerate(scenes):
        # 1) 台灣配音
        ap = work / f"a{i}.mp3"
        await _gen_voice(sc.get("voice", ""), ap, voice)
        dur = await _get_audio_duration(ap) if ap.exists() else 0.0
        secs = min(6.0, max(2.6, (dur or 2.0) + 0.6))  # 上限 6s：控制總幀數 ≈ CI 速度

        # 2) 渲染該場景 HTML 動畫 → 幀
        dj = work / f"d{i}.json"
        dj.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
        scdir = work / f"s{i}"
        subprocess.run([NODE, str(HTML_DIR / "render.js"), str(HTML_DIR / "scene.html"),
                        str(scdir), f"{secs:.2f}", str(fps), str(dj)], check=True)
        for f in sorted(scdir.glob("f_*.jpg")):
            (seqdir / f"g_{gidx:06d}.jpg").write_bytes(f.read_bytes())
            gidx += 1

        # 3) 該場景音軌補靜音到 secs（保持逐段 A/V 對齊）
        seg = work / f"seg{i}.mp3"
        if ap.exists() and dur > 0:
            _ff("-i", str(ap), "-af", f"apad=whole_dur={secs:.2f}", "-t", f"{secs:.2f}", str(seg))
        else:
            _ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{secs:.2f}", str(seg))
        audio_segs.append(seg)

    if gidx == 0:
        return None

    # 4) 串接音軌
    listf = work / "audio.txt"
    listf.write_text("".join(f"file '{s.resolve()}'\n" for s in audio_segs), encoding="utf-8")
    full_audio = work / "full.mp3"
    _ff("-f", "concat", "-safe", "0", "-i", str(listf), "-c", "copy", str(full_audio))

    # 5) 幀 + 音軌 → mp4（可選背景音樂，側鏈 ducking 讓配音永遠在前）
    out = Path(str(output_path)) if output_path else REELS_DIR / f"{work.name}.mp4"
    base = ["-framerate", str(fps), "-i", str(seqdir / "g_%06d.jpg"), "-i", str(full_audio)]
    if music and Path(music).exists():
        # [voice] 全音量；[music] 基底 0.18，配音出現時側鏈壓到更低 → 不蓋配音
        filt = ("[2:a]aloop=loop=-1:size=2000000000,volume=0.18[m];"
                "[1:a]asplit=2[v1][vk];"
                "[m][vk]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=260[md];"
                "[v1][md]amix=inputs=2:duration=first:normalize=0[a]")
        _ff(*base, "-i", str(music), "-filter_complex", filt,
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-r", str(fps), str(out))
    else:
        _ff(*base, "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-r", str(fps), str(out))
    return out if out.exists() else None
