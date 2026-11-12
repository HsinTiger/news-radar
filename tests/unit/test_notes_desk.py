"""2026-11-13 Substack Notes：能接時事就接，接不上或對不上就用原本那則。"""
import json

from substack_radar import notes_desk

ART = "Anthropic 提交 S-1，上半年營收 163 億美元，未來要付 4,750 億美元的算力合約。" * 3
BASE = "Anthropic 上半年營收 163 億美元，卻要付 4,750 億美元的算力合約。" + "這筆帳怎麼算，值得想一想。" * 4
HEAD = [("Anthropic 將在美 IPO，估值上看 1.4 兆美元", "TechNews 科技新報")]


def test_related_news_is_used_when_grounded():
    note = "TechNews 今天報導 Anthropic 將在美 IPO，估值上看 1.4 兆美元。" + "但招股書裡那筆 4,750 億美元的算力合約，才是我想看懂的地方。" * 2
    out = notes_desk.build(article=ART, base_note=BASE, headlines=HEAD,
                           chat=lambda p: json.dumps({"use": 1, "note": note}))
    assert out["news"] == HEAD[0][0] and "1.4" in out["note"]


def test_falls_back_when_unrelated_or_numbers_invented():
    assert notes_desk.build(article=ART, base_note=BASE, headlines=HEAD,
                            chat=lambda p: json.dumps({"use": 0, "note": BASE}))["note"] == BASE
    invented = "今天 Anthropic 宣布 IPO，募資 999 億美元。" + "這個數字值得想一想，它說明了很多事情。" * 4
    out = notes_desk.build(article=ART, base_note=BASE, headlines=HEAD,
                           chat=lambda p: json.dumps({"use": 1, "note": invented}))
    assert out == {"note": BASE, "news": ""}


def test_no_headlines_or_model_failure_keeps_base():
    assert notes_desk.build(article=ART, base_note=BASE, headlines=[])["note"] == BASE

    def boom(p):
        raise RuntimeError("down")
    assert notes_desk.build(article=ART, base_note=BASE, headlines=HEAD, chat=boom)["note"] == BASE
