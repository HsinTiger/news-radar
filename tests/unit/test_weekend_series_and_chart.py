"""2026-10-17：週末兩篇系列（週六上篇、週日下篇同一家）＋公司稿財報趨勢圖。"""
import datetime as dt
import importlib.util
import json
from datetime import date
from pathlib import Path

from substack_radar.company_chart import insert_chart, render_financial_chart
from substack_radar.compose import company_series_brief, company_series_part


def _picker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("pick", Path(__file__).resolve().parents[2] / "scripts" / "pick_company_candidate.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    (tmp_path / "pool.txt").write_text("JNJ\tJohnson & Johnson\nKO\tCoca-Cola\n", encoding="utf-8")
    monkeypatch.setattr(mod, "POOL_PATH", tmp_path / "pool.txt")
    monkeypatch.setattr(mod, "NEXT_PATH", tmp_path / ".company_next")
    monkeypatch.setattr(mod, "DONE_PATH", tmp_path / ".company_done")
    monkeypatch.setattr(mod, "FAILED_PATH", tmp_path / ".company_failed")
    monkeypatch.setattr(mod, "DB", tmp_path / "none.db")
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    return mod


class _Sunday(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 18)


def _saturday_draft(tmp_path, clean=True, part1=True):
    d = tmp_path / "data" / "substack_drafts" / "2026-10-17" / "company_嬌生上"
    d.mkdir(parents=True)
    w = [] if clean else ["[品質迴圈未通過] E1"]
    (d / "metadata.json").write_text(json.dumps({"title": "嬌生（上）：x｜賺錢有道", "audit_warnings": w,
                                                 "source": {"series_part": 1 if part1 else 0}}), encoding="utf-8")


def test_sunday_writes_part_two_of_saturdays_company(tmp_path, monkeypatch):
    mod = _picker(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.datetime, "date", _Sunday)
    (tmp_path / ".company_done").write_text("JNJ\tJohnson & Johnson\t2026-10-17\n", encoding="utf-8")
    _saturday_draft(tmp_path)
    assert mod.main() == 0
    nxt = (tmp_path / ".company_next").read_text(encoding="utf-8")
    assert "part=2" in nxt and "JNJ" in nxt
    assert company_series_part(date(2026, 10, 18), nxt) == 2


def test_sunday_falls_back_to_single_article_when_saturday_failed(tmp_path, monkeypatch):
    mod = _picker(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.datetime, "date", _Sunday)
    (tmp_path / ".company_done").write_text("JNJ\tJohnson & Johnson\t2026-10-17\n", encoding="utf-8")
    _saturday_draft(tmp_path, clean=False)
    assert mod.main() == 0
    nxt = (tmp_path / ".company_next").read_text(encoding="utf-8")
    assert "part=2" not in nxt and "KO" in nxt


def test_part_detection_and_briefs():
    assert company_series_part(date(2026, 10, 17), "JNJ") == 1      # 週六＝上篇
    assert company_series_part(date(2026, 10, 21), "JNJ") == 0      # 平日＝單篇
    assert "上篇" in company_series_brief(1) and "估值" in company_series_brief(1)
    assert company_series_brief(0) == ""


def test_chart_renders_and_lands_before_first_heading(tmp_path):
    data = {"years": [2025, 2024, 2023], "revenue_b": [94.2, 88.8, 85.2], "op_income_b": [25.6, 21.2, 22.0],
            "net_income_b": [26.8, 14.1, 35.2], "currency": "USD"}
    out = render_financial_chart(data, tmp_path / "c.png", "嬌生")
    assert out and out[0].exists() and "十億美元" in out[1] and "資料來源" in out[1]
    md = insert_chart("開場一。\n\n開場二。\n\n## 第一節\n\n內容", out[0], out[1])
    assert md.index("![") < md.index("## 第一節") and md.index("開場二") < md.index("![")
    assert render_financial_chart({"years": [2025], "revenue_b": [1.0]}, tmp_path / "x.png", "x") is None


def test_pre_series_single_article_does_not_trigger_part_two(tmp_path, monkeypatch):
    """10/17 的嬌生是系列上線前的單篇，10/18 不能硬接下篇。"""
    mod = _picker(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.datetime, "date", _Sunday)
    (tmp_path / ".company_done").write_text("JNJ\tJohnson & Johnson\t2026-10-17\n", encoding="utf-8")
    _saturday_draft(tmp_path, part1=False)
    assert mod.main() == 0
    assert "part=2" not in (tmp_path / ".company_next").read_text(encoding="utf-8")


def test_local_chart_is_uploaded_or_removed_never_left_broken(tmp_path):
    from substack_radar.compose import _upload_local_images
    img = tmp_path / "c.png"; img.write_bytes(b"x")
    md = f"開場\n\n![圖說]({img})\n\n*圖說*\n\n## 小標"

    class Ok:
        def get_image(self, p): return {"url": "https://cdn.example/c.png"}

    class Down:
        def get_image(self, p): raise RuntimeError("429")

    assert "https://cdn.example/c.png" in _upload_local_images(md, Ok()) and str(img) not in _upload_local_images(md, Ok())
    gone = _upload_local_images(md, Down())
    assert str(img) not in gone and "*圖說*" not in gone and "## 小標" in gone


def test_part_two_backlinks_into_part_one_without_email(monkeypatch):
    import json as _json
    from datetime import date as _date, timedelta as _td
    from types import SimpleNamespace
    from substack_radar import daily_publish as dp
    calls = {}

    class Api:
        def get_draft(self, i):
            return {"draft_body": _json.dumps({"content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "結尾"}]},
                {"type": "horizontal_rule"},
                {"type": "heading", "content": [{"type": "text", "text": "📚 本文取材"}]}]})}
        def put_draft(self, i, **kw): calls["put"] = kw
        def publish_draft(self, i, send=True, **kw): calls["send"] = send

    monkeypatch.setattr(dp, "_save_ledger", lambda l: None)
    y = (_date.today() - _td(days=1)).isoformat()
    ledger = {y: {"draft_id": "1"}}
    cand = SimpleNamespace(meta={"source": {"series_part": 2}}, headline="嬌生的生意怎麼做")
    assert dp.link_series_part1(Api(), cand, "https://x/p/2", ledger)
    body = _json.loads(calls["put"]["draft_body"])["content"]
    assert "下篇已出" in _json.dumps(body, ensure_ascii=False) and body[1]["type"] == "paragraph"
    assert calls["send"] is False and ledger[y]["series_next_url"] == "https://x/p/2"
    assert not dp.link_series_part1(Api(), cand, "https://x/p/2", ledger)        # 只補一次
    assert not dp.link_series_part1(Api(), SimpleNamespace(meta={}, headline=""), "u", {y: {"draft_id": "1"}})


