"""
News Radar · Reels Visual Engine — 可程式化的短影音視覺效果
==============================================================
完全不需外部素材、不需 AI 影片生成、不需 API key。

視覺升級策略（由低成本到高成本）：
  1. 動態 HSL 漸層背景（Pillow 逐幀渲染，程式碼產生）
  2. 粒子/光點背景（numpy 數值運算）
  3. 文字動畫（打字機、彈跳、淡入、Ken Burns）
  4. 圖表動畫（matplotlib 生長長條圖）
  5. 使用 pipeline 既有的 Branded Carousel 圖卡當背景
"""

from __future__ import annotations
import math
import os
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from PIL import Image, ImageDraw, ImageFont

# ====================================================================
# 工具
# ====================================================================

def _hsl_to_rgb(h: float, s: float, l: float) -> Tuple[int, int, int]:
    """HSL → RGB (0-1 range). Pure Python, no imports."""
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60: r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else: r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def _lerp_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _load_font(size: int = 48) -> ImageFont.FreeTypeFont:
    """Load SourceHanSansTC Bold, with fallback."""
    paths = [
        Path("assets/fonts/SourceHanSansTC-Bold.otf"),
        Path("assets/fonts/SourceHanSansTC-Regular.otf"),
    ]
    for p in paths:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


# ====================================================================
# ReelsBackground — 動態背景產生器
# ====================================================================

