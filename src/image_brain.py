"""
News Radar · Image Brain (Phase 9.x, 2026-05-12)
==================================================

職責劃分（與 llm_brain.py 對比）：

    llm_brain.py    → 文字／結構化 JSON（content composition + research）
                       2026-05-12 起改用 Claude CLI 為主，Gemini 退場
    image_brain.py  → 視覺輸出（AI cover generation）
                       Gemini 在此保留，理由：Gemini 2.5-flash-image 在
                       photorealistic + 手繪風混合的 prompt 上表現比 Claude 強，
                       且 Anthropic 目前沒有對等的 text-to-image API

這個模組目前是**opt-in placeholder**。預設不啟用——cover 仍走原本的
``cover_renderer.py`` photo-overlay 流程。
要開啟 AI 生成的封面（Moleskine handdrawn 風格）：設 ``SUBSTACK_AI_COVER=1``。

落地介面：
    path = await generate_cover_image(
        prompt="A pencil sketch on grid paper of...",
        out_path=Path("/tmp/cover.png"),
        size=(1456, 816),
    )
    if path is None:
        # gen failed or disabled → caller fall back to overlay cover

設計決策：
- 與其在 cover_pipeline.py 直接調 Gemini SDK、把 image_gen 邏輯散在多處，
  獨立一個 image_brain 模組讓「AI 生圖 vs 純合成 vs 真實照片」三條 path 各自
  可被測試與替換。
- 故意不做 retry / cost tracking — image gen 是錦上添花、失敗就退回 overlay。
- 未來若要加 Imagen / DALL-E / Stability / Midjourney，在這裡加 backend，
  不要污染 substack_composer。
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Optional, Tuple


def is_ai_cover_enabled() -> bool:
    """讀環境變數判斷是否要開 AI cover gen。預設 false。"""
    return os.getenv("SUBSTACK_AI_COVER", "0").strip() == "1"


# Gemini image-capable models. As of 2026-05, "gemini-2.5-flash-image-preview"
# is the current text-to-image model that works with API-key auth (free tier
# subject to availability). Imagen 3 needs Vertex AI auth, not API key.
DEFAULT_IMAGE_MODEL = os.getenv(
    "SUBSTACK_IMAGE_MODEL",
    "gemini-2.5-flash-image-preview",
)


async def generate_cover_image(
    *,
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (1456, 816),
    style_hint: str = "moleskine_handdrawn",
) -> Optional[Path]:
    """Generate a cover image via Gemini text-to-image.

    Args:
        prompt: The visual prompt (usually `SubstackDraft.cover_image_prompt`).
        out_path: Where to save the PNG.
        size: Target (width, height). Gemini returns its native size; we resize.
        style_hint: Layered onto the prompt to enforce visual_soul.md aesthetic.
                    Defaults to "moleskine_handdrawn" (matches visual_soul.md
                    §視覺美學 — pencil/charcoal/Moleskine, muted earth tones).

    Returns:
        Path to saved PNG, or None on any failure (disabled / no key / API error).
    """
    if not is_ai_cover_enabled():
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[image_brain] ⚠️ GEMINI_API_KEY missing — cannot generate cover.")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        print(f"[image_brain] ⚠️ google-genai not installed ({exc}); skip.")
        return None

    # Layer the aesthetic into the prompt so the model isn't just guessing.
    full_prompt = _build_styled_prompt(prompt, style_hint)

    try:
        client = genai.Client(api_key=api_key)
        # Run blocking SDK call in a thread to keep async caller non-blocking.
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=DEFAULT_IMAGE_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )
    except Exception as exc:
        print(f"[image_brain] ⚠️ Gemini image gen failed: {type(exc).__name__}: {exc}")
        return None

    # Walk response parts to find inline image bytes.
    img_bytes = _extract_inline_image(resp)
    if not img_bytes:
        print(f"[image_brain] ⚠️ Response had no inline image part. raw model={DEFAULT_IMAGE_MODEL}")
        return None

    # Save + resize to spec.
    try:
        from PIL import Image
        from io import BytesIO

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        # Center-crop to target aspect, then resize.
        img = _center_crop_to_aspect(img, size)
        img = img.resize(size, Image.LANCZOS)
        img.save(out_path, "PNG", optimize=True)
        return out_path
    except Exception as exc:
        print(f"[image_brain] ⚠️ Image post-processing failed: {exc}")
        return None


def _build_styled_prompt(user_prompt: str, style_hint: str) -> str:
    """Inject visual_soul.md aesthetic constraints into the model's prompt."""
    if style_hint == "moleskine_handdrawn":
        suffix = (
            " | Style: pencil sketch on grid paper, handdrawn Moleskine notebook aesthetic, "
            "muted earth tones (sienna / faded ochre / charcoal grey), low-saturation monochrome "
            "with a single accent color. No 3D rendering, no neon, no anime, no exaggerated facial "
            "emotion. If humans appear, only backs of heads / side profiles, observing a system "
            "(diagram / chart / object). Composition reads like a scientific illustration or "
            "vintage botanical plate."
        )
    else:
        suffix = ""
    return user_prompt.strip() + suffix


def _extract_inline_image(resp) -> Optional[bytes]:
    """Pull image bytes out of the GenerateContentResponse structure.

    google-genai puts inline binary at:
        resp.candidates[0].content.parts[i].inline_data.data  (base64 str OR bytes)
    """
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return None
        content = getattr(cands[0], "content", None)
        if not content:
            return None
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if data is None:
                continue
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                # SDK sometimes returns base64-encoded str
                try:
                    return base64.b64decode(data)
                except Exception:
                    return None
    except Exception:
        return None
    return None


def _center_crop_to_aspect(img, target_size: Tuple[int, int]):
    """Center-crop ``img`` to the aspect ratio of ``target_size``."""
    tw, th = target_size
    target_ratio = tw / th
    w, h = img.size
    src_ratio = w / h
    if abs(src_ratio - target_ratio) < 1e-3:
        return img
    if src_ratio > target_ratio:
        # Source too wide → crop sides
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    # Source too tall → crop top/bottom
    new_h = int(w / target_ratio)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


if __name__ == "__main__":
    # Smoke test (requires SUBSTACK_AI_COVER=1 + GEMINI_API_KEY)
    async def _smoke():
        from dotenv import load_dotenv

        load_dotenv()
        path = await generate_cover_image(
            prompt=(
                "A half-filled music sheet — half the bars dense with notes, half blank rests; "
                "rest symbols highlighted in faded sienna; on grid paper."
            ),
            out_path=Path("/tmp/test_cover.png"),
        )
        print(f"Result: {path}")

    asyncio.run(_smoke())
