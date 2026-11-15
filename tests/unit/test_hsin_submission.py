"""2026-11-13 投稿頁重設計：投稿說明不當標題、Hsin選觀點用自己的寫作框架。"""
import os

from substack_radar import compose
from substack_radar.composer import HSIN_PROFILE, resolve_editorial_profile
from substack_radar.hsin_brief import brief_block, extract, split_note, writer_mandate


def test_note_splits_into_angle_and_view_and_legacy_note_is_angle():
    assert split_note("【角度】借題談出路\n【我的想法】我吃過槓桿的虧") == ("借題談出路", "我吃過槓桿的虧")
    assert split_note("不要摘要整集") == ("不要摘要整集", "")


def test_brief_roundtrip_is_removed_from_material():
    body = brief_block("【角度】多行\n角度【我的想法】想法") + "# 真正的標題\n內文"
    brief, rest = extract(body)
    assert brief == {"angle": "多行\n角度", "view": "想法"} and rest.startswith("# 真正的標題")
    assert extract("沒有說明的素材") == ({}, "沒有說明的素材")


def test_mandate_forbids_invented_experience_when_owner_wrote_none():
    assert "不要出現任何" in writer_mandate({"angle": "x", "view": ""})
    assert "不得另外編造" in writer_mandate({"angle": "", "view": "我的經歷"})
    assert "不要翻譯或摘要" in writer_mandate({})


def test_source_link_never_breaks_on_multiline_title():
    header = compose._column_header("標題｜Hsin選觀點", source_title="只著重有數據\n支持的論點 [重點]",
                                    source_url="https://youtu.be/x")
    assert "[只著重有數據 支持的論點 （重點）](https://youtu.be/x)" in header


def test_hsin_profile_only_for_owner_submissions(monkeypatch):
    monkeypatch.setenv("SUBSTACK_COLUMN", "hsin")
    assert resolve_editorial_profile("morning", override="weekly") is HSIN_PROFILE
    assert resolve_editorial_profile("company") is not HSIN_PROFILE
    monkeypatch.delenv("SUBSTACK_COLUMN")
    assert resolve_editorial_profile("morning", override="weekly") is not HSIN_PROFILE


def test_submission_title_is_never_the_note(monkeypatch):
    import scripts.submit_substack as sub
    saved = {}
    monkeypatch.setattr(sub, "_save_substack_item", lambda nid, **kw: saved.update(kw) or {"status": "created"})
    sub.process_text("一段很有意思的素材\n第二行", note="寫一篇感性抒發")
    assert saved["title"] == "一段很有意思的素材" and "寫一篇感性抒發" not in saved["title"]
    assert extract(saved["body"])[0]["angle"] == "寫一篇感性抒發"


def test_screenshot_marker_becomes_recognised_text():
    from substack_radar.screenshots import resolve
    body = "[[SCREENSHOTS:abcd1234-ef:2]]\n\n我補充的一句話"
    out, n = resolve(body, fetch=lambda sid: [{"data_b64": "aGk="}, {"data_b64": "aGk="}],
                     ocr=lambda paths: "圖上的字" * 10)
    assert "[[SCREENSHOTS" not in out and "圖上的字" in out and "我補充的一句話" in out and n == 40
    assert resolve("沒有截圖的素材") == ("沒有截圖的素材", -1)


def test_screenshot_failure_drops_marker_and_reports_zero():
    from substack_radar.screenshots import resolve

    def boom(sid):
        raise RuntimeError("worker down")
    out, n = resolve("[[SCREENSHOTS:abcd1234-ef:1]]\n\n", fetch=boom)
    assert out.strip() == "" and n == 0


def test_bundle_transcript_is_loaded_for_writer(tmp_path):
    """2026-11-13：字幕抓到 31,411 字卻只疊 1,108 字精華，被判成沒素材。"""
    from substack_radar.compose import _load_bundle_transcript

    ok = tmp_path / "a.md"
    ok.write_text("# 包\n\n## 重點參考資料\n- x\n\n---\n## 完整逐字稿素材\n\n### 影片 — 頻道\n\n" + "逐字稿內容。" * 200,
                  encoding="utf-8")
    assert "逐字稿內容" in _load_bundle_transcript(str(ok))
    assert len(_load_bundle_transcript(str(ok), limit=300)) == 300

    failed = tmp_path / "b.md"
    failed.write_text("## 完整逐字稿素材\n\n### 影片 — 頻道\n\n(逐字稿抓取失敗: no captions)\n", encoding="utf-8")
    assert _load_bundle_transcript(str(failed)) == ""
    assert _load_bundle_transcript(str(tmp_path / "missing.md")) == ""


def test_search_echo_of_source_title_is_not_relevance():
    """2026-11-14：「大阪通天閣・新世界商店街」靠主來源標題裡的「新世界」混進延伸研究。"""
    from substack_radar.compose import _relevant_source, _sources_block

    src = "AI撞上美国“新世界”：暴跌、监管、政治博弈，数据中心何去何从？"
    art = f"在〈{src}〉裡，主持人談資料中心審批的時間表與電網，居民投票反對新建案。"
    osaka = "大阪景點【通天閣.新世界商店街】 2025交通資訊、營業時間＆必吃美食"
    assert not _relevant_source(osaka, "https://x.example/osaka", art, src)
    assert _relevant_source("資料中心審批與電網時間表", "https://y.example/a", art, src)
    block = _sources_block(source_title=src, source_url="https://youtu.be/abc", article=art,
                           reports=[{"title": "🎥 " + src, "url": "https://youtu.be/abc"},
                                    {"title": osaka, "url": "https://x.example/osaka"}])
    assert "大阪" not in block


def test_drain_claim_blocks_second_writer(tmp_path, monkeypatch):
    """2026-11-14：快速版與每小時版同時寫同一篇投稿。"""
    import subprocess
    import sys
    sys.path.insert(0, "scripts")
    import drain_substack as d

    monkeypatch.setattr(d, "CLAIM_DIR", tmp_path / "claims")
    assert d._claim("rid") and not d._claim("rid")
    d._release("rid")
    assert d._claim("rid")
    p = subprocess.Popen(["true"]); p.wait()
    (tmp_path / "claims" / "rid" / "pid").write_text(str(p.pid))   # 寫的人已經死了＝殘骸
    assert d._claim("rid")
