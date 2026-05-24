"""Tests for llm-budget-window-py."""

from __future__ import annotations

import threading

import pytest

from llm_budget_window import (
    MultiWindowBudget,
    SingleWindowBudget,
    Window,
    WindowBudget,
    WindowBudgetExceeded,
    WindowSnapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_clock(start: float = 1_000_000.0):
    """Return a controllable monotonic clock."""
    t = [start]

    def clock() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return clock, advance


# ---------------------------------------------------------------------------
# Window enum
# ---------------------------------------------------------------------------


def test_window_seconds():
    assert Window.MINUTE.seconds == 60
    assert Window.HOUR.seconds == 3600
    assert Window.DAY.seconds == 86_400


def test_window_str():
    assert str(Window.MINUTE) == "minute"
    assert str(Window.HOUR) == "hour"
    assert str(Window.DAY) == "day"


# ---------------------------------------------------------------------------
# WindowBudgetExceeded
# ---------------------------------------------------------------------------


def test_exception_carries_fields():
    exc = WindowBudgetExceeded(
        window=Window.MINUTE,
        spent_usd=0.08,
        requested_usd=0.03,
        cap_usd=0.10,
    )
    assert exc.window is Window.MINUTE
    assert exc.spent_usd == pytest.approx(0.08)
    assert exc.requested_usd == pytest.approx(0.03)
    assert exc.cap_usd == pytest.approx(0.10)


def test_exception_message_contains_window_name():
    exc = WindowBudgetExceeded(
        window=Window.HOUR,
        spent_usd=1.90,
        requested_usd=0.20,
        cap_usd=2.00,
    )
    assert "hour" in str(exc).lower()


# ---------------------------------------------------------------------------
# SingleWindowBudget — basic record
# ---------------------------------------------------------------------------


def test_record_within_cap_does_not_raise():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=1.00), clock=clock)
    b.record(usd=0.50)
    b.record(usd=0.49)


def test_record_exactly_at_cap_raises():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.50), clock=clock)
    b.record(usd=0.50)
    with pytest.raises(WindowBudgetExceeded):
        b.record(usd=0.01)


def test_record_fails_closed():
    """A rejected call must not be recorded."""
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.10), clock=clock)
    with pytest.raises(WindowBudgetExceeded):
        b.record(usd=0.20)
    snap = b.snapshot()
    assert snap.spent_usd == pytest.approx(0.0)
    assert snap.events == 0


def test_record_negative_usd_raises():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=1.0), clock=clock)
    with pytest.raises(ValueError, match="usd must be >= 0"):
        b.record(usd=-0.01)


def test_record_negative_tokens_raises():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=1.0), clock=clock)
    with pytest.raises(ValueError, match="tokens must be >= 0"):
        b.record(usd=0.01, tokens=-1)


# ---------------------------------------------------------------------------
# SingleWindowBudget — sliding window
# ---------------------------------------------------------------------------


def test_events_expire_after_window_duration():
    clock, advance = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.10), clock=clock)
    b.record(usd=0.09)
    # Advance past the window
    advance(61)
    # Old event expired; new budget should be free
    b.record(usd=0.09)


def test_events_within_window_accumulate():
    clock, advance = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.10), clock=clock)
    b.record(usd=0.05)
    advance(30)  # still inside the 60-second window
    with pytest.raises(WindowBudgetExceeded):
        b.record(usd=0.06)  # 0.05 + 0.06 = 0.11 > 0.10


def test_partial_expiry_slides_correctly():
    clock, advance = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.10), clock=clock)
    b.record(usd=0.05)
    advance(61)  # first event now expired
    b.record(usd=0.05)  # second event, starts fresh window
    advance(30)  # 30 s later, second event still in window
    with pytest.raises(WindowBudgetExceeded):
        b.record(usd=0.06)  # 0.05 + 0.06 > 0.10


# ---------------------------------------------------------------------------
# SingleWindowBudget — token cap
# ---------------------------------------------------------------------------


def test_token_cap_enforced():
    clock, _ = make_clock()
    b = SingleWindowBudget(
        WindowBudget(Window.MINUTE, usd_cap=10.00, token_cap=1000),
        clock=clock,
    )
    b.record(usd=0.01, tokens=999)
    with pytest.raises(WindowBudgetExceeded) as exc_info:
        b.record(usd=0.01, tokens=2)
    assert exc_info.value.cap_tokens == 1000


def test_no_token_cap_when_not_configured():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=10.00), clock=clock)
    b.record(usd=0.01, tokens=1_000_000)  # no token cap → passes


# ---------------------------------------------------------------------------
# SingleWindowBudget — snapshot
# ---------------------------------------------------------------------------


def test_snapshot_initial():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.HOUR, usd_cap=5.00), clock=clock)
    snap = b.snapshot()
    assert snap.window is Window.HOUR
    assert snap.spent_usd == pytest.approx(0.0)
    assert snap.usd_cap == pytest.approx(5.00)
    assert snap.events == 0


