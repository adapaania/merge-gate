from __future__ import annotations

import unittest
from decimal import Decimal

from clearledger.authorization import can_create_payout, can_release_payout


class AuthorizationTests(unittest.TestCase):
    def test_finance_roles_can_create_payouts(self) -> None:
        self.assertTrue(can_create_payout("finance_admin"))
        self.assertTrue(can_create_payout("treasury_operator"))

    def test_support_and_viewers_cannot_create_payouts(self) -> None:
        self.assertFalse(can_create_payout("support"))
        self.assertFalse(can_create_payout("viewer"))

    def test_only_admin_releases_large_payouts(self) -> None:
        amount = Decimal("6000.00")
        self.assertTrue(can_release_payout("finance_admin", amount))
        self.assertFalse(can_release_payout("treasury_operator", amount))


if __name__ == "__main__":
    unittest.main()
