"""台股文件組（tw_filings）與台幣版 truth_probe 的單元測試：全程假的 get，不連網路。"""
import json

from substack_radar import probes, tw_filings
from substack_radar.fix_desk import FLAG

MONTHLY_ROW = {
    "出表日期": "1151111", "資料年月": "11510", "公司代號": "2330", "公司名稱": "台積電",
    "營業收入-當月營收": "16360000", "營業收入-上月營收": "15000000", "營業收入-去年當月營收": "13890000",
    "營業收入-上月比較增減(%)": "9.0", "營業收入-去年同月增減(%)": "17.78",
    "累計營業收入-當月累計營收": "128800000", "累計營業收入-去年累計營收": "100000000",
    "累計營業收入-前期比較增減(%)": "28.8",
}
INCOME_ROW = {
    "出表日期": "1151110", "年度": "115", "季別": "3", "公司代號": "2330", "公司名稱": "台積電",
    "營業收入": "112522875", "營業成本": "50000000", "營業毛利（毛損）淨額": "62522875",
    "營業費用": "10000000", "營業利益（損失）": "52522875", "稅前淨利（淨損）": "60000000",
    "本期淨利（淨損）": "48000000",
}
# 櫃買中心：損益表的欄位有英文（Date、Year、Season、SecuritiesCompanyCode）
TPEX_MONTHLY_ROW = {**MONTHLY_ROW, "出表日期": "1151112", "公司代號": "6488", "公司名稱": "環球晶",
                    "營業收入-當月營收": "1500000", "營業收入-去年當月營收": "2000000",
                    "營業收入-去年同月增減(%)": "-25.0"}
TPEX_INCOME_ROW = {
    "Date": "1151121", "Year": "115", "Season": "3", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
    "營業收入": "44069861.00", "營業成本": "35045501.00", "營業毛利（毛損）淨額": "9024360.00",
    "營業費用": "4672601.00", "營業利益（損失）": "4351759.00", "本期淨利（淨損）": "7179871.00",
}


def fake_get_factory(data: dict, calls: list | None = None):
    def fake_get(url, as_json=False):
        if calls is not None:
            calls.append(url)
        if url not in data:
            raise RuntimeError(f"沒有這個假接口 {url}")
        return data[url]
    return fake_get


def _data():
    return {
        tw_filings.TWSE_MONTHLY: [{"公司代號": "1101", "公司名稱": "台泥"}, MONTHLY_ROW],
        tw_filings.TWSE_INCOME.format(kind="ci"): [INCOME_ROW],
        tw_filings.TPEX_MONTHLY: [TPEX_MONTHLY_ROW],
        tw_filings.TPEX_INCOME.format(kind="ci"): [TPEX_INCOME_ROW],
    }


def test_listed_company_found_with_conversions(tmp_path):
    pack = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path)
    assert pack and pack.ticker == "2330.TW" and pack.company == "台積電"
    rev = pack.truth["營收"][0]
    assert rev == {"fy": 2026, "end": "2026Q3累計", "usd": None, "twd": 112522875000.0, "currency": "TWD"}
    assert pack.truth["淨利"][0]["twd"] == 48_000_000_000.0
    assert set(pack.truth) == {"營收", "毛利", "營業利益", "淨利"}
    assert pack.filings["monthly"] == {"form": "月營收", "date": "2026-11-11", "url": tw_filings.TWSE_MONTHLY}
    assert pack.filings["income"]["date"] == "2026-11-10"
    assert pack.filings["income"]["url"].endswith("t187ap06_L_ci")


def test_monthly_cards_generated_by_program(tmp_path):
    pack = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path)
    assert len(pack.cards) == 3
    c = pack.cards[0]
    assert c.claim == "2026 年 10 月營收 163.6 億元，年增 17.8%"
    assert c.use == "錢怎麼流" and c.doc == "證交所月營收" and c.url == tw_filings.TWSE_MONTHLY
    assert "營業收入-當月營收: 16360000" in c.quote
    assert "累計營收" in pack.cards[2].claim and "28.8%" in pack.cards[2].claim


def test_otc_company_found_and_negative_growth(tmp_path):
    pack = tw_filings.build_pack("6488.TWO", get=fake_get_factory(_data()), cache_dir=tmp_path)
    assert pack and pack.company == "環球晶"
    assert pack.truth["營收"][0]["twd"] == 44069861000.0
    assert pack.cards[0].doc == "櫃買中心月營收" and pack.cards[0].url == tw_filings.TPEX_MONTHLY
    assert "年減 25.0%" in pack.cards[0].claim
    assert pack.filings["income"]["date"] == "2026-11-21"
    srcs = tw_filings.as_research_sources(pack)
    assert srcs and all(s["publisher"] == "櫃買中心" for s in srcs)


def test_falls_back_to_other_income_kinds(tmp_path):
    data = _data()
    data[tw_filings.TWSE_INCOME.format(kind="ci")] = []
    data[tw_filings.TWSE_INCOME.format(kind="fh")] = [{**INCOME_ROW, "營業收入": ""}]
    pack = tw_filings.build_pack("2330.TW", get=fake_get_factory(data), cache_dir=tmp_path)
    assert pack.filings["income"]["url"].endswith("_fh")
    assert "營收" not in pack.truth


