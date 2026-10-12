"""У каждого вида блокировки есть паспорт: что утверждает, на чём держится, когда врёт и чем это отсечено."""

from __future__ import annotations

import unittest

from diagnostics.block_kind import KINDS
from diagnostics.rule_passports import PASSPORTS


class PassportTests(unittest.TestCase):
    def test_every_kind_has_a_passport(self) -> None:
        self.assertEqual(set(PASSPORTS), set(KINDS))

    def test_every_passport_names_false_alarms_and_their_control(self) -> None:
        for kind, passport in PASSPORTS.items():
            with self.subTest(kind=kind):
                for field in ("claim", "evidence", "false_alarms", "control"):
                    self.assertGreater(len(getattr(passport, field).strip()), 20, field)


if __name__ == "__main__":
    unittest.main()
