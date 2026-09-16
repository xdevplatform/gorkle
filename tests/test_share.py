from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from gorkle import copy
from gorkle.share_card import BASE_IMAGE, compose_share_card
from gorkle.store import Game
from gorkle.x_api import XChatClient

ET = ZoneInfo("America/New_York")


def _game(status: str, questions: int) -> Game:
    today = datetime.now(ET).date().isoformat()
    return Game(
        date=today,
        user_id="1",
        status=status,
        questions_used=questions,
        guessed=None,
        started_at=today,
        finished_at=today,
    )


class ShareCardTests(unittest.TestCase):
    def test_win_copy_has_no_paste_block(self) -> None:
        text = copy.win("Grok", _game("won", 7))
        self.assertIn("mask slips", text)
        self.assertNotIn("Shareable", text)
        self.assertIn("PlayGorkle", copy.welcome())
        self.assertIn("@PlayGorkle", copy.share_card(_game("won", 4)))
        self.assertNotIn("@groksecret", copy.share_card(_game("won", 4)))
        self.assertIn("You did it!", copy.share_caption(_game("won", 4)))
        self.assertNotIn("You did it!", copy.share_caption(_game("lost", 20)))
        self.assertNotIn("souvenir", copy.share_caption(_game("won", 4)).lower())

    def test_overlay_jpeg_uses_logo(self) -> None:
        self.assertTrue(BASE_IMAGE.exists())
        jpeg = compose_share_card(_game("won", 4))
        self.assertGreater(len(jpeg), 1000)
        self.assertEqual(jpeg[:2], b"\xff\xd8")


class TrendsParseTests(unittest.TestCase):
    def test_news_and_woeid_shapes(self) -> None:
        names = XChatClient._trend_names(
            {
                "data": [
                    {"trend_name": "Angie Nixon"},
                    {"name": "Venus Williams gets a U.S. Open wild card"},
                    {"hook": "ignored if name present", "name": "ZZ Top"},
                ]
            }
        )
        self.assertEqual(
            names,
            [
                "Angie Nixon",
                "Venus Williams gets a U.S. Open wild card",
                "ZZ Top",
            ],
        )


if __name__ == "__main__":
    unittest.main()
