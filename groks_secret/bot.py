from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from groks_secret.chat_core import ChatCore, message_text, prep_to_request
from groks_secret.game import GameEngine
from groks_secret.store import Game, Store
from groks_secret.x_api import RateLimited, XChatClient, _dump
from groks_secret import copy
from groks_secret.share_card import cache_path, compose_share_card

logger = logging.getLogger("groks_secret.bot")

RECENT_HOURS = 48
SWEEP_WITH_STREAM = 60.0
SWEEP_WITHOUT_STREAM = 8.0
STREAM_SWEEP_GRACE = 90.0
PLAYED_NOTICE_COOLDOWN = 180.0


def ciphertext_id(encoded: str) -> str:
    digest = hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"enc:{digest}"


def _created_at_key(item: dict[str, Any]) -> str:
    return str(item.get("created_at") or "")


def _parse_created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def activity_to_page(payload: dict[str, Any]) -> dict[str, Any] | None:
    encoded = payload.get("encoded_event")
    key_change = payload.get("conversation_key_change_event")
    if not encoded and not key_change:
        return None
    created = str(payload.get("created_at") or "")
    if not created and payload.get("created_at_msec") is not None:
        try:
            msec = int(payload["created_at_msec"])
            created = datetime.fromtimestamp(msec / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            created = ""
    item = {
        "id": payload.get("id"),
        "sender_id": payload.get("sender_id"),
        "conversation_id": str(payload.get("conversation_id") or "").replace(":", "-"),
        "encoded_event": encoded,
        "created_at": created,
    }
    meta: dict[str, Any] = {}
    if key_change:
        meta["conversation_key_events"] = (
            key_change if isinstance(key_change, list) else [key_change]
        )
    data = [item] if encoded else []
    return {"data": data, "meta": meta}


def _is_recent(item: dict[str, Any], hours: int = RECENT_HOURS) -> bool:
    dt = _parse_created_at(item.get("created_at"))
    if dt is None:
        return True
    return datetime.now(timezone.utc) - dt <= timedelta(hours=hours)


class Bot:
    def __init__(
        self,
        core: ChatCore,
        api: XChatClient,
        store: Store,
        engine: GameEngine,
        bot_user_id: str,
        extra_ids: list[str] | None = None,
        workers: int = 16,
    ) -> None:
        self.core = core
        self.api = api
        self.store = store
        self.engine = engine
        self.bot_user_id = bot_user_id
        self.extra_ids = extra_ids or []
        self._signing_keys: list[dict[str, str]] = []
        self._known_senders: set[str] = set()
        self._conv_keys: dict[str, dict[str, bytes]] = {}
        self._keys_lock = threading.Lock()
        self._crypto = threading.RLock()
        self._user_locks_guard = threading.Lock()
        self._user_locks: dict[str, threading.Lock] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="reply")
        self.core.set_identity(bot_user_id)
        self.core.set_cache_keys(True)
        self._watch: set[str] = set()
        self._resolved_peers: set[str] = set()
        self._discovered: queue.Queue[dict[str, Any]] = queue.Queue()
        self._decrypt_skip_logged = False
        self._requests_logged = False
        self._share_sent: set[str] = set()
        self._played_notice: dict[str, float] = {}
        self.stream_enabled = False
        self._until: dict[str, float] = {}
        self._next_sweep = 0.0
        self._need_poll: set[str] = set()
        self._stream_touch: dict[str, float] = {}
        for cid in store.watched_ids():
            self._watch.add(cid)

    def _remember_keys(self, conversation_id: str, keys: dict[str, bytes]) -> None:
        if not keys:
            return
        aliases = {conversation_id, conversation_id.replace(":", "-")}
        with self._keys_lock:
            for alias in aliases:
                if alias:
                    self._conv_keys.setdefault(alias, {}).update(keys)

    def _user_lock(self, user_id: str) -> threading.Lock:
        with self._user_locks_guard:
            lock = self._user_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                self._user_locks[user_id] = lock
            return lock

    def _watch_conversation(self, conversation_id: str, peer_user_id: str | None = None) -> None:
        cid = conversation_id.replace(":", "-")
        if not cid or cid == self.bot_user_id:
            return
        if cid not in self._watch:
            logger.info("watching conv=%s peer=%s", cid, peer_user_id or "")
        self._watch.add(cid)
        self.store.watch_conversation(cid, peer_user_id)

    def _register_signing_keys(self, events: list[dict[str, Any]]) -> None:
        senders = {
            str(e.get("sender_id"))
            for e in events
            if e.get("sender_id")
        } - self._known_senders
        for sender_id in senders:
            try:
                for pk in self.api.get_public_keys(sender_id):
                    identity = pk.get("public_key") or ""
                    signing = pk.get("signing_public_key") or ""
                    sig = pk.get("identity_public_key_signature") or ""
                    if identity and signing and sig:
                        if not self.core.verify_key_binding(identity, signing, sig):
                            logger.warning("key_binding_failed sender=%s", sender_id)
                            continue
                    elif not signing:
                        continue
                    self._signing_keys.append(
                        {
                            "user_id": sender_id,
                            "public_key_version": str(pk.get("public_key_version") or ""),
                            "public_key": signing,
                            "identity_public_key": identity,
                            "identity_public_key_signature": sig,
                        }
                    )
                if any(k["user_id"] == sender_id and k.get("public_key") for k in self._signing_keys):
                    self._known_senders.add(sender_id)
                else:
                    logger.warning("no_signing_key sender=%s", sender_id)
            except Exception:
                logger.warning("public_keys_fetch_failed sender=%s", sender_id, exc_info=True)
        if senders:
            with self._crypto:
                self.core.set_signing_keys(self._signing_keys)

    def _public_key_input(self, user_id: str) -> dict[str, str]:
        rows = self.api.get_public_keys(user_id)
        if not rows:
            raise RuntimeError(f"no public keys for {user_id}")
        row = rows[0]
        identity = row.get("public_key") or ""
        signing = row.get("signing_public_key") or ""
        sig = row.get("identity_public_key_signature") or ""
        if identity and signing and sig and not self.core.verify_key_binding(identity, signing, sig):
            raise RuntimeError(f"unverified public key for {user_id}")
        return {
            "user_id": user_id,
            "public_key": identity,
            "key_version": str(row.get("public_key_version") or ""),
        }

    def _ensure_keys(self, conversation_id: str, peer_id: str) -> None:
        try:
            bot_pk = self._public_key_input(self.bot_user_id)
            peer_pk = self._public_key_input(peer_id)
            with self._crypto:
                prepared = self.core.prepare_conversation_key_change([bot_pk, peer_pk])
            resp = self.api.add_conversation_keys(peer_id, prep_to_request(prepared))
            canonical = str((resp.get("data") or {}).get("conversation_id") or conversation_id)
            raw = prepared.get("conversation_key")
            version = prepared.get("conversation_key_version")
            if raw and version:
                self._remember_keys(canonical, {str(version): raw})
            logger.info("conversation_keys_added conv=%s", canonical)
        except Exception:
            logger.exception("conversation_keys_failed peer=%s", peer_id)

    def _ingest_page(
        self,
        conversation_id: str,
        page: dict[str, Any],
        *,
        reply: bool,
        only_recent: bool = False,
        reply_last_inbound: bool = False,
    ) -> None:
        raw: list[dict[str, Any]] = []
        for item in page.get("data") or []:
            dumped = item if isinstance(item, dict) else _dump(item)
            if not isinstance(dumped, dict):
                continue
            api_id = str(dumped.get("id") or "")
            event_conv = str(dumped.get("conversation_id") or conversation_id).replace(":", "-")
            encoded = str(dumped.get("encoded_event") or "")
            if api_id and self.store.seen(event_conv, api_id):
                continue
            if encoded and self.store.seen(event_conv, ciphertext_id(encoded)):
                if api_id:
                    self.store.mark_seen(event_conv, api_id)
                continue
            raw.append(dumped)
        raw.sort(key=_created_at_key)
        if not raw:
            return
        self._register_signing_keys(raw)
        key_events_b64 = (page.get("meta") or {}).get("conversation_key_events") or []
        events_b64 = list(key_events_b64) + [
            e["encoded_event"] for e in raw if e.get("encoded_event")
        ]
        if events_b64:
            with self._crypto:
                batch = self.core.decrypt_batch(events_b64)
            keys = (batch.get("conversation_keys") or {}).get("keys") or {}
            self._remember_keys(conversation_id, keys)
            for item in raw:
                event_conv = str(item.get("conversation_id") or "").replace(":", "-")
                if event_conv:
                    self._remember_keys(event_conv, keys)

        last_inbound_id = ""
        if reply_last_inbound:
            for candidate in raw:
                sender = str(candidate.get("sender_id") or "")
                if sender and sender != self.bot_user_id and (
                    not only_recent or _is_recent(candidate)
                ):
                    last_inbound_id = str(candidate.get("id") or "")

        for item in raw:
            event_b64 = item.get("encoded_event")
            if not event_b64:
                continue
            event_conv = str(item.get("conversation_id") or conversation_id).replace(":", "-")
            api_id = str(item.get("id") or "")
            keyring = self._conv_keys.get(event_conv) or self._conv_keys.get(conversation_id, {})
            try:
                with self._crypto:
                    event = self.core.decrypt_one(event_b64, keyring)
            except Exception as err:
                if api_id:
                    self.store.mark_seen(event_conv, api_id)
                if not self._decrypt_skip_logged:
                    logger.info(
                        "skipping events that fail signature verification "
                        "(permanent; not retried)"
                    )
                    self._decrypt_skip_logged = True
                logger.debug("decrypt_skipped conv=%s id=%s err=%s", event_conv, api_id, err)
                continue
            if event.get("type") == "KeyChange":
                with self._crypto:
                    rotated = self.core.decrypt_batch([event_b64])
                rotated_keys = (rotated.get("conversation_keys") or {}).get("keys") or {}
                self._remember_keys(conversation_id, rotated_keys)
                self._remember_keys(event_conv, rotated_keys)
                if api_id:
                    self.store.mark_seen(event_conv, api_id)
                continue
            mid = str(event.get("message_id") or "")
            if mid and self.store.seen(event_conv, f"mid:{mid}"):
                if api_id:
                    self.store.mark_seen(event_conv, api_id)
                self.store.mark_seen(event_conv, ciphertext_id(str(event_b64)))
                continue
            should_reply = reply and (not only_recent or _is_recent(item))
            if reply_last_inbound:
                should_reply = should_reply and api_id == last_inbound_id
            self._maybe_reply(
                event_conv or conversation_id,
                event,
                reply=should_reply,
                api_id=api_id,
                encoded=str(event_b64 or ""),
            )

    def pending(self) -> bool:
        return not self._discovered.empty()

    def _cooling(self, key: str = "global") -> bool:
        return time.monotonic() < self._until.get(key, 0.0)

    def _cool(self, key: str, seconds: float) -> None:
        until = time.monotonic() + max(1.0, seconds)
        self._until[key] = max(self._until.get(key, 0.0), until)

    def _on_rate_limited(self, err: RateLimited, conv: str | None = None) -> None:
        self._cool("global", err.retry_after)
        if conv:
            self._cool(conv, err.retry_after)
            self._need_poll.add(conv)
        logger.warning("rate_limited where=%s retry_after=%.0fs", err.where or conv, err.retry_after)

    def bootstrap(self, conversation_id: str) -> None:
        """Load keys. Reply to recent inbound; skip older history."""
        cur = self.store.cursor(conversation_id)
        if cur["bootstrapped"]:
            return
        first = self.api.get_events(conversation_id, max_results=100, all_pages=True)
        inbound = [
            item
            for item in (first.get("data") or [])
            if str((item if isinstance(item, dict) else _dump(item)).get("sender_id") or "")
            not in {"", self.bot_user_id}
        ]
        self._ingest_page(
            conversation_id, first, reply=True, only_recent=True, reply_last_inbound=True
        )
        self.store.set_cursor(conversation_id, None, True)
        logger.info("bootstrapped conv=%s inbound=%d", conversation_id, len(inbound))

    def poll_conversation(self, conversation_id: str) -> None:
        cid = conversation_id.replace(":", "-")
        if self._cooling("global") or self._cooling(cid):
            self._need_poll.add(cid)
            return
        try:
            self.bootstrap(cid)
        except RateLimited as err:
            self._on_rate_limited(err, cid)
            return
        self._need_poll.discard(cid)
        # The activity stream is the live path. Re-GET /events on known threads
        # replays history under new ids and sends a pile of unprompted replies.
        if self.stream_enabled:
            return
        try:
            page = self.api.get_events(cid, max_results=50)
        except RateLimited as err:
            self._on_rate_limited(err, cid)
            return
        for item in page.get("data") or []:
            dumped = item if isinstance(item, dict) else _dump(item)
            event_conv = str((dumped or {}).get("conversation_id") or cid).replace(":", "-")
            sender = str((dumped or {}).get("sender_id") or "")
            peer = sender if sender and sender != self.bot_user_id else None
            self._watch_conversation(event_conv, peer)
        self._ingest_page(cid, page, reply=True, only_recent=True)

    def _claim_inbound(
        self,
        conv: str,
        api_id: str,
        encoded: str,
        event: dict[str, Any],
    ) -> bool:
        keys: list[str] = []
        if encoded:
            keys.append(ciphertext_id(encoded))
        if api_id:
            keys.append(api_id)
        mid = str(event.get("message_id") or "")
        if mid and mid not in keys:
            keys.append(f"mid:{mid}")
        if not keys:
            return False
        if any(self.store.seen(conv, key) for key in keys):
            for key in keys:
                self.store.mark_seen(conv, key)
            return False
        if not self.store.try_claim(conv, keys[0]):
            return False
        for key in keys[1:]:
            self.store.mark_seen(conv, key)
        return True

    def _maybe_reply(
        self,
        conversation_id: str,
        event: dict[str, Any],
        *,
        reply: bool,
        api_id: str = "",
        encoded: str = "",
    ) -> None:
        event_id = api_id or str(event.get("id") or event.get("message_id") or "")
        sender_id = str(event.get("sender_id") or "")
        conv = str(event.get("conversation_id") or conversation_id).replace(":", "-")
        if not event_id and not encoded:
            return
        if sender_id and sender_id != self.bot_user_id:
            self._watch_conversation(conv, sender_id)
        if not reply or sender_id == self.bot_user_id:
            if event_id:
                self.store.mark_seen(conv, event_id)
            if encoded:
                self.store.mark_seen(conv, ciphertext_id(encoded))
            return
        text = message_text(event)
        if not text:
            if event_id:
                self.store.mark_seen(conv, event_id)
            if encoded:
                self.store.mark_seen(conv, ciphertext_id(encoded))
            return
        if not self._claim_inbound(conv, event_id, encoded, event):
            return
        self._pool.submit(self._reply_job, conv, sender_id, event_id, text)

    def _reply_job(self, conv: str, sender_id: str, event_id: str, text: str) -> None:
        with self._user_lock(sender_id):
            prior = self.store.get_game(sender_id)
            if prior and prior.status in {"won", "lost"}:
                notice = f"{prior.date}:{sender_id}"
                last = self._played_notice.get(notice, 0.0)
                if time.monotonic() - last < PLAYED_NOTICE_COOLDOWN:
                    return
            just_finished = not prior or prior.status == "in_progress"
            try:
                answer = self.engine.handle(sender_id, text)
            except Exception:
                logger.exception("game_failed user=%s", sender_id)
                answer = "Something glitched on my side. Try that again in a second."
            if not self._send_text(conv, sender_id, answer):
                logger.error("reply_unsent user=%s event=%s", sender_id, event_id)
                return
            if prior and prior.status in {"won", "lost"}:
                self._played_notice[f"{prior.date}:{sender_id}"] = time.monotonic()
            game = self.store.get_game(sender_id)
            if game and game.status in {"won", "lost"}:
                token = f"{game.date}:{game.user_id}"
                if just_finished or token not in self._share_sent:
                    if self._send_share_card(conv, sender_id, game):
                        self._share_sent.add(token)

    def _latest_key(self, conversation_id: str) -> tuple[str, bytes] | None:
        def _ver(value: str) -> int:
            try:
                return int(value)
            except ValueError:
                return 0

        with self._keys_lock:
            keys = self._conv_keys.get(conversation_id) or self._conv_keys.get(
                conversation_id.replace("-", ":"), {}
            )
            if not keys:
                return None
            version = max(keys, key=_ver)
            return str(version), keys[version]

    def _encrypt(
        self,
        conv: str,
        sender_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str] | None:
        try:
            with self._crypto:
                return self.core.encrypt_message(conv, text, attachments=attachments)
        except ValueError:
            logger.warning("no_conversation_key conv=%s — establishing", conv)
            self._ensure_keys(conv, sender_id)
            try:
                with self._crypto:
                    return self.core.encrypt_message(conv, text, attachments=attachments)
            except Exception:
                logger.exception("encrypt_failed conv=%s", conv)
                return None
        except Exception:
            logger.exception("encrypt_failed conv=%s", conv)
            return None

    def _send_text(self, conv: str, sender_id: str, text: str) -> bool:
        body = self._encrypt(conv, sender_id, text)
        if not body:
            return False
        delay = 0.4
        for attempt in range(4):
            try:
                self.api.send_message(conv, body)
                logger.info("replied user=%s len=%d", sender_id, len(text))
                return True
            except RateLimited as err:
                wait = min(err.retry_after, 15.0)
                logger.warning("send_rate_limited user=%s wait=%.0fs", sender_id, wait)
                time.sleep(wait)
            except Exception:
                logger.warning("send_retry user=%s attempt=%d", sender_id, attempt + 1, exc_info=True)
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
        logger.error("send_failed conv=%s", conv)
        return False

    def _share_jpeg(self, game: Game) -> bytes:
        path = cache_path(self.store.path, game)
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        jpeg = compose_share_card(game)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)
        return jpeg

    def _send_share_card(self, conv: str, sender_id: str, game: Game) -> bool:
        try:
            jpeg = self._share_jpeg(game)
        except Exception:
            logger.exception("share_card_compose_failed user=%s", sender_id)
            self._send_text(conv, sender_id, copy.share_card(game))
            return False
        key = self._latest_key(conv)
        if key is None:
            self._ensure_keys(conv, sender_id)
            key = self._latest_key(conv)
        if key is None:
            logger.warning("share_card_no_key conv=%s", conv)
            self._send_text(conv, sender_id, copy.share_card(game))
            return False
        version, raw_key = key
        try:
            from chat_xdk import detect_image_dimensions

            with self._crypto:
                ciphertext = self.core.encrypt_stream(jpeg, raw_key)
            media_hash_key = self.api.upload_chat_media(conv, ciphertext)
            dims = detect_image_dimensions(jpeg) or (1024, 1024)
            attachments = [
                {
                    "attachment_type": "media",
                    "media_hash_key": media_hash_key,
                    "width": dims[0],
                    "height": dims[1],
                    "filesize_bytes": len(jpeg),
                    "filename": "playgrokkle.jpg",
                }
            ]
            with self._crypto:
                body = self.core.encrypt_message(
                    conv,
                    copy.share_caption(game),
                    conversation_key=raw_key,
                    conversation_key_version=version,
                    attachments=attachments,
                )
            self.api.send_message(conv, body)
            logger.info("share_card_sent user=%s bytes=%d", sender_id, len(jpeg))
            return True
        except Exception:
            logger.exception("share_card_send_failed conv=%s", conv)
            self._send_text(conv, sender_id, copy.share_card(game))
            return False

    def _already_watching_peer(self, peer_id: str) -> bool:
        if peer_id in self._watch:
            return True
        return any(peer_id in cid.split("-") for cid in self._watch)

    def _resolve_peer_threads(self) -> None:
        pending = list(dict.fromkeys([*self.extra_ids, *self.store.player_ids()]))
        for extra in pending:
            if extra == self.bot_user_id or extra in self._resolved_peers:
                continue
            if self._already_watching_peer(extra):
                self._resolved_peers.add(extra)
                continue
            if self._cooling("global"):
                return
            try:
                canonical = self.api.canonical_conversation_id(extra)
            except RateLimited as err:
                self._on_rate_limited(err)
                return
            except Exception:
                logger.warning(
                    "peer_conversation_lookup_failed peer=%s", extra, exc_info=True
                )
                continue
            if canonical:
                self._watch_conversation(canonical, extra)
                self._resolved_peers.add(extra)

    def note_activity(self, event: dict[str, Any]) -> None:
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        conv = str(payload.get("conversation_id") or "").replace(":", "-")
        sender = str(payload.get("sender_id") or "")
        event_type = str((data or {}).get("event_type") or "")
        event_uuid = str((data or {}).get("event_uuid") or "")
        if conv or sender:
            logger.info("activity %s conv=%s sender=%s", event_type or "chat", conv, sender)
            self._discovered.put(
                {
                    "conv": conv,
                    "sender": sender or None,
                    "event_uuid": event_uuid,
                    "payload": payload,
                }
            )

    def _handle_discovered(self, item: dict[str, Any]) -> None:
        conv = str(item.get("conv") or "").replace(":", "-")
        sender = item.get("sender")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        event_uuid = str(item.get("event_uuid") or "")
        if conv:
            self._watch_conversation(conv, sender if isinstance(sender, str) else None)
        if sender and not self._already_watching_peer(str(sender)):
            if self._cooling("global"):
                self._need_poll.add(conv or str(sender))
            else:
                try:
                    canonical = self.api.canonical_conversation_id(str(sender))
                    if canonical:
                        self._watch_conversation(canonical, str(sender))
                        conv = conv or canonical
                except RateLimited as err:
                    self._on_rate_limited(err, conv or str(sender))
                    return
                except Exception:
                    logger.warning("discovered_peer_lookup_failed peer=%s", sender, exc_info=True)
        if not conv:
            return
        if event_uuid and self.store.seen(conv, f"uuid:{event_uuid}"):
            return
        bootstrapped = bool(self.store.cursor(conv)["bootstrapped"])
        page = activity_to_page(payload)
        if bootstrapped and page:
            if event_uuid:
                self.store.mark_seen(conv, f"uuid:{event_uuid}")
            self._stream_touch[conv] = time.monotonic()
            self._ingest_page(conv, page, reply=True)
            return
        self.poll_conversation(conv)

    def _drain_discovered(self) -> None:
        while True:
            try:
                item = self._discovered.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_discovered(item)
            except RateLimited as err:
                conv = str(item.get("conv") or "")
                self._on_rate_limited(err, conv or None)
            except Exception:
                logger.exception("discovered_event_failed conv=%s", item.get("conv"))

    def _retry_needed_polls(self) -> None:
        if self._cooling("global"):
            return
        for cid in list(self._need_poll):
            if self._cooling(cid):
                continue
            try:
                self.poll_conversation(cid)
            except RateLimited as err:
                self._on_rate_limited(err, cid)
                return
            except Exception:
                logger.exception("poll_conversation_failed conv=%s", cid)

    def poll_inbox(self) -> None:
        self.engine.ensure_daily_secret()
        self._drain_discovered()
        self._retry_needed_polls()
        now = time.monotonic()
        if self._cooling("global") or now < self._next_sweep:
            return
        sweep_every = SWEEP_WITH_STREAM if self.stream_enabled else SWEEP_WITHOUT_STREAM
        self._next_sweep = now + sweep_every
        try:
            ids, meta = self.api.conversation_ids()
        except RateLimited as err:
            self._on_rate_limited(err)
            return
        except Exception:
            logger.exception("list_conversations_failed")
            return
        for cid in ids:
            self._watch_conversation(cid)
        self._resolve_peer_threads()
        if meta.get("has_message_requests") and not self._requests_logged:
            logger.info(
                "message_requests_pending inbox=%d watching=%d — discovering "
                "those threads via chat.received on the activity stream",
                len(ids),
                len(self._watch),
            )
            self._requests_logged = True
        logger.info(
            "sweep inbox=%d watching=%d requests=%s extras=%d",
            len(ids),
            len(self._watch),
            meta.get("has_message_requests"),
            len(self.extra_ids),
        )
        for cid in list(self._watch):
            try:
                self.poll_conversation(cid)
            except RateLimited as err:
                self._on_rate_limited(err, cid)
                return
            except Exception:
                logger.exception("poll_conversation_failed conv=%s", cid)
            time.sleep(0.35)
