from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gorkle.store import Store


class StoreClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_claim_once(self) -> None:
        self.assertTrue(self.store.try_claim("c1", "e1"))
        self.assertFalse(self.store.try_claim("c1", "e1"))
        self.assertTrue(self.store.seen("c1", "e1"))
        self.assertTrue(self.store.try_claim("c1", "e2"))

    def test_play_state_reset_runs_once(self) -> None:
        today = self.store.today()
        self.store.save_secret(today, "Test topic", ["Test topic"])
        self.store.start_game("u1", today)
        self.store.record_turn("u1", today, "user", "Is it alive?")
        self.store.mark_seen("c1", "keep-me")

        self.assertTrue(self.store.reset_play_state_once("release-a"))
        self.assertIsNone(self.store.get_secret(today))
        self.assertIsNone(self.store.get_game("u1", today))
        self.assertEqual(self.store.player_ids(), [])
        self.assertTrue(self.store.seen("c1", "keep-me"))

        self.store.start_game("u1", today)
        self.assertFalse(self.store.reset_play_state_once("release-a"))
        self.assertIsNotNone(self.store.get_game("u1", today))


class CiphertextIdTests(unittest.TestCase):
    def test_stable_and_distinct(self) -> None:
        from gorkle.bot import ciphertext_id

        self.assertEqual(ciphertext_id("abc"), ciphertext_id("abc"))
        self.assertTrue(ciphertext_id("abc").startswith("enc:"))
        self.assertNotEqual(ciphertext_id("abc"), ciphertext_id("abd"))


if __name__ == "__main__":
    unittest.main()
