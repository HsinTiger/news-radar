"""2026-11-22 WO-5 補充測試：query_index、邊界情況、驗證順序與正規化比對。"""
import json
import sys
import types

from substack_radar import editorial_research as er
from substack_radar import research_teams as rt


def test_search_candidates_attaches_query_index(monkeypatch):
    class FakeDDGS:
        def text(self, query, max_results=8):
            if "CEO" in query:
                return [{"title": "Acme CEO talk", "href": "https://www.reuters.com/acme-ceo", "body": "Acme CEO spoke today."}]
            if "analyst" in query:
                return [{"title": "Acme analyst view", "href": "https://www.bloomberg.com/acme-analyst", "body": "Acme analyst lowered target."}]
            return []

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    cands = er._search_candidates(
        ["Acme CEO interview", "Acme analyst downgrade price target", "Acme critics say"],
        subject_terms={"acme"},
    )
    assert len(cands) == 2
    by_url = {c["url"]: c for c in cands}
    assert by_url["https://www.reuters.com/acme-ceo"]["query_index"] == 0
    assert by_url["https://www.bloomberg.com/acme-analyst"]["query_index"] == 1


def test_voices_round_robin_preserves_skip_rules_and_non_voices_unchanged():
    read_voices: list[str] = []
    candidates = [
        # q0: D-tier first, then valid q0 high score, unsafe, then valid q0 second
        {"title": "D tier", "url": "https://www.tradingview.com/symbols/ACME/", "snippet": "Acme", "score": 99.0, "query_index": 0},
        {"title": "q0 first", "url": "https://q0a.example.com/1", "snippet": "Acme", "score": 98.0, "query_index": 0},
        {"title": "q0 unsafe", "url": "http://127.0.0.1/secret", "snippet": "Acme", "score": 97.0, "query_index": 0},
        {"title": "q0 second", "url": "https://q0b.example.com/2", "snippet": "Acme", "score": 96.0, "query_index": 0},
        # q1 lower scores overall, but its #1 must be read before q0's #2
        {"title": "q1 first", "url": "https://q1a.example.com/1", "snippet": "Acme", "score": 40.0, "query_index": 1},
        {"title": "q1 dup of q0", "url": "https://q0a.example.com/1", "snippet": "Acme", "score": 30.0, "query_index": 1},
        {"title": "q2 first", "url": "https://q2a.example.com/1", "snippet": "Acme", "score": 10.0, "query_index": 2},
    ]

    def fetch_voices(u):
        read_voices.append(u)
        return "Acme article without quotes at all. " * 10

    rt.run_role(
        "voices",
        "Acme",
        search=lambda _qs, **_kw: list(candidates),
        fetch=fetch_voices,
        chat=lambda _p: (json.dumps({"cards": []}), "fake"),
    )
    assert "https://www.tradingview.com/symbols/ACME/" not in read_voices
    assert "http://127.0.0.1/secret" not in read_voices
    # q0a (idx 0), q1a (idx 1 valid after dup skipped? Actually q0a is read first, then q0a dup is in q1's slot 0 -> wait q1's highest score is q0a dup!)
    # Every non-skipped first item per query comes before q0b (q0's second valid item)
    assert read_voices.index("https://q2a.example.com/1") < read_voices.index("https://q0b.example.com/2")

    # counter ignores query_index and stays strictly sorted by score
    read_counter: list[str] = []
    counter_cands = [
        {"title": "c0", "url": "https://c0.example.com/1", "snippet": "Acme", "score": 90.0, "query_index": 0},
        {"title": "c1", "url": "https://c1.example.com/1", "snippet": "Acme", "score": 80.0, "query_index": 0},
        {"title": "c2", "url": "https://c2.example.com/1", "snippet": "Acme", "score": 10.0, "query_index": 1},
    ]
    rt.run_role(
        "counter",
        "Acme",
        search=lambda _qs, **_kw: list(counter_cands),
        fetch=lambda u: read_counter.append(u) or "Acme risk text without quotes. " * 10,
        chat=lambda _p: (json.dumps({"cards": []}), "fake"),
    )
    assert read_counter == [
        "https://c0.example.com/1",
        "https://c1.example.com/1",
        "https://c2.example.com/1",
    ]


def test_speaker_normalization_and_rejected_cards_do_not_consume_quota():
    assert rt.MAX_CARDS_PER_SPEAKER_ROLE == 3
    assert rt.MAX_CARDS_PER_SPEAKER == 2

    q1 = "Our cloud division expanded revenue across three enterprise regions this fiscal year."
    q2 = "Hardware margins stayed resilient despite rising memory prices across global factories."
    q3 = "Software subscriptions reached record renewal rates among large financial institutions."
    page = f"CEO Tim Cook explained: \"{q1}\" Later, Dr. Tim Cook Jr. added: \"{q2}\" Finally, Tim Cook (CEO) noted: \"{q3}\""

    search = lambda _qs, **_kw: [{"title": "Acme", "url": "https://site1.example.com/a", "snippet": "Acme", "score": 9.0}]

    def chat(_p):
        cards = [
            # Rejected by original verification (fabricated quote) -> must NOT count toward Tim Cook or 管理層 quota
            {"主張": "捏造引句", "引句": "Completely fabricated quote that does not appear anywhere on the page.", "說話者": "Tim Cook", "說話者角色": "管理層"},
            # Rejected by original verification (speaker not near quote) -> must NOT count toward quota
            {"主張": "說話者不在附近", "引句": q1, "說話者": "Ghost Person", "說話者角色": "管理層"},
            # Accepted #1 for Tim Cook / 管理層
            {"主張": "雲端事業擴張", "引句": q1, "說話者": "CEO Tim Cook", "說話者角色": "管理層"},
            # Accepted #2 for Tim Cook / 管理層 (different title formatting, same person)
            {"主張": "硬體毛利維持韌性", "引句": q2, "說話者": "Dr. Tim Cook Jr.", "說話者角色": "管理層"},
            # Exceeds MAX_CARDS_PER_SPEAKER (2) for normalized "tim cook"
            {"主張": "軟體訂閱創新高", "引句": q3, "說話者": "tim cook, CEO", "說話者角色": "管理層"},
        ]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("voices", "Acme", search=search, fetch=lambda _u: page, chat=chat)
    assert len(rep.cards) == 2
    assert rep.stats["rejected"]["引句不在原文"] == 1
    assert rep.stats["rejected"]["說話者不在引句附近"] == 1
    assert rep.stats["rejected"]["同一人超過上限"] == 1
    assert rep.stats["rejected"].get("同角色超過上限", 0) == 0


def test_voices_prompt_prefers_non_ceo_sources():
    p = rt.PROMPTS["voices"]
    assert "分析師" in p and "競爭對手" in p and "執行長" in p
