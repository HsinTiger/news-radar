"""2026-11-21 週末旗艦第 2 步：調研小組（voices／counter／timeline）單元測試。"""
import datetime as dt
import json

from substack_radar import research_teams as rt
from substack_radar.editorial_research import ResearchSource


def _voices_pages():
    p1 = (
        "Apple CEO Tim Cook spoke at the conference on Tuesday. "
        "\"Demand for services and enterprise devices grew 18% across Asia this year, "
        "and developers are building more tools on our silicon,\" Cook told reporters. "
        "Later in the same interview, longtime critic John Gruber argued differently. "
        "\"The App Store fee structure continues to alienate independent software makers "
        "who want direct billing flexibility with customers,\" Gruber wrote in his column."
    )
    p2 = (
        "Morgan Stanley analyst Katy Huberty raised her channel forecast in a note. "
        "\"Supply chain checks point to steady shipments through the fourth quarter, "
        "with flagship orders holding above expectations,\" Huberty said on Friday."
    )
    return {
        "https://www.reuters.com/tech/apple-ceo-remarks": p1,
        "https://www.bloomberg.com/news/apple-analyst-note": p2,
    }


def _counter_pages():
    p1 = (
        "Federal regulators opened a formal review into app marketplace practices. "
        "\"The Department of Justice alleged that restrictive developer contracts prevented "
        "competing payment services from gaining traction in mobile ecosystems,\" court filings show."
    )
    p2 = (
        "A short seller report published on Monday challenged hardware margin assumptions. "
        "\"Rising component prices and intensifying smartphone competition in China could "
        "compress hardware gross margins by 200 basis points next year,\" the report warned."
    )
    p3 = (
        "Consumer advocates criticized repair restrictions across newer laptop models. "
        "\"Independent repair shops still face steep part serialization barriers that raise "
        "replacement costs for everyday customers,\" the coalition said in its statement."
    )
    return {
        "https://www.reuters.com/legal/apple-doj": p1,
        "https://www.wsj.com/tech/apple-margin-risk": p2,
        "https://www.theverge.com/tech/apple-repair-critics": p3,
    }


def _timeline_pages():
    p1 = (
        "Founded in 1976 inside a suburban garage, the company initially sold kit computers to hobbyists. "
        "\"Jobs and Wozniak built the first circuit boards by hand to prove personal computing "
        "could leave research labs and enter living rooms,\" the company biography notes."
    )
    p2 = (
        "In 2007, Apple unveiled a touchscreen phone that reshaped mobile computing. "
        "\"Combining an iPod, a phone, and an internet communicator into one device turned "
        "hardware design into a software platform battleground,\" industry historians observed."
    )
    p3 = (
        "By 2020, management decided to replace third-party desktop processors. "
        "\"Transitioning Macs to custom in-house silicon unified battery efficiency and "
        "performance across the entire product lineup,\" engineers explained in the launch briefing."
    )
    return {
        "https://www.sec.gov/Archives/edgar/data/1/apple-history": p1,
        "https://www.ft.com/content/apple-2007-iphone": p2,
        "https://www.cnbc.com/2020/apple-silicon-shift": p3,
    }


