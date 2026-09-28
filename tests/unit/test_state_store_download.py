"""state_store 下載：截斷要重試，不能把半個檔案當成功（2026-09-20 實際斷在 26/29 MB）。"""
import sys, types, pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve()))
import importlib.util
spec = importlib.util.spec_from_file_location("state_store", "/Users/hsin/news_radar/scripts/state_store.py")
state_store = importlib.util.module_from_spec(spec); spec.loader.exec_module(state_store)


class _Resp:
    is_error = False
    def __init__(self, chunks): self._chunks = chunks
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_bytes(self, _n): yield from self._chunks


def _store(responses):
    store = state_store.__dict__["GitHubReleaseStore"].__new__(state_store.GitHubReleaseStore)
    store.client = types.SimpleNamespace(stream=lambda *a, **k: responses.pop(0))
    return store


def test_truncated_download_is_retried_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store.time, "sleep", lambda _s: None)
    store = _store([_Resp([b"x" * 5]), _Resp([b"x" * 10])])
    store.download_asset({"url": "u", "size": 10}, tmp_path / "f.zip")
    assert (tmp_path / "f.zip").stat().st_size == 10


def test_all_attempts_truncated_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store.time, "sleep", lambda _s: None)
    store = _store([_Resp([b"x" * 5]) for _ in range(4)])
    with pytest.raises(state_store.StateStoreError, match="truncated|after 4 attempts"):
        store.download_asset({"url": "u", "size": 10}, tmp_path / "f.zip", attempts=4)


class _ErrResp:
    """模擬 httpx 串流回應：沒 read() 就取 .text 會炸（真實 httpx 的行為）。"""
    is_error = True

    def __init__(self, status):
        self.status_code = status
        self._read = False

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): self._read = True

    @property
    def text(self):
        if not self._read:
            import httpx
            raise httpx.ResponseNotRead()
        return "Not Found"


def test_lease_vanished_raises_state_store_error_not_response_not_read(tmp_path, monkeypatch):
    """2026-09-28 23:40：租約列出後被刪 → 404 → ResponseNotRead 繞過 acquire_lock 的保護 → exit 5。"""
    monkeypatch.setattr(state_store.time, "sleep", lambda _s: None)
    store = _store([_ErrResp(404)])
    with pytest.raises(state_store.StateStoreError):
        store.download_asset({"url": "u", "size": 10}, tmp_path / "lease.json")


def test_server_error_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store.time, "sleep", lambda _s: None)
    store = _store([_ErrResp(502), _Resp([b"x" * 10])])
    store.download_asset({"url": "u", "size": 10}, tmp_path / "f.zip")
    assert (tmp_path / "f.zip").stat().st_size == 10
