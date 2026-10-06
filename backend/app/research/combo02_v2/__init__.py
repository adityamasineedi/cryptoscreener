"""COMBO_02 v2 — adaptive 3-regime research playbook (rollback-friendly).

Does not mutate frozen COMBO_02 v1. Uses existing stop/target/RR engines.
"""

from app.research.combo02_v2.evaluate import evaluate_combo02_v2_at_bar

__all__ = ["evaluate_combo02_v2_at_bar"]