def test_all_three_roles_extract_verify_and_meet_criteria():
    pages = {**_voices_pages(), **_counter_pages(), **_timeline_pages()}

    def fake_search(queries, **_kw):
        q = " ".join(queries)
        if "CEO interview" in q:
            return [
                {"title": "Reuters CEO", "url": "https://www.reuters.com/tech/apple-ceo-remarks", "snippet": "s", "score": 9.0},
                {"title": "Bloomberg Analyst", "url": "https://www.bloomberg.com/news/apple-analyst-note", "snippet": "s", "score": 8.0},
            ]
        if "criticism" in q:
            return [
                {"title": "Reuters DOJ", "url": "https://www.reuters.com/legal/apple-doj", "snippet": "s", "score": 9.0},
                {"title": "WSJ Margin", "url": "https://www.wsj.com/tech/apple-margin-risk", "snippet": "s", "score": 8.0},
                {"title": "Verge Repair", "url": "https://www.theverge.com/tech/apple-repair-critics", "snippet": "s", "score": 7.0},
            ]
        return [
            {"title": "SEC History", "url": "https://www.sec.gov/Archives/edgar/data/1/apple-history", "snippet": "s", "score": 9.0},
            {"title": "FT 2007", "url": "https://www.ft.com/content/apple-2007-iphone", "snippet": "s", "score": 8.0},
            {"title": "CNBC 2020", "url": "https://www.cnbc.com/2020/apple-silicon-shift", "snippet": "s", "score": 7.0},
        ]

    def fake_chat(prompt):
        if "apple-ceo-remarks" in prompt:
            cards = [
                {
                    "主張": "服務與企業裝置在亞洲成長 18%",
                    "引句": "Demand for services and enterprise devices grew 18% across Asia this year, and developers are building more tools on our silicon,",
                    "說話者": "Tim Cook",
                    "說話者角色": "管理層",
                },
                {
                    "主張": "抽成制度持續引發獨立開發者不滿",
                    "引句": "The App Store fee structure continues to alienate independent software makers who want direct billing flexibility with customers,",
                    "說話者": "John Gruber",
                    "說話者角色": "批評者",
                },
            ]
        elif "apple-doj" in prompt:
            cards = [{"主張": "司法部指控合約限制競爭支付服務", "引句": "The Department of Justice alleged that restrictive developer contracts prevented competing payment services from gaining traction in mobile ecosystems,", "嚴重度": "少見但致命"}]
        elif "apple-margin-risk" in prompt:
            cards = [{"主張": "零組件漲價與中國競爭可能壓縮毛利 200 個基點", "引句": "Rising component prices and intensifying smartphone competition in China could compress hardware gross margins by 200 basis points next year,", "嚴重度": "常見但輕"}]
        elif "apple-repair-critics" in prompt:
            cards = [{"主張": "獨立維修商面臨零件序號鎖定限制", "引句": "Independent repair shops still face steep part serialization barriers that raise replacement costs for everyday customers,", "嚴重度": "常見但輕"}]
        elif "apple-history" in prompt:
            cards = [{"主張": "1976 年在車庫手工打造首批主機板創立公司", "引句": "Jobs and Wozniak built the first circuit boards by hand to prove personal computing could leave research labs and enter living rooms,", "日期": "1976", "事件": "創立公司"}]
        elif "apple-2007-iphone" in prompt:
            cards = [{"主張": "2007 年推出觸控手機重塑行動運算", "引句": "Combining an iPod, a phone, and an internet communicator into one device turned hardware design into a software platform battleground,", "日期": "2007", "事件": "推出 iPhone"}]
        else:
            cards = [{"主張": "2020 年將桌上型電腦轉向自研晶片", "引句": "Transitioning Macs to custom in-house silicon unified battery efficiency and performance across the entire product lineup,", "日期": "2020", "事件": "轉向自研晶片"}]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake-fast"

    reports = {
        role: rt.run_role(role, "Apple", search=fake_search, fetch=pages.__getitem__, chat=fake_chat)
        for role in rt.ROLES
    }

    assert reports["voices"].met is True and len({c.speaker_role for c in reports["voices"].cards}) >= 2
    assert reports["counter"].met is True and len(reports["counter"].cards) >= 3
    assert reports["timeline"].met is True and len(reports["timeline"].cards) >= 3

    sources = rt.as_research_sources(reports)
    assert len(sources) == 7
    by_url = {s["url"]: s for s in sources}
    assert by_url["https://www.sec.gov/Archives/edgar/data/1/apple-history"]["evidence_role"] == "official"
    assert by_url["https://www.reuters.com/legal/apple-doj"]["evidence_role"] == "countercase"
    assert by_url["https://www.reuters.com/tech/apple-ceo-remarks"]["evidence_role"] == "analysis"
    for item in sources:
        ResearchSource.model_validate(item)


