"""2026-11-22 WO-5：聲音組不能只找到執行長。查詢分角色、讀頁輪流、同角色／同一人設上限。"""
import json

from substack_radar import research_teams as rt

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


def test_voice_queries_cover_different_roles():
    tpls = rt.QUERY_TEMPLATES["voices"]
    assert len(tpls) == 4
    joined = " ".join(tpls).lower()
    assert "analyst" in joined and "critic" in joined
    assert "competitor" in joined or "rival" in joined


def _fake_search_factory(subject):
    order = rt.role_queries("voices", subject)

    def search(qs, **kw):
        out = []
        for q in qs:
            i = order.index(q)
            for j in range(6):
                out.append({"title": f"{subject} {q}", "url": f"https://q{i}s{j}.example.com/{WORDS[j]}",
                            "snippet": subject, "score": 100 - i * 10 - j, "query_index": i})
        return out
    return search


def test_pages_are_read_round_robin_across_queries():
    read = []

    def fetch(u):
        read.append(u)
        return "Acme news page with no quotes at all. " * 20

    rt.run_role("voices", "Acme", search=_fake_search_factory("Acme"), fetch=fetch,
                chat=lambda p: (json.dumps({"cards": []}), "fake"))
    first4 = {u.split("//")[1].split("s")[0] for u in read[:4]}       # q0 / q1 / ...
    assert len(read) >= 4
    assert len(first4) >= 3, read


def _exec_page(k):
    return " ".join(f'Exec{k} Lastname{k}, the chief executive, said: "Our {w} strategy for Acme and the {WORDS[k]} unit keeps '
                    f'improving every single quarter for loyal customers." ' for w in WORDS)


def test_role_and_speaker_caps():
    pages = {f"https://site{k}.example.com/{WORDS[k]}": _exec_page(k) for k in range(6)}
    search = lambda qs, **kw: [{"title": "Acme", "url": u, "snippet": "Acme", "score": 9}  # noqa: E731
                               for u in pages]

    def chat(prompt):
        k = next(i for i in range(6) if f"site{i}.example.com" in prompt)
        cards = [{"主張": "管理層談策略",
                  "引句": f"Our {w} strategy for Acme and the {WORDS[k]} unit keeps improving every single quarter for loyal customers.",
                  "說話者": f"Exec{k} Lastname{k}", "說話者角色": "管理層"} for w in WORDS[:3]]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("voices", "Acme", search=search, fetch=lambda u: pages[u], chat=chat)
    mgmt = [c for c in rep.cards if c.speaker_role == "管理層"]
    assert 1 <= len(mgmt) <= 3
    per_speaker = {}
    for c in rep.cards:
        per_speaker[c.speaker] = per_speaker.get(c.speaker, 0) + 1
    assert max(per_speaker.values()) <= 2
    rej = rep.stats["rejected"]
    assert rej.get("同角色超過上限", 0) >= 1
    assert rej.get("同一人超過上限", 0) >= 1
