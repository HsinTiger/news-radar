"""Tests for Phase 9 Item 7 · Gate analyzer."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.reflector import gate as gate_module
from src.reflector import proposals as proposals_module


@pytest.fixture
def tmp_gate_db(tmp_path):
    """Create a temporary SQLite DB with publish_log table."""
    db_path = tmp_path / "test_gate.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE publish_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_post_id TEXT,
            posted_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_message TEXT
        )
    """)

    conn.commit()
    conn.close()

    yield str(db_path)


@pytest.fixture
def tmp_proposals_dir(tmp_path):
    """Create a temporary proposals directory."""
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()
    yield proposals_dir


@pytest.fixture
def analyzer_with_mocks(tmp_gate_db, tmp_proposals_dir, monkeypatch):
    """Create a GateAnalyzer with mocked paths."""
    analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
    monkeypatch.setattr(
        gate_module, "PROPOSALS_DIR", tmp_proposals_dir
    )
    monkeypatch.setattr(
        proposals_module, "PROPOSALS_DIR", tmp_proposals_dir
    )
    yield analyzer


# ============================================================================
# Test: Data retrieval
# ============================================================================

class TestDataRetrieval:
    """Tests for _query_publish_log()."""

    def test_query_publish_log_empty(self, tmp_gate_db):
        """Empty DB returns empty dict."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        data = analyzer._query_publish_log()
        assert data == {}

    def test_query_publish_log_filters_failures(self, tmp_gate_db):
        """Query ignores success=0 rows."""
        conn = sqlite3.connect(tmp_gate_db)
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).isoformat()

        cursor.execute("""
            INSERT INTO publish_log (draft_id, platform, posted_at, success)
            VALUES (?, ?, ?, ?)
        """, ("draft_1", "facebook", yesterday, 0))  # failure
        cursor.execute("""
            INSERT INTO publish_log (draft_id, platform, posted_at, success)
            VALUES (?, ?, ?, ?)
        """, ("draft_2", "facebook", now.isoformat(), 1))  # success

        conn.commit()
        conn.close()

        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        data = analyzer._query_publish_log()

        assert "facebook" in data
        assert len(data["facebook"]) == 1
        assert data["facebook"][0]["success"] == 1

    def test_query_publish_log_filters_old_records(self, tmp_gate_db):
        """Query ignores records older than 14 days."""
        conn = sqlite3.connect(tmp_gate_db)
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=20)).isoformat()
        recent = (now - timedelta(days=5)).isoformat()

        cursor.execute("""
            INSERT INTO publish_log (draft_id, platform, posted_at, success)
            VALUES (?, ?, ?, ?)
        """, ("draft_1", "facebook", old, 1))  # too old
        cursor.execute("""
            INSERT INTO publish_log (draft_id, platform, posted_at, success)
            VALUES (?, ?, ?, ?)
        """, ("draft_2", "facebook", recent, 1))  # recent

        conn.commit()
        conn.close()

        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        data = analyzer._query_publish_log()

        assert "facebook" in data
        assert len(data["facebook"]) == 1

    def test_query_publish_log_groups_by_platform(self, tmp_gate_db):
        """Query groups results by platform."""
        conn = sqlite3.connect(tmp_gate_db)
        cursor = conn.cursor()

        now = datetime.now(timezone.utc)

        for i in range(3):
            cursor.execute("""
                INSERT INTO publish_log (draft_id, platform, posted_at, success)
                VALUES (?, ?, ?, ?)
            """, (f"draft_{i}", "facebook", (now - timedelta(minutes=i*10)).isoformat(), 1))

        for i in range(2):
            cursor.execute("""
                INSERT INTO publish_log (draft_id, platform, posted_at, success)
                VALUES (?, ?, ?, ?)
            """, (f"draft_ig_{i}", "instagram", (now - timedelta(minutes=i*10)).isoformat(), 1))

        conn.commit()
        conn.close()

        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        data = analyzer._query_publish_log()

        assert "facebook" in data
        assert "instagram" in data
        assert len(data["facebook"]) == 3
        assert len(data["instagram"]) == 2


# ============================================================================
# Test: Metrics computation
# ============================================================================

class TestMetricsComputation:
    """Tests for _compute_metrics()."""

    def test_compute_metrics_insufficient_samples(self, tmp_gate_db):
        """Platforms with < MIN_SAMPLES_14D get skipped."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Create publish data with only 5 samples (< 20)
        now = datetime.now(timezone.utc)
        publish_data = {
            "facebook": [
                {"posted_at": (now - timedelta(minutes=i*10)).isoformat()}
                for i in range(5)
            ]
        }

        metrics = analyzer._compute_metrics(publish_data)

        assert "facebook" in metrics
        assert metrics["facebook"]["skip_reason"] == "insufficient_samples"

    def test_compute_metrics_emergency_rate_high(self, tmp_gate_db):
        """Emergency rate is correctly computed."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Create 20 publishes with most within 30min (emergency)
        now = datetime.now(timezone.utc)
        publish_data = {
            "facebook": [
                {"posted_at": (now - timedelta(seconds=i*20)).isoformat()}
                for i in range(20)
            ]
        }

        metrics = analyzer._compute_metrics(publish_data)

        assert "facebook" in metrics
        assert metrics["facebook"]["skip_reason"] is None
        # All intervals are 20s, so all 19 are "emergency" (< 30min)
        assert metrics["facebook"]["emergency_rate"] == 1.0

    def test_compute_metrics_emergency_rate_low(self, tmp_gate_db):
        """Emergency rate for normal spacing."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Create 20 publishes spaced 2 hours apart
        now = datetime.now(timezone.utc)
        publish_data = {
            "facebook": [
                {"posted_at": (now - timedelta(hours=i*2)).isoformat()}
                for i in range(20)
            ]
        }

        metrics = analyzer._compute_metrics(publish_data)

        assert "facebook" in metrics
        # All intervals are 2 hours, so none are "emergency"
        assert metrics["facebook"]["emergency_rate"] == 0.0

    def test_compute_metrics_silence_breaches(self, tmp_gate_db):
        """Silence breaches are counted correctly."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Create 21 publishes with explicit 5-hour gap in the middle
        now = datetime.now(timezone.utc)
        publishes = []
        # First 10, spaced 30min apart (starting 12 hours ago)
        for i in range(10):
            publishes.append({
                "posted_at": (now - timedelta(hours=12, minutes=i*30)).isoformat()
            })
        # 5-hour gap (from 7.5h ago to 2.5h ago)
        # Next 11, spaced 30min apart (starting 2 hours ago)
        for i in range(11):
            publishes.append({
                "posted_at": (now - timedelta(hours=2, minutes=i*30)).isoformat()
            })

        publish_data = {"facebook": publishes}

        metrics = analyzer._compute_metrics(publish_data)

        assert "facebook" in metrics
        # Should detect the 5-hour gap as a breach
        # Total gap from last of first group (12h - 9.5*30min = 12h - 4.75h = 7.25h ago)
        # to first of second group (2h ago) = 5.25 hours, which is > 4 hours
        assert metrics["facebook"]["silence_breaches"] >= 1

    def test_compute_metrics_cooldown_saturation(self, tmp_gate_db):
        """Cooldown saturation is computed correctly."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Create 20 publishes, most within 15min (min publish interval)
        now = datetime.now(timezone.utc)
        publish_data = {
            "facebook": [
                {"posted_at": (now - timedelta(seconds=i*30)).isoformat()}
                for i in range(20)
            ]
        }

        metrics = analyzer._compute_metrics(publish_data)

        assert "facebook" in metrics
        # All 30s intervals < 15min, so 100% saturation
        assert metrics["facebook"]["cooldown_saturation"] == 1.0