def test_fabricated_quote_and_hallucinated_numbers_rejected():
    page = (
        "Apple CFO Luca Maestri told investors on Thursday that hardware sales fell 4.3 billion dollars "
        "in the quarter while cloud subscriptions rose 12% year over year across enterprise accounts."
    )
    search = lambda _q: [{"title": "Reuters", "url": "https://www.reuters.com/a", "snippet": "s", "score": 9.0}]

    def fake_chat(_p):
        cards = [
            # 1. 捏造的引句
            {
                "主張": "在台北蓋新晶圓廠",
                "引句": "Apple confirmed it will construct three wafer fabs in Taipei by next summer.",
                "說話者": "Luca Maestri",
                "說話者角色": "管理層",
            },
            # 2. 數字不在引句（寫 85% 但原文是 12%）
            {
                "主張": "雲端訂閱年增 85%",
                "引句": "hardware sales fell 4.3 billion dollars in the quarter while cloud subscriptions rose 12% year over year across enterprise accounts.",
                "說話者": "Luca Maestri",
                "說話者角色": "管理層",
            },
            # 3. 5% 不能誤配到 50%（這裡寫 2% 但原文只有 12%）
            {
                "主張": "訂閱只成長 2%",
                "引句": "hardware sales fell 4.3 billion dollars in the quarter while cloud subscriptions rose 12% year over year across enterprise accounts.",
                "說話者": "Luca Maestri",
                "說話者角色": "管理層",
            },
            # 4. 單位換算：43 億美元 == 4.3 billion dollars，12% == 12% -> 應通過
            {
                "主張": "硬體銷售減少 43 億美元，雲端訂閱年增 12%",
                "引句": "hardware sales fell 4.3 billion dollars in the quarter while cloud subscriptions rose 12% year over year across enterprise accounts.",
                "說話者": "Luca Maestri",
                "說話者角色": "管理層",
            },
        ]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("voices", "Apple", search=search, fetch=lambda _u: page, chat=fake_chat)
    assert len(rep.cards) == 1
    assert "43 億" in rep.cards[0].claim
    assert rep.stats["rejected"]["引句不在原文"] == 1
    assert rep.stats["rejected"]["數字不在引句"] == 2


def test_voices_speaker_proximity_check():
    far_text = (
        "Executive Tim Cook opened the keynote with product highlights. "
        + ("Filler industry background paragraph describing supply chains and retail stores. " * 12)
        + "Meanwhile, global tablet demand softened noticeably as corporate buyers delayed hardware upgrades across Europe."
    )
    near_text = (
        "In his closing remarks, Cook emphasized long-term silicon investments. "
        "\"We continue investing heavily in custom silicon design and energy efficiency across every product line,\" he noted."
    )
    pages = {
        "https://www.reuters.com/far": far_text,
        "https://www.bloomberg.com/near": near_text,
    }
    search = lambda _q: [
        {"title": "Far", "url": "https://www.reuters.com/far", "snippet": "s", "score": 9.0},
        {"title": "Near", "url": "https://www.bloomberg.com/near", "snippet": "s", "score": 8.0},
    ]

    def fake_chat(prompt):
        if "reuters.com/far" in prompt:
            cards = [{
                "主張": "歐洲企業買家延後平板升級",
                "引句": "Meanwhile, global tablet demand softened noticeably as corporate buyers delayed hardware upgrades across Europe.",
                "說話者": "Tim Cook",
                "說話者角色": "管理層",
            }]
        else:
            cards = [{
                "主張": "持續加碼自研晶片投資",
                "引句": "We continue investing heavily in custom silicon design and energy efficiency across every product line,",
                "說話者": "Tim Cook",  # 網頁只寫姓氏 Cook，且在 400 字內 → 應通過
                "說話者角色": "管理層",
            }]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("voices", "Apple", search=search, fetch=pages.__getitem__, chat=fake_chat)
    assert len(rep.cards) == 1
    assert rep.cards[0].url == "https://www.bloomberg.com/near"
    assert rep.stats["rejected"]["說話者不在引句附近"] == 1


