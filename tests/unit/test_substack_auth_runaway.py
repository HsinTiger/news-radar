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
