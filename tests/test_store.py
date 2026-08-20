from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from groks_secret.store import Store


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


if __name__ == "__main__":
    unittest.main()