def test_timeline_year_proximity_check():
    far_text = (
        "Back in 2011, leadership reorganized regional sales teams. "
        + ("Extensive commentary on quarterly inventory management and distribution partners. " * 8)
        + "The company eventually transitioned its logistics network to direct regional fulfillment hubs."
    )
    near_text = (
        "In March 2018, management announced a major shift toward recurring services. "
        "\"Subscriptions across music, cloud storage, and payments became the fastest-growing profit engine,\" analysts noted."
    )
    pages = {
        "https://www.wsj.com/far": far_text,
        "https://www.ft.com/near": near_text,
    }
    search = lambda _q: [
        {"title": "Far", "url": "https://www.wsj.com/far", "snippet": "s", "score": 9.0},
        {"title": "Near", "url": "https://www.ft.com/near", "snippet": "s", "score": 8.0},
    ]

    def fake_chat(prompt):
        if "wsj.com/far" in prompt:
            cards = [{
                "主張": "2011 年改為直營區域物流中心",
                "引句": "The company eventually transitioned its logistics network to direct regional fulfillment hubs.",
                "日期": "2011",
                "事件": "物流轉型",
            }]
        else:
            cards = [{
                "主張": "2018 年把營運重心轉往訂閱服務",
                "引句": "Subscriptions across music, cloud storage, and payments became the fastest-growing profit engine,",
                "日期": "2018-03",
                "事件": "2018 年轉向訂閱服務",
            }]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("timeline", "Apple", search=search, fetch=pages.__getitem__, chat=fake_chat)
    assert len(rep.cards) == 1
    assert rep.cards[0].date == "2018-03"
    assert rep.stats["rejected"]["年份不在引句附近"] == 1


def test_domain_limits_and_tier_d_and_ssrf_skipped():
    fetched: list[str] = []
    pages = {
        "https://www.reuters.com/p1": "First Reuters analysis on supply chain risks. " * 6,
        "https://www.reuters.com/p2": "Second Reuters piece covering litigation and antitrust exposure. " * 6,
        "https://www.reuters.com/p3": "Third Reuters page that should never be fetched.",
        "https://www.wsj.com/p4": "Wall Street Journal reports mounting regulatory scrutiny in Europe. " * 4,
    }

    def fake_search(_q):
        return [
            {"title": "D Tier", "url": "https://www.tradingview.com/symbols/AAPL/", "snippet": "s", "score": 99.0},
            {"title": "SSRF", "url": "http://127.0.0.1:8080/secret", "snippet": "s", "score": 98.0},
            {"title": "R1", "url": "https://www.reuters.com/p1", "snippet": "s", "score": 9.0},
            {"title": "R2", "url": "https://www.reuters.com/p2", "snippet": "s", "score": 8.0},
            {"title": "R3", "url": "https://www.reuters.com/p3", "snippet": "s", "score": 7.0},
            {"title": "W4", "url": "https://www.wsj.com/p4", "snippet": "s", "score": 6.0},
        ]

    def fake_fetch(url):
        fetched.append(url)
        return pages[url]

    def fake_chat(prompt):
        if "reuters.com/p1" in prompt:
            # 同一頁給 4 張不同的合法引句（但該網域上限 3 張）
            base = "First Reuters analysis on supply chain risks. "
            cards = [
                {"主張": "供應鏈風險一", "引句": base * 1, "嚴重度": "常見但輕"},
                {"主張": "供應鏈風險二", "引句": base * 2, "嚴重度": "常見但輕"},
                {"主張": "供應鏈風險三", "引句": base * 3, "嚴重度": "少見但致命"},
                {"主張": "供應鏈風險四", "引句": base * 4, "嚴重度": "少見但致命"},
            ]
        elif "reuters.com/p2" in prompt:
            cards = [{"主張": "面臨反壟斷訴訟", "引句": "Second Reuters piece covering litigation and antitrust exposure. " * 2, "嚴重度": "少見但致命"}]
        else:
            cards = [{"主張": "歐洲監管審查升高", "引句": "Wall Street Journal reports mounting regulatory scrutiny in Europe. " * 2, "嚴重度": "少見但致命"}]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("counter", "Apple", search=fake_search, fetch=fake_fetch, chat=fake_chat)
    assert "https://www.tradingview.com/symbols/AAPL/" not in fetched
    assert "http://127.0.0.1:8080/secret" not in fetched
    assert "https://www.reuters.com/p3" not in fetched
    # 第 1 頁已經拿滿 reuters.com 的 3 張卡，第 2 頁跳過不讀，直接跳去讀 wsj.com/p4
    assert sum(1 for u in fetched if "reuters.com" in u) <= 2
    assert sum(1 for c in rep.cards if c.domain == "reuters.com") == 3
    assert rep.stats["rejected"]["同網域超過上限"] >= 1


