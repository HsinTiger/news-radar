"""Substack 登入失效時的空轉防護（2026-10-07～09：cookie 過期 → 每 5 分鐘重寫同三篇、
三天 300 多次、燒光所有 AI 額度，一週沒有草稿也沒有 FB）。"""

import json
import sys
import types

from scripts import drain_substack
from substack_radar import compose


def test_auth_marker_blocks_until_cookie_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "SUBSTACK_AUTH_MARKER", tmp_path / ".substack_auth_failed")
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=old")
    monkeypatch.setattr("src.notify.notify_substack_failure", lambda **_k: None)
    assert not compose.substack_auth_blocked()
    compose._mark_substack_auth_failed(Exception("APIError(code=401): Please sign in"))
    assert compose.substack_auth_blocked()
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "substack.sid=new")   # 換了 cookie 就自動恢復
    assert not compose.substack_auth_blocked()


def test_only_auth_errors_trip_the_marker():
    assert compose._is_auth_error(Exception("APIError(code=401): Please sign in"))
    assert not compose._is_auth_error(Exception("APIError(code=429): Too many requests"))


def _run_drain(monkeypatch, tmp_path, returncodes):
    rows = [(f"news-{i}", f"t{i}", 100, "manual-text://x", "body", set()) for i in range(3)]
    monkeypatch.setattr(drain_substack, "ATTEMPTS_FILE", tmp_path / ".substack_attempts.json")
    monkeypatch.setattr(drain_substack, "DONE_FILE", tmp_path / ".done.json")
    monkeypatch.setattr(drain_substack, "_candidates", lambda **_k: rows)
    monkeypatch.setattr(drain_substack, "reconcile_remote_receipts", lambda *a, **k: (set(), 0))
    monkeypatch.setattr(drain_substack, "_substack_auth_blocked", lambda: False)  # 不讀本機真實標記
    calls = []
    def fake_run(cmd, cwd=None):
        calls.append(cmd[cmd.index("--news-id") + 1])
        return types.SimpleNamespace(returncode=returncodes.pop(0) if returncodes else 0)
    monkeypatch.setattr(drain_substack.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["drain", "--only-current-control", "--no-enrich"])
    drain_substack.main()
    return calls


def test_auth_failure_stops_the_whole_run(monkeypatch, tmp_path):
    calls = _run_drain(monkeypatch, tmp_path, [9, 0, 0])
    assert calls == ["news-0"]          # 第一篇就回登入失效，後面兩篇不再浪費 AI


def test_push_failures_are_capped_then_parked(monkeypatch, tmp_path):
    for _ in range(3):
        _run_drain(monkeypatch, tmp_path, [5, 0, 0])
    attempts = json.loads((tmp_path / ".substack_attempts.json").read_text())
    assert attempts == {"news-0": 3}
    calls = _run_drain(monkeypatch, tmp_path, [0, 0, 0])
    assert "news-0" not in calls        # 擱置，不再重寫


def test_auth_failures_do_not_count_toward_the_cap(monkeypatch, tmp_path):
    for _ in range(5):
        _run_drain(monkeypatch, tmp_path, [9])
    assert not (tmp_path / ".substack_attempts.json").exists() or \
        json.loads((tmp_path / ".substack_attempts.json").read_text()) == {}


def test_drain_stops_before_building_source_bundles(monkeypatch, tmp_path):
    """標記生效時連素材包（Whisper／agy 讀字幕）都不能做。"""
    monkeypatch.setattr(drain_substack, "_substack_auth_blocked", lambda: True)
    monkeypatch.setattr(drain_substack, "_candidates", lambda **_k: (_ for _ in ()).throw(AssertionError("不該走到挑稿")))
    monkeypatch.setattr(drain_substack, "_enrich", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該建素材包")))
    monkeypatch.setattr(sys, "argv", ["drain", "--only-current-control"])
    assert drain_substack.main() == 0


def test_drain_checks_login_before_any_work(monkeypatch, tmp_path):
    """標記被刪掉也一樣要停：直接試登入是最後一道防線。"""
    monkeypatch.setattr(drain_substack, "_substack_auth_blocked", lambda: False)   # 標記不見了
    monkeypatch.setattr(drain_substack, "_substack_login_ok", lambda: False)       # 但 cookie 是壞的
    monkeypatch.setattr(drain_substack, "DONE_FILE", tmp_path / ".done.json")
    monkeypatch.setattr(drain_substack, "reconcile_remote_receipts", lambda *a, **k: (set(), 0))
    monkeypatch.setattr(drain_substack, "_candidates",
                        lambda **_k: [("news-0", "t", 1, "manual-text://x", "b", set())])
    monkeypatch.setattr(drain_substack.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該開始寫稿")))
    monkeypatch.setattr(sys, "argv", ["drain", "--only-current-control"])
    assert drain_substack.main() == 0


def test_test_suite_cannot_touch_the_real_marker():
    from pathlib import Path
    assert "runtime-markers" in str(compose.SUBSTACK_AUTH_MARKER)
    assert Path(compose.SUBSTACK_AUTH_MARKER).parent != Path(compose._REPO_ROOT) / "data" / "substack_drafts"
