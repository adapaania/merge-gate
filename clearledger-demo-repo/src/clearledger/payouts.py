"""Payout evaluation and settlement calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from clearledger.authorization import can_create_payout, can_release_payout


MAX_AUTOMATIC_PAYOUT = Decimal("5000.00")
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP"}


@dataclass(frozen=True)
class PayoutDecision:
    approved: bool
    requires_manual_review: bool
    reason: str


def evaluate_payout(role: str, amount: Decimal, currency: str) -> PayoutDecision:
    """Evaluate whether a payout can enter the settlement queue."""

    if amount <= 0:
        raise ValueError("amount must be positive")
    if currency not in SUPPORTED_CURRENCIES:
        return PayoutDecision(False, True, "Unsupported settlement currency.")
    if not can_create_payout(role):
        return PayoutDecision(False, True, "Role cannot create payouts.")
    if amount > MAX_AUTOMATIC_PAYOUT or not can_release_payout(role, amount):
        return PayoutDecision(
            False,
            True,
            "Payout requires a finance administrator.",
        )
    return PayoutDecision(True, False, "Payout satisfies automatic controls.")


def settlement_amount(amount: Decimal, fee_rate: Decimal) -> Decimal:
    """Return the net settlement amount with currency precision."""

    if amount <= 0:
        raise ValueError("amount must be positive")
    if fee_rate < 0 or fee_rate > Decimal("0.05"):
        raise ValueError("fee_rate must be between 0 and 0.05")
    net = amount * (Decimal("1") - fee_rate)
    return net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
