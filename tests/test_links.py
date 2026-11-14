import unittest
from messenger_links import unlink


class LinkTests(unittest.TestCase):
    def test_legacy_keys_duplicates_and_repeat(self):
        class Ref:
            def get(self):
                return self.rows
            def transaction(self, fn):
                self.rows = fn(self.rows)
        ref = Ref()
        other = {"siteUserId": "new", "tgUserId": 789}
        ref.rows = {"new": other, "legacy": {"siteUserId": "new", "tgUserId": 123},
                    "123": {"uid": "old"}, "another": {"telegramUserId": "123"}}
        self.assertEqual(3, len(unlink(ref, 123)))
        self.assertEqual({"new": other}, ref.rows)
        self.assertEqual([], unlink(ref, 123))
