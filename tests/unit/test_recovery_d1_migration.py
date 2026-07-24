import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_recovery_control_migration_enforces_modes_and_experiment_identity() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0001_operational_control.sql").read_text(encoding="utf-8")
    )
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0007_recovery_control.sql").read_text(encoding="utf-8")
    )
    assert conn.execute("SELECT mode FROM automation_state WHERE id='runtime'").fetchone()[0] == "paused"
    conn.execute(
        """
        INSERT INTO recovery_experiments(
          id,draft_id,platform,experiment_type,hypothesis,baseline_followers,
          baseline_primary_metric,baseline_primary_value,baseline_captured_at,
          content_format,topic,created_at,updated_at
        ) VALUES(
          'rx','draft-1','threads','trust','named-source test',3748,'views',279.5,
          '2026-07-23T00:00:00Z','feed','current_affairs',
          '2026-07-24T00:00:00Z','2026-07-24T00:00:00Z'
        )
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE automation_state SET mode='unsafe' WHERE id='runtime'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO recovery_experiments(
              id,draft_id,platform,experiment_type,hypothesis,baseline_followers,
              baseline_primary_metric,baseline_primary_value,baseline_captured_at,
              content_format,topic,created_at,updated_at
            ) VALUES(
              'rx-2','draft-1','threads','interest','duplicate',3748,'views',279.5,
              '2026-07-23T00:00:00Z','feed','other',
              '2026-07-24T00:00:00Z','2026-07-24T00:00:00Z'
            )
            """
        )
