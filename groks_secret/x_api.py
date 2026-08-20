"""X Chat + trends HTTP. Mirrors the official chat-xdk Python example, plus inbox/trends."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xdk import Client
from xdk.chat.models import SendMessageRequest

logger = logging.getLogger("groks_secret.x_api")

BASE_URL = "https://api.x.com"


class RateLimited(Exception):
    def __init__(self, retry_after: float = 60.0, where: str = "") -> None:
        self.retry_after = max(1.0, min(float(retry_after), 300.0))
        self.where = where
        super().__init__(f"rate limited {where} retry_after={self.retry_after:.0f}s")


def http_status(err: BaseException) -> int | None:
    if isinstance(err, HTTPError):
        return int(err.code)
    response = getattr(err, "response", None)
    code = getattr(response, "status_code", None) if response is not None else None
    if code is None:
        return None
    return int(code)


def retry_after_seconds(err: BaseException, default: float = 60.0) -> float:
    headers = None
    if isinstance(err, HTTPError):
        headers = err.headers
    else:
        response = getattr(err, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
    raw = ""
    if headers is not None:
        try:
            raw = str(headers.get("Retry-After") or headers.get("retry-after") or "")
        except Exception:
            raw = ""
    if raw.strip().isdigit():
        return max(1.0, min(float(raw.strip()), 300.0))
    return default


def raise_if_rate_limited(err: BaseException, where: str) -> None:
    if isinstance(err, RateLimited):
        raise err
    if http_status(err) == 429:
        raise RateLimited(retry_after_seconds(err), where) from err


def _dump(obj: Any) -> Any:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump()
        return dumped if isinstance(dumped, dict) else {"data": dumped}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return [_dump(x) for x in obj]
    return obj


def _pages(resp: Any, max_pages: int | None = None) -> list[dict[str, Any]]:
    """XDK list endpoints yield pages; HTTP fallbacks return one dict."""
    if resp is None:
        return []
    if isinstance(resp, dict):
        return [resp]
    if hasattr(resp, "model_dump"):
        return [_dump(resp)]
    if hasattr(resp, "__iter__") and not isinstance(resp, (str, bytes, dict)):
        out: list[dict[str, Any]] = []
        for page in resp:
            dumped = _dump(page)
            if isinstance(dumped, dict):
                out.append(dumped)
            if max_pages is not None and len(out) >= max_pages:
                break
        return out
    dumped = _dump(resp)
    return [dumped] if isinstance(dumped, dict) else []


def _merge_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    data: list[Any] = []
    key_events: list[Any] = []
    merged_meta: dict[str, Any] = {}
    next_token = None
    has_more = False
    for page in pages:
        chunk = page.get("data") or []
        if isinstance(chunk, dict):
            chunk = [chunk]
        data.extend(chunk)
        meta = page.get("meta") or {}
        for key, value in meta.items():
            if key == "conversation_key_events":
                continue
            if value is not None:
                merged_meta[key] = value
        key_events.extend(meta.get("conversation_key_events") or [])
        next_token = meta.get("next_token") or next_token
        has_more = bool(meta.get("has_more") or next_token)
    merged_meta.update(
        {
            "conversation_key_events": key_events,
            "next_token": next_token,
            "has_more": has_more,
        }
    )
    return {"data": data, "meta": merged_meta}


class XChatClient:
    def __init__(self, access_token: str, bearer_token: str = "") -> None:
        self.client = Client(access_token=access_token)
        self._token = access_token
        self._app = Client(bearer_token=bearer_token) if bearer_token else None
        self._pubkey_lock = threading.Lock()
        self._pubkeys: dict[str, list[dict[str, Any]]] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        req = Request(
            BASE_URL + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(),
            method=method,
        )
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or raw[:1] in (b"{", b"["):
                    return json.loads(raw.decode())
                return raw
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            if err.code == 429:
                raise RateLimited(retry_after_seconds(err), f"{method} {path}") from err
            if err.code == 400 and "DuplicateSubscription" in detail:
                logger.info("activity_already_subscribed %s", path)
                return {"duplicate": True}
            logger.error("http_%s %s %s %s", err.code, method, path, detail[:500])
            raise

    def get_my_user_id(self) -> str:
        return str(self.client.users.get_me().data.id)

    def get_public_keys(self, user_id: str) -> list[dict[str, Any]]:
        with self._pubkey_lock:
            cached = self._pubkeys.get(user_id)
        if cached is not None:
            return cached
        resp = self.client.users.get_public_key(
            user_id,
            public_key_fields=[
                "identity_public_key_signature",
                "juicebox_config",
                "public_key",
                "public_key_version",
                "signing_public_key",
            ],
        )
        data = resp.data or []
        items = data if isinstance(data, list) else [data]
        rows = [_dump(d) if not isinstance(d, dict) else d for d in items]
        with self._pubkey_lock:
            self._pubkeys[user_id] = rows
        return rows

    def get_juicebox_config(self, user_id: str) -> tuple[str, str, str]:
        items = self.get_public_keys(user_id)
        if not items:
            raise RuntimeError(f"No public keys registered for {user_id}")
        latest = max(items, key=lambda d: int(d.get("public_key_version") or 0))
        config = latest.get("juicebox_config")
        if not config:
            raise RuntimeError("Public-key record has no juicebox_config")
        return (
            json.dumps(config),
            str(latest.get("public_key_version") or "1"),
            str(latest.get("public_key_fingerprint") or ""),
        )

    def list_conversations(self, pagination_token: str | None = None) -> dict[str, Any]:
        try:
            resp = self.client.chat.get_conversations(
                max_results=100,
                pagination_token=pagination_token,
                chat_conversation_fields=["id", "type", "updated_at"],
            )
            pages = _pages(resp)
            return _merge_pages(pages) if pages else {"data": [], "meta": {}}
        except Exception as err:
            raise_if_rate_limited(err, "GET /2/chat/conversations")
            logger.warning("xdk get_conversations failed; using HTTP", extra={}, exc_info=True)
            qs = "?max_results=100&chat_conversation.fields=id,type,updated_at"
            if pagination_token:
                qs += f"&pagination_token={pagination_token}"
            return self._request("GET", f"/2/chat/conversations{qs}") or {"data": []}

    def conversation_ids(self) -> tuple[list[str], dict[str, Any]]:
        page = self.list_conversations()
        ids: list[str] = []
        for item in page.get("data") or []:
            if not isinstance(item, dict):
                item = _dump(item)
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or item.get("conversation_id") or "")
            if cid:
                ids.append(cid)
        return ids, page.get("meta") or {}

    def resolve_user_id(self, handle_or_id: str) -> str:
        value = handle_or_id.strip().lstrip("@")
        if value.isdigit():
            return value
        resp = self.client.users.get_by_username(value)
        dumped = _dump(resp)
        data = dumped.get("data") if isinstance(dumped, dict) else dumped
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
        raise RuntimeError(f"Could not resolve @{value}")

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conv = conversation_id.replace(":", "-")
        try:
            resp = self.client.chat.get_conversation(
                conv,
                chat_conversation_fields=["id", "type", "updated_at"],
                expansions=["participant_ids"],
            )
            dumped = _dump(resp)
            return dumped if isinstance(dumped, dict) else {"data": dumped}
        except Exception as err:
            raise_if_rate_limited(err, f"GET /2/chat/conversations/{conv}")
            logger.warning("xdk get_conversation failed; using HTTP", exc_info=True)
            return self._request("GET", f"/2/chat/conversations/{conv}") or {}

    def canonical_conversation_id(self, conversation_id: str) -> str | None:
        dumped = self.get_conversation(conversation_id)
        data = dumped.get("data") if isinstance(dumped, dict) else dumped
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"]).replace(":", "-")
        return None

    def get_events(
        self,
        conversation_id: str,
        *,
        max_results: int = 50,
        pagination_token: str | None = None,
        all_pages: bool = False,
    ) -> dict[str, Any]:
        conv = conversation_id.replace(":", "-")
        try:
            resp = self.client.chat.get_conversation_events(
                conv,
                max_results=max_results,
                pagination_token=pagination_token,
                chat_message_event_fields=[
                    "conversation_id",
                    "created_at",
                    "encoded_event",
                    "id",
                    "sender_id",
                ],
            )
            pages = _pages(resp, max_pages=None if all_pages else 1)
            return _merge_pages(pages)
        except Exception as err:
            raise_if_rate_limited(err, f"GET /2/chat/conversations/{conv}/events")
            logger.warning("xdk get_conversation_events failed; using HTTP", exc_info=True)
            qs = (
                f"?max_results={max_results}"
                "&chat_message_event.fields=conversation_id,created_at,encoded_event,id,sender_id"
            )
            if pagination_token:
                qs += f"&pagination_token={pagination_token}"
            return self._request("GET", f"/2/chat/conversations/{conv}/events{qs}") or {
                "data": []
            }

    def add_conversation_keys(self, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"/2/chat/conversations/{conversation_id.replace(':', '-')}/keys"
        return self._request("POST", path, body)

    def send_message(self, conversation_id: str, body: dict[str, str]) -> Any:
        request = SendMessageRequest.model_validate(body)
        return self.client.chat.send_message(conversation_id.replace(":", "-"), request)

    def upload_chat_media(self, conversation_id: str, ciphertext: bytes) -> str:
        # Bodies want the colon form; path ids stay hyphenated. segment_index
        # and num_parts are numeric strings (xurl / OpenAPI MediaSegments).
        conv = conversation_id.replace("-", ":")
        init = self._request(
            "POST",
            "/2/chat/media/upload/initialize",
            {"conversation_id": conv, "total_bytes": len(ciphertext)},
        )
        data = (init.get("data") if isinstance(init, dict) else None) or {}
        session_id = str(data.get("session_id") or "")
        media_hash_key = str(data.get("media_hash_key") or "")
        if not session_id or not media_hash_key:
            raise RuntimeError(f"media init missing ids: {init}")
        chunk = 3 * 1024 * 1024
        parts = 0
        for i, start in enumerate(range(0, len(ciphertext), chunk)):
            piece = ciphertext[start : start + chunk]
            self._request(
                "POST",
                f"/2/chat/media/upload/{session_id}/append",
                {
                    "conversation_id": conv,
                    "media_hash_key": media_hash_key,
                    "segment_index": str(i),
                    "media": base64.b64encode(piece).decode("ascii"),
                },
            )
            parts += 1
        self._request(
            "POST",
            f"/2/chat/media/upload/{session_id}/finalize",
            {
                "conversation_id": conv,
                "media_hash_key": media_hash_key,
                "num_parts": str(max(parts, 1)),
            },
        )
        return media_hash_key

    @staticmethod
    def _trend_names(payload: Any) -> list[str]:
        dumped = _dump(payload)
        data = dumped.get("data") if isinstance(dumped, dict) else dumped
        names: list[str] = []
        for item in data or []:
            if isinstance(item, str):
                names.append(item)
                continue
            name = (
                item.get("trend_name")
                or item.get("name")
                or item.get("trend")
                or item.get("hook")
            )
            if name:
                names.append(str(name).strip())
        return [n for n in names if n]

    def get_trends(self, woeid: int) -> list[str]:
        names: list[str] = []
        attempts: list[tuple[str, Any]] = []
        if self._app:
            attempts.append(
                (
                    "woeid_bearer",
                    lambda: self._app.trends.get_by_woeid(
                        woeid, max_trends=50, trend_fields=["trend_name", "tweet_count"]
                    ),
                )
            )
            attempts.append(
                (
                    "woeid_worldwide_bearer",
                    lambda: self._app.trends.get_by_woeid(
                        1, max_trends=50, trend_fields=["trend_name", "tweet_count"]
                    ),
                )
            )
        attempts.extend(
            [
                (
                    "woeid_user",
                    lambda: self.client.trends.get_by_woeid(
                        woeid, max_trends=50, trend_fields=["trend_name", "tweet_count"]
                    ),
                ),
                (
                    "news_us",
                    lambda: self.client.news.search(
                        "US",
                        max_results=20,
                        max_age_hours=24,
                        news_fields=["name", "category", "hook"],
                    ),
                ),
                (
                    "news_trending",
                    lambda: self.client.news.search(
                        "trending",
                        max_results=15,
                        max_age_hours=24,
                        news_fields=["name", "category", "hook"],
                    ),
                ),
            ]
        )
        seen: set[str] = set()
        for label, fn in attempts:
            try:
                chunk = self._trend_names(fn())
            except Exception as err:
                logger.warning("trends_failed source=%s err=%s", label, err)
                continue
            if not chunk:
                continue
            logger.info("trends_ok source=%s count=%d", label, len(chunk))
            for name in chunk:
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    names.append(name)
        if names:
            logger.info("trends_merged count=%d", len(names))
        return names

    def ensure_chat_subscriptions(self, bot_user_id: str) -> None:
        for event_type in ("chat.received", "chat.conversation_join"):
            try:
                self._request(
                    "POST",
                    "/2/activity/subscriptions",
                    {"event_type": event_type, "filter": {"user_id": bot_user_id}},
                )
                logger.info("activity_subscribed event=%s", event_type)
            except Exception as err:
                logger.info("activity_subscribe event=%s result=%s", event_type, err)

    def iter_activity_stream(self, bearer_token: str, *, backfill_minutes: int = 5):
        from xdk import Client
        from xdk.streaming import StreamConfig

        app = Client(bearer_token=bearer_token)
        cfg = StreamConfig(max_retries=-1, timeout=90)
        minutes = max(0, min(int(backfill_minutes), 5))
        while True:
            try:
                for item in app.stream.activity(
                    backfill_minutes=minutes,
                    stream_config=cfg,
                ):
                    dumped = _dump(item)
                    if isinstance(dumped, dict):
                        yield dumped
            except Exception:
                logger.exception("activity_stream_disconnected")
                time.sleep(5)
