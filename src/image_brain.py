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


# 2026-05-22 brand aesthetic version stamp. Bump when visual_brand_system.md
# changes so future drift is detectable (grep for "BRAND_AESTHETIC_VERSION"
# in any rendered cover_prompts.md to verify it's not stale).
BRAND_AESTHETIC_VERSION = "v0.2.2_cold_print_editorial"

# 2026-05-16 大字 enforcement keyphrases. Per substack_soul.md §10.2 #1, EVERY
# cover variant (scene / concept / abstract) must contain large hero text
# occupying 40-60% of canvas area. The 3-version design contract requires
# all 3 prompts to encode this, not just the abstract T01 one.
# Validators (in tools/push_pasted_draft.py) grep for these keyphrases in
# each prompt to assert 大字 compliance before push.
HERO_TEXT_KEYPHRASES = ("hero text", "≤6 字", "40-60%")


def build_cover_prompt_block(
    cover_image_prompt: str,
    *,
    title: str = "",
    subtitle: str = "",
    scene_prompt: str | None = None,
    concept_prompt: str | None = None,
    abstract_prompt: str | None = None,
    single: bool = False,
) -> str:
    """產出 markdown 區塊放在 Article_Substack.md 結尾（或注入 ad-hoc draft body）。

    Caller responsibility：把這段 append 進 article markdown。Hsin 收到草稿後
    複製 prompt → ChatGPT image / NanoBanana / Midjourney → 拿圖回來手動上傳。

    兩種 caller 模式：
      (1) **Auto-fanout**：caller 只提供 `cover_image_prompt`、function 自動展成
          場景/概念/抽象三版本（給 LLM 走 compose pipeline 的場景）。
      (2) **Explicit-3**：caller 提供 scene/concept/abstract 三個 prompt（給 PM
          手寫 ad-hoc draft 的場景；tools/push_pasted_draft.py 走這條）。

    為什麼提供 3 個版本（場景 / 概念 / 抽象）：
        text-to-image 不同 prompt 出來差異大，給 3 個方向讓 Hsin 挑 1 個丟生圖。
        場景 = 文章直接畫面；概念 = 視覺隱喻；抽象 = T01 純文字 / T03 chart。

    2026-05-22 aesthetic_tail 換成 v0.2.2 cold-print editorial（從原本 Moleskine
    handdrawn 改）。詳見 BRAND_AESTHETIC_VERSION。對應 config/visual_brand_system.md。

    2026-05-16 enforce 大字 rule: aesthetic_tail 加入 HERO_TEXT_KEYPHRASES
    必須出現的硬規範。每個 prompt（含 scene/concept、不只 abstract）都要有
    hero text 占 40-60% 面積、≤6 字 preferred。
    """
    aesthetic_tail = (
        " — Style: COLD-PRINT EDITORIAL (1950s financial broadsheet). "
        "Background: warm off-white #F2EEE5 (NEVER pure white). "
        "Text + lines: near-black #141414 (NEVER pure #000). "
        "Single accent: sienna red #C84A32, used ONCE total per cover. "
        "Typography (when text appears): Noto Serif TC weight 900 for hero, "
        "JetBrains Mono for kicker/labels. "
        "MANDATORY: hero text must dominate 40-60% of canvas area, "
        "≤6 字 preferred (≤8 max), thumbnail-readable at 60×40 px. "
        "Scene variant: documentary photo as base + large overlaid hero text. "
        "Concept variant: infographic + enlarged core number/concept as hero. "
        "Abstract variant: T01 typography-only, 300-360px hero. "
        "Forbidden: gradients, drop shadows, 3D, glows, neon, anime, cartoon, "
        "cartoon people, faces with visible features, emoji, decorative borders. "
        "If humans appear: backs of heads / side profiles only, no facial emotion. "
        "Aesthetic reference: 1960s Wall Street Journal / 1980s Business Week / "
        "The Economist / Financial Times. Flat 2D editorial print. "
        "Render aspect 16:9 (1456×816 px). "
        f"[BRAND_AESTHETIC_VERSION = {BRAND_AESTHETIC_VERSION}]"
    )

    # Resolve the three slots. If explicit prompts not given, fan out from
    # the single cover_image_prompt as a base + title/subtitle for variants.
    v_scene = (scene_prompt or cover_image_prompt).strip()
    v_concept = (concept_prompt or
        f"用一張視覺隱喻代替文章直接場景，主題：「{title or '(本文主題)'}」"
    ).strip()
    v_abstract = (abstract_prompt or
        f"T01 純文字封面、hero text 從「{title or subtitle or '(本文核心)'}」"
        f"抽 ≤6 字最強短語，Noto Serif TC 900 / 300-360px、"
        f"關鍵 1-2 字 sienna #C84A32 single accent。"
    ).strip()

    # 2026-05-30 (Hsin directive): Substack pipeline wants ONE cover prompt, not
    # three — the 3-version fan-out bloats Article_Substack.md (it's deterministic
    # Python templating, not LLM tokens, but it's noise to scroll past / delete).
    # The manual tool (push_pasted_draft) keeps the 3-version menu (single=False).
    if single:
        return (
            "\n\n---\n\n"
            "## 📸 封面圖 Prompt · 發文前請刪除\n\n"
            "挑這個 prompt 丟 ChatGPT image / NanoBanana / Midjourney → 拿圖回來換掉 "
            "cover.png 再 publish。發文前把整段刪掉。\n\n"
            f"> {v_scene}{aesthetic_tail}\n"
        )

    return (
        "\n\n---\n\n"
        "## 📸 封面圖 Prompt · 發文前請刪除\n\n"
        "PM 替你寫好的 3 版本封面 prompt（全套 v0.2.2 cold-print editorial 美學）。"
        "挑 1 個（或全試）→ 丟 ChatGPT image / NanoBanana / Midjourney → "
        "拿圖回來換掉 cover.png 再 publish。發文前把整段刪掉。\n\n"
        "### 版本 A · 場景式（documentary photo / scene）\n\n"
        f"> {v_scene}{aesthetic_tail}\n\n"
        "### 版本 B · 概念式（visual metaphor / infographic）\n\n"
        f"> {v_concept}{aesthetic_tail}\n\n"
        "### 版本 C · 抽象式（T01 typography-only）\n\n"
        f"> {v_abstract}{aesthetic_tail}\n"
    )


