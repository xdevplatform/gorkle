# PlayGrokkle

Daily yes-or-no guessing over [X Chat](https://docs.x.com/xchat/introduction.md), as [@PlayGrokkle](https://x.com/PlayGrokkle).

Every night at midnight Eastern, the bot reads what’s trending on X. Grok picks one niche secret. Anyone who DMs `@PlayGrokkle` gets **20 yes-or-no questions** that day. A correct name wins; 20 used without a hit loses. The recap is a spoiler-free share image in the DM (the bot does not post it).

One game per person per day. Help, score, and the opening welcome are templates — Grok only runs for real questions and guesses.

## Chat XDK setup

X Chat is **encrypted**. X stores and routes ciphertext only. Plaintext exists on this host because the process holds `@PlayGrokkle`’s Chat identity keys. That is a different stack from legacy unencrypted DMs.

The bot is two libraries plus a live stream:

| Piece | Package | Role |
| --- | --- | --- |
| **Chat XDK** | `chatxdk` | Generate / unlock keys, encrypt, decrypt, sign, verify. No HTTP. |
| **X API** | `xdk` (and a few raw `https://api.x.com` calls) | Public keys, conversation keys, send, events, media, trends. |
| **Delivery** | X Activity API | Live `chat.received` so Message-request DMs show up without polling the whole inbox. |

Flow:

```
inbound ciphertext  →  Chat XDK decrypt/verify  →  game logic (and maybe Grok)
reply plaintext     →  Chat XDK encrypt/sign    →  POST /2/chat/conversations/{id}/messages
```

Official intro: [X Chat](https://docs.x.com/xchat/introduction.md). SDK examples: [chat-xdk](https://github.com/xdevplatform/chat-xdk).

### Identity (once, then every boot)

The Chat identity is registered on user `2089844175688597504` (`@PlayGrokkle`). OAuth and crypto keys are separate: revoking a token does not wipe private keys.

On boot the process either:

1. **Juicebox PIN** (`CHAT_PIN`) — fetches the account’s `juicebox_config` from `GET /2/users/{id}/public_keys`, then Chat XDK `unlock(pin)`, or
2. **Key blob** (`CHAT_PRIVATE_KEYS_B64`) — Chat XDK `import_keys(blob)`. If the blob is set, it **wins** and the PIN is ignored. Do not put a dummy blob in Replit Secrets.

Then: `set_identity(bot_user_id, public_key_version)` and `set_cache_keys(True)` so verified conversation keys stick in memory.

`CHAT_SIGNING_KEY_VERSION` must match the registered `public_key_version`. `CHAT_FINGERPRINT` is an optional sanity check against the API record.

Never log the PIN, the blob, unwrapped conversation keys, or plaintext DMs.

### Two tokens

| Token | Env | Used for |
| --- | --- | --- |
| **User** (`xcbot_…`) | `X_ACCESS_TOKEN` | Chat HTTP: send, events, public keys, conversation keys, media. Scopes: `dm.read dm.write tweet.read users.read media.write`. |
| **App Bearer** (`AAAA…`) | `X_BEARER_TOKEN` | `GET /2/activity/stream` (live DMs) and `GET /2/trends/by/woeid`. The user token cannot read that stream. |

### Conversations

`GET /2/chat/conversations` is **primary inbox only**. People who don’t follow `@PlayGrokkle` land in Message requests and never appear on that list. “Allow messages from anyone” does not move them.

The app Bearer stream is how those threads are discovered: `chat.received` carries `sender_id` / `conversation_id`, then `GET /2/chat/conversations/{id}` and decrypt still work. Without `X_BEARER_TOKEN`, either list handles in `CHAT_PEER_USER_IDS` or ask players to follow first.

First time we see a player, Chat XDK needs their public keys (`verify_key_binding` before wrapping a conversation key). After that, keys are cached and replies stay short. Later messages on a known thread are answered from the stream event — we do not re-GET history (that replayed old DMs).

### Grok

| Call | Model env | When |
| --- | --- | --- |
| Daily topic pick | `XAI_MODEL` (default `grok-4.3`) | Once after midnight ET, from live trends. Empty trends → no invented secret. |
| Yes / no / guess | `XAI_ANSWER_MODEL` (default `grok-4.20-0309-non-reasoning`) | Each real question. Greeting / help / already-played skip this. |

Replies run on a worker pool (`WORKERS`, default 16) so one player’s Grok call does not stall everyone else.

## Local run

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m groks_secret.main
```

Fill `.env` (never commit it):

| Variable | What |
| --- | --- |
| `X_ACCESS_TOKEN` | User token for `@PlayGrokkle` |
| `X_BEARER_TOKEN` | App-only Bearer (stream + WOEID trends) |
| `CHAT_BOT_USER_ID` | `2089844175688597504` |
| `CHAT_PIN` | Juicebox PIN, unless you set a blob |
| `CHAT_SIGNING_KEY_VERSION` | Registered `public_key_version` |
| `CHAT_FINGERPRINT` | Optional fingerprint check |
| `CHAT_PRIVATE_KEYS_B64` | Optional `export_keys` blob; overrides PIN |
| `XAI_API_KEY` | xAI key |

Optional: `TRENDS_WOEID` (default US), `POLL_INTERVAL`, `WORKERS`, `PORT`, `CHAT_PEER_USER_IDS`, `XAI_MODEL`, `XAI_ANSWER_MODEL`.

Daily topic pick is the first poll after 00:00 America/New_York — no cron job.

```bash
python -m unittest discover -s tests
```

## Replit (always on)

Long-running process. Publish as a **Reserved VM**, not Autoscale — Autoscale sleeps and drops the stream.

1. Import this repo.
2. Secrets: the same values as `.env`. Do **not** set a dummy `CHAT_PRIVATE_KEYS_B64` if you unlock with `CHAT_PIN`.
3. Create a **SQL Database**. That injects `DATABASE_URL` (Postgres). Games, scores, and seen-message ids survive publishes; the Replit disk does not.
4. Publish → Reserved VM. Run: `python -m groks_secret.main`.
5. Logs should show `health_listening`, then `keys_unlocked_via_juicebox` (or `keys_loaded_from_blob`), then `playgrokkle_running` with `store=postgres`.

The process binds `0.0.0.0:$PORT` (`/` and `/health` return `{"ok":true}`). Run **one** copy — local laptop plus workspace Run plus the VM will double-reply.

Locally, with no `DATABASE_URL`, SQLite lives at `data/groks_secret.sqlite`.
