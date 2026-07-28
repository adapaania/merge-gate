from __future__ import annotations

import unittest

from src.auth import can_view_admin_audit


class AuthorizationTests(unittest.TestCase):
    def test_admin_can_view_audit_screen(self) -> None:
        self.assertTrue(can_view_admin_audit("admin"))

    def test_regular_user_cannot_view_audit_screen(self) -> None:
        self.assertFalse(can_view_admin_audit("user"))

    def test_support_cannot_view_audit_screen(self) -> None:
        self.assertFalse(can_view_admin_audit("support"))


if __name__ == "__main__":
    unittest.main()