import requests
import urllib.parse

def _download_file(url: str, out_path: Path) -> bool:
    """Download a file from URL to out_path using curl (more robust)."""
    try:
        # Use curl to bypass library-specific blocks (402 Payment Required etc)
        cmd = [
            "curl", "-s", "-L",
            "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-o", str(out_path),
            url
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        return res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1000
    except Exception as exc:
        print(f"[image_brain] ⚠️ Download failed via curl: {exc}")
        return False

import subprocess

async def generate_image_via_pollinations(
    *,
    prompt: str,
    out_path: Path,
    width: int = 1024,
    height: int = 576, # 16:9
    seed: int | None = None,
) -> Optional[Path]:
    """Generate and download an image via Pollinations.ai (FREE)."""
    clean_prompt = prompt.strip().replace("🖼", "").replace("> ", "").replace("視覺位置 ·", "").strip()
    
    # Simpler aesthetic string to avoid URL length issues
    aesthetic = " Style: editorial illustration, financial broadsheet, high contrast, warm paper."
    full_prompt = f"{clean_prompt}. {aesthetic}"
    encoded = urllib.parse.quote(full_prompt)
    
    # Use a clean URL with parameters
    params = {"nologo": "true"}
    if seed:
        params["seed"] = str(seed)
    if width:
        params["width"] = str(width)
    if height:
        params["height"] = str(height)
        
    query_str = urllib.parse.urlencode(params)
    url = f"https://image.pollinations.ai/prompt/{encoded}?{query_str}"
        
    out_path.parent.mkdir(parents=True, exist_ok=True)
    success = await asyncio.to_thread(_download_file, url, out_path)
    return out_path if success else None

# Gemini image-capable models. As of 2026-05, "gemini-2.5-flash-image-preview"
# is the current text-to-image model that works with API-key auth (free tier
# subject to availability). Imagen 3 needs Vertex AI auth, not API key.
DEFAULT_IMAGE_MODEL = os.getenv(
    "SUBSTACK_IMAGE_MODEL",
    "gemini-2.0-flash",
)


async def generate_image(
    *,
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (1024, 576), # 16:9 default
    use_gemini: bool = True,
) -> Optional[Path]:
    """Generate an image via Gemini or Pollinations.ai.

    Args:
        prompt: The visual prompt.
        out_path: Where to save.
        size: Target (width, height).
        use_gemini: Try Gemini first if API key available.

    Returns:
        Path to saved PNG, or None on failure.
    """
    if use_gemini and os.getenv("GEMINI_API_KEY"):
        # Try a few known image-capable model names if the default fails
        models_to_try = [DEFAULT_IMAGE_MODEL, "gemini-2.0-flash-exp", "gemini-2.0-pro-exp"]
        
        for model_name in models_to_try:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
                
                print(f"[image_brain] Attempting Gemini gen with {model_name}...")
                resp = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"], # Only request IMAGE
                    ),
                )
                img_bytes = _extract_inline_image(resp)
                if img_bytes:
                    from PIL import Image
                    from io import BytesIO
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    img = Image.open(BytesIO(img_bytes)).convert("RGB")
                    img = _center_crop_to_aspect(img, size)
                    img = img.resize(size, getattr(Image, "Resampling", Image).LANCZOS)
                    img.save(out_path, "PNG", optimize=True)
                    return out_path
            except Exception as exc:
                print(f"[image_brain] Gemini gen with {model_name} failed: {exc}")

    # Fallback to Pollinations
    print(f"[image_brain] Falling back to Pollinations for prompt: {prompt[:50]}...")
    return await generate_image_via_pollinations(
        prompt=prompt,
        out_path=out_path,
        width=size[0],
        height=size[1]
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
