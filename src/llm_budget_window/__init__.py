"""llm-budget-window: time-windowed USD/token budget for LLM calls.

Enforce per-minute, per-hour, and per-day spending caps simultaneously.
Thread-safe via a single lock. Zero external dependencies.

Python port of `llm-budget-window` (Rust crate).

Quick start::

    from llm_budget_window import MultiWindowBudget, Window, WindowBudget, WindowBudgetExceeded

    budget = MultiWindowBudget([
        WindowBudget(window=Window.MINUTE, usd_cap=0.10),
        WindowBudget(window=Window.HOUR,   usd_cap=2.00),
        WindowBudget(window=Window.DAY,    usd_cap=20.00),
    ])

    try:
        budget.record(usd=0.05, tokens=500)   # passes all three windows
    except WindowBudgetExceeded as e:
        print(e.window, e.spent_usd, e.cap_usd)
"""

from .core import (
    MultiWindowBudget,
    SingleWindowBudget,
    Window,
    WindowBudget,
    WindowBudgetExceeded,
    WindowSnapshot,
)

__all__ = [
    "MultiWindowBudget",
    "SingleWindowBudget",
    "Window",
    "WindowBudget",
    "WindowBudgetExceeded",
    "WindowSnapshot",
]

__version__ = "0.1.0"
