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
import json
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple


def _is_quota_error(err: str) -> bool:
    e = err.lower()
    return ("429" in e) or ("resource_exhausted" in e) or ("quota" in e) or ("rate limit" in e)


def _gemini_cli_dirs() -> list[str]:
    """多帳號輪替用的 HOME 目錄清單（GEMINI_CLI_CONFIG_DIRS，逗號分隔，按優先序）。"""
    raw = os.getenv("GEMINI_CLI_CONFIG_DIRS", "").split(",")
    dirs = [d.strip() for d in raw if d.strip()]
    return dirs if dirs else [""]


# NOTE (2026-06-21): module-level `from google import genai` removed — it crashed
# `import src.image_brain` whenever google-genai wasn't installed, which silently
# disabled the cover-prompt block (compose.py caught the ImportError). The legacy
# gen path imports google lazily inside generate_cover_image(), so prompt-only mode
# (build_cover_prompt_block) now imports cleanly with zero heavy deps.

def _get_api_keys() -> list[str]:
    """取得所有可用的 API Keys，供失敗時輪替。"""
    keys = []
    # 嘗試抓取可能設定的 Pro Key (雖然目前沒有)
    for k in ["GEMINI_PRO_KEY_TINGSYUAN", "GEMINI_PRO_KEY_HSIN"]:
        val = os.getenv(k)
        if val and val.strip():
            keys.append(val.strip())
            
    # 抓取 .env 中既有的 免費/一般 Key
    for k in ["GEMINI_API_KEY", "GEMINI_API_KEY_2"]:
        val = os.getenv(k)
        if val and val.strip():
            # 支援逗號分隔
            keys.extend([x.strip() for x in val.split(",") if x.strip()])
            
    # 去重並保留順序
    seen = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out

async def generate_image(
    *,
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (1024, 576),  # 16:9 default
) -> Optional[Path]:
    """誠實回退模式：因目前 Google AI Studio API 不支援直接呼叫 Imagen 3，在此直接回傳 None。
    
    2026-06-01: 經過完整驗證，Gemini API Key (含 Pro) 在目前的 v1beta/v1alpha 
    API 端點中，皆會對 imagen-3.0-generate-001 報 404 錯誤。
    為了遵守「誠實且不可欺瞞」的原則，絕不使用外部免費 API (如 Pollinations) 偷底。
    
    因此，本函數直接放棄自動生圖，保留 Markdown 中的 prompt 標記，交由使用者後續手動於 Web UI 生成。
    """
    print(f"[image_brain] ⚠️ Skipping image generation (API limitation). Falling back to text prompt only.")
    return None


def _get_aesthetic_tail() -> str:
    """2026-05-22 aesthetic_tail 換成 v0.2.2 cold-print editorial（從原本 Moleskine
    handdrawn 改）。詳見 BRAND_AESTHETIC_VERSION。對應 config/visual_brand_system.md。

    2026-05-16 enforce 大字 rule: aesthetic_tail 加入 HERO_TEXT_KEYPHRASES
    必須出現的硬規範。每個 prompt（含 scene/concept、不只 abstract）都要有
    hero text 占 40-60% 面積、≤6 字 preferred。
    """
    return (
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
        "[BRAND_AESTHETIC_VERSION = v0.2.2]"
    )


# ==========================================================================
# Cover IP — 2-character claymation system (2026-06-21, Hsin directive)
# ==========================================================================
# 每篇 Substack 封面固定出現同一個可愛又專業的 IP（robot=瑞瑞 機器人 / owl=達達 貓頭鷹），
# 搭配當篇場景 → 品牌一致 + 標題更有帶入感。撰稿 AI 動態挑角色 + 寫一句 scene；
# 固定造型 + 黏土美學由這裡統一補上（模型不必每次重畫，省 token、避免走樣）。
# 完整人設文件：substack_radar/config/cover_characters.md。
# 代號：robot / owl 是 species 代號（name-proof，永不隨改名動）；顯示名在 display 欄（瑞瑞/達達，暫定）。

_CLAY_STYLE_TAIL = (
    " — Style: warm soft-clay claymation miniature, tilt-shift macro, soft diffused "
    "studio light, tactile hand-molded fingerprint texture, rounded chunky forms, "
    "medium detail. Palette: paper-cream background #F2EEE5, ink-black #141414, ONE "
    "sienna-red #C84A32 accent used once, muted stone-grey #8A8378. Cute but credible "
    "(GitHub-Octocat-level charm, NOT babyish, NOT chibi-overload). Single subject, "
    "centered, generous negative space for a title overlay. No text, no watermark, "
    "no logo. Render aspect 16:9 (1456×816 px). [COVER_IP_VERSION = v1.0]"
)

