"""Unit tests for core.cost — the shared cost meter, record, and sink.

Runnable with pytest *or* directly: ``python tests/test_cost.py``.
No Azure SDK or network required (the sink construction is lazy).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cost import (  # noqa: E402
    CostMeter,
    CostRecord,
    NullCostSink,
    build_sink_from_env,
    cost_rates,
)


def test_cost_rates_defaults():
    r = cost_rates()
    assert r["rtAudioInPerMTok"] == 32.0
    assert r["rtAudioOutPerMTok"] == 64.0
    assert r["rtTextInPerMTok"] == 4.0
    assert r["rtTextOutPerMTok"] == 16.0
    assert r["visionInPerMTok"] == 0.40
    assert r["visionOutPerMTok"] == 1.60
    assert r["acsPerMin"] == 0.004


def test_cost_rates_env_override(monkeypatch=None):
    os.environ["COST_AVATAR_PER_MIN"] = "0.99"
    try:
        assert cost_rates()["avatarPerMin"] == 0.99
    finally:
        del os.environ["COST_AVATAR_PER_MIN"]


def test_meter_snapshot_math():
    m = CostMeter(is_teams=True)
    # 1M audio-in tokens at $32/M = $32; 1M audio-out at $64/M = $64.
    m.rt_audio_in = 1_000_000
    m.rt_audio_out = 1_000_000
    m.add_vision_usage(500_000, 250_000)  # $0.20 in + $0.40 out = $0.60
    snap = m.snapshot(cost_rates())
    rt = snap["components"]["realtime"]["costUsd"]
    vis = snap["components"]["vision"]["costUsd"]
    assert abs(rt - 96.0) < 1e-6
    assert abs(vis - 0.60) < 1e-6
    assert snap["channel"] == "teams"
    assert snap["components"]["acs"]["applicable"] is True
    # total is the sum of the four components.
    comp_sum = sum(c["costUsd"] for c in snap["components"].values())
    assert abs(snap["totalUsd"] - comp_sum) < 1e-6


def test_meter_page_channel_no_acs():
    m = CostMeter(is_teams=False)
    snap = m.snapshot(cost_rates())
    assert snap["channel"] == "page"
    assert snap["components"]["acs"]["applicable"] is False
    assert snap["components"]["acs"]["costUsd"] == 0.0


def test_record_from_snapshot_roundtrip():
    m = CostMeter(is_teams=True)
    m.rt_text_in = 2_000_000  # $8
    snap = m.snapshot(cost_rates())
    rec = CostRecord.from_snapshot(
        "run123",
        snap,
        transport="browser",
        persona="lisa",
        model="gpt-realtime",
        meeting_id="cand-42",
        started_at="2026-06-15T10:00:00+00:00",
        rates=cost_rates(),
    )
    assert rec.runId == "run123"
    assert rec.transport == "browser"
    assert rec.persona == "lisa"
    assert rec.channel == "teams"
    assert rec.rtTextInTokens == 2_000_000
    assert abs(rec.realtimeUsd - 8.0) < 1e-6
    assert rec.partition_key() == "2026-06"
    ent = rec.to_entity()
    assert ent["PartitionKey"] == "2026-06"
    assert ent["RowKey"] == "run123"
    assert ent["totalUsd"] == rec.totalUsd


def test_sink_disabled_by_default():
    for k in ("COST_STORE_KIND", "COST_STORE_ACCOUNT", "COST_STORE_ENDPOINT",
              "COST_STORE_CONNECTION_STRING"):
        os.environ.pop(k, None)
    sink = build_sink_from_env()
    assert isinstance(sink, NullCostSink)
    assert sink.enabled is False


def test_sink_enabled_when_account_set():
    os.environ["COST_STORE_ACCOUNT"] = "examplestor"
    try:
        sink = build_sink_from_env()
        # Lazy construction: enabled flag set without importing azure-data-tables.
        assert sink.enabled is True
        assert getattr(sink, "table_name", "") == "callcosts"
        assert getattr(sink, "endpoint", "") == "https://examplestor.table.core.windows.net"
    finally:
        os.environ.pop("COST_STORE_ACCOUNT", None)


def test_sink_kind_none_forces_disabled():
    os.environ["COST_STORE_ACCOUNT"] = "examplestor"
    os.environ["COST_STORE_KIND"] = "none"
    try:
        assert isinstance(build_sink_from_env(), NullCostSink)
    finally:
        os.environ.pop("COST_STORE_ACCOUNT", None)
        os.environ.pop("COST_STORE_KIND", None)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{('ALL PASSED' if not failures else str(failures) + ' FAILED')}")
    sys.exit(1 if failures else 0)
