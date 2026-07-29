from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from src.publisher import (
    publish_fb_carousel,
    publish_ig_carousel,
    publish_threads_carousel,
)
from src.schema import CarouselCards
from substack_radar.cards import ASPECTS, build_cards, render_cards


def _carousel() -> CarouselCards:
    return CarouselCards(
        insight_statement="食藥署公布可驗證的調查結論",
        insight_support="公告列出原料、製程與檢驗三個缺口",
        source_attribution="來源：食藥署第三方調查報告",
        stat_number="3項",
        stat_caption="報告列出的主要缺口",
        takeaways=["核對產品批號", "追蹤修法進度"],
        reader_question="你會先查產品批號還是修法進度？",
    )


def test_builder_emits_exact_governed_sequence() -> None:
    cards = build_cards(
        title="食藥署公布調查結果",
        subtitle="",
        carousel=_carousel(),
    )

    assert [card["type"] for card in cards] == ["cover", "evidence", "action"]
    assert cards[1]["source"].startswith("來源：食藥署")
    assert cards[2]["question"].endswith("？")


def test_builder_fails_closed_on_incomplete_content() -> None:
    incomplete = CarouselCards(
        insight_statement="只有一句判讀",
        insight_support="",
        takeaways=[],
    )

    assert build_cards(title="不完整", subtitle="", carousel=incomplete) == []
    assert build_cards(title="", subtitle="", carousel=_carousel()) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"source_attribution": None},
        {"reader_question": None},
        {"reader_question": "你先查批號？還是先看修法？"},
        {"takeaways": ["只有一個動作"]},
    ],
)
def test_builder_rejects_semantically_incomplete_cards(updates: dict) -> None:
    carousel = _carousel().model_copy(update=updates)

    assert build_cards(title="食安調查有三個缺口", subtitle="", carousel=carousel) == []


@pytest.mark.parametrize("platform", ["fb", "ig", "threads"])
def test_renderer_outputs_three_native_size_pngs(
    monkeypatch, tmp_path, platform: str
) -> None:
    monkeypatch.setenv("META_CHARACTER_COVER", "0")
    cards = build_cards(title="食安調查有三個缺口", subtitle="", carousel=_carousel())

    paths = render_cards(
        cards=cards,
        topic_category="food_safety",
        aspect=platform,
        output_dir=tmp_path / platform,
    )

    assert len(paths) == 3
    assert [path.name for path in paths] == [
        f"card_{platform}_1.png",
        f"card_{platform}_2.png",
        f"card_{platform}_3.png",
    ]
    assert all(Image.open(path).size == ASPECTS[platform] for path in paths)


def test_renderer_rejects_wrong_count_or_order(tmp_path) -> None:
    cards = build_cards(title="食安調查有三個缺口", subtitle="", carousel=_carousel())

    with pytest.raises(ValueError, match="exactly cover,evidence,action"):
        render_cards(
            cards=cards[:2],
            topic_category="food_safety",
            aspect="ig",
            output_dir=tmp_path,
        )
    with pytest.raises(ValueError, match="exactly cover,evidence,action"):
        render_cards(
            cards=[cards[0], cards[2], cards[1]],
            topic_category="food_safety",
            aspect="ig",
            output_dir=tmp_path,
        )


def test_renderer_blocks_when_required_mascot_cannot_render(
    monkeypatch,
    tmp_path,
) -> None:
    import src.character_cover_meta as character_cover

    monkeypatch.setenv("META_CHARACTER_COVER", "1")
    monkeypatch.setattr(character_cover, "compose_meta_character_cover", lambda **_kwargs: None)
    cards = build_cards(title="食安調查有三個缺口", subtitle="", carousel=_carousel())

    with pytest.raises(RuntimeError, match="mascot cover render returned no image"):
        render_cards(
            cards=cards,
            topic_category="food_safety",
            aspect="ig",
            output_dir=tmp_path,
        )


@pytest.mark.parametrize(
    ("publisher", "copy"),
    [
        (publish_fb_carousel, "caption"),
        (publish_ig_carousel, "caption"),
        (publish_threads_carousel, "caption"),
    ],
)
def test_publishers_reject_non_three_card_payloads_without_network(
    publisher, copy: str
) -> None:
    result = asyncio.run(publisher(["https://example.com/1.png"] * 2, copy))

    assert result["success"] is False
    assert "恰好 3 張" in result["error"]["local_reject"]


@pytest.mark.parametrize(
    ("relative_path", "guard_marker"),
    [
        (
            "scripts/first_batch_publish.py",
            "Legacy first_batch_publish live path is retired",
        ),
        (
            "tools/emergency_oneshot.py",
            "Legacy emergency live publishing is retired",
        ),
        (
            "tools/retry_publish.py",
            "Legacy single-image retry is retired",
        ),
        (
            "scripts/publish_reel.py",
            "LIVE_REEL_PUBLISHING_RETIRED",
        ),
    ],
)
def test_legacy_single_image_entrypoints_are_retired(
    relative_path: str,
    guard_marker: str,
) -> None:
    source = (Path(__file__).resolve().parents[2] / relative_path).read_text(
        encoding="utf-8"
    )

    assert guard_marker in source
