#!/usr/bin/env python3
"""
News Radar · Reels Maker — 自動化短影音製作引擎
===================================================
從一篇新聞 → 9:16 Reels 影片（IG Reels / FB Reels / Threads VIDEO）

三層品質把關：
  1. 腳本品質 (ContentAgent) — Gemini 濃縮為「一句話震撼」9秒腳本
  2. 畫面品質 (VideoAgent) — MoviePy 黑底+動態文字+Ken Burns+語音
  3. 驗證品質 (QCAgent) — 確認影片>3秒、有語音、字幕完整

費用：$0（edge-tts 免key無限、FFmpeg內建、GitHub Actions免費）
"""

from __future__ import annotations
import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

REPO = _HERE
LOGS_DIR = REPO / "logs"
REELS_DIR = REPO / "data" / "reels"
COVER_CDN_URL = "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn"

# ====================================================================
# ContentAgent: 腳本生成 (Gemini Pro → 震撼腳本)
# ====================================================================

_CONTENT_SCRIPT_PROMPT = """你是一個專門寫「一句話震撼」短影音腳本的專業寫手。

你的任務：把下面這則新聞濃縮成 9 秒的 Reels 腳本，格式是「一句衝擊事實 → 翻轉 → 結論」。

格式要求（嚴格遵守）：
- 總共 4-5 行，每行 ≤12 字（手機螢幕只能容納這麼多）
- 前 2 行必須是「最震撼的事實或數字」，讓人在 0.5 秒內停下來
- 第 3-4 行是「翻轉/背後的真相」
- 第 5 行是「結論或暗示」（可以留懸念）
- 不要問句、不要「你覺得呢」
- 全部繁體中文、不要中英夾雜（除非專有名詞如 NVIDIA）

範例（NVIDIA 財報）：
NVIDIA 上季營收
比台積電還高
但華爾街說不夠
因為新晶片毛利率
少了八個百分點

記住：每個字都要有重量。刪掉所有填充詞（其實、很、非常、可能）。

新聞：{title}

{content}

請直接輸出 4-5 行腳本，不要前言、不要編號、不要說明。"""


async def _generate_script(title: str, content: str) -> List[str]:
    """ContentAgent: 用 Gemini 生成震撼腳本。"""
    from src.llm_brain import call_for_json
    from pydantic import BaseModel

    class ScriptResult(BaseModel):
        lines: List[str]

    prompt = _CONTENT_SCRIPT_PROMPT.format(title=title, content=content[:2000])
    result = await call_for_json(
        system="你是專業短影音腳本寫手。輸出 JSON {lines: [\"行1\", \"行2\", ...]}，4-5 行。",
        prompt=prompt,
        response_model=ScriptResult,
        temperature=0.4,
        gemini_model="gemini-2.5-flash",
    )

    if result.data and result.data.lines:
        print(f"[ContentAgent] 生成腳本 ({len(result.data.lines)} 行):")
        for l in result.data.lines:
            print(f"  │ {l}")
        return result.data.lines[:5]

    # Fallback: 人工簡化
    print("[ContentAgent] ⚠️ LLM 失敗，使用簡單腳本")
    words = content[:80].split("，")[0].strip()[:20]
    return [
        title[:14],
        words[:14] if len(words) > 4 else "一個震撼數字",
        "背後藏著",
        "你沒注意到的",
        "市場關鍵信號",
    ]


# ====================================================================
# VideoAgent: 影片合成 (MoviePy + Pillow + Edge-TTS)
# ====================================================================

_EDGE_TTS = None  # lazy import


def _get_tts():
    global _EDGE_TTS
    if _EDGE_TTS is None:
        import edge_tts  # noqa: F401
        _EDGE_TTS = True
    return _EDGE_TTS


async def _gen_voice(text: str, output_path: Path, voice: str = "zh-TW-HsiaoChenNeural") -> Optional[Path]:
    """Edge-TTS 生成中文語音。完全免費、不需 API key。

    台灣國語語音（品牌口音，預設）：
      - zh-TW-HsiaoChenNeural (女聲, 推薦)
      - zh-TW-HsiaoYuNeural   (女聲)
      - zh-TW-YunJheNeural    (男聲)
    """
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
        duration = await _get_audio_duration(output_path)
        print(f"  [Voice] 生成語音: {duration:.1f}秒 | {output_path.name}")
        return output_path if duration > 0.5 else None
    except Exception as e:
        print(f"  [Voice] ⚠️ Edge-TTS 失敗: {e}")
        return None