def test_same_domain_max_two_pages_even_with_zero_cards():
    fetched: list[str] = []
    search = lambda _q: [
        {"title": "R1", "url": "https://www.reuters.com/1", "snippet": "s", "score": 9.0},
        {"title": "R2", "url": "https://www.reuters.com/2", "snippet": "s", "score": 8.0},
        {"title": "R3", "url": "https://www.reuters.com/3", "snippet": "s", "score": 7.0},
        {"title": "B1", "url": "https://www.bloomberg.com/1", "snippet": "s", "score": 6.0},
    ]

    def fake_fetch(url):
        fetched.append(url)
        return "Some generic article body text without useful evidence cards for testing."

    rep = rt.run_role(
        "counter",
        "Apple",
        search=search,
        fetch=fake_fetch,
        chat=lambda _p: ('{"cards": []}', "fake"),
    )
    assert fetched == [
        "https://www.reuters.com/1",
        "https://www.reuters.com/2",
        "https://www.bloomberg.com/1",
    ]
    assert rep.met is False


def test_early_stop_skips_remaining_pages():
    fetched: list[str] = []
    pages = {
        f"https://site{i}.example.com/article": (
            f"Tim Cook spoke on day {i}. \"Sales grew steadily in enterprise markets this quarter.\" "
            f"Analyst Jane Doe commented: \"Margins remain resilient despite component headwinds.\""
        )
        for i in range(1, 6)
    }
    search = lambda _q: [
        {"title": f"P{i}", "url": u, "snippet": "s", "score": 10.0 - i}
        for i, u in enumerate(pages, 1)
    ]

    def fake_fetch(url):
        fetched.append(url)
        return pages[url]

    def fake_chat(_p):
        cards = [
            {
                "主張": "企業市場穩健成長",
                "引句": "Sales grew steadily in enterprise markets this quarter.",
                "說話者": "Tim Cook",
                "說話者角色": "管理層",
            },
            {
                "主張": "分析師看好毛利率韌性",
                "引句": "Margins remain resilient despite component headwinds.",
                "說話者": "Jane Doe",
                "說話者角色": "分析師",
            },
        ]
        return json.dumps({"cards": cards}, ensure_ascii=False), "fake"

    rep = rt.run_role("voices", "Apple", search=search, fetch=fake_fetch, chat=fake_chat)
    assert rep.met is True
    assert len(fetched) == 1
    assert len(fetched) < len(pages)
    assert rep.stats["pages"] == 1 and rep.stats["chats"] == 1


