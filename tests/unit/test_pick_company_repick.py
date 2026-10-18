"""2026-10-17：今天挑的公司沒產出可發的稿 → 重試時換一家，不再挑回同一家。"""
import datetime
import importlib.util
from pathlib import Path


def _load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("pick", Path(__file__).resolve().parents[2] / "scripts" / "pick_company_candidate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pool = tmp_path / "pool.txt"
    pool.write_text("PEP\tPepsiCo\nKO\tCoca-Cola\nMCD\tMcDonald's\nNKE\tNike\n", encoding="utf-8")
    monkeypatch.setattr(mod, "POOL_PATH", pool)
    monkeypatch.setattr(mod, "NEXT_PATH", tmp_path / ".company_next")
    monkeypatch.setattr(mod, "DONE_PATH", tmp_path / ".company_done")
    monkeypatch.setattr(mod, "FAILED_PATH", tmp_path / ".company_failed")
    monkeypatch.setattr(mod, "DB", tmp_path / "none.db")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    return mod


def test_same_day_failure_picks_a_different_company(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    today = datetime.date.today().isoformat()
    (tmp_path / ".company_done").write_text(f"PEP\tPepsiCo\t{today}\n", encoding="utf-8")
    assert mod.main() == 0
    nxt = (tmp_path / ".company_next").read_text(encoding="utf-8")
    assert "PEP" not in nxt and "KO" in nxt
    done = (tmp_path / ".company_done").read_text(encoding="utf-8")
    assert "PEP" not in done                     # 失敗那家之後還能再輪到
    assert "PEP" in (tmp_path / ".company_failed").read_text(encoding="utf-8")


def test_gives_up_after_three_companies_in_one_day(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    today = datetime.date.today().isoformat()
    (tmp_path / ".company_failed").write_text(f"PEP\t{today}\nKO\t{today}\n", encoding="utf-8")
    (tmp_path / ".company_done").write_text(f"MCD\tMcDonald's\t{today}\n", encoding="utf-8")
    assert mod.main() == 1
