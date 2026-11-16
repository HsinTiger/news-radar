"""2026-11-16：信哥要「根據過往經驗」讓寫手少被退稿。

翻了 32 篇的記錄後改的五件事，各一組測試：程式誤報、審稿看不到素材後段、審稿每輪重審全文、
第一稿太短、把過去的退稿原因提醒給寫手。
"""
from substack_radar import quality_loop as q
from substack_radar.evidence_gate import check

import pytest


@pytest.fixture(autouse=True)
def _legacy_review_mode(monkeypatch):
    """這個檔案測的是舊版「請模型挑毛病」的迴圈（SUBSTACK_REVIEW_MODE=legacy 仍可切回）。
    預設的對帳式迴圈在 test_fact_audit.py。"""
    monkeypatch.setenv("SUBSTACK_REVIEW_MODE", "legacy")



SRC = [{"publisher": "BBC News", "title": "Huang agrees to appear before Congress",
        "url": "https://www.bbc.com/news/live/c6",
        "excerpt": "Huang willing to appear before Congress. The two deals total about $214 billion."},
       {"publisher": "Epoch AI", "title": "cost", "url": "https://epoch.ai/x",
        "excerpt": "Since 2023 the cost of AI has fallen about 47% per quarter, 4x faster than DNA sequencing."},
       {"publisher": "Other", "title": "phones", "url": "https://o.example/x",
        "excerpt": "Smartphone shipments in India rose 47% last year according to Canalys."}]
FACTS = "In the interview Burkeman mentioned induced demand."


def _rules(sentence):
    return [i.rule[:2] for i in check(sentence, sources=SRC, fact_block=FACTS)]


def test_gate_false_alarms_from_the_logs_no_longer_fire():
    assert _rules("BBC 則提到，黃仁勳表示願意出席國會，說明國家安全是否受損。") == []
    assert _rules("我無法證明它正在累積，只能指出五個案例共用同一個例外邏輯。") == []
    assert _rules("Burkeman 在訪談中引用了交通規劃領域常見的「誘發需求」（Induced Demand）現象。") == []
    assert _rules("至於水，另一位業界嘉賓表示，一座資料中心耗水量大約相當於 10 個家庭。") == []
    assert _rules("根據 BBC 的報導，這兩筆合計約 2,140 億美元。") == []          # 千分位＋英文單位
    assert _rules("Epoch AI 的研究顯示，同樣效能的 AI，成本自 2023 年以來每季下降約 47%。") == []


def test_gate_still_catches_real_problems():
    assert _rules("根據高盛證券的報告，這個市場明年會成長 37%。") == ["E1", "E3"]
    assert _rules("根據 BBC 的報導，這兩筆合計約 9,999 億美元。") == ["E3"]
    # 別的來源剛好也有 47%，但講的是另一件事
    assert "E3" in _rules("根據 Gartner 的調查，企業導入 Copilot 的比例是 47%。")


def test_long_english_transcript_is_given_whole():
    transcript = ("filler talk about nothing. " * 2000) + "Let's listen to Demis Hassabis on closed loop RSI."
    assert len(transcript) > 50000 and q._est_tokens(transcript) < q.EVIDENCE_TOKENS
    assert "Demis Hassabis on closed loop" in q.evidence_pack(transcript, None, article="文章提到 Hassabis。")


def test_overlong_material_keeps_passages_the_draft_relies_on():
    filler = "filler talk about nothing in particular here. "
    transcript = (filler * 1500 + "Polymarket gives a 30 plus percent chance he returns. "
                  + filler * 1500 + "Paul Christiano opened the lectures in Toronto. " + filler * 1500)
    article = "節目提到 Polymarket 預測回任機率三成以上，還播了 Christiano 的演講。"
    pack = q.select_evidence(transcript, article, 6000)
    assert "Polymarket gives a 30 plus" in pack and "Christiano opened the lectures" in pack
    assert "節錄裡找不到，不代表素材裡沒有" in pack and "略過約" in pack
    assert q._est_tokens(pack) < 7500