def test_budget_limits_and_12000_truncation_and_taiwan_query():
    seen_queries: list[str] = []
    seen_prompts: list[str] = []
    long_page = ("A" * 11900) + "MARKER_INSIDE_WINDOW " + ("B" * 1000) + "MARKER_BEYOND_12000"
    search_items = [
        {"title": f"P{i}", "url": f"https://site{i}.example.com/a", "snippet": "s", "score": 10.0 - i}
        for i in range(1, 10)
    ]

    def fake_search(queries, **_kw):
        seen_queries.extend(queries)
        return search_items

    def fake_chat(prompt):
        seen_prompts.append(prompt)
        return '{"cards": []}', "fake"

    rep = rt.run_role(
        "counter",
        "Nvidia",
        search=fake_search,
        fetch=lambda _u: long_page,
        chat=fake_chat,
        budget={"queries": 2, "pages": 3, "chats": 2},
        taiwan_query="輝達 台灣 供應鏈",
    )
    assert len(seen_queries) == 2
    assert "輝達 台灣 供應鏈" in seen_queries
    assert rep.stats["queries"] == 2
    assert rep.stats["pages"] == 2  # chats 上限 2，不會多讀第 3 頁
    assert rep.stats["chats"] == 2
    assert "MARKER_INSIDE_WINDOW" in seen_prompts[0]
    assert "MARKER_BEYOND_12000" not in seen_prompts[0]


def test_run_teams_caches_by_subject_and_iso_week(tmp_path, monkeypatch):
    pages = {**_voices_pages(), **_counter_pages(), **_timeline_pages()}

    def fake_search(queries, **_kw):
        q = " ".join(queries)
        if "CEO interview" in q:
            return [{"title": "Reuters", "url": "https://www.reuters.com/tech/apple-ceo-remarks", "snippet": "s", "score": 9.0}]
        if "criticism" in q:
            return [{"title": "WSJ", "url": "https://www.wsj.com/tech/apple-margin-risk", "snippet": "s", "score": 8.0}]
        return [{"title": "FT", "url": "https://www.ft.com/content/apple-2007-iphone", "snippet": "s", "score": 8.0}]

    def fake_chat(prompt):
        if "apple-ceo-remarks" in prompt:
            return json.dumps({"cards": [{
                "主張": "服務與企業成長 18%",
                "引句": "Demand for services and enterprise devices grew 18% across Asia this year, and developers are building more tools on our silicon,",
                "說話者": "Tim Cook",
                "說話者角色": "管理層",
            }]}, ensure_ascii=False), "fake"
        if "apple-margin-risk" in prompt:
            return json.dumps({"cards": [{
                "主張": "競爭壓縮毛利 200 個基點",
                "引句": "Rising component prices and intensifying smartphone competition in China could compress hardware gross margins by 200 basis points next year,",
                "嚴重度": "常見但輕",
            }]}, ensure_ascii=False), "fake"
        return json.dumps({"cards": [{
            "主張": "2007 年推出 iPhone",
            "引句": "Combining an iPod, a phone, and an internet communicator into one device turned hardware design into a software platform battleground,",
            "日期": "2007",
            "事件": "推出 iPhone",
        }]}, ensure_ascii=False), "fake"

    class _Week1(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 21)

    class _Week2(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 30)

    monkeypatch.setattr(rt, "date", _Week1)
    first = rt.run_teams("Apple Inc.", cache_dir=tmp_path, search=fake_search, fetch=pages.__getitem__, chat=fake_chat)
    assert set(first) == {"voices", "counter", "timeline"}

    boom = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("快取命中不該重跑"))
    second = rt.run_teams("Apple Inc.", cache_dir=tmp_path, search=boom, fetch=boom, chat=boom)
    assert second["voices"].cards == first["voices"].cards

    # 換到下一個 ISO 週次 → 快取失效重跑
    monkeypatch.setattr(rt, "date", _Week2)
    calls = {"n": 0}

    def counting_search(queries, **kw):
        calls["n"] += 1
        return fake_search(queries, **kw)

    rt.run_teams("Apple Inc.", cache_dir=tmp_path, search=counting_search, fetch=pages.__getitem__, chat=fake_chat)
    assert calls["n"] == 3


