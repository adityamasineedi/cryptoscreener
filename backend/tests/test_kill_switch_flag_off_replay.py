"""D128d / C2: with KILL_SWITCHES_ENABLED off, paper decisions are byte-identical to the base commit.

tests/fixtures/kill_switch_replay_b009aca.json was produced by running tests/kill_switch_replay_scenario.py
against live HEAD b009aca (before kill switches existed):

    cd <worktree at b009aca>/backend
    PYTHONPATH=. python -B <this branch>/backend/tests/kill_switch_replay_scenario.py out.json

SHA-256 of the fixture: fa7045f37c6e551b6ea82ae0896af1538326db6a865eb2289dc061b1bf8a51fd. The scenario drives
the real engine through 48 hourly bars on three coins (passed-book, experimental and V2.1-A opens, live-mark and
causal closed-bar exits, gate refusals, latching stops) and records every outcome.
Re-checked after live moved to b19f731 (P14 + trade alerts): the base output is the same file, same hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.services.kill_switch import KillSwitch, KillSwitchConfig
from kill_switch_replay_scenario import canonical_json, run_scenario

FIXTURE = Path(__file__).parent / "fixtures" / "kill_switch_replay_b009aca.json"
FIXTURE_SHA256 = "fa7045f37c6e551b6ea82ae0896af1538326db6a865eb2289dc061b1bf8a51fd"


def _base() -> str:
    text = FIXTURE.read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == FIXTURE_SHA256
    return text


def test_flag_off_is_byte_identical_to_base():
    """Default: the engine's kill_switch is None (what the orchestrator does with the flag off)."""
    out = canonical_json(run_scenario())
    assert out == _base()


def test_disabled_kill_switch_object_is_byte_identical_to_base():
    """Even a KillSwitch attached with enabled=False changes nothing."""

    def attach(eng):
        eng.kill_switch = KillSwitch(KillSwitchConfig(enabled=False))

    assert canonical_json(run_scenario(attach)) == _base()


def test_enabled_with_limits_never_reached_is_identical_to_base():
    """On, but with limits the scenario never reaches: the same decisions (the check itself has no side effect)."""

    def attach(eng):
        ks = KillSwitch(KillSwitchConfig(enabled=True, daily_loss_limit_pct=0.99, max_open_risk_pct=0.99,
                                         max_position_notional_pct=99.0), now=eng._now)  # noqa: SLF001
        ks.attach(eng)
        eng.kill_switch = ks

    assert canonical_json(run_scenario(attach)) == _base()


def test_scenario_exercises_opens_exits_and_refusals():
    """Guard: the replay is not trivially empty."""
    log = run_scenario()
    opens = [r for r in log if r["op"].startswith("open") and r["pos"]]
    skips = [r for r in log if r["op"].startswith("open") and not r["pos"]]
    assert len(opens) >= 20 and len(skips) >= 20
    assert any(r["op"] == "tick_close" for r in log) and any(r["op"] == "bar_close" for r in log)
    assert log[-1]["halts"]
