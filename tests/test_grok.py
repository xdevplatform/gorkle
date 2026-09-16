from __future__ import annotations

import unittest

from gorkle.grok import clamp_say, leaks_secret


class LeakClampTests(unittest.TestCase):
    def test_detects_name_and_token(self) -> None:
        self.assertTrue(leaks_secret("Yes. Bo Jackson, obviously.", "Bo Jackson"))
        self.assertTrue(leaks_secret("Yes. Jackson-era stuff.", "Bo Jackson"))
        self.assertFalse(leaks_secret("Yes.", "Bo Jackson"))
        self.assertFalse(leaks_secret("No. Keep going.", "Bo Jackson"))

    def test_prompt_resolves_nicknames(self) -> None:
        from gorkle.grok import ANSWER_SYSTEM

        text = ANSWER_SYSTEM.format(topic="Gronk (Rob Gronkowski)")
        self.assertIn("REAL-WORLD ENTITY", text)
        self.assertIn("Is it a person?", text)
        self.assertIn("Yes.", text)

    def test_strips_yesno_to_verdict(self) -> None:
        self.assertEqual(
            clamp_say("yesno", "Yes. Two sports, one Jackson.", "Bo Jackson"),
            "Yes.",
        )
        self.assertEqual(clamp_say("guess_no", "Not Bo Jackson.", "Bo Jackson"), "Not that.")
        self.assertEqual(clamp_say("yesno", "Yes.", "Bo Jackson"), "Yes.")
        self.assertEqual(
            clamp_say("guess_yes", "The mask slips. Bo Jackson.", "Bo Jackson"),
            "The mask slips. Bo Jackson.",
        )


if __name__ == "__main__":
    unittest.main()
