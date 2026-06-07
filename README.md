# llm-budget-window-py

Time-windowed USD/token budget for LLM calls. Enforce per-minute, per-hour, and per-day spending caps simultaneously. Thread-safe. Zero dependencies.

Python port of [llm-budget-window](https://github.com/MukundaKatta/llm-budget-window) (Rust crate).

Part of the [@mukundakatta agent-stack](https://github.com/MukundaKatta).

## Install

```bash
pip install llm-budget-window
```

## Quickstart

```python
from llm_budget_window import MultiWindowBudget, Window, WindowBudget, WindowBudgetExceeded

budget = MultiWindowBudget([
    WindowBudget(window=Window.MINUTE, usd_cap=0.10),
    WindowBudget(window=Window.HOUR,   usd_cap=2.00),
    WindowBudget(window=Window.DAY,    usd_cap=20.00),
])

try:
    budget.record(usd=0.05, tokens=500)
except WindowBudgetExceeded as e:
    print(f"{e.window} budget exceeded: spent={e.spent_usd:.4f} cap={e.cap_usd:.4f}")
```

## API

### `Window`

Enum with `MINUTE` (60 s), `HOUR` (3600 s), `DAY` (86400 s).

### `WindowBudget(window, usd_cap, token_cap=None)`

Configuration for one sliding-window enforcer.

### `SingleWindowBudget(config, *, clock=None)`

Single sliding-window budget. Methods:

- `record(*, usd, tokens=0)` — record a call; raises `WindowBudgetExceeded` if cap would be breached
- `snapshot() -> WindowSnapshot` — point-in-time view of current usage
- `reset()` — clear all events (useful in tests)

### `MultiWindowBudget(budgets, clock=None)`

Enforces multiple windows simultaneously. Methods:

- `record(*, usd, tokens=0)` — checks all windows; raises on first breach
- `snapshots() -> list[WindowSnapshot]` — snapshot of all windows
- `reset()` — reset all windows

### `WindowSnapshot`

NamedTuple: `window`, `spent_usd`, `usd_cap`, `spent_tokens`, `token_cap`, `events`.

### `WindowBudgetExceeded`

Exception with fields: `window`, `spent_usd`, `requested_usd`, `cap_usd`, `spent_tokens`, `requested_tokens`, `cap_tokens`.

## Token cap

```python
from llm_budget_window import SingleWindowBudget, Window, WindowBudget

b = SingleWindowBudget(
    WindowBudget(Window.HOUR, usd_cap=5.00, token_cap=500_000)
)
b.record(usd=0.01, tokens=1000)
```

## Pairing with `token-budget-py`

`token-budget-py` provides a global in-process cap across all calls. `llm-budget-window` adds time-scoped enforcement so a burst in one minute doesn't exhaust a daily budget. Use both:

```python
from token_budget import BudgetCap
from llm_budget_window import MultiWindowBudget, Window, WindowBudget

global_cap = BudgetCap(usd_cap=100.00)
window_cap = MultiWindowBudget([
    WindowBudget(Window.MINUTE, usd_cap=0.50),
    WindowBudget(Window.DAY, usd_cap=20.00),
])

def call_llm(prompt):
    cost = estimate_cost(prompt)
    global_cap.check(cost)    # global lifetime cap
    window_cap.record(usd=cost)  # sliding-window cap
    return actual_llm_call(prompt)
```

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

30 tests. Covers sliding-window expiry, token caps, thread safety, multi-window enforcement, `WindowSnapshot`, `reset()`, and type-hint resolution.

## License

MIT.
