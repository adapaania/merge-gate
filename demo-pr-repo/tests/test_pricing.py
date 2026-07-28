from __future__ import annotations

import unittest
from decimal import Decimal

from src.pricing import discounted_price


class PricingTests(unittest.TestCase):
    def test_discount_is_applied_with_currency_precision(self) -> None:
        self.assertEqual(
            discounted_price(Decimal("19.99"), Decimal("0.10")),
            Decimal("17.99"),
        )

    def test_discount_rate_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            discounted_price(Decimal("10.00"), Decimal("0.75"))


if __name__ == "__main__":
    unittest.main()
