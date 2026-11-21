"""每週學習迴圈（substack_radar/lessons.py ＋ scripts/weekly_learning.py）的單元測試。

用 tmp_path 造假的 data/ 與 logs/，chat 用假函式，不連網路、不叫模型。
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from substack_radar import lessons
from scripts import weekly_learning

SINCE, UNTIL = date(2026, 11, 14), date(2026, 11, 21)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _build_root(root: Path) -> None:
    folder = root / "data" / "substack_drafts" / "2026-11-20" / "a-folder"
    _write_json(folder / "fix_log.json", [
        {"at": "t", "model": "m", "error": "", "items": [
            {"warning": "FY 年份寫錯", "verdict": "x", "old": "2023 年營收", "new": "2024", "basis": "", "status": "ok", "url": ""},
        ]},
    ])
    _write_json(folder / "metadata.json", {"audit_warnings": [
        "[跟素材不符，還沒修] 引用的網頁上找不到「毛利率 70%」",
        "[已糾錯] 單位換算：把 3 億寫成 3 兆",
        "[品質迴圈未通過] 字數不足",
        "隱藏抽查：人名拼錯",
        "[別的標籤] 不收這一條",
    ]})
    # 超出日期範圍的資料夾不收
    _write_json(root / "data" / "substack_drafts" / "2026-10-01" / "old" / "fix_log.json",
                [{"items": [{"warning": "年份錯", "old": "舊"}]}])
    csv_path = root / "logs" / "writing" / "rejections.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "run", "round", "source", "kind", "quote"])
        w.writerow(["2026-11-19", "run1", "1", "文風", "破折號太多", "他——其實——很強"])
        w.writerow(["2026-11-19", "run1", "1", "文風", "破折號太多", "他——其實——很強"])   # 重複
        w.writerow(["2026-09-01", "run0", "1", "文風", "破折號太多", "太舊"])


def _case(kind: str, quote: str = "", source: str = "rejection") -> dict:
    return lessons._make_case("2026-11-20", source, kind, quote, "f")


# ---------------------------------------------------------------------------
# collect_cases
# ---------------------------------------------------------------------------

def test_collect_cases_三種紀錄都收得到並去重(tmp_path):
    _build_root(tmp_path)
    cases = lessons.collect_cases(SINCE, UNTIL, root=tmp_path)
    by_source: dict[str, list[dict]] = {}
    for c in cases:
        by_source.setdefault(c["source"], []).append(c)
    assert len(by_source["fix_log"]) == 1
    assert len(by_source["warning"]) == 4                 # 4 條要收的，1 條別的標籤不收
    assert len(by_source["rejection"]) == 1               # 重複的退稿去重，過舊的不收
    assert all({"date", "source", "kind_text", "quote", "folder"} <= set(c) for c in cases)
    fix = by_source["fix_log"][0]
    assert fix["quote"] == "2023 年營收" and fix["folder"].endswith("a-folder")
    assert len({c["id"] for c in cases}) == len(cases)
    # 再收一次結果一樣（id 穩定）
    assert {c["id"] for c in lessons.collect_cases(SINCE, UNTIL, root=tmp_path)} == {c["id"] for c in cases}


def test_collect_cases_id是sha1():
    import hashlib
    c = _case("破折號", "他——")
    assert c["id"] == hashlib.sha1("rejection破折號他——".encode()).hexdigest()


def test_collect_cases_沒有資料夾也不會壞(tmp_path):
    assert lessons.collect_cases(SINCE, UNTIL, root=tmp_path) == []


# ---------------------------------------------------------------------------
# 分類與提案
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,expected", [
    ("FY2024 寫成 FY2023", "數字或年份錯"),
    ("引用的網頁上找不到這句話", "掛錯來源"),
    ("3 億寫成 3 兆", "單位換算"),
    ("人名拼錯", "人名或專有名詞"),
    ("把結論說反了", "意思說反"),
    ("引用 D 級整理站", "引用整理站"),
    ("字數不足", "字數"),
    ("對仗句太多", "AI 套話與文風"),
    ("完全不相干的事", "其他"),
])
def test_categorize_分類正確(kind, expected):
    groups = lessons.categorize([_case(kind)])
    assert list(groups) == [expected]


def test_categorize_第一個符合的勝():
    # 同時符合「數字或年份錯」與「單位換算」，排在前面的贏
    assert list(lessons.categorize([_case("年份寫錯，單位也不對")])) == ["數字或年份錯"]


def test_propose_lessons_未達3件的類別不提案():
    calls: list[str] = []

    def chat(prompt):
        calls.append(prompt)
        return json.dumps({"lesson": "數字要核對原文"}), "fake"

    groups = {"字數": [_case(f"字數不足 {i}") for i in range(2)],
              "掛錯來源": [_case(f"出處不對 {i}", f"q{i}") for i in range(6)]}
    out = lessons.propose_lessons(groups, chat=chat)
    assert len(calls) == 1 and "掛錯來源" in calls[0]
    assert len(out) == 1
    assert out[0]["category"] == "掛錯來源" and out[0]["role"] == "researcher"
    assert out[0]["lesson"] == "數字要核對原文"
    assert len(out[0]["examples"]) == 4                       # 最多 4 個例句


def test_propose_lessons_模型壞掉就跳過():
    def chat(prompt):
        raise RuntimeError("閘道掛了")

    assert lessons.propose_lessons({"字數": [_case(f"字數 {i}") for i in range(3)]}, chat=chat) == []


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------

def test_accept_通過():
    ok, _ = lessons.accept("引用數字前，先核對原文的年份與單位。", ["- 字數寫到上限附近（字數，2026-11-01）"])
    assert ok


def test_accept_擋超長():
    ok, why = lessons.accept("核對" + "很長" * 50, [])
    assert not ok and "太長" in why


@pytest.mark.parametrize("word", ["曼報", "Manny", "Claude", "Gemini", "GPT", "Opus", "Sonnet", "Haiku", "agy",
                                  "DeepSeek", "產文路線"])
def test_accept_擋禁用詞(word):
    ok, why = lessons.accept(f"請核對{word}的數字", [])
    assert not ok and "禁用詞" in why


def test_accept_擋相似():
    existing = ["- 引用數字前要核對原文的年份（數字或年份錯，2026-11-01）"]
    ok, why = lessons.accept("引用數字前要核對原文的年份和單位", existing)
    assert not ok and "太像" in why


def test_accept_擋口號():
    ok, why = lessons.accept("寫稿要更用心、更嚴謹", [])
    assert not ok and "口號" in why


def test_accept_擋空的():
    assert not lessons.accept("  ", [])[0]


# ---------------------------------------------------------------------------
# 教訓清單檔
# ---------------------------------------------------------------------------

def test_save_lessons_超過12條刪最舊(tmp_path):
    lines = [f"- 教訓{i}要核對（字數，2026-11-{i + 1:02d}）" for i in range(15)]
    lessons.save_lessons("writer", lines, root=tmp_path)
    kept = lessons.read_lessons("writer", root=tmp_path)
    assert len(kept) == 12
    assert kept[0].startswith("- 教訓3") and kept[-1].startswith("- 教訓14")


def test_save_lessons_依日期刪最舊不看檔案順序(tmp_path):
    lines = ["- 新的要核對（字數，2026-11-20）"] + [f"- 舊{i}要核對（字數，2026-10-{i + 1:02d}）" for i in range(12)]
    lessons.save_lessons("writer", lines, root=tmp_path)
    kept = lessons.read_lessons("writer", root=tmp_path)
    assert len(kept) == 12 and any("新的" in k for k in kept) and not any(k.startswith("- 舊0") for k in kept)


def test_load_lessons_沒有檔案回空字串(tmp_path):
    assert lessons.load_lessons("writer", root=tmp_path) == ""
    assert lessons.load_lessons("researcher", root=tmp_path) == ""


def test_load_lessons_有教訓就回區塊(tmp_path):
    lessons.save_lessons("researcher", ["- 出處要附上網址（掛錯來源，2026-11-20）"], root=tmp_path)
    block = lessons.load_lessons("researcher", root=tmp_path)
    assert "出處要附上網址" in block
    assert lessons.load_lessons("writer", root=tmp_path) == ""


# ---------------------------------------------------------------------------
# recurring
# ---------------------------------------------------------------------------

def _history(path: Path, weeks: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"until": f"2026-11-{7 * i + 1:02d}", "counts": c}, ensure_ascii=False)
                              for i, c in enumerate(weeks)) + "\n", encoding="utf-8")
    return path


def test_recurring_連續三週都有3件以上(tmp_path):
    h = _history(tmp_path / "weekly.jsonl", [
        {"字數": 3, "掛錯來源": 5, "其他": 9},
        {"字數": 4, "掛錯來源": 2, "其他": 9},
        {"字數": 3, "掛錯來源": 6, "其他": 9},
    ])
    assert lessons.recurring(h) == ["字數"]                   # 掛錯來源中間一週不到 3 件；「其他」不算


def test_recurring_不足三週或沒有檔案回空(tmp_path):
    h = _history(tmp_path / "weekly.jsonl", [{"字數": 5}, {"字數": 5}])
    assert lessons.recurring(h) == []
    assert lessons.recurring(tmp_path / "不存在.jsonl") == []


def test_recurring_只看最近三週(tmp_path):
    h = _history(tmp_path / "weekly.jsonl", [{"字數": 0}, {"字數": 3}, {"字數": 3}, {"字數": 3}])
    assert lessons.recurring(h) == ["字數"]


# ---------------------------------------------------------------------------
# weekly_learning
# ---------------------------------------------------------------------------

def _boom(*a, **k):
    raise AssertionError("不該被呼叫")


def _fake_chat(prompt):
    return json.dumps({"lesson": "標出數字的年份，並核對原文"}), "fake"


def _snapshot_tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file()}


def test_weekly_learning_dry_run不寫檔不寄信(tmp_path, monkeypatch):
    _build_root(tmp_path)
    # 補到 ≥3 件才會叫 chat，確保 dry-run 真的有東西「想寫」
    folder = tmp_path / "data" / "substack_drafts" / "2026-11-20" / "b"
    _write_json(folder / "metadata.json", {"audit_warnings": [f"[已糾錯] 字數不足 {i}" for i in range(4)]})
    import src.notify as notify
    monkeypatch.setattr(notify, "_dispatch", _boom)
    monkeypatch.setattr(weekly_learning, "_dispatch", _boom)
    before = _snapshot_tree(tmp_path)
    report = weekly_learning.run(7, dry_run=True, root=tmp_path, chat=_fake_chat, today=UNTIL,
                                 run_tests=_boom, send=_boom)
    assert _snapshot_tree(tmp_path) == before
    assert not (tmp_path / "data" / "learning").exists()
    assert not (tmp_path / "substack_radar").exists()
    assert "本週案例數" in report and "試跑" in report


def test_weekly_learning_dry_run_main不呼叫_dispatch(tmp_path, monkeypatch):
    import src.notify as notify
    calls: list = []
    monkeypatch.setattr(notify, "_dispatch", lambda **kw: calls.append(kw))
    monkeypatch.setattr(weekly_learning, "_dispatch", lambda **kw: calls.append(kw))
    assert weekly_learning.main(["--dry-run", "--days", "7"], root=tmp_path, chat=_fake_chat) == 0
    assert calls == []
    assert not (tmp_path / "data").exists()


def test_weekly_learning_正式跑會寫檔寄信並附標題(tmp_path):
    _build_root(tmp_path)
    folder = tmp_path / "data" / "substack_drafts" / "2026-11-20" / "b"
    _write_json(folder / "metadata.json", {"audit_warnings": [f"[已糾錯] 字數不足 {i}" for i in range(4)]})
    sent: list[tuple[str, str]] = []
    report = weekly_learning.run(7, root=tmp_path, chat=_fake_chat, today=UNTIL, run_tests=lambda root: True,
                                 send=lambda subject, body: sent.append((subject, body)))
    assert len(sent) == 1 and sent[0][0].startswith("【每週學習】")
    assert (tmp_path / "data" / "learning" / "cases.jsonl").exists()
    weekly = (tmp_path / "data" / "learning" / "weekly.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(weekly) == 1 and json.loads(weekly[0])["counts"]["字數"] >= 3
    assert "標出數字的年份" in lessons.load_lessons("writer", root=tmp_path)
    assert "採用的教訓" in report
    # 案例庫 id 去重：再跑一次不會重複記
    weekly_learning.run(7, root=tmp_path, chat=_fake_chat, today=UNTIL, run_tests=lambda root: True,
                        send=lambda s, b: None)
    ids = [json.loads(ln)["id"] for ln in (tmp_path / "data" / "learning" / "cases.jsonl").read_text("utf-8").splitlines()]
    assert len(ids) == len(set(ids))


def test_weekly_learning_回歸測試沒過就還原(tmp_path):
    _build_root(tmp_path)
    folder = tmp_path / "data" / "substack_drafts" / "2026-11-20" / "b"
    _write_json(folder / "metadata.json", {"audit_warnings": [f"[已糾錯] 字數不足 {i}" for i in range(4)]})
    sent: list[tuple[str, str]] = []
    report = weekly_learning.run(7, root=tmp_path, chat=_fake_chat, today=UNTIL, run_tests=lambda root: False,
                                 send=lambda subject, body: sent.append((subject, body)))
    assert lessons.load_lessons("writer", root=tmp_path) == ""
    assert not lessons.lesson_path("writer", root=tmp_path).exists()
    assert "回歸測試沒過，已還原" in report and sent


def test_weekly_learning_寄信失敗也不會掛(tmp_path, capsys):
    def bad_send(subject, body):
        raise RuntimeError("smtp 掛了")

    report = weekly_learning.run(7, root=tmp_path, chat=_fake_chat, today=UNTIL, send=bad_send)
    assert "本週案例數" in report
    assert "寄週報失敗" in capsys.readouterr().out