def test_series_mark_goes_on_main_title_once():
    from substack_radar.compose import apply_column_prefix, with_series_mark
    assert apply_column_prefix(with_series_mark("嬌生的生意怎麼做", 1), "company") == "嬌生的生意怎麼做（上）｜賺錢有道"
    assert with_series_mark("嬌生的生意怎麼做（下）", 2) == "嬌生的生意怎麼做（下）"      # 模型自己寫了也不重複
    assert with_series_mark("嬌生的生意怎麼做(上)", 2) == "嬌生的生意怎麼做（下）"
    assert with_series_mark("單篇標題", 0) == "單篇標題"


def test_company_self_citation_is_not_an_unknown_named_source():
    """2026-10-18 嬌生（下）：「根據公司公布的 2025 全年數據」被當成外部機構，整篇不能發。"""
    from substack_radar import evidence_gate
    art = "根據公司公布的 2025 全年數據，處方藥部門占營收大宗。"
    assert not [i for i in evidence_gate.check(art, sources=[], fact_block="營收 942 億") if i.rule.startswith("E1")]
    assert [i for i in evidence_gate.check("根據摩根士丹利研究，處方藥部門占營收大宗。", sources=[], fact_block="x")
            if i.rule.startswith("E1")]


def test_colon_titles_are_rewritten_naturally():
    from substack_radar.compose import decolon_title
    assert decolon_title("嬌生：本業穩得像塊石頭", ("嬌生",)) == "嬌生的本業穩得像塊石頭"
    assert decolon_title("情緒：可能不是一種感覺") == "情緒，可能不是一種感覺"
    assert decolon_title("沒有冒號的標題") == "沒有冒號的標題"


def test_chart_path_with_parentheses_still_uploads(tmp_path):
    """2026-10-18：資料夾名有「(下)」，Markdown 連結在括號斷掉 → 破圖上線。"""
    from substack_radar.compose import _upload_local_images
    folder = tmp_path / "company_嬌生本業穩得像塊石頭(下)賺錢有道"; folder.mkdir()
    img = folder / "chart_financials.png"; img.write_bytes(b"x")
    md = insert_chart("開場\n\n## 小標\n\n內容", img, "圖說")

    class Ok:
        def get_image(self, p):
            assert p == str(img)
            return {"url": "https://cdn.example/c.png"}

    out = _upload_local_images(md, Ok())
    assert "https://cdn.example/c.png" in out and str(folder) not in out and "*圖說*" in out


def test_part_two_header_links_back_to_part_one(monkeypatch):
    from substack_radar import compose
    monkeypatch.setattr(compose, "_series_prev_post", lambda today=None: ("營收一路漲（上）｜賺錢有道", "https://x/p/a34"))
    h = compose._column_header("嬌生的本業（下）｜賺錢有道", series_part=2)
    assert "還沒看上篇" in h and "(https://x/p/a34)" in h and "｜賺錢有道]" not in h
