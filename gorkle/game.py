from __future__ import annotations

import logging

from typing import Protocol

from gorkle import copy
from gorkle.store import MAX_QUESTIONS, Store


class TrendsSource(Protocol):
    def get_trends(self, woeid: int) -> list[str]: ...


class TopicHost(Protocol):
    def pick_topic(self, trends: list[str]) -> str: ...

    def interpret(self, topic: str, player_text: str, questions_left: int) -> dict[str, str]: ...

logger = logging.getLogger("gorkle.game")

META = {"help", "?", "score", "rules", "how", "how to play"}
_SMALLTALK_FIRST = {"hi", "hello", "hey", "yo", "sup", "gm", "thanks", "thx"}
_SMALLTALK = _SMALLTALK_FIRST | {"start", "play", "good morning", "thank you", "thanks!"}
_QUESTION_START = {
    "am",
    "are",
    "can",
    "could",
    "did",
    "do",
    "does",
    "had",
    "has",
    "have",
    "how",
    "is",
    "should",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "why",
    "will",
    "would",
}


def is_smalltalk(text: str) -> bool:
    lowered = text.strip().lower().rstrip("!.")
    if not lowered or lowered in META or lowered == "score":
        return False
    if lowered in _SMALLTALK:
        return True
    first = lowered.split()[0].strip("\"'“”‘’,")
    return first in _SMALLTALK_FIRST


def is_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    first = stripped.split()[0].lower().strip("\"'“”‘’,.!;:")
    first = first.replace("n’t", "n't")
    if first.endswith("n't"):
        first = first[:-3]
    return first in _QUESTION_START


class GameEngine:
    def __init__(self, store: Store, grok: TopicHost, api: TrendsSource, woeid: int) -> None:
        self.store = store
        self.grok = grok
        self.api = api
        self.woeid = woeid

    def ensure_daily_secret(self) -> str | None:
        today = self.store.today()
        self.store.expire_open_games(today)
        existing = self.store.get_secret(today)
        if existing:
            return existing.topic
        trends = self.api.get_trends(self.woeid)
        if not trends:
            logger.error("no_live_trends woeid=%s", self.woeid)
            return None
        topic = self.grok.pick_topic(trends)
        self.store.save_secret(today, topic, trends)
        logger.info("daily_secret date=%s topic=%s trends=%d", today, topic, len(trends))
        return topic

    def handle(self, user_id: str, text: str) -> str:
        today = self.store.today()
        secret = self.store.get_secret(today)
        if not secret:
            topic = self.ensure_daily_secret()
            if not topic:
                return copy.no_secret_yet()
            secret = self.store.get_secret(today)
            assert secret is not None

        stripped = text.strip()
        lowered = stripped.lower()

        game = self.store.get_game(user_id, today)
        if game and game.status in {"won", "lost"}:
            return copy.already_played(game, secret.topic)

        intro = ""
        if game is None:
            game = self.store.start_game(user_id, today)
            if lowered in META or is_smalltalk(stripped):
                self.store.record_turn(user_id, today, "assistant", copy.welcome())
                return copy.welcome()
            intro = copy.welcome(ask=False) + "\n\n"
            self.store.record_turn(user_id, today, "assistant", copy.welcome(ask=False))

        if lowered in META or lowered == "score":
            return f"{copy.HELP}\n\n{copy.remaining_line(game)}"

        if is_smalltalk(stripped):
            say = "Ask it as a question."
            reply = intro + f"{say}\n\n{copy.remaining_line(game)}"
            self.store.record_turn(user_id, today, "user", stripped)
            self.store.record_turn(user_id, today, "assistant", reply)
            return reply

        left_before = MAX_QUESTIONS - game.questions_used
        verdict = self.grok.interpret(secret.topic, stripped, left_before)
        kind = verdict["kind"]
        say = verdict["say"]

        if kind == "help":
            self.store.record_turn(user_id, today, "user", stripped)
            self.store.record_turn(user_id, today, "assistant", say)
            return f"{say}\n\n{copy.remaining_line(game)}"

        counts = is_question(stripped) and kind in {"yesno", "guess_yes", "guess_no"}

        self.store.record_turn(user_id, today, "user", stripped)
        if counts:
            game = self.store.increment_question(user_id, today)

        if kind == "guess_yes":
            game = self.store.finish_game(user_id, today, "won", stripped)
            reply = intro + copy.win(secret.topic, game)
            self.store.record_turn(user_id, today, "assistant", reply)
            return reply

        if counts and game.questions_used >= MAX_QUESTIONS:
            game = self.store.finish_game(user_id, today, "lost", None)
            reply = intro + copy.lose(secret.topic, game)
            self.store.record_turn(user_id, today, "assistant", reply)
            return reply

        reply = intro + f"{say}\n\n{copy.remaining_line(game)}"
        self.store.record_turn(user_id, today, "assistant", reply)
        return reply
