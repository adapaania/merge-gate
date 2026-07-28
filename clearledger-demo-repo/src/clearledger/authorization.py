"""Role boundaries for creating and releasing payouts."""

from __future__ import annotations

from decimal import Decimal


PAYOUT_CREATORS = {"finance_admin", "treasury_operator"}


def can_create_payout(role: str) -> bool:
    """Return whether a role can create a payout request."""

    return role in PAYOUT_CREATORS


def can_release_payout(role: str, amount: Decimal) -> bool:
    """Return whether a role may release a payout without another approver."""

    if role == "finance_admin":
        return True
    return role == "treasury_operator" and amount <= Decimal("5000.00")