def test_gross_margin_and_operating_margin(tmp_path):
    pack = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path)
    md = tw_filings.pack_md(pack)
    assert "===== 一手文件（A 級，證交所／櫃買中心公開資料）=====" in md
    assert "營收：1,125.2 億元" in md and "毛利：625.2 億元" in md
    assert "毛利率（毛利÷營收，程式計算）：55.6%" in md        # 62522875 / 112522875
    assert "營益率（營業利益÷營收，程式計算）：46.7%" in md    # 52522875 / 112522875
    assert tw_filings.TWSE_MONTHLY in md and "t187ap06_L_ci" in md


def test_unknown_company_returns_none(tmp_path, capsys):
    assert tw_filings.build_pack("9999.TW", get=fake_get_factory(_data()), cache_dir=tmp_path) is None
    assert "9999.TW" in capsys.readouterr().out
    assert tw_filings.build_pack("AAPL", get=fake_get_factory(_data()), cache_dir=tmp_path) is None
    assert tw_filings.pack_md(None) == "" and tw_filings.as_research_sources(None) == []


def test_cache_hit_does_not_call_get(tmp_path):
    calls: list = []
    first = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data(), calls), cache_dir=tmp_path)
    assert calls
    cache_file = tmp_path / "2330.TW" / "11510_1153.json"
    assert cache_file.exists() and json.loads(cache_file.read_text(encoding="utf-8"))["company"] == "台積電"

    calls.clear()
    second = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data(), calls), cache_dir=tmp_path)
    assert calls == []
    assert second.truth == first.truth and second.cards == first.cards


def test_keyed_cache_hit_when_ttl_expired(tmp_path):
    tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path)
    again = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path, ttl_hours=0)
    assert again and len(again.cards) == 3


def test_research_sources_format(tmp_path):
    pack = tw_filings.build_pack("2330.TW", get=fake_get_factory(_data()), cache_dir=tmp_path)
    srcs = tw_filings.as_research_sources(pack)
    assert len(srcs) == 2
    for s in srcs:
        assert s["publisher"] == "臺灣證券交易所" and s["evidence_role"] == "official"
        assert 120 <= len(s["excerpt"]) <= 2600 and s["url"].startswith("https://")
        assert {"title", "url", "publisher", "excerpt", "evidence_role"} <= set(s)


# --------------------------- 台幣版 truth_probe ---------------------------

TW_TRUTH = {"營收": [{"fy": 2026, "end": "2026Q3累計", "usd": None, "twd": 112522875000.0, "currency": "TWD"}],
            "淨利": [{"fy": 2026, "end": "2026Q3累計", "usd": None, "twd": 48_000_000_000.0, "currency": "TWD"}]}


def test_twd_probe_flags_wrong_number():
    out = probes.truth_probe("台積電 2026 年前三季營收 1,300 億元，成長強勁。", TW_TRUTH)
    assert len(out) == 1 and out[0].startswith(FLAG) and "營收" in out[0] and "1,125.2" in out[0]


def test_twd_probe_accepts_correct_number_and_other_phrasings():
    assert probes.truth_probe("台積電 2026 年前三季營收 1,125.2 億元。", TW_TRUTH) == []
    assert probes.truth_probe("2026 年 1–9 月營收 1,125 億新台幣，創高。", TW_TRUTH) == []
    assert probes.truth_probe("2026 年第三季累計淨利 480 億元。", TW_TRUTH) == []
    assert len(probes.truth_probe("2026 年 1–9 月淨利 520 億新台幣。", TW_TRUTH)) == 1


def test_twd_probe_skips_when_period_or_year_does_not_match():
    for art in ("台積電 2026 年第三季營收 500 億元。",          # 單季，不是累計
                "台積電 2026 年 10 月營收 163.6 億元。",        # 月營收
                "台積電 2026 年全年營收 1,500 億元。",          # 期間對不上（truth 是前三季）
                "台積電 2025 年前三季營收 900 億元。",          # 年份對不上
                "台積電前三季營收 1,300 億元。",                # 沒寫年份
                "台積電 2026 年前三季營收 1,300 億美元。"):     # 美元不拿來比台幣
        assert probes.truth_probe(art, TW_TRUTH) == [], art


def test_usd_behaviour_unchanged_and_ignores_twd_rows():
    usd_truth = {"營收": [{"fy": 2026, "end": "2026-09-26", "usd": 416_161_000_000}]}
    assert len(probes.truth_probe("蘋果 FY2026 全年營收 4,300 億美元。", usd_truth)) == 1
    assert probes.truth_probe("蘋果 FY2026 全年營收 4,161.6 億美元。", usd_truth) == []
    # usd 為 None 的台幣列不會讓美元流程出錯
    assert probes.truth_probe("FY2026 全年營收 4,300 億美元。", TW_TRUTH) == []