def test_teams_md_caps_at_8_sorts_tier_and_diversity_and_marks_unmet():
    cards = [
        rt.TeamCard("voices", f"C級主張{i}", f"Quote C{i} about company products.", f"https://blog{i}.example.com/p", f"blog{i}.example.com", "C", speaker=f"Blogger {i}", speaker_role="分析師")
        for i in range(4)
    ] + [
        rt.TeamCard("voices", f"B級管理{i}", f"Quote BM{i} from management.", f"https://reuters.com/{i}", "reuters.com", "B", speaker=f"Exec {i}", speaker_role="管理層")
        for i in range(4)
    ] + [
        rt.TeamCard("voices", "B級批評", "Quote BC from a well-known critic.", "https://wsj.com/1", "wsj.com", "B", speaker="Critic", speaker_role="批評者"),
        rt.TeamCard("voices", "B級客戶", "Quote BCU from a major customer.", "https://ft.com/1", "ft.com", "B", speaker="Customer", speaker_role="客戶"),
        rt.TeamCard("voices", "A級一手", "Quote A from official filing.", "https://www.sec.gov/1", "sec.gov", "A", speaker="CEO", speaker_role="管理層"),
    ]
    reports = {
        "voices": rt.TeamReport(role="voices", cards=cards, met=True),
        "counter": rt.TeamReport(role="counter", cards=[], met=False),
        "timeline": rt.TeamReport(role="timeline", cards=[
            rt.TeamCard("timeline", "創立", "Founded in 1976.", "https://sec.gov/h", "sec.gov", "A", date="1976", event="創立"),
        ], met=False),
    }

    md = rt.teams_md(reports)
    voices_section = md.split("## 反方組")[0]
    voice_lines = [ln for ln in voices_section.splitlines() if ln.startswith("- ")]
    assert len(voice_lines) == 8
    # A 級最前，接下來 B 級（而且不能被 C 級插隊），同為 B 級時不同 speaker_role 輪流出現
    assert "A級一手" in voice_lines[0] and "（A 級）" in voice_lines[0]
    assert "（C 級）" in voice_lines[-1]
    b_lines = [ln for ln in voice_lines if "（B 級）" in ln]
    first_three_b = " ".join(b_lines[:3])
    assert "（管理層）" in first_three_b and "（批評者）" in first_three_b and "（客戶）" in first_three_b
    assert "｜“Quote A from official filing.”｜CEO（管理層）｜https://www.sec.gov/1（A 級）" in voice_lines[0]
    assert md.count("（這組證據不足，不要硬寫這一塊）") == 2


def test_as_research_sources_deduplicates_urls_and_prioritizes_official_and_countercase():
    shared_b = "https://www.reuters.com/tech/shared-story"
    shared_a = "https://www.sec.gov/Archives/edgar/data/1/10k"
    reports = {
        "voices": rt.TeamReport(
            role="voices",
            cards=[
                rt.TeamCard(
                    "voices",
                    "管理層談展望",
                    "Management noted that gross margin pressure could ease once new fabs ramp next year across regions.",
                    shared_b,
                    "reuters.com",
                    "B",
                    speaker="CEO",
                    speaker_role="管理層",
                ),
            ],
            met=False,
            titles={shared_b: "Reuters Shared Story"},
        ),
        "counter": rt.TeamReport(
            role="counter",
            cards=[
                rt.TeamCard(
                    "counter",
                    "反方質疑毛利風險",
                    "Critics countered that capital expenditures remain elevated and customer concentration poses lasting risks.",
                    shared_b,
                    "reuters.com",
                    "B",
                    severity="少見但致命",
                ),
                rt.TeamCard(
                    "counter",
                    "年報揭露單一供應商依賴",
                    "The Company relies heavily on single-source manufacturing partners located in Taiwan for advanced chips.",
                    shared_a,
                    "sec.gov",
                    "A",
                    severity="少見但致命",
                ),
            ],
            met=False,
            titles={shared_a: "Apple 10-K"},
        ),
    }

    srcs = rt.as_research_sources(reports)
    assert len(srcs) == 2
    assert srcs[0]["url"] == shared_a and srcs[0]["evidence_role"] == "official"
    assert srcs[1]["url"] == shared_b and srcs[1]["evidence_role"] == "countercase"
    assert "gross margin" in srcs[1]["excerpt"] and "Critics countered" in srcs[1]["excerpt"]
    for item in srcs:
        ResearchSource.model_validate(item)