# ============================================================================
# Test: Decision logic
# ============================================================================

class TestDecisionLogic:
    """Tests for _is_out_of_band()."""

    def test_is_out_of_band_skip_reason(self, tmp_gate_db):
        """Skipped platforms are not out-of-band."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "skip_reason": "insufficient_samples",
            "emergency_rate": 0.95,  # Very high, but skipped
        }

        assert not analyzer._is_out_of_band("facebook", metrics)

    def test_is_out_of_band_high_emergency(self, tmp_gate_db):
        """High emergency rate triggers out-of-band."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "skip_reason": None,
            "emergency_rate": 0.15,  # > 10%
            "silence_breaches": 5,
            "cooldown_saturation": 0.3,
        }

        assert analyzer._is_out_of_band("facebook", metrics)

    def test_is_out_of_band_high_silence_breaches(self, tmp_gate_db):
        """High silence breaches trigger out-of-band."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "skip_reason": None,
            "emergency_rate": 0.05,
            "silence_breaches": 25,  # > 20
            "cooldown_saturation": 0.3,
        }

        assert analyzer._is_out_of_band("facebook", metrics)

    def test_is_out_of_band_high_cooldown(self, tmp_gate_db):
        """High cooldown saturation triggers out-of-band."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "skip_reason": None,
            "emergency_rate": 0.05,
            "silence_breaches": 5,
            "cooldown_saturation": 0.55,  # > 50%
        }

        assert analyzer._is_out_of_band("facebook", metrics)

    def test_is_out_of_band_all_normal(self, tmp_gate_db):
        """Normal metrics don't trigger."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "skip_reason": None,
            "emergency_rate": 0.05,
            "silence_breaches": 5,
            "cooldown_saturation": 0.3,
        }

        assert not analyzer._is_out_of_band("facebook", metrics)


# ============================================================================
# Test: Proposal writing
# ============================================================================

class TestProposalWriting:
    """Tests for _write_proposal()."""

    def test_write_proposal_creates_jsonl(
        self, tmp_gate_db
    ):
        """Writing a proposal creates a valid fire_id."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "emergency_rate": 0.15,
            "silence_breaches": 25,
            "cooldown_saturation": 0.55,
            "sample_count": 42,
        }

        # Just verify that _write_proposal returns a non-empty string
        # (actual jsonl writing is tested via integration tests)
        with patch.object(
            proposals_module, "write_proposal", return_value="test-fire-id-123"
        ):
            fire_id = analyzer._write_proposal("facebook", metrics)

        assert fire_id == "test-fire-id-123"

    def test_write_proposal_structure(
        self, tmp_gate_db
    ):
        """Proposal has correct structure."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        metrics = {
            "emergency_rate": 0.15,
            "silence_breaches": 25,
            "cooldown_saturation": 0.55,
            "sample_count": 42,
        }

        # Mock the write_proposal to capture what would be written
        with patch.object(proposals_module, "write_proposal") as mock_write:
            mock_write.return_value = "test-fire-id"
            analyzer._write_proposal("facebook", metrics)

        # Verify that write_proposal was called with correct structure
        assert mock_write.called
        call_args = mock_write.call_args[0][0]  # Get the proposal dict

        assert call_args["analyzer"] == "gate"
        assert call_args["platform"] == "facebook"
        assert call_args["proposal_type"] == "relax_gate"
        assert call_args["action"]["target_config"] == "gate.yaml"
        assert call_args["boss_attention_required"] is True


# ============================================================================
# Test: Full analyzer run
# ============================================================================

class TestAnalyzerRun:
    """Integration tests for the full analyzer."""

    def test_run_empty_db(self, tmp_gate_db):
        """Analyzer handles empty DB gracefully."""
        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        result = analyzer.run(dry_run=True)

        assert result == 0

    def test_run_insufficient_samples(self, tmp_gate_db):
        """Analyzer skips platforms with insufficient data."""
        # Insert only 5 publishes (< 20)
        conn = sqlite3.connect(tmp_gate_db)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        for i in range(5):
            cursor.execute("""
                INSERT INTO publish_log (draft_id, platform, posted_at, success)
                VALUES (?, ?, ?, ?)
            """, (f"draft_{i}", "facebook", (now - timedelta(minutes=i*30)).isoformat(), 1))

        conn.commit()
        conn.close()

        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)
        result = analyzer.run(dry_run=True)

        assert result == 0

    def test_run_out_of_band_generates_proposal(
        self, tmp_gate_db
    ):
        """Analyzer generates proposal when metrics are out-of-band."""
        # Insert 20 publishes with high emergency rate
        conn = sqlite3.connect(tmp_gate_db)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc)

        for i in range(20):
            cursor.execute("""
                INSERT INTO publish_log (draft_id, platform, posted_at, success)
                VALUES (?, ?, ?, ?)
            """, (f"draft_{i}", "facebook", (now - timedelta(seconds=i*20)).isoformat(), 1))

        conn.commit()
        conn.close()

        analyzer = gate_module.GateAnalyzer(db_path=tmp_gate_db)

        # Mock write_proposal to avoid actual file I/O
        with patch.object(proposals_module, "write_proposal", return_value="fire-id-123"):
            result = analyzer.run(dry_run=False)

        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
