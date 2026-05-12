"""
News Radar · Image Brain (Phase 9.x, 2026-05-12)
==================================================

職責劃分（與 llm_brain.py 對比）：

    llm_brain.py    → 文字／結構化 JSON（content composition + research）
                       2026-05-12 起改用 Claude CLI 為主，Gemini 退場
    image_brain.py  → 視覺輸出
                       **2026-05-12 模式調整**：預設改成「產 prompt、不打 API」。
                       Claude CLI 對封面構圖的描述能力勝過任何 text-to-image
                       SDK，且讓 Hsin 自己把 prompt 丟 GPT web / NanoBanana 手動
                       生成。這條 path 取消了 API 費用、避開 preview 模型不穩。

兩種模式（互斥）：

    (A) **prompt-only mode** （**預設**）
        ``build_cover_prompt_block(cover_image_prompt)`` 產一段 markdown 區塊，
        Caller 把它附在 Article_Substack.md 結尾。Hsin 複製→丟生圖工具。

    (B) **legacy Gemini gen mode**（**deprecated；keep for archeology**）
        舊的 ``generate_cover_image()`` 仍在，但只在 ``SUBSTACK_AI_COVER=1``
        時觸發。實測 ``gemini-2.5-flash-image-preview`` 在莫蘭迪/手繪 prompt
        上不穩定，且要 API key，所以 Hsin 決議下架。
        新專案不要走這條 path。

設計決策：
- 改 prompt-only 後，「AI 生圖 vs 純合成 vs 真實照片」這三條 path 的選擇權
  完全交給 Hsin。pipeline 不再替他做。
- generate_cover_image() 程式碼保留是因為「拿掉容易、回復難」——萬一 prompt-only
  之後想再回到全自動，5 行 env-var flip 就能切回去。
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Optional, Tuple


def is_ai_cover_enabled() -> bool:
    """讀環境變數判斷是否要開 AI cover gen。預設 false。

    2026-05-12 起預設關閉。Hsin 改用 prompt-only mode（見
    ``build_cover_prompt_block``），把 prompt 手動丟 GPT web/NanoBanana。
    """
    return os.getenv("SUBSTACK_AI_COVER", "0").strip() == "1"


def build_cover_prompt_block(
    cover_image_prompt: str,
    *,
    title: str = "",
    subtitle: str = "",
) -> str:
    """產出 markdown 區塊放在 Article_Substack.md 結尾。

    Caller responsibility：把這段 append 進 article markdown。Hsin 收到草稿後
    複製 prompt → GPT web / NanoBanana / Midjourney → 拿圖回來手動上傳。

    為什麼提供 3 個版本（場景 / 概念 / 抽象）：
        text-to-image 不同 prompt 出來差異大，給 3 個方向讓 Hsin 挑 1 個丟生圖，
        或全丟、選最對味的。不增加 token 成本（caller 端 LLM 已經寫好 prompt
        了，這裡只做 markdown formatting）。

    Aesthetic enforcement：每個版本都自動 append visual_soul.md 約束尾段。
    """
    aesthetic_tail = (
        " — Style: pencil sketch on grid paper, handdrawn Moleskine notebook "
        "aesthetic, muted earth tones (sienna / faded ochre / charcoal grey), "
        "low-saturation monochrome with single accent color. No 3D rendering, "
        "no neon, no anime, no exaggerated facial emotion. If humans appear, "
        "only backs of heads / side profiles observing a system (diagram / "
        "chart / object). Composition reads like a scientific illustration "
        "or vintage botanical plate. 16:9 aspect (1456×816)."
    )

    # Three angles to give the human picker. Caller can also pass a single
    # prompt and we'll just echo it once if they don't want variants.
    return (
        "\n\n---\n\n"
        "## 📸 封面圖 Prompt（手動丟 GPT web / NanoBanana 生圖）\n\n"
        "Claude 寫文章順便產的視覺指引。挑一個（或全試）→ 丟生圖工具 → "
        "回來換掉 cover.png 再 publish。\n\n"
        "### 版本 A · 場景直譯\n\n"
        f"> {cover_image_prompt.strip()}{aesthetic_tail}\n\n"
        "### 版本 B · 概念符號（Hsin 自行決定要不要試）\n\n"
        f"> 用一張視覺隱喻代替文章直接場景，主題：「{title or '(本文主題)'}」"
        f"。{aesthetic_tail.lstrip(' —')}\n\n"
        "### 版本 C · 抽象地圖／流程圖\n\n"
        f"> 一張手繪的關係網路圖，用線條與小型符號表達『{subtitle or title or '(本文核心)'}』"
        f"中的多個元素之間的關係與張力。{aesthetic_tail.lstrip(' —')}\n"
    )


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
