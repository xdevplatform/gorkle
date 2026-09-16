from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from gorkle.store import MAX_QUESTIONS, Game

ET = ZoneInfo("America/New_York")

HANDLE = "@PlayGorkle"

HELP = (
    "This is PlayGorkle. One secret, pulled from what's trending on X. "
    "Yes-or-no only — you get 20. A guess counts if you ask it as a question.\n\n"
    "One game per person per day. Midnight Eastern, a new one."
)


def day_label(day: str) -> str:
    d = datetime.fromisoformat(day)
    return f"{d.strftime('%A, %b')} {d.day}"


_label = day_label


def welcome(questions_left: int = MAX_QUESTIONS, *, ask: bool = True) -> str:
    text = (
        f"PlayGorkle · {day_label(datetime.now(ET).date().isoformat())}\n\n"
        f"{HELP}"
    )
    if ask:
        text += f"\n\n{questions_left} left. Ask."
    return text


def already_played(game: Game, topic: str) -> str:
    if game.status == "won":
        n = game.questions_used
        return (
            f"You already found it — in {n} question{'s' if n != 1 else ''}.\n\n"
            f"It was {topic}."
        )
    if game.status == "lost":
        return f"Today's trail is cold. The secret was {topic}."
    return "Midnight Eastern. Then we play again."


def share_score_line(game: Game) -> str:
    return f"{game.questions_used}/{MAX_QUESTIONS}"


def share_caption(game: Game) -> str:
    if game.status == "won":
        return "You did it! Share how you did, and come back tomorrow for your next shot..."
    return "Share how you did, and come back tomorrow for your next shot..."


def share_card(game: Game) -> str:
    label = day_label(game.date)
    if game.status == "won":
        body = f"Guessed it in {game.questions_used}/{MAX_QUESTIONS}."
    elif game.status == "lost":
        body = f"Didn't crack it in {MAX_QUESTIONS}."
    else:
        body = "Still playing."
    return f"PlayGorkle · {label}\n{body}\nPlay at {HANDLE}"


def remaining_line(game: Game) -> str:
    left = MAX_QUESTIONS - game.questions_used
    if left == 0:
        return "None left."
    return f"{left} left."


def win(topic: str, game: Game) -> str:
    return (
        f"The mask slips. It was {topic}.\n"
        f"{game.questions_used}/{MAX_QUESTIONS}."
    )


def lose(topic: str, game: Game) -> str:
    return f"PlayGorkle ends here. It was {topic}."


def expire_then_start(old_topic: str, old_date: str) -> str:
    return (
        f"New day. Yesterday ({day_label(old_date)}) it was {old_topic}.\n\n"
        f"{welcome()}"
    )


def no_secret_yet() -> str:
    return "The puzzle isn't set yet. Try me in a minute."
