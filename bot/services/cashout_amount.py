"""Player cashout amounts are whole dollars: cents are floored and stay in the account."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal


def floor_cashout_amount(amount: Decimal) -> Decimal:
    """Drop the cents (50.50 -> 50). Anything under $1 floors to 0."""
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount or 0))
    return value.to_integral_value(rounding=ROUND_FLOOR)