# Canonical look — Python owns this so the model never has to redraw the character.
CHARACTERS: dict = {
    "robot": {
        "display": "瑞瑞 · 單眼雷達機器人（好奇探索）",  # 顯示名；改名只動這欄
        "look": (
            "A chunky rounded desk-robot analyst made of soft matte clay, stone-grey "
            "#8A8378 body, a small spinning radar-dish antenna on its head emitting a "
            "tiny 'ping!' spark, one big glossy single lens-eye that sparkles, stubby "
            "articulated arms, a sienna-red #C84A32 knitted scarf, squash-and-stretch "
            "rubbery lively posing"
        ),
        "default_pose": (
            "thrusting forward in a 'gotcha!' pose, holding a magnifying glass that "
            "blows its single lens-eye up huge and sparkling, smug little grin"
        ),
    },
    "owl": {
        "display": "達達 · 雷達貓頭鷹（智慧洞察）",  # 顯示名；改名只動這欄
        "look": (
            "A plump rounded owl made of soft matte clay, warm stone-grey #8A8378 "
            "feathers with hand-molded texture, two huge radar-dish eyes behind round "
            "wire spectacles, a small sienna-red #C84A32 bow-tie scarf, stubby wings, "
            "feathers puffed up, theatrical squash-and-stretch posing"
        ),
        "default_pose": (
            "in an 'ah-ha!' burst with feathers flung open and both wings thrown up, "
            "one eye magnified huge through a magnifying glass, delighted realization"
        ),
    },
}

# Topics that lean robot/瑞瑞 (hard tech / data / financials). Everything else → owl/達達.
_ROBOT_TOPICS = {
    "us_stocks", "tw_stocks", "ai_model", "ai_agent", "ai_application",
    "tech_product_launch", "supply_chain", "earnings",
}


def pick_character(topic_category=None, mode=None) -> str:
    """Deterministic fallback when the model didn't choose a character. mode wins
    over topic: company→robot, podcast→owl; else hard-tech topics→robot, rest→owl."""
    if mode == "company":
        return "robot"
    if mode == "podcast":
        return "owl"
    if (topic_category or "") in _ROBOT_TOPICS:
        return "robot"
    return "owl"


def build_cover_prompt_block(
    scene=None,
    *,
    character=None,
    title=None,
    subtitle=None,
    topic_category=None,
    mode=None,
    single=True,
    # --- legacy manual 3-version path (substack_radar/push_pasted_draft.py) ---
    cover_image_prompt=None,
    scene_prompt=None,
    concept_prompt=None,
    abstract_prompt=None,
) -> str:
    """Assemble the cover-image prompt markdown block.

    Two modes, auto-dispatched:

    (1) **Character IP path (default, auto pipeline)** — the model supplies only
        ``character`` (robot/owl) + a short ``scene`` (what the IP is doing in THIS
        article's setting). Python supplies the canonical look + clay style bible,
        so the IP stays on-model every time and the model spends ~zero tokens
        redrawing it. Missing/invalid ``character`` → ``pick_character(topic, mode)``
        so the cover never opens a hole.

    (2) **Legacy 3-version cold-print path** — triggered when any of
        ``scene_prompt``/``concept_prompt``/``abstract_prompt`` is passed (only
        push_pasted_draft.py does this). Preserves the old cold-print editorial
        output + the " — Style: COLD-PRINT EDITORIAL" sentinel that tool greps for.
    """
    if scene_prompt is not None or concept_prompt is not None or abstract_prompt is not None:
        return _build_legacy_cover_block(
            cover_image_prompt=cover_image_prompt or "",
            title=title, subtitle=subtitle,
            scene_prompt=scene_prompt, concept_prompt=concept_prompt,
            abstract_prompt=abstract_prompt, single=single,
        )

    char_key = character if character in CHARACTERS else pick_character(topic_category, mode)
    char = CHARACTERS[char_key]
    scene = (scene or "").strip()
    if not scene:
        # No model scene → fall back to the character's signature pose + a title hint.
        scene = f"{char['default_pose']}; evoking the theme 「{title or subtitle or '本文主題'}」"
    full = f"{char['look']}, {scene}{_CLAY_STYLE_TAIL}"
    return (
        "\n\n---\n\n"
        "## 📸 封面圖 Prompt · 發文前請刪除\n\n"
        f"**本篇出場角色：{char['display']}**　"
        "（複製下面整段丟 ChatGPT image / NanoBanana / Midjourney → 拿圖換掉 cover.png "
        "再 publish；發文前把這段刪掉。）\n\n"
        f"> {full}\n"
    )


def _build_legacy_cover_block(
    *, cover_image_prompt, title, subtitle,
    scene_prompt, concept_prompt, abstract_prompt, single,
) -> str:
    """Old cold-print editorial cover block (3-version fan-out). Kept verbatim so
    the manual paste tool (push_pasted_draft.py) and its COLD-PRINT sentinel still
    work. New auto pipeline uses the character path above instead."""
    aesthetic_tail = _get_aesthetic_tail()
    v_scene = (scene_prompt or cover_image_prompt or "").strip()
    v_concept = (concept_prompt or
        f"用一張視覺隱喻代替文章直接場景，主題：「{title or '(本文主題)'}」"
    ).strip()
    v_abstract = (abstract_prompt or
        f"T01 純文字封面、hero text 從「{title or subtitle or '(本文核心)'}」"
        f"抽 ≤6 字最強短語，Noto Serif TC 900 / 300-360px、"
        f"關鍵 1-2 字 sienna #C84A32 single accent。"
    ).strip()
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


# Gemini image-capable models. As of 2026-05, "gemini-2.5-flash-image-preview"
# is the current text-to-image model that works with API-key auth (free tier
# subject to availability). Imagen 3 needs Vertex AI auth, not API key.
DEFAULT_IMAGE_MODEL = os.getenv(
    "SUBSTACK_IMAGE_MODEL",
    "gemini-2.0-flash",
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
