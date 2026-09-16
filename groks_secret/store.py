from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from groks_secret.config import ET, ROOT

MAX_QUESTIONS = 20

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS daily_secret (
    date TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    trends_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    date TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    questions_used INTEGER NOT NULL DEFAULT 0,
    guessed TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY (date, user_id)
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_events (
    conversation_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (conversation_id, event_id)
);
CREATE TABLE IF NOT EXISTS cursors (
    conversation_id TEXT PRIMARY KEY,
    pagination_token TEXT,
    bootstrapped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watched_conversations (
    conversation_id TEXT PRIMARY KEY,
    peer_user_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id);
CREATE INDEX IF NOT EXISTS idx_turns_date_user ON turns(date, user_id);
"""

_SCHEMA_PG = [
    """
    CREATE TABLE IF NOT EXISTS daily_secret (
        date TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        trends_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
        date TEXT NOT NULL,
        user_id TEXT NOT NULL,
        status TEXT NOT NULL,
        questions_used INTEGER NOT NULL DEFAULT 0,
        guessed TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        PRIMARY KEY (date, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        id BIGSERIAL PRIMARY KEY,
        date TEXT NOT NULL,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seen_events (
        conversation_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        PRIMARY KEY (conversation_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cursors (
        conversation_id TEXT PRIMARY KEY,
        pagination_token TEXT,
        bootstrapped INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watched_conversations (
        conversation_id TEXT PRIMARY KEY,
        peer_user_id TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_turns_date_user ON turns(date, user_id)",
]


@dataclass
class DailySecret:
    date: str
    topic: str
    trends_json: str


@dataclass
class Game:
    date: str
    user_id: str
    status: str  # in_progress | won | lost | expired
    questions_used: int
    guessed: str | None
    started_at: str
    finished_at: str | None


def _pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


class Store:
    def __init__(self, path: Path | None = None, database_url: str = "") -> None:
        self._lock = threading.RLock()
        self._pool = None
        self._conn: sqlite3.Connection | None = None
        self._pg = bool(database_url.strip())
        if self._pg:
            try:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool
            except ImportError as err:
                raise SystemExit("DATABASE_URL is set but psycopg is not installed") from err
            cache = path or (ROOT / "data")
            cache.mkdir(parents=True, exist_ok=True)
            self.path = cache / "cache.sqlite"
            self._pool = ConnectionPool(
                _pg_url(database_url.strip()),
                min_size=1,
                max_size=8,
                kwargs={"row_factory": dict_row, "autocommit": True},
            )
            self._init_pg()
        else:
            if path is None:
                raise ValueError("Store needs a sqlite path or DATABASE_URL")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.path = path
            self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA_SQLITE)
            self._conn.commit()

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._pg else sql

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        sql = self._sql(sql)
        if self._pool is not None:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    return dict(row) if row else None
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        sql = self._sql(sql)
        if self._pool is not None:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return [dict(r) for r in cur.fetchall()]
        assert self._conn is not None
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        sql = self._sql(sql)
        if self._pool is not None:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            return
        assert self._conn is not None
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def _init_pg(self) -> None:
        assert self._pool is not None
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                for stmt in _SCHEMA_PG:
                    cur.execute(stmt)

    @staticmethod
    def today() -> str:
        return datetime.now(ET).date().isoformat()

    def get_secret(self, date: str | None = None) -> DailySecret | None:
        day = date or self.today()
        row = self._one(
            "SELECT date, topic, trends_json FROM daily_secret WHERE date = ?",
            (day,),
        )
        return DailySecret(**row) if row else None

    def save_secret(self, date: str, topic: str, trends: list[str]) -> DailySecret:
        payload = json.dumps(trends)
        now = datetime.now(ET).isoformat()
        self._run(
            """
            INSERT INTO daily_secret(date, topic, trends_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (date) DO UPDATE SET topic = excluded.topic, trends_json = excluded.trends_json
            """,
            (date, topic, payload, now),
        )
        return DailySecret(date=date, topic=topic, trends_json=payload)

    def get_game(self, user_id: str, date: str | None = None) -> Game | None:
        day = date or self.today()
        row = self._one(
            """
            SELECT date, user_id, status, questions_used, guessed, started_at, finished_at
            FROM games WHERE date = ? AND user_id = ?
            """,
            (day, user_id),
        )
        return Game(**row) if row else None

    def start_game(self, user_id: str, date: str | None = None) -> Game:
        day = date or self.today()
        now = datetime.now(ET).isoformat()
        self._run(
            """
            INSERT INTO games(date, user_id, status, questions_used, guessed, started_at, finished_at)
            VALUES (?, ?, 'in_progress', 0, NULL, ?, NULL)
            ON CONFLICT (date, user_id) DO NOTHING
            """,
            (day, user_id, now),
        )
        game = self.get_game(user_id, day)
        assert game is not None
        return game

    def expire_open_games(self, before_date: str) -> None:
        now = datetime.now(ET).isoformat()
        self._run(
            """
            UPDATE games
            SET status = 'expired', finished_at = ?
            WHERE status = 'in_progress' AND date < ?
            """,
            (now, before_date),
        )

    def record_turn(self, user_id: str, date: str, role: str, text: str) -> None:
        self._run(
            "INSERT INTO turns(date, user_id, role, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (date, user_id, role, text, datetime.now(ET).isoformat()),
        )

    def increment_question(self, user_id: str, date: str) -> Game:
        self._run(
            "UPDATE games SET questions_used = questions_used + 1 WHERE date = ? AND user_id = ?",
            (date, user_id),
        )
        game = self.get_game(user_id, date)
        assert game is not None
        return game

    def finish_game(self, user_id: str, date: str, status: str, guessed: str | None) -> Game:
        self._run(
            """
            UPDATE games
            SET status = ?, guessed = ?, finished_at = ?
            WHERE date = ? AND user_id = ?
            """,
            (status, guessed, datetime.now(ET).isoformat(), date, user_id),
        )
        game = self.get_game(user_id, date)
        assert game is not None
        return game

    def seen(self, conversation_id: str, event_id: str) -> bool:
        row = self._one(
            "SELECT 1 AS ok FROM seen_events WHERE conversation_id = ? AND event_id = ?",
            (conversation_id, event_id),
        )
        return row is not None

    def mark_seen(self, conversation_id: str, event_id: str) -> None:
        self.try_claim(conversation_id, event_id)

    def try_claim(self, conversation_id: str, event_id: str) -> bool:
        row = self._one(
            """
            INSERT INTO seen_events(conversation_id, event_id)
            VALUES (?, ?)
            ON CONFLICT (conversation_id, event_id) DO NOTHING
            RETURNING conversation_id
            """,
            (conversation_id, event_id),
        )
        return row is not None

    def cursor(self, conversation_id: str) -> dict[str, Any]:
        row = self._one(
            "SELECT pagination_token, bootstrapped FROM cursors WHERE conversation_id = ?",
            (conversation_id,),
        )
        if not row:
            return {"pagination_token": None, "bootstrapped": False}
        return {
            "pagination_token": row["pagination_token"],
            "bootstrapped": bool(row["bootstrapped"]),
        }

    def set_cursor(
        self,
        conversation_id: str,
        pagination_token: str | None,
        bootstrapped: bool,
    ) -> None:
        self._run(
            """
            INSERT INTO cursors(conversation_id, pagination_token, bootstrapped)
            VALUES (?, ?, ?)
            ON CONFLICT (conversation_id) DO UPDATE SET
                pagination_token = excluded.pagination_token,
                bootstrapped = excluded.bootstrapped
            """,
            (conversation_id, pagination_token, int(bootstrapped)),
        )

    def watch_conversation(self, conversation_id: str, peer_user_id: str | None = None) -> None:
        cid = conversation_id.replace(":", "-")
        if not cid:
            return
        self._run(
            """
            INSERT INTO watched_conversations(conversation_id, peer_user_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (conversation_id) DO UPDATE SET
                peer_user_id = COALESCE(excluded.peer_user_id, watched_conversations.peer_user_id),
                updated_at = excluded.updated_at
            """,
            (cid, peer_user_id, datetime.now(ET).isoformat()),
        )

    def watched_ids(self) -> list[str]:
        rows = self._all(
            "SELECT conversation_id FROM watched_conversations ORDER BY updated_at DESC"
        )
        return [str(row["conversation_id"]) for row in rows]

    def player_ids(self) -> list[str]:
        rows = self._all("SELECT DISTINCT user_id FROM games")
        return [str(row["user_id"]) for row in rows]

    def reset_play_state(self) -> None:
        for table in ("daily_secret", "games", "turns"):
            self._run(f"DELETE FROM {table}")
        cards = self.path.parent / "share_cards"
        if cards.is_dir():
            for item in cards.glob("*"):
                if item.is_file():
                    item.unlink()

    def reset_play_state_once(self, release: str) -> bool:
        marker_conversation = "__maintenance__"
        marker_event = f"play-reset:{release}"
        if self.seen(marker_conversation, marker_event):
            return False
        self.reset_play_state()
        self.mark_seen(marker_conversation, marker_event)
        return True

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
