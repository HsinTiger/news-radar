"""
內文生圖引擎與 🖼 標記處理的單元測試（2026-08-01）

這裡不呼叫任何真實 CLI——``codex`` / ``agy`` 只存在於 Hsin 的 Mac，CI 沒有。
測的是**不依賴 CLI 的那一半**：設定解析、標記剖析、post-condition、
以及「引擎關閉時絕不動草稿」這個最重要的保證。

真實 CLI 行為由 ``scripts/probe_image_engines.py`` 在 Mac 上實測，不在此處假裝。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BASE))

from src import image_engines  # noqa: E402
from substack_radar.compose import (  # noqa: E402
    _parse_visual_marker,
    _safe_slug,
    generate_inline_images,
)

# composer.py:706-711 定義的真實標記格式
MARKER_BLOCK = (
    "> 🖼 視覺位置 · 貨櫃塞港\n"
    "> 場景描述：2026 年 3 月，洛杉磯長灘港外海排隊的貨櫃輪。\n"
    "> 🔍 Path B · Google 搜：「Long Beach port congestion 2026」｜推薦來源：Reuters, WSJ\n"
    "> 🎨 Path C · 生圖 prompt：A black-and-white documentary photograph of container "
    "ships queued outside a port at dawn, 1960s LIFE magazine style, side profile, "
    "high contrast, grainy film, no text"
)

ARTICLE = (
    "# 標題\n\n"
    "開場段落，兌現標題的承諾。\n\n"
    f"{MARKER_BLOCK}\n\n"
    "中段段落，全篇最強的數據對比。\n"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "SUBSTACK_IMAGE_ENGINE",
        "SUBSTACK_IMAGE_MAX_PER_RUN",
        "SUBSTACK_IMAGE_CMD_CODEX",
        "SUBSTACK_IMAGE_CMD_AGY",
        "CODEX_BIN",
        "AGY_BIN",
    ):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------
# 設定解析
# --------------------------------------------------------------------------

def test_engine_off_by_default():
    assert image_engines.engine_name() == "off"
    assert image_engines.resolve_engine() is None


def test_unknown_engine_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("SUBSTACK_IMAGE_ENGINE", "midjourney")
    assert image_engines.engine_name() == "off"
    assert image_engines.resolve_engine() is None


def test_engine_configured_but_binary_absent(monkeypatch, tmp_path):
    """雲端 runner 的情境：設定開了，但這台機器沒裝 CLI → 不是錯誤，是 None。"""
    monkeypatch.setenv("SUBSTACK_IMAGE_ENGINE", "codex")
    monkeypatch.setenv("CODEX_BIN", str(tmp_path / "nope" / "codex"))
    assert image_engines.engine_name() == "codex"
    assert image_engines.resolve_engine() is None
    assert image_engines.engine_status()["bin_exists"] is False


def test_max_per_run_defaults_and_survives_garbage(monkeypatch):
    assert image_engines.max_images_per_run() == 3
    monkeypatch.setenv("SUBSTACK_IMAGE_MAX_PER_RUN", "5")
    assert image_engines.max_images_per_run() == 5
    monkeypatch.setenv("SUBSTACK_IMAGE_MAX_PER_RUN", "抱歉")
    assert image_engines.max_images_per_run() == 3


# --------------------------------------------------------------------------
# 標記剖析 — 2026-08-01 修正的迴歸測試
# --------------------------------------------------------------------------

def test_parse_marker_extracts_path_c_prompt_not_label():
    """迴歸：舊版讀 Path C 的『下一行』，但 prompt 在同一行且 Path C 是最後一行，
    因此永遠抽到空字串、退回用 3-8 字 label 去生圖。"""
    label, prompt = _parse_visual_marker(MARKER_BLOCK)
    assert label == "貨櫃塞港"
    assert prompt.startswith("A black-and-white documentary photograph")
    assert "container ships queued" in prompt
    assert prompt != label
    # Path B 的搜尋字串不該混進生圖 prompt
    assert "Long Beach port congestion" not in prompt


def test_parse_marker_without_path_c_returns_empty_prompt():
    block = "> 🖼 視覺位置 · 只有標題\n> 場景描述：沒有 Path C。"
    label, prompt = _parse_visual_marker(block)
    assert label == "只有標題"
    assert prompt == ""


def test_safe_slug_neutralises_path_hostile_chars():
    assert "/" not in _safe_slug("A/B：測試？")
    assert _safe_slug("") == "visual"
    assert len(_safe_slug("超長" * 40)) <= 24


# --------------------------------------------------------------------------
# post-condition：verify_image
# --------------------------------------------------------------------------

def test_verify_image_rejects_missing_and_empty(tmp_path):
    assert image_engines.verify_image(tmp_path / "nope.png", (64, 32)) is None
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert image_engines.verify_image(empty, (64, 32)) is None


def test_verify_image_rejects_non_image(tmp_path):
    """CLI 常見失敗樣態：回 exit 0，但寫出來的是文字報告不是圖。"""
    fake = tmp_path / "fake.png"
    fake.write_text("I have generated the image for you!", encoding="utf-8")
    assert image_engines.verify_image(fake, (64, 32)) is None


def test_verify_image_normalises_wrong_size(tmp_path):
    """agy 已知會退回 1024×1024 方圖；規格不符要裁切修正而不是丟掉。"""
    from PIL import Image

    p = tmp_path / "square.png"
    Image.new("RGB", (256, 256), (200, 200, 200)).save(p)
    got = image_engines.verify_image(p, (128, 72))
    assert got is not None
    with Image.open(got) as im:
        assert im.size == (128, 72)


# --------------------------------------------------------------------------
# generate_inline_images — 整合行為
# --------------------------------------------------------------------------

def test_engine_off_leaves_markdown_byte_identical(tmp_path):
    """最重要的保證：沒開生圖時，這個函式一個 byte 都不能動。"""
    md = tmp_path / "Article_Substack.md"
    md.write_text(ARTICLE, encoding="utf-8")
    before = md.read_bytes()

    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))

    assert md.read_bytes() == before
    assert not (tmp_path / "inline_images.json").exists()


def _stub_engine(monkeypatch, tmp_path, *, succeed=True):
    """把引擎解析與生圖都換成 stub，測 compose 這一層的行為。"""
    monkeypatch.setattr(image_engines, "resolve_engine", lambda: "codex")
    calls = []

    async def fake_generate(*, prompt, out_path, size):
        calls.append({"prompt": prompt, "out": out_path, "size": size})
        if not succeed:
            return None
        from PIL import Image

        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (240, 238, 229)).save(out_path)
        return out_path

    import src.image_brain as ib

    monkeypatch.setattr(ib, "generate_image", fake_generate)
    return calls


def test_success_embeds_image_and_preserves_prompt(monkeypatch, tmp_path):
    """Hsin 的核心需求：圖要進去，而且要看得出這張圖是照什麼 prompt 生的。"""
    md = tmp_path / "Article_Substack.md"
    md.write_text(ARTICLE, encoding="utf-8")
    calls = _stub_engine(monkeypatch, tmp_path)

    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))

    out = md.read_text(encoding="utf-8")
    # 1) 圖片真的嵌入，而且檔案在磁碟上
    assert "![貨櫃塞港](" in out
    imgs = list(tmp_path.glob("inline_1_*.png"))
    assert len(imgs) == 1 and imgs[0].stat().st_size > 0
    # 2) prompt 保留在 markdown 註解裡
    assert "<!-- inline-image-prompt" in out
    assert "container ships queued" in out
    # 3) 機讀清單也寫了
    manifest = json.loads((tmp_path / "inline_images.json").read_text(encoding="utf-8"))
    assert manifest["images"][0]["label"] == "貨櫃塞港"
    assert manifest["images"][0]["prompt"].startswith("A black-and-white")
    # 4) 送進引擎的是 Path C 的完整 prompt，不是 4 個字的 label
    assert calls[0]["prompt"].startswith("A black-and-white documentary")


def test_failed_generation_preserves_marker_for_manual_use(monkeypatch, tmp_path):
    """生圖失敗不能吃掉標記——Hsin 還要照那個 prompt 手動生。"""
    md = tmp_path / "Article_Substack.md"
    md.write_text(ARTICLE, encoding="utf-8")
    before = md.read_bytes()
    _stub_engine(monkeypatch, tmp_path, succeed=False)

    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))

    assert md.read_bytes() == before
    assert "🎨 Path C" in md.read_text(encoding="utf-8")


def test_rerun_is_idempotent_and_does_not_burn_quota(monkeypatch, tmp_path):
    """重跑 compose 不該把同一張圖再生一次（額度是有限的）。"""
    md = tmp_path / "Article_Substack.md"
    md.write_text(ARTICLE, encoding="utf-8")
    calls = _stub_engine(monkeypatch, tmp_path)

    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))
    after_first = md.read_bytes()
    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))

    assert md.read_bytes() == after_first
    assert len(calls) == 1


def test_budget_cap_limits_generation(monkeypatch, tmp_path):
    md = tmp_path / "Article_Substack.md"
    md.write_text(
        "# 標題\n\n" + f"{MARKER_BLOCK}\n\n" * 4 + "結尾。\n", encoding="utf-8"
    )
    monkeypatch.setenv("SUBSTACK_IMAGE_MAX_PER_RUN", "2")
    calls = _stub_engine(monkeypatch, tmp_path)

    asyncio.run(generate_inline_images(article_md_path=md, output_dir=tmp_path))

    assert len(calls) == 2
    # 沒生到的標記仍原樣保留
    assert md.read_text(encoding="utf-8").count("🎨 Path C") == 2
