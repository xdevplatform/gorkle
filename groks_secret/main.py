from __future__ import annotations

import logging
import threading
import time

from groks_secret.bot import Bot
from groks_secret.chat_core import ChatCore
from groks_secret.config import Settings
from groks_secret.game import GameEngine
from groks_secret.grok import Grok
from groks_secret.health import start_health_server
from groks_secret.store import Store
from groks_secret.x_api import XChatClient

logger = logging.getLogger("groks_secret")


def build(settings: Settings) -> Bot:
    api = XChatClient(settings.access_token, bearer_token=settings.bearer_token)
    bot_user_id = settings.bot_user_id or api.get_my_user_id()
    core = ChatCore()
    if settings.private_keys_b64:
        version = settings.signing_key_version or "1"
        core.load_keys(settings.private_keys_b64, version)
        logger.info("keys_loaded_from_blob version=%s", version)
    else:
        juicebox_json, api_version, fingerprint = api.get_juicebox_config(bot_user_id)
        version = settings.signing_key_version or api_version
        if settings.fingerprint and fingerprint and settings.fingerprint != fingerprint:
            raise SystemExit(
                f"CHAT_FINGERPRINT mismatch: env={settings.fingerprint} api={fingerprint}"
            )
        core.unlock(juicebox_json, settings.pin, version)
        logger.info("keys_unlocked_via_juicebox version=%s", version)

    extra: list[str] = []
    for peer in settings.peer_user_ids:
        try:
            resolved = api.resolve_user_id(peer)
            extra.append(resolved)
            logger.info("peer_resolved %s -> %s", peer, resolved)
        except Exception:
            logger.warning("peer_resolve_failed peer=%s", peer, exc_info=True)
    store = Store(settings.db_path, database_url=settings.database_url)
    grok = Grok(settings)
    engine = GameEngine(store, grok, api, settings.trends_woeid)
    return Bot(core, api, store, engine, bot_user_id, extra_ids=extra, workers=settings.workers)


def _run_activity_stream(bot: Bot, bearer_token: str) -> None:
    logger.info("activity_stream_starting")
    while True:
        try:
            bot.api.ensure_chat_subscriptions(bot.bot_user_id)
            for event in bot.api.iter_activity_stream(bearer_token):
                try:
                    bot.note_activity(event)
                except Exception:
                    logger.exception("activity_event_failed")
        except Exception:
            logger.exception("activity_stream_disconnected")
            time.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    start_health_server(settings.port)
    bot = build(settings)
    backend = "postgres" if settings.database_url else "sqlite"
    logger.info(
        "playgrokkle_running bot=%s poll=%.1fs workers=%d store=%s",
        bot.bot_user_id,
        settings.poll_interval,
        settings.workers,
        backend,
    )
    bot.engine.ensure_daily_secret()
    bot.api.ensure_chat_subscriptions(bot.bot_user_id)
    if settings.bearer_token:
        bot.stream_enabled = True
        threading.Thread(
            target=_run_activity_stream,
            args=(bot, settings.bearer_token),
            name="activity-stream",
            daemon=True,
        ).start()
    else:
        logger.warning(
            "No X_BEARER_TOKEN. Inbox list is primary-only; request-folder DMs "
            "arrive via GET /2/activity/stream with an app-only Bearer token."
        )
    while True:
        try:
            bot.poll_inbox()
        except Exception:
            logger.exception("poll_inbox_failed")
        if bot.pending():
            delay = 0.2
        elif bot.stream_enabled:
            delay = 1.0
        else:
            delay = settings.poll_interval
        time.sleep(delay)


if __name__ == "__main__":
    main()
