from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from groks_secret.game import GameEngine, is_question, is_smalltalk
from groks_secret.store import MAX_QUESTIONS, Store


class FakeGrok:
    def __init__(self) -> None:
        self.next = {"kind": "yesno", "say": "Yes."}
        self.interpret_calls = 0

    def pick_topic(self, trends: list[str]) -> str:
        return "Grok"

    def interpret(self, topic: str, player_text: str, questions_left: int) -> dict[str, str]:
        self.interpret_calls += 1
        return dict(self.next)


class GameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite")
        self.grok = FakeGrok()
        self.api = MagicMock()
        self.api.get_trends.return_value = ["Grok", "SpaceX"]
        self.engine = GameEngine(self.store, self.grok, self.api, 23424977)
        self.engine.ensure_daily_secret()

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_help_does_not_consume_a_question(self) -> None:
        self.engine.handle("1", "help")
        game = self.store.get_game("1")
        assert game is not None
        self.assertEqual(game.questions_used, 0)
        self.assertEqual(game.status, "in_progress")

    def test_win_and_second_play_blocked(self) -> None:
        self.grok.next = {"kind": "guess_yes", "say": "You got it."}
        reply = self.engine.handle("2", "is it grok?")
        self.assertIn("mask slips", reply)
        game = self.store.get_game("2")
        assert game is not None
        self.assertEqual(game.status, "won")
        again = self.engine.handle("2", "play again")
        self.assertIn("already", again)

    def test_twenty_wrong_guesses_lose(self) -> None:
        self.grok.next = {"kind": "guess_no", "say": "No."}
        last = ""
        for i in range(MAX_QUESTIONS):
            last = self.engine.handle("3", f"is it thing {i}?")
        self.assertIn("PlayGrokkle ends here", last)
        game = self.store.get_game("3")
        assert game is not None
        self.assertEqual(game.status, "lost")
        self.assertEqual(game.questions_used, MAX_QUESTIONS)

    def test_reset_clears_games_and_secret(self) -> None:
        self.engine.handle("9", "help")
        self.assertIsNotNone(self.store.get_secret())
        self.assertIsNotNone(self.store.get_game("9"))
        self.store.reset_play_state()
        self.assertIsNone(self.store.get_secret())
        self.assertEqual(self.store.player_ids(), [])

    def test_chat_does_not_consume_a_question(self) -> None:
        self.grok.next = {"kind": "other", "say": "Ask it as a question."}
        self.engine.handle("4", "hi again")
        game = self.store.get_game("4")
        assert game is not None
        self.assertEqual(game.questions_used, 0)

    def test_statement_guess_does_not_consume(self) -> None:
        self.grok.next = {"kind": "guess_no", "say": "Not that."}
        self.engine.handle("5", "batman")
        game = self.store.get_game("5")
        assert game is not None
        self.assertEqual(game.questions_used, 0)

    def test_question_guess_does_consume(self) -> None:
        self.grok.next = {"kind": "guess_no", "say": "Not that."}
        self.engine.handle("6", "is it batman?")
        game = self.store.get_game("6")
        assert game is not None
        self.assertEqual(game.questions_used, 1)

    def test_yesno_statement_does_not_consume(self) -> None:
        self.grok.next = {"kind": "yesno", "say": "Yes."}
        self.engine.handle("7", "sounds like a movie")
        game = self.store.get_game("7")
        assert game is not None
        self.assertEqual(game.questions_used, 0)

    def test_yesno_question_does_consume(self) -> None:
        self.grok.next = {"kind": "yesno", "say": "Yes."}
        self.engine.handle("8", "is it a movie")
        game = self.store.get_game("8")
        assert game is not None
        self.assertEqual(game.questions_used, 1)

    def test_smalltalk_skips_grok(self) -> None:
        self.engine.handle("10", "help")
        before = self.grok.interpret_calls
        reply = self.engine.handle("10", "hi again")
        self.assertEqual(self.grok.interpret_calls, before)
        self.assertIn("question", reply.lower())
        game = self.store.get_game("10")
        assert game is not None
        self.assertEqual(game.questions_used, 0)

    def test_no_trends_does_not_invent_a_secret(self) -> None:
        self.api.get_trends.return_value = []
        self.store.reset_play_state()
        self.assertIsNone(self.engine.ensure_daily_secret())
        self.assertIsNone(self.store.get_secret())


class QuestionDetectTests(unittest.TestCase):
    def test_marks_and_starts(self) -> None:
        self.assertTrue(is_question("batman?"))
        self.assertTrue(is_question("is it a movie"))
        self.assertTrue(is_question("Does it fly"))
        self.assertFalse(is_question("batman"))
        self.assertFalse(is_question("hi"))
        self.assertFalse(is_question("sounds like a movie"))

    def test_smalltalk(self) -> None:
        self.assertTrue(is_smalltalk("hi"))
        self.assertTrue(is_smalltalk("hi again"))
        self.assertTrue(is_smalltalk("thanks!"))
        self.assertFalse(is_smalltalk("is it a movie"))
        self.assertFalse(is_smalltalk("batman"))
        self.assertFalse(is_smalltalk("help"))


if __name__ == "__main__":
    unittest.main()