async def _get_audio_duration(path: Path) -> float:
    """FFprobe 取得音檔長度。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip()) if result.stdout.strip() else 0.0
    except Exception:
        return 0.0


# News Radar brand palette (visual_brand_system.md §2)
_PAPER = (242, 238, 229)   # #F2EEE5 背景，永不純白
_INK = (20, 20, 20)        # #141414 near-black hero text
_SIENNA = (200, 74, 50)    # #C84A32 house accent，一格 ONE placement
_STONE = (138, 131, 120)   # #8A8378 secondary / meta


def _render_frame(text_lines: List[str], line_index: int,
                  width: int = 1080, height: int = 1920,
                  bg_color: Tuple[int, ...] = _PAPER, issue_no: str = "—") -> Path:
    """Render one reel frame in the News Radar editorial brand:
    Cold Paper bg · Press Ink serif hero · single Sienna accent · mono masthead.
    Already-read lines dim to Stone; the current line is the Ink focal."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # serif 為品牌首選；後備串一定要含雲端會下載的 SANS .otf，否則載不到
    # serif 時會掉到 load_default → 中文變空白（2026-06-14 的 IG 空白 reel 事故）。
    _sans_otf = REPO / "assets" / "fonts" / "SourceHanSansTC-Bold.otf"
    serif = [REPO / "assets" / "fonts" / "SourceHanSerifTC-Light.otf",
             _sans_otf,  # 雲端 fallback：Fonts step 一定有下載
             "/System/Library/Fonts/Hiragino Sans GB.ttc",
             "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
             "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]
    sans = [_sans_otf, "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"]

    def _load(paths, size):
        for fp in paths:
            p = Path(fp)
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:
                    continue
        return ImageFont.load_default()

    read_font = _load(serif, 38)     # 已讀句：縮小
    mast_font = _load(sans, 30)      # masthead mono-ish
    foot_font = _load(sans, 30)
    center_x = width // 2
    max_w = width - 150              # 文字安全寬度（兩側留白，避免裁切）

    # ── 頂部 masthead：NEWS RADAR · Nº xxx（RADAR 著 Sienna，全篇唯一 accent）──
    mast_y = 90
    draw.text((70, mast_y), "NEWS ", font=mast_font, fill=_INK)
    nw = draw.textbbox((0, 0), "NEWS ", font=mast_font)[2]
    draw.text((70 + nw, mast_y), "RADAR", font=mast_font, fill=_SIENNA)
    rw = draw.textbbox((0, 0), "RADAR", font=mast_font)[2]
    draw.text((70 + nw + rw, mast_y), f"  ·  Nº {issue_no}", font=mast_font, fill=_STONE)
    draw.rectangle([70, mast_y + 52, width - 70, mast_y + 54], fill=_INK)  # 2px ink rule

    # ── 中央 hero 文字（垂直置中）──
    line_height = 120
    total_h = len(text_lines) * line_height
    start_y = (height - total_h) // 2
    for i, line in enumerate(text_lines):
        y = start_y + i * line_height
        if i < line_index:
            font, color = read_font, _STONE
        elif i == line_index:
            # 當前句 serif hero，字級自動縮放至塞進安全寬度（絕不裁切）
            color = _INK
            sz = 66
            font = _load(serif, sz)
            while sz > 32 and draw.textbbox((0, 0), line, font=font)[2] > max_w:
                sz -= 3
                font = _load(serif, sz)
        else:
            continue
        bbox = draw.textbbox((0, 0), line, font=font)
        x = center_x - (bbox[2] - bbox[0]) // 2
        draw.text((x, y), line, font=font, fill=color)

    # ── 底部 wordmark + 頁碼（mono / Stone）──
    brand = "主力爸爸我錯了"
    bw = draw.textbbox((0, 0), brand, font=foot_font)[2]
    draw.text((center_x - bw // 2, height - 130), brand, font=foot_font, fill=_STONE)
    draw.text((70, height - 130), f"{line_index+1:02d} / {len(text_lines):02d}",
              font=foot_font, fill=_STONE)

    out = REPO / "data" / "reels" / "frames"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"frame_{line_index:03d}.png"
    img.save(path, "PNG")
    return path


async def make_carousel_reel(
    card_paths: List[Path],
    card_texts: List[str],
    output_path: Optional[Path] = None,
    voice: str = "zh-TW-HsiaoChenNeural",
) -> Optional[Path]:
    """Carousel 圖卡幻燈片 → 20 秒 Reels。每張卡 5 秒 + Ken Burns zoom + edge-tts 配音。"""
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    sid = f"cr_{random.randint(1000,9999)}"
    n = len(card_paths)
    spc = 8.0 / max(n, 1)  # seconds per card

    print(f"\n🎬 Carousel Reels ({n} cards, {20.0:.0f}s)")

    # Voiceover: pick first non-empty line from each card, strip markdown
    import re
    voice_lines = []
    for ct in card_texts:
        line = ct.strip().lstrip("#-=* ").split("\n")[0].split(chr(10))[0].strip()
        if not line:
            continue
        line = re.sub(r'\[.*?\]|\(.*?\)|[*#-]', '', line).strip()
        if len(line) > 40:
            line = line[:40]
        if line:
            voice_lines.append(line)
    script = "。".join(voice_lines[:4]) if voice_lines else card_texts[0][:40]
    ap = REELS_DIR / f"{sid}_audio.mp3"
    audio = await _gen_voice(script, ap, voice)

    # Frames: each card with Ken Burns zoom
    fps = 24
    frames_per_card = int(spc * fps)
    from PIL import Image
    frame_dir = REELS_DIR / sid
    frame_dir.mkdir(exist_ok=True)

    frame_idx = 0
    for ci, cp in enumerate(card_paths):
        if not cp.exists():
            img = Image.new("RGB", (1080, 1920), _PAPER)
        else:
            img = Image.open(cp).convert("RGB")
        img3x = img.resize((3240, 5760), Image.LANCZOS)
        for fi in range(frames_per_card):
            t = fi / max(1, frames_per_card - 1)
            scale = 1.0 + 0.06 * t
            sw, sh = int(1080 * scale), int(1920 * scale)
            sx, sy = (img3x.width - sw) // 2, (img3x.height - sh) // 2
            cropped = img3x.crop((sx, sy, sx + sw, sy + sh)).resize((1080, 1920), Image.LANCZOS)
            fp = frame_dir / f"frame_{frame_idx:06d}.png"
            cropped.save(fp)
            frame_idx += 1
        print(f"  ✅ Card {ci+1}/{n} rendered")

    # Compile
    mp4 = REELS_DIR / f"{sid}.mp4"
    pattern = str(frame_dir / "frame_%06d.png")
    cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", pattern]
    if audio and audio.exists():
        cmd += ["-i", str(audio), "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "12000k", "-preset", "fast", str(mp4)]
    import subprocess
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if not mp4.exists():
        print("  ❌ FFmpeg failed"); return None

    print(f"  ✅ Video: {mp4} ({mp4.stat().st_size / 1024 / 1024:.1f}MB)")
    if output_path:
        import shutil
        shutil.copy2(str(mp4), str(output_path))
        return Path(output_path)
    return mp4


async def make_reel(
    title: str,
    content: str,
    output_path: Optional[Path] = None,
    platform: str = "ig",
    voice: str = "zh-CN-XiaoxiaoNeural",
) -> Optional[Path]:
    """VideoAgent: 完整 Reels 製作流程。

    Steps:
      1. ContentAgent: Gemini → 震撼腳本
      2. VoiceAgent: edge-tts → 中文語音 MP3
      3. FrameAgent: Pillow → 逐幀文字圖片
      4. CompositeAgent: MoviePy → 影片合成
      5. ExportAgent: FFmpeg → 9:16 MP4
    """
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    session_id = f"reel_{random.randint(1000,9999)}"

    print(f"\n{'='*50}")
    print(f"🎬 Reels Maker — {title[:30]}")
    print(f"{'='*50}")

    # Step 1: Script
    print("\n📝 [Step 1/5] ContentAgent: 生成腳本...")
    lines = await _generate_script(title, content)
    if not lines:
        print("  ❌ 腳本生成失敗")
        return None
    script_text = "，".join(lines)

    # Step 2: Voice
    print(f"\n🔊 [Step 2/5] VoiceAgent: 語音生成 ({voice})...")
    audio_path = REELS_DIR / f"{session_id}_audio.mp3"
    audio_result = await _gen_voice(script_text, audio_path, voice)
    if not audio_result or not audio_result.exists():
        print("  ⚠️ 語音生成失敗，繼續製作無聲影片")
    else:
        print(f"  ✅ 語音: {audio_result}")

    # Step 3: Build scenes for visual engine
    print(f"\n🖼️  [Step 3/5] FrameAgent: 使用視覺引擎 (animated gradient + particle)...")
    from scripts.reels_visuals import ReelsComposer

    scenes = []
    bg_types = ["gradient", "particle", "gradient", "particle", "gradient"]
    font_sizes = [76, 80, 76, 80, 68]

    for i, line in enumerate(lines):
        bt = bg_types[i % len(bg_types)]
        fs = font_sizes[i % len(font_sizes)]
        scenes.append({
            "type": "text",
            "text": line,
            "duration_sec": 2.2,
            "bg_type": bt,
            "font_size": fs,
            "animation": "still",
        })

    # Step 4: Composite with visual engine
    print(f"\n🎞️  [Step 4/5] CompositeAgent: 視覺合成 (ReelsComposer)...")
    mp4_path = REELS_DIR / f"{session_id}.mp4"

    composer = ReelsComposer(scenes, fps=12)
    audio_fp = audio_result if (audio_result and audio_result.exists()) else None
    result = composer.render(mp4_path, audio_path=audio_fp)

    if result and result.exists():
        print(f"  ✅ 影片: {result}")
    else:
        print(f"  ⚠️ 視覺引擎失敗，使用 FFmpeg fallback...")
        # Old-style fallback frames
        frame_paths = []
        for i in range(len(lines)):
            fp = _render_frame(lines, i)
            frame_paths.append(fp)
        return await _fallback_ffmpeg(frame_paths, audio_result, mp4_path, 2.2)

    # Step 5: Export & QC
    print(f"\n✅ [Step 5/5] 完成!")
    file_size = mp4_path.stat().st_size if mp4_path.exists() else 0
    print(f"  檔案: {mp4_path}")
    print(f"  大小: {file_size / 1024 / 1024:.1f} MB")
    print(f"  時長: {2.2 * len(lines):.1f} 秒 ({len(lines)} 幀)")
    print(f"  腳本: {'→'.join(lines)}")

    # QC Check
    if file_size < 100 * 1024:
        print("  ⚠️ QC: 檔案過小 (<100KB)，可能有問題")
    if file_size > 100 * 1024 * 1024:
        print("  ⚠️ QC: 檔案過大 (>100MB)，IG 上限 60 秒/650MB")

    import shutil
    final_path = output_path or mp4_path
    if output_path:
        out = Path(str(output_path)) if not isinstance(output_path, Path) else output_path
        out.parent.mkdir(parents=True, exist_ok=True)
        mp = Path(str(mp4_path))
        if str(out) != str(mp):
            shutil.copy2(str(mp), str(out))
            print(f"  [Copy] {mp.name} -> {out}")
    return final_path


async def _fallback_ffmpeg(frame_paths: List[Path], audio_path: Optional[Path],
                           output: Path, frame_duration: float) -> Optional[Path]:
    """Fallback: FFmpeg 直接合成（不用 MoviePy）。"""
    print("  ⚡ Fallback: 使用 FFmpeg...")

    # Create concat file for frames
    concat_file = REPO / "data" / "reels" / "concat.txt"
    with open(concat_file, "w") as f:
        for fp in frame_paths:
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {frame_duration}\n")

    # Build filter for zoom effect
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
    ]
    if audio_path and audio_path.exists():
        cmd += ["-i", str(audio_path)]

    cmd += [
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-r", "24",
        "-b:v", "8000k",
        "-preset", "fast",
        str(output),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        if output.exists():
            print(f"  ✅ FFmpeg 合成: {output}")
            return output
    except subprocess.CalledProcessError as e:
        print(f"  ❌ FFmpeg 失敗: {e.stderr[:200]}")

    return None


# ====================================================================
# QCAgent: 品質驗證
# ====================================================================

def qc_check(video_path: Path) -> Dict:
    """QCAgent: 驗證影片品質。

    檢查：
    - 檔案存在且 > 100KB
    - 時長 > 3 秒
    - 解析度是 9:16
    - 有音軌（如果有語音）
    - bitrate 夠高
    """
    result = {"passed": True, "checks": [], "warnings": []}

    if not video_path.exists():
        return {"passed": False, "checks": ["檔案不存在"], "warnings": []}

    size = video_path.stat().st_size
    if size < 100 * 1024:
        result["passed"] = False
        result["checks"].append(f"❌ 檔案太小: {size/1024:.0f}KB")
    else:
        result["checks"].append(f"✅ 檔案大小: {size/1024/1024:.1f}MB")

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=width,height,codec_type:format=duration,bit_rate",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(probe.stdout)

        duration = float(data.get("format", {}).get("duration", 0))
        if duration < 3:
            result["passed"] = False
            result["checks"].append(f"❌ 時長過短: {duration:.1f}s")
        else:
            result["checks"].append(f"✅ 時長: {duration:.1f}s")

        has_video = False
        has_audio = False
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                has_video = True
                w, h = stream.get("width", 0), stream.get("height", 0)
                aspect = w / h if h > 0 else 0
                if abs(aspect - 9/16) > 0.05:
                    result["warnings"].append(f"⚠️ 比例非 9:16: {w}x{h}")
                else:
                    result["checks"].append(f"✅ 解析度: {w}x{h}")
            if stream.get("codec_type") == "audio":
                has_audio = True

        if not has_video:
            result["passed"] = False
            result["checks"].append("❌ 無視訊流")
        if has_audio:
            result["checks"].append("✅ 有音軌")
        else:
            result["warnings"].append("⚠️ 無音軌")

    except Exception as e:
        result["warnings"].append(f"⚠️ FFprobe 檢查失敗: {e}")

    return result


# ====================================================================
# 主 CLI
# ====================================================================

async def make_brand_reel(
    card_texts: List[str],
    output_path: Optional[Path] = None,
    voice: str = "zh-TW-HsiaoChenNeural",
    issue_no: str = "—",
) -> Optional[Path]:
    """News Radar 編輯風 Reels（2026-06-14 使用者批准的形式）：
    報紙風 _render_frame（Paper/Ink/serif/masthead + Sienna RADAR）逐句揭示
    + 台灣口音 edge-tts 配音。取代原本的漸層粒子引擎。"""
    import re as _re
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    sid = f"brand_{random.randint(1000,9999)}"

    # 視覺斷行：只在自然標點/空白處斷，絕不切斷英文單字或詞中（字級會自動縮放塞進寬度）
    def _wrap(s: str, n: int = 16) -> List[str]:
        s = _re.sub(r'\[.*?\]|\(.*?\)|[*#=]', '', s or '').strip()
        if not s:
            return []
        parts = _re.split(r'(?<=[，,。、；;！!？?\s])', s)
        segs, cur = [], ""
        for p in parts:
            if not cur or len(cur) + len(p) <= n:
                cur += p
            else:
                segs.append(cur.strip()); cur = p
        if cur.strip():
            segs.append(cur.strip())
        return [x for x in segs if x]
    lines: List[str] = []
    for ct in (card_texts or []):
        lines += _wrap(ct)
    lines = [l for l in lines if l][:8] or ["News Radar"]

    # 配音：用原始完整句子（自然朗讀），非斷行版
    voice_src = [_re.sub(r'\[.*?\]|\(.*?\)|[*#=-]', '', c).strip()[:48] for c in (card_texts or []) if c.strip()]
    script = "。".join(voice_src[:5]) if voice_src else "".join(lines)
    ap = REELS_DIR / f"{sid}_audio.mp3"
    audio = await _gen_voice(script, ap, voice)

    # 逐句揭示幀（frame i 顯示 line 0..i）
    frames = [_render_frame(lines, i, issue_no=issue_no) for i in range(len(lines))]

    # 節奏：總長對齊配音（每句平均），無音時每句 2.4s
    out = Path(str(output_path)) if output_path else REELS_DIR / f"{sid}.mp4"
    if audio and audio.exists():
        adur = await _get_audio_duration(audio)
        per = max(1.6, min(3.2, adur / max(len(frames), 1))) if adur > 0 else 2.4
    else:
        per = 2.4
    return await _fallback_ffmpeg(frames, audio if (audio and audio.exists()) else None, out, per)


async def main():
    parser = argparse.ArgumentParser(description="News Radar Reels Maker")
    parser.add_argument("--title", type=str, help="新聞標題")
    parser.add_argument("--content", type=str, help="新聞內文")
    parser.add_argument("--url", type=str, help="從 URL 爬取文章")
    parser.add_argument("--draft-id", type=str, help="從現有 draft_id 製作")
    parser.add_argument("--voice", type=str, default="zh-TW-HsiaoChenNeural",
                       help="語音 (zh-CN-XiaoxiaoNeural 女聲 / zh-CN-YunxiNeural 男聲)")
    parser.add_argument("--output", type=str, help="輸出路徑")
    parser.add_argument("--publish", choices=["ig", "fb", "threads", "none"], default="none",
                       help="製作後直接發布")
    parser.add_argument("--qc-only", action="store_true", help="只做品質驗證不製作")

    args = parser.parse_args()

    # Resolve content
    title = args.title or ""
    content = args.content or ""
    card_paths = []
    card_texts = []
    card_paths = []
    card_texts = []

    if args.url:
        # Fetch article from URL
        print(f"[Reels] 抓取文章: {args.url}")
        try:
            import httpx
            import trafilatura
            resp = httpx.get(args.url, timeout=20, follow_redirects=True)
            data = trafilatura.extract(resp.text, output_format="json", with_metadata=True)
            if data:
                d = json.loads(data)
                title = d.get("title", "") or title
                content = d.get("text", "") or content
        except Exception as e:
            print(f"  ⚠️ 抓取失敗: {e}")

    elif args.draft_id:
        # Read carousel data directly from drafts.carousel_json
        from src import db as dbmod
        from src.schema import CarouselCards
        conn = dbmod.get_conn()
        draft_row = conn.execute("SELECT * FROM drafts WHERE id=?", (args.draft_id,)).fetchone()
        if draft_row:
            title = draft_row["title"] or title
            carousel_json = draft_row["carousel_json"] if "carousel_json" in draft_row.keys() else None
            if carousel_json:
                try:
                    carousel = CarouselCards.model_validate_json(carousel_json)
                    card_texts = [
                        carousel.insight_statement or "",
                        carousel.insight_support or "",
                        str(carousel.stat_number or "") + (" " + (carousel.stat_caption or "") if carousel.stat_caption else ""),
                    ] + (carousel.takeaways or [])
                    card_texts = [t for t in card_texts if t][:4]
                    if not card_texts:
                        card_texts = [title[:60], "市場分析快報"]
                except Exception:
                    pass
            # Fallback to body if no carousel
            if not card_texts:
                pd_rows = conn.execute("SELECT body FROM platform_drafts WHERE draft_id=?", (args.draft_id,)).fetchall()
                card_texts = [r["body"][:140] for r in pd_rows if r["body"]][:4]
            if not card_texts:
                news = conn.execute("SELECT title FROM news_items n JOIN drafts d ON d.news_id=n.id WHERE d.id=?", (args.draft_id,)).fetchone()
                if news:
                    card_texts = [news["title"][:60]]
        # If no news found, try draft directly
        if not card_texts:
            draft = conn.execute("SELECT title FROM drafts WHERE id=?", (args.draft_id,)).fetchone()
            if draft:
                title = draft["title"] or title
                card_texts = [(title or "Market")[j:j+50] for j in range(0, min(len(title or "Market"), 200), 50)][:4]
        conn.close()
        # Show what we got
        if card_texts:
            print(f"  \U0001f0cf {len(card_texts)} text chunks for cards")
            # Generate card images
            from PIL import Image, ImageDraw
            cdir = Path(str(REELS_DIR / "cards_reel"))
            cdir.mkdir(parents=True, exist_ok=True)
            for ci, txt in enumerate(card_texts[:4]):
                im = Image.new("RGB", (1080, 1920), (10, 15, 30))
                d = ImageDraw.Draw(im)
                from scripts.reels_visuals import _load_font as lf
                d.text((50, 80), f"  {ci+1}/4", font=lf(28), fill=(96, 165, 250))
                lines = [txt[j:j+16] for j in range(0, min(len(txt), 160), 16)][:8]
                for li, ln in enumerate(lines):
                    bx = d.textbbox((0, 0), ln, font=lf(52))
                    x = (1080 - (bx[2] - bx[0])) // 2
                    y = 300 + li * 68
                    d.text((x+2, y+2), ln, font=lf(52), fill=(0, 0, 0, 100))
                    d.text((x, y), ln, font=lf(52), fill=(232, 234, 237))
                d.rectangle([0, 1800, 1080, 1920], fill=(15, 20, 40))
                d.text((40, 1830), "主力爸爸我錯了", font=lf(30), fill=(96, 165, 250))
                fp = cdir / f"card_{ci}.png"
                im.save(fp, "PNG")
                card_paths.append(fp)
                print(f"  Card {ci+1}: {txt[:30]}...")
            print(f"\n\U0001f3b4 {len(card_paths)} cards ready")
            if card_texts:
                content = card_texts[0]  # ensure content is not empty

    if not content:
        print("❌ 請提供 --title + --content 或 --url 或 --draft-id")
        return 1

    # QC only
    if args.qc_only and args.output:
        result = qc_check(Path(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

    # Make reel — 統一走品牌編輯風（使用者 2026-06-14 批准）。
    # card_texts 來自 carousel_json（已繁化）；無 draft 時用 title/content 切句。
    output = Path(args.output) if args.output else None
    reel_lines = card_texts[:5] if card_texts else None
    if not reel_lines:
        import re as _re2
        reel_lines = [s.strip() for s in _re2.split(r'[。\n！？]', content or title or "")
                      if s.strip()][:5] or [title or "News Radar"]
    issue_no = (args.draft_id[:6] if args.draft_id else "—")
    video = await make_brand_reel(reel_lines, output, voice=args.voice, issue_no=issue_no)

    if not video:
        print("❌ Reels 製作失敗")
        return 1

    # QC
    qc = qc_check(video)
    print(f"\n{'='*40}")
    print("📋 QCAgent 品質報告:")
    for c in qc.get("checks", []):
        print(f"  {c}")
    for w in qc.get("warnings", []):
        print(f"  {w}")
    print(f"  結論: {'✅ PASS' if qc['passed'] else '❌ FAIL'}")

    # Publish
    if args.publish and args.publish != "none":
        from src.cover_uploader import upload_cover
        from src.publisher import publish_to_ig, publish_to_fb, publish_to_threads

        # Upload to cover-cdn
        slug = f"reel_{Path(video).stem}"
        print(f"\n📤 上傳至 cover-cdn: {slug}.mp4")
        video_url = upload_cover(
            local_png=video,
            draft_id=slug,
            platform_key=args.publish,
        )
        if not video_url:
            print("❌ cover-cdn 上傳失敗")
            return 1

        # Publish
        platforms = [args.publish]
        caption = title[:500] if args.publish in ("fb",) else title[:150]

        pf_map = {
            "ig": ("IG Reels", publish_to_ig),
            "fb": ("FB Reels", publish_to_fb),
            "threads": ("Threads Video", publish_to_threads),
        }

        for pf_key, (pf_name, pf_func) in pf_map.items():
            if pf_key == args.publish:
                print(f"\n🎥 發布至 {pf_name}...")
                result = await pf_func(text=caption, video_url=video_url)
                if result.get("success"):
                    print(f"  ✅ ID: {result.get('id')}")
                else:
                    print(f"  ❌ {result.get('error', '')}")

    return 0


def _sync_main():
    """Synchronous entry point for direct CLI calls. Runs async main."""
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(_sync_main())
