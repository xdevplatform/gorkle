from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


@dataclass(frozen=True)
class Settings:
    access_token: str
    bot_user_id: str
    pin: str
    signing_key_version: str
    fingerprint: str
    private_keys_b64: str
    xai_api_key: str
    xai_model: str
    xai_answer_model: str
    imagine_model: str
    trends_woeid: int
    poll_interval: float
    db_path: Path
    peer_user_ids: tuple[str, ...]
    bearer_token: str
    database_url: str
    workers: int
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        pin = os.environ.get("CHAT_PIN", "").strip()
        blob = os.environ.get("CHAT_PRIVATE_KEYS_B64", "").strip()
        if not pin and not blob:
            raise SystemExit("Set CHAT_PIN or CHAT_PRIVATE_KEYS_B64 in .env")
        peers = tuple(
            p.strip().lstrip("@")
            for p in os.environ.get("CHAT_PEER_USER_IDS", "").split(",")
            if p.strip()
        )
        return cls(
            access_token=_require("X_ACCESS_TOKEN"),
            bot_user_id=os.environ.get("CHAT_BOT_USER_ID", "").strip(),
            pin=pin,
            signing_key_version=os.environ.get("CHAT_SIGNING_KEY_VERSION", "").strip(),
            fingerprint=os.environ.get("CHAT_FINGERPRINT", "").strip(),
            private_keys_b64=blob,
            xai_api_key=_require("XAI_API_KEY"),
            xai_model=os.environ.get("XAI_MODEL", "grok-4.3").strip() or "grok-4.3",
            # Per-question yes/no. grok-4.3 / 4.6 reason; this one does not.
            xai_answer_model=(
                os.environ.get("XAI_ANSWER_MODEL", "grok-4.20-0309-non-reasoning").strip()
                or "grok-4.20-0309-non-reasoning"
            ),
            imagine_model=(
                os.environ.get("XAI_IMAGINE_MODEL", "grok-imagine-image-2.0").strip()
                or "grok-imagine-image-2.0"
            ),
            trends_woeid=int(os.environ.get("TRENDS_WOEID", "23424977")),
            poll_interval=float(os.environ.get("POLL_INTERVAL", "4")),
            db_path=Path(os.environ.get("DB_PATH", str(ROOT / "data" / "gorkle.sqlite"))),
            peer_user_ids=peers,
            bearer_token=(
                os.environ.get("X_BEARER_TOKEN", "").strip()
                or os.environ.get("X_APP_BEARER", "").strip()
            ),
            database_url=(
                os.environ.get("DATABASE_URL", "").strip()
                or os.environ.get("POSTGRES_URL", "").strip()
            ),
            workers=max(1, int(os.environ.get("WORKERS", "16"))),
            port=int(os.environ.get("PORT", "8080")),
        )
