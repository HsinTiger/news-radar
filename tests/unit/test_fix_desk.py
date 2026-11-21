"""2026-11-21 糾錯台：蘋果那篇的真實情況（歐洲數字真錯、大中華區是審稿看錯表格）。"""
import json

from substack_radar import fix_desk

PAGE = """<html><body><table>
<tr><th>Segment</th><th>FY 2023</th><th>FY 2024</th><th>FY 2025</th><th>FY 2026</th></tr>
<tr><td>Europe</td><td>$94.29B</td><td>$101.33B</td><td>$111.03B</td><td>$127.60B</td></tr>
<tr><td>Greater China</td><td>$72.56B</td><td>$66.95B</td><td>$64.38B</td><td>$82.38B</td></tr>
</table><p>ahead of Europe at $127.60B (26.6%) and Greater China at $82.38B (17.2%).</p></body></html>"""
URL = "https://businessquant.com/stocks/aapl/revenue-breakdown"
EU_OLD = "FY2026 美洲最大；歐洲 1110.3 億美元居次。"
EU_NEW = "FY2026 美洲最大；歐洲 1276.0 億美元居次。"
GC = "大中華區從 FY2023 的 725.6 億美元，到 FY2025 的 643.8 億。"
ARTICLE = f"開場。\n\n依 [BusinessQuant]({URL}) 的數字，{EU_OLD}{GC}\n"
WARN = [f"{fix_desk.FLAG} 歐洲應為 127.60 億美元。（原句：歐洲 1110.3 億美元居次）",
        f"{fix_desk.FLAG} 年份看錯，屬於 FY 2008。（原句：大中華區從 FY2023 的 725.6 億美元）",
        "[字數低於下限] 3618 字"]


class FakeApi:
    def __init__(self):
        self.body = {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "依 "},
                {"type": "text", "text": "BusinessQuant", "marks": [{"type": "link", "attrs": {"href": URL}}]},
                {"type": "text", "text": f" 的數字，{EU_OLD}{GC}"}]}]}
        self.puts = 0

    def get_draft(self, _id):
        return {"draft_body": json.dumps(self.body, ensure_ascii=False), "is_published": False}

    def put_draft(self, _id, draft_body):
        self.puts += 1
        self.body = json.loads(draft_body)


def _setup(tmp_path):
    (tmp_path / "Article_Substack.md").write_text(ARTICLE, encoding="utf-8")
    meta = {"title": "蘋果", "audit_warnings": list(WARN)}
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _reply(items):
    return lambda prompt: (json.dumps({"items": items}, ensure_ascii=False), "fake")


def test_real_error_fixed_and_false_alarm_cleared(tmp_path):
    meta, api = _setup(tmp_path), FakeApi()
    chat = _reply([
        {"編號": 1, "判定": "真錯", "原句": EU_OLD, "改成": EU_NEW,
         "依據": "ahead of Europe at $127.60B (26.6%)", "來源": URL},
        {"編號": 2, "判定": "誤報", "原句": GC, "改成": "",
         "依據": "Greater China ｜ $72.56B ｜ $66.95B ｜ $64.38B ｜ $82.38B", "來源": URL}])
    rep = fix_desk.run(tmp_path, meta, api, "1", chat=chat, fetch=lambda u: PAGE)
    assert rep.ok and rep.changed == 1
    assert "1276.0" in json.dumps(api.body, ensure_ascii=False) and "1110.3" not in json.dumps(api.body, ensure_ascii=False)
    assert "1276.0" in (tmp_path / "Article_Substack.md").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["audit_warnings"]
    assert not fix_desk.flagged(saved) and saved[0].startswith(fix_desk.FIXED) and saved[1].startswith(fix_desk.CLEARED)
    assert (tmp_path / "fix_log.json").exists()
    subject, body = fix_desk.work_log("蘋果", rep, "已發布")
    assert "改 1 處、誤報 1 條" in subject and "改成：" in body


def test_invented_number_is_rejected_and_nothing_published(tmp_path):
    meta, api = _setup(tmp_path), FakeApi()
    chat = _reply([
        {"編號": 1, "判定": "真錯", "原句": EU_OLD, "改成": EU_OLD.replace("1110.3", "1350.0"),
         "依據": "ahead of Europe at $127.60B (26.6%)", "來源": URL},
        {"編號": 2, "判定": "誤報", "原句": GC, "改成": "", "依據": "這段原文根本不存在於網頁上面喔喔喔", "來源": URL}])
    rep = fix_desk.run(tmp_path, meta, api, "1", chat=chat, fetch=lambda u: PAGE)
    assert not rep.ok and api.puts == 0
    assert "找不到" in rep.items[0].status and "找不到" in rep.items[1].status
    assert len(fix_desk.flagged(meta["audit_warnings"])) == 2      # 沒修好的維持原標記，發稿端會跳過


def test_page_text_keeps_table_rows_aligned():
    text = fix_desk.page_text(PAGE)
    assert "Greater China ｜ $72.56B ｜ $66.95B ｜ $64.38B ｜ $82.38B" in text


def test_place_edit_crosses_link_nodes_safely():
    body = FakeApi().body
    assert fix_desk.place_edit(body, EU_OLD, EU_NEW)
    assert fix_desk.place_edit(body, "完全不存在的句子在這裡", "x") is False


def test_two_warnings_on_same_sentence_are_merged(tmp_path):
    meta, api = _setup(tmp_path), FakeApi()
    fix = {"判定": "真錯", "原句": EU_OLD, "改成": EU_NEW, "依據": "ahead of Europe at $127.60B (26.6%)", "來源": URL}
    chat = _reply([dict(fix, 編號=1), dict(fix, 編號=2)])
    rep = fix_desk.run(tmp_path, meta, api, "1", chat=chat, fetch=lambda u: PAGE)
    assert rep.ok and api.puts == 1
    assert (tmp_path / "Article_Substack.md").read_text(encoding="utf-8").count("1276.0") == 1
