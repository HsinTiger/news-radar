"""News Radar · Phase 9 unified reflector package.

Created 2026-04-27 (Phase 9 Item 2). Will host the analyzer modules
introduced by Items 3-7:

  - topic.py     (Item 3 — refactor of legacy src/reflector_topic.py)
  - harvest.py   (Item 4)
  - composer.py  (Item 5)
  - scorer.py    (Item 6)
  - gate.py      (Item 7)
  - proposals.py (Item 2 — write-path API for proposals.jsonl + lineage)

All analyzers write through ``proposals.write_proposal``; no analyzer
opens the jsonl file or the lineage table directly.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md
"""
