"""Pricing helpers for the sandbox service."""

from decimal import Decimal, ROUND_HALF_UP


def discounted_price(amount: Decimal, discount_rate: Decimal) -> Decimal:
    """Apply a bounded discount and return a two-decimal currency amount."""

    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not Decimal("0") <= discount_rate <= Decimal("0.50"):
        raise ValueError("discount_rate must be between 0 and 0.50")

    discounted = amount * (Decimal("1") - discount_rate)
    return discounted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
