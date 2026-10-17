"""
core/costs.py

What Escanor's model calls cost, and a daily spend cap.

Prices are US$ per million tokens (input, output). The Claude rows are
Anthropic's list prices (Haiku 5.5's short-prompt rate); add or override
any model under [budget.prices] in config.toml, e.g.
    [budget.prices]
    "gpt-5" = [1.25, 10.0]
Unknown models count as $0, so set prices for paid non-Claude models if
the cap should include them. Cache reads are cheaper than full input, so
these numbers overestimate -- the safe direction for a cap.
"""
from __future__ import annotations
from datetime import datetime, time, timezone
from typing import Callable

PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-1": (10.00, 50.00),
    "mock-model": (0.0, 0.0),
}


def price_of(model: str, overrides: dict | None = None) -> tuple[float, float] | None:
    overrides = overrides or {}
    if model in overrides:
        value = overrides[model]
        return float(value[0]), float(value[1])
    return PRICES.get(model)


def cost(rows: list[dict], overrides: dict | None = None) -> tuple[float, list[dict]]:
    """Total US$ and the rows annotated with `usd` (None when unpriced)."""
    total = 0.0
    annotated = []
    for row in rows:
        price = price_of(row["model"], overrides)
        usd = None
        if price is not None:
            usd = (row["input_tokens"] * price[0] + row["output_tokens"] * price[1]) / 1_000_000
            total += usd
        annotated.append({**row, "usd": usd})
    return total, annotated


def start_of_local_day_utc(now: datetime | None = None) -> str:
    """Today's local midnight, as the UTC ISO string telemetry compares with."""
    local = (now or datetime.now()).astimezone()
    midnight = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


class DailyBudget:
    """Checked by the agent before every model call."""

    def __init__(self, daily_usd: float, usage_since: Callable[[str], list[dict]], overrides: dict | None = None,
                 now: Callable[[], datetime] = datetime.now):
        self.daily_usd = daily_usd
        self._usage_since = usage_since
        self._overrides = overrides or {}
        self._now = now

    def spent_today(self) -> float:
        total, _ = cost(self._usage_since(start_of_local_day_utc(self._now())), self._overrides)
        return total

    def check(self) -> str | None:
        """None if within budget, else a message explaining the stop."""
        if self.daily_usd <= 0:
            return None
        spent = self.spent_today()
        if spent >= self.daily_usd:
            return (f"Today's model budget of ${self.daily_usd:.2f} is used up (${spent:.2f}). "
                    "Raise [budget] daily_usd in ~/.escanor/config.toml, or switch to /model local.")
        return None
