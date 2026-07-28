from __future__ import annotations

import unittest
from decimal import Decimal

from clearledger.payouts import evaluate_payout, settlement_amount


class PayoutTests(unittest.TestCase):
    def test_small_payout_can_be_approved(self) -> None:
        result = evaluate_payout(
            "treasury_operator",
            Decimal("1250.00"),
            "USD",
        )
        self.assertTrue(result.approved)
        self.assertFalse(result.requires_manual_review)

    def test_large_payout_requires_manual_review(self) -> None:
        result = evaluate_payout(
            "treasury_operator",
            Decimal("6000.00"),
            "USD",
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.requires_manual_review)

    def test_unsupported_currency_requires_review(self) -> None:
        result = evaluate_payout(
            "finance_admin",
            Decimal("100.00"),
            "BTC",
        )
        self.assertFalse(result.approved)

    def test_settlement_keeps_currency_precision(self) -> None:
        self.assertEqual(
            settlement_amount(Decimal("100.00"), Decimal("0.025")),
            Decimal("97.50"),
        )


if __name__ == "__main__":
    unittest.main()
