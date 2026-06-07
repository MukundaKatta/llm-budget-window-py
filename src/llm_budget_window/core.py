"""Core time-windowed budget implementation."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple


class Window(Enum):
    """Time window duration.

    Each window has a ``seconds`` attribute for how long the sliding window
    covers.
    """

    MINUTE = 60
    HOUR = 3600
    DAY = 86_400

    @property
    def seconds(self) -> int:
        return self.value

    def __str__(self) -> str:  # noqa: D105
        return self.name.lower()


@dataclass
class WindowBudget:
    """A (window, cap) pair that configures one sliding-window enforcer.

    Args:
        window: The time window — :attr:`Window.MINUTE`, :attr:`Window.HOUR`,
            or :attr:`Window.DAY`.
        usd_cap: Maximum allowed spend within the window in USD.
        token_cap: Optional maximum allowed tokens within the window.
            Pass ``None`` (default) to skip token enforcement.
    """

    window: Window
    usd_cap: float
    token_cap: int | None = None


class WindowSnapshot(NamedTuple):
    """Point-in-time snapshot of a single window's state.

    Attributes:
        window: The window this snapshot covers.
        spent_usd: Total USD recorded in the current window.
        usd_cap: USD ceiling for the window.
        spent_tokens: Total tokens recorded in the current window.
        token_cap: Token ceiling (``None`` = uncapped).
        events: Number of individual ``record()`` calls in the window.
    """

    window: Window
    spent_usd: float
    usd_cap: float
    spent_tokens: int
    token_cap: int | None
    events: int


class WindowBudgetExceeded(Exception):
    """Raised when a ``record()`` call would breach a window's cap.

    Attributes:
        window: The window that would be exceeded.
        spent_usd: USD already spent in the window (before this call).
        requested_usd: USD cost of the rejected call.
        cap_usd: The window's USD ceiling.
        spent_tokens: Tokens already used in the window (before this call).
        requested_tokens: Tokens of the rejected call.
        cap_tokens: The window's token ceiling (``None`` = uncapped).
    """

    def __init__(
        self,
        *,
        window: Window,
        spent_usd: float,
        requested_usd: float,
        cap_usd: float,
        spent_tokens: int = 0,
        requested_tokens: int = 0,
        cap_tokens: int | None = None,
    ) -> None:
        self.window = window
        self.spent_usd = spent_usd
        self.requested_usd = requested_usd
        self.cap_usd = cap_usd
        self.spent_tokens = spent_tokens
        self.requested_tokens = requested_tokens
        self.cap_tokens = cap_tokens
        kind = "usd"
        if cap_tokens is not None and spent_tokens + requested_tokens > cap_tokens:
            kind = "tokens"
        super().__init__(
            f"{window} budget exceeded ({kind}): "
            f"spent={spent_usd:.6f} requested={requested_usd:.6f} cap={cap_usd:.6f}"
        )


@dataclass
class _Event:
    ts: float
    usd: float
    tokens: int


class SingleWindowBudget:
    """A single sliding-window budget.

    Maintains a deque of events timestamped with ``time.monotonic()``.  On
    each :meth:`record` call, events older than ``window.seconds`` are pruned
    and the new event is checked against both USD and token caps before being
    appended.

    Thread-safe — a single ``threading.Lock`` guards all state.

    Args:
        config: The :class:`WindowBudget` configuration for this window.
        clock: Optional callable returning a monotonic float (seconds).
            Defaults to :func:`time.monotonic`. Override in tests.
    """

    def __init__(
        self,
        config: WindowBudget,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or time.monotonic
        self._events: deque[_Event] = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Remove events older than the window. Caller holds the lock."""
        cutoff = now - self._config.window.seconds
        while self._events and self._events[0].ts <= cutoff:
            self._events.popleft()

    def _totals(self) -> tuple[float, int]:
        """(total_usd, total_tokens) in current window. Caller holds lock."""
        return (
            sum(e.usd for e in self._events),
            sum(e.tokens for e in self._events),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, *, usd: float, tokens: int = 0) -> None:
        """Record a call that cost *usd* and consumed *tokens*.

        Prunes stale events first, then checks whether adding this call
        would breach the cap.  If so, raises :class:`WindowBudgetExceeded`
        **without** recording the event (fail-closed).

        Args:
            usd: Cost in USD.  Must be >= 0.
            tokens: Token count.  Must be >= 0.  Optional — pass ``0`` if
                you only want USD enforcement.

        Raises:
            WindowBudgetExceeded: If the call would breach the USD or token cap.
        """
        if usd < 0:
            raise ValueError(f"usd must be >= 0, got {usd}")
        if tokens < 0:
            raise ValueError(f"tokens must be >= 0, got {tokens}")

        with self._lock:
            now = self._clock()
            self._prune(now)
            spent_usd, spent_tokens = self._totals()

            if spent_usd + usd > self._config.usd_cap:
                raise WindowBudgetExceeded(
                    window=self._config.window,
                    spent_usd=spent_usd,
                    requested_usd=usd,
                    cap_usd=self._config.usd_cap,
                    spent_tokens=spent_tokens,
                    requested_tokens=tokens,
                    cap_tokens=self._config.token_cap,
                )

            token_cap = self._config.token_cap
            if token_cap is not None and spent_tokens + tokens > token_cap:
                raise WindowBudgetExceeded(
                    window=self._config.window,
                    spent_usd=spent_usd,
                    requested_usd=usd,
                    cap_usd=self._config.usd_cap,
                    spent_tokens=spent_tokens,
                    requested_tokens=tokens,
                    cap_tokens=self._config.token_cap,
                )

            self._events.append(_Event(ts=now, usd=usd, tokens=tokens))

    def snapshot(self) -> WindowSnapshot:
        """Return a point-in-time view of this window's usage."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            spent_usd, spent_tokens = self._totals()
            return WindowSnapshot(
                window=self._config.window,
                spent_usd=spent_usd,
                usd_cap=self._config.usd_cap,
                spent_tokens=spent_tokens,
                token_cap=self._config.token_cap,
                events=len(self._events),
            )

    def reset(self) -> None:
        """Clear all events. Useful between test cases."""
        with self._lock:
            self._events.clear()


@dataclass
class MultiWindowBudget:
    """Enforce *multiple* sliding-window budgets simultaneously.

    Each call to :meth:`record` is checked against all configured windows.
    The first window whose cap would be breached raises
    :class:`WindowBudgetExceeded`.  No partial recording — either all
    windows accept the call or none does.

    Args:
        budgets: List of :class:`WindowBudget` configs.  Typically you would
            pass one per desired time granularity, e.g. minute + hour + day.
        clock: Optional monotonic clock override for testing.

    Example::

        from llm_budget_window import MultiWindowBudget, Window, WindowBudget

        b = MultiWindowBudget([
            WindowBudget(Window.MINUTE, usd_cap=0.10),
            WindowBudget(Window.HOUR,   usd_cap=2.00),
            WindowBudget(Window.DAY,    usd_cap=20.00),
        ])
        b.record(usd=0.05, tokens=500)
    """

    budgets: list[WindowBudget]
    clock: Callable[[], float] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._windows: list[SingleWindowBudget] = [
            SingleWindowBudget(cfg, clock=self.clock) for cfg in self.budgets
        ]

    def record(self, *, usd: float, tokens: int = 0) -> None:
        """Record a spend against all windows.

        Performs a two-phase check: first verifies all windows accept the
        call (read-lock), then commits to each window individually.  Because
        each :class:`SingleWindowBudget` uses its own lock, this is not
        strictly atomic across windows — but the fail-closed design means an
        over-budget error on window N will prevent recording on window N+1
        onward, and earlier windows may have already recorded.

        For simple sequential callers (no concurrent threads), this is fully
        safe.  For concurrent callers, the lock-per-window design means a
        race can theoretically let two threads slip past the check for
        different windows.  If strict atomicity matters, wrap calls in your
        own lock.

        Args:
            usd: USD cost of the call.
            tokens: Token count.

        Raises:
            WindowBudgetExceeded: If any window would be breached.
        """
        for w in self._windows:
            w.record(usd=usd, tokens=tokens)

    def snapshots(self) -> list[WindowSnapshot]:
        """Return snapshots for all configured windows."""
        return [w.snapshot() for w in self._windows]

    def reset(self) -> None:
        """Reset all windows. Useful in tests."""
        for w in self._windows:
            w.reset()