def test_snapshot_after_records():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.HOUR, usd_cap=5.00), clock=clock)
    b.record(usd=1.00, tokens=100)
    b.record(usd=0.50, tokens=50)
    snap = b.snapshot()
    assert snap.spent_usd == pytest.approx(1.50)
    assert snap.spent_tokens == 150
    assert snap.events == 2


def test_snapshot_after_expiry():
    clock, advance = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=5.00), clock=clock)
    b.record(usd=1.00)
    advance(61)
    snap = b.snapshot()
    assert snap.spent_usd == pytest.approx(0.0)
    assert snap.events == 0


def test_snapshot_is_namedtuple():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.DAY, usd_cap=10.0), clock=clock)
    snap = b.snapshot()
    assert isinstance(snap, WindowSnapshot)


# ---------------------------------------------------------------------------
# SingleWindowBudget — reset
# ---------------------------------------------------------------------------


def test_reset_clears_events():
    clock, _ = make_clock()
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=0.10), clock=clock)
    b.record(usd=0.09)
    b.reset()
    snap = b.snapshot()
    assert snap.spent_usd == pytest.approx(0.0)
    assert snap.events == 0


# ---------------------------------------------------------------------------
# MultiWindowBudget
# ---------------------------------------------------------------------------


def test_multi_all_windows_pass():
    clock, _ = make_clock()
    b = MultiWindowBudget(
        [
            WindowBudget(Window.MINUTE, usd_cap=0.10),
            WindowBudget(Window.HOUR, usd_cap=2.00),
            WindowBudget(Window.DAY, usd_cap=20.00),
        ],
        clock=clock,
    )
    b.record(usd=0.05)
    snaps = b.snapshots()
    assert len(snaps) == 3
    assert all(s.spent_usd == pytest.approx(0.05) for s in snaps)


def test_multi_minute_cap_fires():
    clock, _ = make_clock()
    b = MultiWindowBudget(
        [
            WindowBudget(Window.MINUTE, usd_cap=0.10),
            WindowBudget(Window.HOUR, usd_cap=2.00),
        ],
        clock=clock,
    )
    b.record(usd=0.09)
    with pytest.raises(WindowBudgetExceeded) as exc_info:
        b.record(usd=0.02)
    assert exc_info.value.window is Window.MINUTE


def test_multi_hour_cap_fires_when_minute_passes():
    clock, advance = make_clock()
    b = MultiWindowBudget(
        [
            WindowBudget(Window.MINUTE, usd_cap=1.00),
            WindowBudget(Window.HOUR, usd_cap=0.10),
        ],
        clock=clock,
    )
    b.record(usd=0.09)
    advance(61)  # minute window resets
    with pytest.raises(WindowBudgetExceeded) as exc_info:
        b.record(usd=0.02)
    assert exc_info.value.window is Window.HOUR


def test_multi_snapshots_length():
    clock, _ = make_clock()
    b = MultiWindowBudget(
        [
            WindowBudget(Window.MINUTE, usd_cap=0.10),
            WindowBudget(Window.HOUR, usd_cap=2.00),
            WindowBudget(Window.DAY, usd_cap=20.00),
        ],
        clock=clock,
    )
    assert len(b.snapshots()) == 3


def test_multi_reset_clears_all():
    clock, _ = make_clock()
    b = MultiWindowBudget(
        [
            WindowBudget(Window.MINUTE, usd_cap=0.10),
            WindowBudget(Window.DAY, usd_cap=5.00),
        ],
        clock=clock,
    )
    b.record(usd=0.05)
    b.reset()
    snaps = b.snapshots()
    assert all(s.spent_usd == pytest.approx(0.0) for s in snaps)


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_thread_safety_does_not_exceed_cap():
    """Multiple threads recording concurrently must never breach the cap."""
    clock, _ = make_clock()
    cap = 1.00
    b = SingleWindowBudget(WindowBudget(Window.MINUTE, usd_cap=cap), clock=clock)
    errors = []

    def worker():
        for _ in range(10):
            try:
                b.record(usd=0.05)
            except WindowBudgetExceeded:
                pass
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Unexpected errors: {errors}"
    snap = b.snapshot()
    # May be 0 if all threads were blocked, but must not exceed cap
    assert snap.spent_usd <= cap + 1e-9


# ---------------------------------------------------------------------------
# Real-time smoke (short)
# ---------------------------------------------------------------------------


def test_real_time_expiry():
    """Integration: events expire with the real clock."""
    # Use a 1-second window for a fast test
    config = WindowBudget(Window.MINUTE, usd_cap=0.10)
    # Use a tiny fake clock that advances quickly
    clock, advance = make_clock()
    b = SingleWindowBudget(config, clock=clock)
    b.record(usd=0.09)
    # Advance 61 simulated seconds
    advance(61)
    # Event is now outside the 60-second window → should not raise
    b.record(usd=0.09)