class ReelsBackground:
    W = 1080
    H = 1920

    @staticmethod
    def gradient(
        width: int = 1080,
        height: int = 1920,
        color1: Tuple[int, int, int] = (15, 23, 42),
        color2: Tuple[int, int, int] = (30, 41, 82),
        angle: float = 45,
    ) -> Path:
        """Render a single gradient background PNG."""
        img = Image.new("RGB", (width, height))
        pixels = img.load()
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        cx, cy = width // 2, height // 2
        diag = math.sqrt(width ** 2 + height ** 2) / 2
        for y in range(height):
            for x in range(width):
                dx, dy = x - cx, y - cy
                t = (dx * cos_a + dy * sin_a) / diag
                t = max(0, min(1, (t + 1) / 2))
                pixels[x, y] = _lerp_color(color1, color2, t)
        path = Path(f"/tmp/reel_bg_gradient.png")
        img.save(path, "PNG")
        return path

    @staticmethod
    def animated_gradient(
        colors: List[Tuple[int, int, int]] = None,
        num_frames: int = 60,
        width: int = 1080,
        height: int = 1920,
        out_dir: Optional[Path] = None,
    ) -> List[Path]:
        """Render N frames of animated gradient — colors smoothly morph."""
        if colors is None:
            colors = [
                (15, 23, 42), (30, 41, 82),
                (20, 15, 35), (40, 20, 60),
                (10, 30, 50), (15, 23, 42),
            ]
        out = out_dir or Path("/tmp/reel_bg_frames")
        out.mkdir(parents=True, exist_ok=True)
        frames: List[Path] = []
        total_transitions = len(colors) - 1
        frames_per_transition = max(1, num_frames // total_transitions) if total_transitions > 0 else num_frames

        for t_idx in range(total_transitions):
            c1, c2 = colors[t_idx], colors[t_idx + 1]
            for f_idx in range(frames_per_transition):
                t = f_idx / max(1, frames_per_transition - 1)
                rgb = _lerp_color(c1, c2, t)
                path = ReelsBackground.gradient(width, height, rgb, _lerp_color(rgb, (0, 0, 0), 0.3), angle=45 + t * 15)
                frames.append(path)
        return frames

    @staticmethod
    def particle_field(
        num_frames: int = 48,
        width: int = 1080,
        height: int = 1920,
        particle_count: int = 50,
        out_dir: Optional[Path] = None,
    ) -> List[Path]:
        """Particle field background using numpy."""
        if not HAS_NUMPY:
            return ReelsBackground.animated_gradient(num_frames=num_frames)
        out = out_dir or Path("/tmp/reel_particles")
        out.mkdir(parents=True, exist_ok=True)
        frames: List[Path] = []
        particles = np.random.rand(particle_count, 4)  # x, y, vx, vy
        for frame_idx in range(num_frames):
            img = Image.new("RGB", (width, height), (5, 5, 15))
            draw = ImageDraw.Draw(img)
            particles[:, 0] += particles[:, 2]
            particles[:, 1] += particles[:, 3]
            particles[:, 0] = np.mod(particles[:, 0], 1)
            particles[:, 1] = np.mod(particles[:, 1], 1)
            for p in particles:
                px, py = int(p[0] * width), int(p[1] * height)
                alpha = int(100 + 155 * (py / height))
                color = (100, 150, 255, alpha)
                r = max(1, int(3 * (1 - py / height)))
                draw.ellipse([px - r, py - r, px + r, py + r], fill=color[:3])
            fp = out / f"particle_{frame_idx:04d}.png"
            img.save(fp, "PNG")
            frames.append(fp)
        return frames

    @staticmethod
    def from_carousel_card(card_path: Path, blur: bool = True) -> Path:
        """Use a pipeline carousel card as background. Optionally blur + dim it."""
        if not card_path.exists():
            return ReelsBackground.gradient()
        img = Image.open(card_path).convert("RGB").resize((1080, 1920), Image.LANCZOS)
        if blur:
            # Simple box blur via PIL
            for _ in range(3):
                img = img.filter(ImageFilter.BoxBlur(10)) if hasattr(ImageFilter, 'BoxBlur') else img
            # Dim
            overlay = Image.new("RGB", img.size, (0, 0, 0))
            img = Image.blend(img, overlay, 0.4)
        path = Path("/tmp/reel_bg_card.png")
        img.save(path, "PNG")
        return path

    @staticmethod
    def scene_background(scene_type: str, frame_idx: int = 0, total_frames: int = 48) -> Path:
        """Dispatch to the right background type per scene."""
        if scene_type == "particle":
            return ReelsBackground.particle_field(num_frames=1, out_dir=Path("/tmp"))[-1]
        elif scene_type == "gradient":
            t = frame_idx / max(1, total_frames)
            colors = [
                (15, 23, 42),
                (30, 15, 45),
                (10, 40, 60),
                (15, 23, 42),
            ]
            idx = int(t * (len(colors) - 1))
            c1, c2 = colors[min(idx, len(colors) - 2)], colors[min(idx + 1, len(colors) - 1)]
            lt = (t * (len(colors) - 1)) % 1
            return ReelsBackground.gradient(
                color1=_lerp_color(c1, c2, lt),
                color2=_lerp_color(c2, (0, 0, 0), 0.3),
                angle=45 + t * 30,
            )
        return ReelsBackground.gradient()


# ====================================================================
# ReelsText — 文字動畫效果
# ====================================================================

class ReelsText:
    @staticmethod
    def typewriter_frames(
        text: str,
        font_size: int = 72,
        width: int = 1080,
        height: int = 1920,
        bg_path: Optional[Path] = None,
        color: Tuple[int, int, int] = (232, 234, 237),
    ) -> List[Path]:
        """Typewriter effect: each frame reveals one more character."""
        font = _load_font(font_size)
        frames: List[Path] = []
        for i in range(1, len(text) + 1):
            partial = text[:i]
            bg = Image.open(bg_path).resize((width, height), Image.LANCZOS) if bg_path and bg_path.exists() else Image.new("RGB", (width, height), (15, 17, 23))
            draw = ImageDraw.Draw(bg)
            bbox = draw.textbbox((0, 0), partial, font=font)
            x = (width - (bbox[2] - bbox[0])) // 2
            y = height // 2 - 60
            draw.text((x, y), partial, font=font, fill=color)
            fp = Path(f"/tmp/reel_typewriter_{i:04d}.png")
            bg.save(fp, "PNG")
            frames.append(fp)
        return frames

    @staticmethod
    def bounce_in_frame(
        text: str,
        font_size: int = 72,
        width: int = 1080,
        height: int = 1920,
        frame_num: int = 0,
        total_bounce_frames: int = 12,
        bg_path: Optional[Path] = None,
        color: Tuple[int, int, int] = (232, 234, 237),
    ) -> Path:
        """Single frame of a bounce-in animation (ease-out overshoot)."""
        t = frame_num / max(1, total_bounce_frames - 1)
        # Ease-out elastic
        scale = 1.0
        if t < 0.6:
            scale = 0.3 + 0.7 * (t / 0.6) ** 0.5
        elif t < 0.85:
            scale = 1.0 + 0.15 * math.sin((t - 0.6) / 0.25 * math.pi)
        else:
            scale = 1.0 - 0.05 * (t - 0.85) / 0.15
        current_size = max(12, int(font_size * scale))
        font = _load_font(current_size)
        bg = Image.open(bg_path).resize((width, height), Image.LANCZOS) if bg_path and bg_path.exists() else Image.new("RGB", (width, height), (15, 17, 23))
        draw = ImageDraw.Draw(bg)
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (width - (bbox[2] - bbox[0])) // 2
        y = height // 2 - current_size // 2
        # Shadow
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 128))
        draw.text((x, y), text, font=font, fill=color)
        fp = Path(f"/tmp/reel_bounce_{frame_num:04d}.png")
        bg.save(fp, "PNG")
        return fp