def _loop(monkeypatch, drafts, audits):
    """跑審稿迴圈：drafts 是每次重寫交出的稿，audits 是每輪審稿回的意見；回 (結果, 每輪的 focus)。"""
    seen = []
    it_audit, it_draft = iter(audits), iter(drafts)

    def fake_critique(article, violations, pack, auditor, focus=None):
        seen.append(focus)
        return next(it_audit)

    monkeypatch.setattr(q, "evaluate", lambda article, **kw: [])
    monkeypatch.setattr(q, "critique", fake_critique)
    monkeypatch.setattr(q, "run_writer_text",
                        lambda p, t: (f"<<<ARTICLE>>>\n{next(it_draft)}\n<<<END>>>", "測試寫手"))
    out = q.run_review_loop(DRAFT, fact_block="", auditor="x", eval_kwargs={}, max_attempts=2)
    return out, seen


_PAD = "".join(f"這是第 {n} 段用來湊篇幅、而且沒有任何問題的敘述句子。" for n in range(30))   # 修改稿要 >400 字才算數
DRAFT = ("第一句開場講了一個具體的場景。第二句交代這件事的背景。\n\n"
         "## 這是一個夠長的完整小標\n\n第三句是原本就寫對的內容。第四句有一個寫錯的數字。\n\n" + _PAD)
FIXED = DRAFT.replace("第四句有一個寫錯的數字。", "第四句的數字已經改成對的了。")


def test_second_round_only_audits_changed_sentences(monkeypatch):
    (final, report, rewrites), seen = _loop(
        monkeypatch, drafts=[FIXED], audits=[[{"問題": "事實錯誤", "原句": "第四句", "建議改法": "改"}], []])
    assert seen[0] is None                                   # 第一輪審全文
    assert seen[1] == ["第四句的數字已經改成對的了。"]         # 第二輪只看改過的那句
    assert rewrites == 1 and report.passed and final.strip() == FIXED


def test_full_reaudit_can_be_switched_back(monkeypatch):
    monkeypatch.setenv("SUBSTACK_REAUDIT_FULL", "1")
    _, seen = _loop(monkeypatch, drafts=[FIXED],
                    audits=[[{"問題": "事實錯誤", "原句": "第四句", "建議改法": "改"}], []])
    assert seen == [None, None]


def test_auditor_items_are_capped(monkeypatch):
    many = '{"items": [' + ",".join('{"問題": "事實錯誤%d", "原句": "x", "建議改法": "y"}' % i for i in range(11)) + "]}"
    monkeypatch.setattr(q, "_run_agy", lambda prompt, model, timeout: many)
    assert len(q.critique("文章", [], "素材", "x")) == q.MAX_AUDIT_ITEMS


def test_rejections_become_next_drafts_checklist():
    from substack_radar import lessons
    rows = []
    for n in range(4):
        run = f"2026-11-0{n + 1} 04:00 podcast"
        rows.append({"run": run, "round": "1", "source": "篇幅", "kind": "篇幅不足：目前約 2100 字"})
        if n < 2:
            rows.append({"run": run, "round": "1", "source": "程式", "kind": "E1 具名來源不在取材清單"})
        rows.append({"run": run, "round": "1", "source": "審稿", "kind": "事實錯誤：每篇都不一樣"})
        rows.append({"run": run, "round": "2", "source": "文風", "kind": "AI 套話：我的判斷是"})
    rows.append({"run": "2026-11-05 04:00 podcast", "round": "1", "source": "文風", "kind": "英文沒翻譯：x"})
    names = [name for name, *_ in lessons.top_lessons("podcast", rows=rows)]
    assert names == ["寫太短", "出處對不上"]        # 只出現一次的、第二輪的、審稿的事實意見都不算
    block = lessons.prompt_block("podcast", rows=rows)
    assert "交稿前自查" in block and "最近 5 篇有 4 篇" in block
    assert lessons.prompt_block("podcast", rows=[]) == ""


def test_writer_is_told_how_length_is_counted():
    from substack_radar.composer import _length_brief
    brief = _length_brief(3000, 4000)
    assert "英文、數字、網址不算" in brief and "以 4000 個中文字為目標" in brief
