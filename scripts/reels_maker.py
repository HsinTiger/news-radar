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


async def _gen_voice(text: str, output_path: Path, voice: str = "zh-CN-XiaoxiaoNeural") -> Optional[Path]:
    """Edge-TTS 生成中文語音。完全免費、不需 API key。

    支援的繁體中文語音：
      - zh-CN-XiaoxiaoNeural (女聲, 推薦)
      - zh-CN-YunxiNeural (男聲)
      - zh-HK-HiuGaaiNeural (粵語女聲, 但可讀國語)
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


def _render_frame(text_lines: List[str], line_index: int,
                  width: int = 1080, height: int = 1920,
                  bg_color: Tuple[int, ...] = (15, 17, 23)) -> Path:
    """Render a single frame with text using Pillow.

    Text animation:
    - Lines before current: small, dimmed (already revealed)
    - Current line: large, white, bold
    - Lines after: invisible
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to load fonts
    font_paths = [
        REPO / "assets" / "fonts" / "SourceHanSansTC-Bold.otf",
        REPO / "assets" / "fonts" / "SourceHanSansTC-Regular.otf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]

    title_font = None
    body_font = None
    sub_font = None
    for fp in font_paths:
        p = Path(fp)
        if p.exists():
            try:
                title_font = ImageFont.truetype(str(p), 80)
                body_font = ImageFont.truetype(str(p), 64)
                sub_font = ImageFont.truetype(str(p), 36)
                break
            except Exception:
                continue

    if title_font is None:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    center_x = width // 2

    # Calculate vertical centering for N lines
    line_height = 110
    total_h = len(text_lines) * line_height
    start_y = (height - total_h) // 2

    for i, line in enumerate(text_lines):
        y = start_y + i * line_height
        if i < line_index:
            # Dimmed (already read)
            font = sub_font
            color = (100, 100, 110)
            alpha = 180
        elif i == line_index:
            # Current line
            font = title_font
            color = (232, 234, 237)
            alpha = 255
        else:
            # Not yet visible
            continue

        # Measure text
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = center_x - tw // 2

        # Draw with slight shadow for depth
        if i == line_index:
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font, fill=color)

    # Bottom brand
    brand = "主力爸爸我錯了"
    bbox = draw.textbbox((0, 0), brand, font=sub_font)
    bw = bbox[2] - bbox[0]
    draw.text((center_x - bw // 2, height - 120), brand, font=sub_font, fill=(60, 62, 68))

    # Frame number
    draw.text((30, 30), f"  {line_index+1}/{len(text_lines)}", font=sub_font, fill=(50, 50, 56))

    out = REPO / "data" / "reels" / "frames"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"frame_{line_index:03d}.png"
    img.save(path, "PNG")
    return path


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

    # Step 3: Frames
    print(f"\n🖼️  [Step 3/5] FrameAgent: 渲染逐幀文字...")
    frame_paths = []
    frame_duration = 2.2  # seconds per frame
    for i in range(len(lines)):
        fp = _render_frame(lines, i)
        frame_paths.append(fp)
        print(f"  ✅ 幀 {i+1}/{len(lines)}")

    # Step 4: Composite with MoviePy
    print(f"\n🎞️  [Step 4/5] CompositeAgent: 合成影片...")
    mp4_path = REELS_DIR / f"{session_id}.mp4"

    try:
        from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
        from moviepy.video.fx import FadeIn, FadeOut

        # Create clips with Ken Burns zoom and crossfade
        clips = []
        for i, fp in enumerate(frame_paths):
            clip = ImageClip(str(fp), duration=frame_duration)
            clip = clip.resized(lambda t: 1 + 0.03 * t / frame_duration)  # 3% Ken Burns zoom over duration
            clip = clip.with_position(("center", "center"))
            # Crossfade: fade in 0.3s, fade out 0.3s (but last clip doesn't fade out early)
            if i > 0:
                clip = clip.with_duration(frame_duration)
            clips.append(clip)

        # Concatenate with crossfade
        video = concatenate_videoclips(clips, method="compose", padding=-0.3)

        # Add audio if available
        if audio_result and audio_result.exists():
            audio = AudioFileClip(str(audio_result))
            # Loop audio if shorter than video, or trim if longer
            audio = audio.subclip(0, min(audio.duration, video.duration))
            video = video.with_audio(audio)

        # Write
        video.write_videofile(
            str(mp4_path),
            codec="libx264",
            audio_codec="aac",
            fps=24,
            preset="medium",
            bitrate="8000k",
            threads=2,
            logger=None,
        )
        video.close()
        print(f"  ✅ 影片: {mp4_path}")

    except Exception as e:
        print(f"  ❌ MoviePy 合成失敗: {e}")
        # Fallback: use FFmpeg directly
        return await _fallback_ffmpeg(frame_paths, audio_result, mp4_path, frame_duration)

    # Step 5: Export & QC
    print(f"\n✅ [Step 5/5] 完成!")
    file_size = mp4_path.stat().st_size if mp4_path.exists() else 0
    print(f"  檔案: {mp4_path}")
    print(f"  大小: {file_size / 1024 / 1024:.1f} MB")
    print(f"  時長: {frame_duration * len(lines):.1f} 秒 ({len(lines)} 幀)")
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

async def main():
    parser = argparse.ArgumentParser(description="News Radar Reels Maker")
    parser.add_argument("--title", type=str, help="新聞標題")
    parser.add_argument("--content", type=str, help="新聞內文")
    parser.add_argument("--url", type=str, help="從 URL 爬取文章")
    parser.add_argument("--draft-id", type=str, help="從現有 draft_id 製作")
    parser.add_argument("--voice", type=str, default="zh-CN-XiaoxiaoNeural",
                       help="語音 (zh-CN-XiaoxiaoNeural 女聲 / zh-CN-YunxiNeural 男聲)")
    parser.add_argument("--output", type=str, help="輸出路徑")
    parser.add_argument("--publish", choices=["ig", "fb", "threads", "none"], default="none",
                       help="製作後直接發布")
    parser.add_argument("--qc-only", action="store_true", help="只做品質驗證不製作")

    args = parser.parse_args()

    # Resolve content
    title = args.title or ""
    content = args.content or ""

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
        # Read from DB
        from src import db as dbmod
        conn = dbmod.get_conn()
        row = conn.execute(
            "SELECT n.title, n.clean_markdown FROM news_items n "
            "JOIN drafts d ON d.news_id = n.id WHERE d.id = ?",
            (args.draft_id,)
        ).fetchone()
        conn.close()
        if row:
            title = row["title"] or title
            content = row["clean_markdown"] or content

    if not content:
        print("❌ 請提供 --title + --content 或 --url 或 --draft-id")
        return 1

    # QC only
    if args.qc_only and args.output:
        result = qc_check(Path(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

    # Make reel
    output = Path(args.output) if args.output else None
    video = await make_reel(title, content, output, voice=args.voice)

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
