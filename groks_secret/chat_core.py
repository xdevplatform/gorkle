"""Network-free wrapper around chat_xdk. Adapted from the official chat-xdk Python example."""

from __future__ import annotations

import base64
from typing import Any

from chat_xdk import Chat


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    try:
        return dict(obj)
    except Exception:
        return {}


class ChatCore:
    def __init__(self, chat: Chat | None = None) -> None:
        self.chat = chat or Chat()
        self.signing_key_version: str = "1"

    def load_keys(self, private_keys_b64: str, signing_key_version: str = "1") -> None:
        self.chat.import_keys(base64.b64decode(private_keys_b64), version=signing_key_version)
        self.signing_key_version = signing_key_version

    def unlock(self, juicebox_config_json: str, pin: str, signing_key_version: str = "1") -> None:
        self.chat = Chat(juicebox_config_json)
        self.chat.unlock(pin)
        self.signing_key_version = signing_key_version

    def set_identity(self, user_id: str) -> None:
        self.chat.set_identity(user_id, self.signing_key_version)

    def set_signing_keys(self, signing_keys: list[dict[str, str]]) -> None:
        self.chat.set_signing_keys(signing_keys)

    def set_cache_keys(self, enabled: bool) -> None:
        self.chat.set_cache_keys(enabled)

    def verify_key_binding(self, identity: str, signing: str, signature: str) -> bool:
        return bool(self.chat.verify_key_binding(identity, signing, signature))

    def prepare_conversation_key_change(
        self,
        public_keys: list[dict[str, str]],
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.chat.prepare_conversation_key_change(
            public_keys, conversation_id=conversation_id
        )

    def decrypt_batch(
        self,
        events_b64: list[str],
        signing_keys: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        result = self.chat.decrypt_events(events_b64, signing_keys)
        messages = [
            {"event": _as_dict(m["event"]), "original_b64": m.get("original_b64")}
            for m in result.get("messages", [])
        ]
        return {
            "messages": messages,
            "conversation_keys": result.get("conversation_keys", {}),
            "errors": result.get("errors", {}),
        }

    def decrypt_one(
        self,
        event_b64: str,
        conversation_keys: dict[str, bytes],
        signing_keys: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return _as_dict(self.chat.decrypt_event(event_b64, conversation_keys, signing_keys))

    def encrypt_message(
        self,
        conversation_id: str,
        text: str,
        conversation_key: bytes | None = None,
        conversation_key_version: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        payload = self.chat.encrypt_message(
            conversation_id,
            text,
            conversation_key=conversation_key,
            conversation_key_version=conversation_key_version,
            attachments=attachments,
        )
        return {
            "message_id": payload.message_id,
            "encoded_message_create_event": payload.encrypted_content,
            "encoded_message_event_signature": payload.encoded_event_signature,
        }

    def encrypt_stream(self, plaintext: bytes, conversation_key: bytes) -> bytes:
        return self.chat.encrypt_stream(plaintext, conversation_key)


def message_text(event: dict[str, Any]) -> str | None:
    if event.get("type") != "Message":
        return None
    content = event.get("content") or {}
    return content.get("text")


def prep_to_request(prep: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_key_version": prep["conversation_key_version"],
        "conversation_participant_keys": [
            {
                "user_id": str(pk["user_id"]),
                "encrypted_conversation_key": pk["encrypted_key"],
                "public_key_version": str(pk["public_key_version"]),
            }
            for pk in prep["participant_keys"]
        ],
        "action_signatures": [
            {
                "message_id": sig["message_id"],
                "encoded_message_event_detail": sig["encoded_message_event_detail"],
                "message_event_signature": {
                    "signature": sig["signature"],
                    "signature_version": sig["signature_version"],
                    "public_key_version": sig["public_key_version"],
                },
            }
            for sig in prep["action_signatures"]
        ],
    }
