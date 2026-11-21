"""2026-11-21 週末旗艦第 1 步：來源分級、文件組（SEC）、隱藏抽查。"""
import json

from substack_radar import filings_desk as fd
from substack_radar import probes
from substack_radar.fix_desk import FLAG, _number_in
from substack_radar.source_tier import tier


def test_source_tiers():
    assert tier("https://www.sec.gov/Archives/edgar/data/1/x.htm") == "A"
    assert tier("https://www.apple.com/newsroom/2026/07/apple-reports") == "A"
    assert tier("https://investor.nvidia.com/news") == "A"
    assert tier("https://www.reuters.com/tech/x") == "B"
    assert tier("https://axis-intelligence.com/apple-statistics") == "D"
    assert tier("https://www.tradingview.com/symbols/NASDAQ-AAPL") == "D"
    assert tier("https://someblog.example.com/post") == "C"


TENK = ("<html><body><p>Table of Contents</p><p>Item 1. Business 3</p><p>Item 1A. Risk Factors 9</p>"
        "<p>Item 1. Business</p>" + "<p>The Company designs and sells smartphones through carriers.</p>" * 20 +
        "<p>Net sales through indirect channels accounted for 60% of total net sales.</p>"
        "<p>Item 1A. Risk Factors</p>" + "<p>The Company depends on single-source suppliers in Taiwan.</p>" * 20 +
        "<p>Item 1B. Unresolved</p><p>Item 7. Management's Discussion</p>" + "<p>Services grew 13%.</p>" * 60 +
        "<p>Item 8. Financial Statements</p></body></html>")


def fake_get(url, as_json=False):
    if url.endswith("company_tickers.json"):
        return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    if "submissions" in url:
        return {"filings": {"recent": {"form": ["10-K"], "filingDate": ["2026-11-03"],
                                       "accessionNumber": ["0000320193-26-000026"],
                                       "primaryDocument": ["aapl-20260926.htm"], "items": [""]}}}
    if "companyfacts" in url:
        row = lambda end, val: {"start": f"{int(end[:4]) - 1}{end[4:]}", "end": end, "val": val,  # noqa: E731
                                "form": "10-K", "filed": "2026-11-03"}
        return {"facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                row("2026-09-26", 478_830_000_000), row("2025-09-27", 416_160_000_000)]}},
            "GrossProfit": {"units": {"USD": [row("2026-09-26", 233_600_000_000)]}}}}}
    return TENK


def fake_chat(prompt):
    cards = [{"主張": "六成營收經由電信商等間接通路", "引句": "Net sales through indirect channels accounted for 60% of total net sales.",
              "數字": "60%", "用途": "錢怎麼流"},
             {"主張": "捏造的卡", "引句": "Apple owns all factories in Taiwan.", "數字": "", "用途": "台灣"}]
    return json.dumps({"cards": cards}), "fake"


def test_filings_pack_verifies_cards_and_caches(tmp_path):
    pack = fd.build_pack("AAPL", "蘋果怎麼賺錢", get=fake_get, chat=fake_chat, cache_dir=tmp_path)
    assert pack.truth["營收"][0] == {"fy": 2026, "end": "2026-09-26", "usd": 478_830_000_000}
    assert pack.cards and all("factories" not in c.quote for c in pack.cards)    # 原文沒有的引句被退回
    assert pack.rejected >= 1
    md = fd.pack_md(pack)
    assert "4,788.3 億美元" in md and "毛利率" in md and "48.8%" in md
    src = fd.as_research_sources(pack)
    assert src and src[0]["publisher"] == "SEC EDGAR" and src[0]["url"].startswith("https://www.sec.gov/")
    again = fd.build_pack("AAPL", get=fake_get, chat=lambda p: (_ for _ in ()).throw(AssertionError("不該再讀")),
                          cache_dir=tmp_path)
    assert len(again.cards) == len(pack.cards)                                    # 週日下篇走快取


def test_section_picks_body_not_table_of_contents():
    from substack_radar.fix_desk import page_text
    body = fd.sections("annual", page_text(TENK))
    assert "carriers" in body["business"] and "single-source" in body["risk"] and "Services grew" in body["mdna"]


TRUTH = {"營收": [{"fy": 2026, "end": "2026-09-26", "usd": 478_830_000_000}],
         "淨利": [{"fy": 2026, "end": "2026-09-26", "usd": 132_180_000_000}]}


def test_truth_probe_flags_wrong_annual_numbers_only():
    art = ("蘋果 FY2026 全年營收 4,788 億美元，比去年多。"
           "FY2026 全年淨利 1,500 億美元。"
           "2026 年 9 月那一季營收 1,020 億美元。")
    out = probes.truth_probe(art, TRUTH)
    assert len(out) == 1 and out[0].startswith(FLAG) and "淨利" in out[0] and "1,321.8" in out[0]


def test_citation_probe_catches_number_not_on_cited_page():
    art = ("毛利率升到 49%（[某整理站](https://markhub24.com/a)）。"
           "服務營收 1091.6 億美元（[資料](https://example.com/b)）。")
    pages = {"https://markhub24.com/a": "<p>gross margin 46.2% in FY2024</p>",
             "https://example.com/b": "<p>Services revenue $109.16B</p>"}
    out = probes.citation_probe(art, fetch=lambda u: pages[u], seed="x")
    assert len(out) == 1 and "49" in out[0] and "markhub24" in out[0]


def test_number_match_tolerates_million_rounding():
    assert _number_in("4161.6", "Total net sales 416161")       # 億 → 百萬的四捨五入
    assert not _number_in("4300", "Total net sales 416161")


def test_truth_probe_ignores_segment_revenue_and_other_clause():
    art = ("FY2026 美洲全年 1980.8 億美元、佔營收約 41%；歐洲 1276.0 億美元居次。"
           "FY2026 服務營收 1233 億美元，全年成長。")
    assert probes.truth_probe(art, TRUTH) == []


def test_citation_probe_only_checks_the_linked_clause():
    art = "市值一度摸到 5.1 兆美元；[分析師平均目標價是 343.69 美元，年漲 29%](https://www.tradingview.com/x)。"
    page = "<p>Price target 343.69, up 29% this year</p>"
    assert probes.citation_probe(art, fetch=lambda u: page, seed="x") == []


def test_truth_probe_skips_cost_of_revenue_and_cloud_segment():
    art = ("FY2026 全年微軟雲端營收 2,144 億美元，成長 23%。"
           "FY2026 全年營收成本多出 26 億美元。")
    assert probes.truth_probe(art, TRUTH) == []