# ====================================================================
# ReelsChart — 圖表動畫
# ====================================================================

class ReelsChart:
    @staticmethod
    def bar_chart_growth(
        labels: List[str],
        values: List[float],
        title: str = "",
        width: int = 1080,
        height: int = 1920,
        duration_sec: float = 3.0,
        fps: int = 12,
    ) -> Optional[Path]:
        """Animated bar chart: bars grow from 0 to target values."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.animation as animation
        except ImportError:
            return None

        fig, ax = plt.subplots(figsize=(width / 100, height / 100), facecolor="#0f1117")
        ax.set_facecolor("#0f1117")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#303446")
        ax.spines["bottom"].set_color("#303446")
        ax.tick_params(colors="#9aa0a6", labelsize=20)
        ax.set_ylim(0, max(values) * 1.2)

        if title:
            ax.set_title(title, color="#e8eaed", fontsize=32, pad=20)

        colors = ["#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]

        bars = ax.bar(labels, [0] * len(values), color=colors[:len(values)], width=0.6)
        total_frames = int(duration_sec * fps)

        def update(frame):
            t = frame / max(1, total_frames - 1)
            current_values = [v * min(1, t * 1.3) for v in values]
            for bar, val in zip(bars, current_values):
                bar.set_height(val)
            return bars

        # Write as MP4 using Pillow frames (fallback)
        out_dir = Path("/tmp/reel_chart_frames")
        out_dir.mkdir(parents=True, exist_ok=True)
        frame_paths = []
        for f_idx in range(total_frames):
            t = f_idx / max(1, total_frames - 1)
            cur_val = [v * min(1, t * 1.3) for v in values]
            for bar, val in zip(bars, cur_val):
                bar.set_height(val)
            fig.canvas.draw()
            arr = plt.gcf().canvas.buffer_rgba() if hasattr(plt.gcf().canvas, 'buffer_rgba') else None
            if arr is not None:
                from PIL import Image
                img = Image.frombuffer("RGBA", fig.canvas.get_width_height(), arr, "raw", "RGBA", 0, 1)
                fp = out_dir / f"chart_{f_idx:04d}.png"
                img.convert("RGB").save(fp)
                frame_paths.append(fp)

        plt.close(fig)

        if not frame_paths:
            return None

        # Compile frames to MP4
        output = Path("/tmp/reel_chart.mp4")
        try:
            cmd = [
                "ffmpeg", "-y", "-framerate", str(fps),
                "-i", str(out_dir / "chart_%04d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-b:v", "4000k", str(output),
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return output if output.exists() else None
        except Exception:
            return None

    @staticmethod
    def big_number_frame(
        number: str,
        label: str = "",
        width: int = 1080,
        height: int = 1920,
        bg_path: Optional[Path] = None,
    ) -> Path:
        """Single frame: one big number + caption. Use as stat card overlay."""
        bg = Image.open(bg_path).resize((width, height), Image.LANCZOS) if bg_path and bg_path.exists() else Image.new("RGB", (width, height), (15, 17, 23))
        draw = ImageDraw.Draw(bg)
        num_font = _load_font(140)
        lbl_font = _load_font(36)
        # Number
        bbox = draw.textbbox((0, 0), number, font=num_font)
        x = (width - (bbox[2] - bbox[0])) // 2
        y = height // 2 - 100
        draw.text((x + 3, y + 3), number, font=num_font, fill=(0, 0, 0, 100))
        draw.text((x, y), number, font=num_font, fill=(96, 165, 250))
        # Label
        if label:
            bbox2 = draw.textbbox((0, 0), label, font=lbl_font)
            x2 = (width - (bbox2[2] - bbox2[0])) // 2
            y2 = y + 160
            draw.text((x2, y2), label, font=lbl_font, fill=(154, 160, 166))
        fp = Path(f"/tmp/reel_big_number.png")
        bg.save(fp, "PNG")
        return fp


# ====================================================================
# ReelsComposer — 多場景合成
# ====================================================================

class ReelsComposer:
    """Compose multiple visual scenes into one finished reel MP4.

    Each scene is a dict:
      {
        "type": "text" | "chart" | "number" | "card",
        "text": "一行文字" (for text scenes),
        "animation": "typewriter" | "bounce" | "fade" | "still",
        "duration_sec": 2.5,
        "font_size": 72,
        "bg_type": "gradient" | "particle" | "card",
        "chart_data": {"labels": [...], "values": [...]}, (for chart scenes)
        "number_data": {"number": "...", "label": "..."}, (for number scenes)
        "card_path": "...", (for card scenes)
        "voice_line": "一起念的語音文字", (optional, for subtitle sync)
      }
    """

    def __init__(self, scenes: List[Dict], fps: int = 24, width: int = 1080, height: int = 1920):
        self.scenes = scenes
        self.fps = fps
        self.w = width
        self.h = height
        self.temp_dir = Path("/tmp/reels_composer")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def render(self, output_path: Path, audio_path: Optional[Path] = None) -> Optional[Path]:
        """Render all scenes into one MP4. Uses FFmpeg for final assembly."""
        all_frames: List[Path] = []
        frame_no = 0

        for scene in self.scenes:
            duration = scene.get("duration_sec", 2.5)
            num_frames = int(duration * self.fps)
            bg_type = scene.get("bg_type", "gradient")
            animation = scene.get("animation", "still")
            text = scene.get("text", "")
            font_size = scene.get("font_size", 72)

            for f_idx in range(num_frames):
                # Get background
                if scene.get("type") == "card" and scene.get("card_path"):
                    bg = ReelsBackground.from_carousel_card(Path(scene["card_path"]))
                else:
                    bg = ReelsBackground.scene_background(bg_type, f_idx, num_frames)

                if scene["type"] == "number":
                    nd = scene.get("number_data", {})
                    fp = ReelsChart.big_number_frame(
                        nd.get("number", ""),
                        nd.get("label", ""),
                        self.w, self.h, bg
                    )
                elif animation == "typewriter" and text:
                    # Show full text on all frames (simplified - not animating per char for speed)
                    font = _load_font(font_size)
                    img = Image.open(bg).resize((self.w, self.h), Image.LANCZOS)
                    draw = ImageDraw.Draw(img)
                    lines = text.split("\n")
                    total_h = len(lines) * (font_size + 10)
                    start_y = (self.h - total_h) // 2
                    for li, line in enumerate(lines):
                        bbox = draw.textbbox((0, 0), line, font=font)
                        x = (self.w - (bbox[2] - bbox[0])) // 2
                        y = start_y + li * (font_size + 10)
                        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 128))
                        draw.text((x, y), line, font=font, fill=(232, 234, 237))
                    fp = self.temp_dir / f"composer_{frame_no:06d}.png"
                    img.save(fp, "PNG")
                else:
                    # Static text on background
                    font = _load_font(font_size)
                    img = Image.open(bg).resize((self.w, self.h), Image.LANCZOS)
                    draw = ImageDraw.Draw(img)
                    lines = text.split("\n") if text else []
                    total_h = len(lines) * (font_size + 10)
                    start_y = (self.h - total_h) // 2
                    for li, line in enumerate(lines):
                        bbox = draw.textbbox((0, 0), line, font=font)
                        x = (self.w - (bbox[2] - bbox[0])) // 2
                        y = start_y + li * (font_size + 10)
                        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 128))
                        draw.text((x, y), line, font=font, fill=(232, 234, 237))
                    fp = self.temp_dir / f"composer_{frame_no:06d}.png"
                    img.save(fp, "PNG")

                all_frames.append(fp)
                frame_no += 1

        if not all_frames:
            return None

        # Compile to MP4 with FFmpeg
        try:
            cmd = [
                "ffmpeg", "-y", "-framerate", str(self.fps),
                "-i", str(self.temp_dir / "composer_%06d.png"),
            ]
            if audio_path and audio_path.exists():
                cmd += ["-i", str(audio_path), "-c:a", "aac", "-shortest"]
            cmd += [
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-b:v", "8000k", "-preset", "fast",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return output_path if output_path.exists() else None
        except Exception as e:
            print(f"[ReelsComposer] FFmpeg failed: {e}")
            return None


# ====================================================================
# 快速測試
# ====================================================================
if __name__ == "__main__":
    scenes = [
        {"type": "text", "text": "NVIDIA\n上季營收", "duration_sec": 2.5, "bg_type": "particle", "font_size": 80, "animation": "typewriter"},
        {"type": "number", "number_data": {"number": "$39B", "label": "比去年同期成長 145%"}, "duration_sec": 3.0, "bg_type": "gradient"},
        {"type": "text", "text": "但華爾街說\n還不夠", "duration_sec": 2.5, "bg_type": "gradient", "font_size": 80},
        {"type": "number", "number_data": {"number": "8%", "label": "Blackwell 毛利率降幅"}, "duration_sec": 3.0, "bg_type": "particle"},
        {"type": "text", "text": "明天開盤\n注意這個數字", "duration_sec": 2.5, "bg_type": "gradient", "font_size": 72},
    ]

    composer = ReelsComposer(scenes, fps=12)
    result = composer.render(Path("/tmp/reel_final_test.mp4"))
    if result:
        print(f"✅ Reel rendered: {result}")
    else:
        print("❌ Failed")
