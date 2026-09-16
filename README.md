# Gorkle

A daily yes-or-no guessing game that lives entirely in [X Chat](https://docs.x.com/xchat/introduction.md). This is the Chat Agent behind [@PlayGorkle](https://x.com/PlayGorkle) — clone it, run it as your own bot, and use it as a reference for building Chat Agents on X.

**Play the live one:** DM [@PlayGorkle](https://x.com/PlayGorkle).

Every night at midnight Eastern, the bot reads what's trending on X. Grok picks one niche secret. Anyone who DMs the bot gets **20 yes-or-no questions** that day. Name it and you win. Burn the 20 without a hit and you lose. The recap is a spoiler-free share image in the DM (the bot does not post it).

One game per person per day. Help, score, and the opening welcome are templates — Grok only runs for real questions and guesses.

## Why this exists

X Chat is encrypted. X stores and routes ciphertext only. A Chat Agent holds the bot account's identity keys, decrypts inbound DMs, thinks, then encrypts the reply. This repo is a complete, working example of that loop:

| Piece | Package | Role |
| --- | --- | --- |
| **Chat XDK** | `chatxdk` | Generate / unlock keys, encrypt, decrypt, sign, verify. No HTTP. |
| **X API** | `xdk` | Public keys, conversation keys, send, events, media, trends. |
| **Delivery** | X Activity API | Live `chat.received` so Message-request DMs show up without polling the whole inbox. |
| **Grok** | xAI API | Picks the daily secret from trends; answers yes/no and guesses. |

```
inbound ciphertext  →  Chat XDK decrypt/verify  →  game logic (and maybe Grok)
reply plaintext     →  Chat XDK encrypt/sign    →  POST /2/chat/conversations/{id}/messages
```

Official intro: [X Chat](https://docs.x.com/xchat/introduction.md). Smaller echo-bot starters: [chat-xdk examples](https://github.com/xdevplatform/chat-xdk/tree/main/examples).

## Run your own

This is a long-running process. You need a Chat Bot token from the [X Developer Console](https://console.x.com) and an [xAI](https://console.x.ai) key for Grok.

Python 3.10+.

```bash
git clone https://github.com/xdevplatform/gorkle.git
cd gorkle
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

### 1. Chat Bot access token

In the [X Developer Console](https://console.x.com), create a **Chat Bot access token**. That token belongs to the bot — it is not an app-only Bearer, and it is not a generic OAuth login. Creating it also creates the bot's Chat identity, so the console gives you all three of these together:

| From the console | Env var | What it is |
| --- | --- | --- |
| Bot access token | `X_ACCESS_TOKEN` | Starts with `xcbot_`. Used to encrypt, decrypt, and send Chat. |
| Juicebox PIN | `CHAT_PIN` | Unlocks the Chat identity. Don't guess it later — wrong PINs can lock Juicebox. |
| Version | `CHAT_SIGNING_KEY_VERSION` | The registered `public_key_version` for that identity. |

Also copy the bot's numeric user id into `CHAT_BOT_USER_ID`. If you omit it, the process uses the user on the `xcbot_` token.

Then open **Apps** for the app that Chat Bot is associated with and copy that app's **Bearer Token** (`AAAA…`) into `X_BEARER_TOKEN`. The `xcbot_` token cannot call `GET /2/activity/stream`. Without the Bearer, people who don't follow the bot land in Message requests and the inbox list never sees them.

On boot, `CHAT_PIN` fetches `juicebox_config` from `GET /2/users/{id}/public_keys` and Chat XDK `unlock(pin)` with `CHAT_SIGNING_KEY_VERSION`. If you already have a Chat XDK `export_keys` blob, you can set `CHAT_PRIVATE_KEYS_B64` instead of the PIN. If both are set, `CHAT_PIN` wins.

Never commit `.env`. Never log the PIN, the blob, unwrapped conversation keys, or plaintext DMs.

### 2. Grok

| Variable | Default | When |
| --- | --- | --- |
| `XAI_API_KEY` | required | All Grok calls |
| `XAI_MODEL` | `grok-4.3` | Daily topic pick from live trends |
| `XAI_ANSWER_MODEL` | `grok-4.20-0309-non-reasoning` | Each real question / guess |

Then:

```bash
python -m gorkle.main
```

Daily topic selection happens on the first poll after midnight America/New_York — no extra cron job.

```bash
python -m unittest discover -s tests
```

## How a day works

1. First poll after 00:00 ET: live X trends + news → Grok picks one guessable niche topic.
2. Live `chat.received` events (and a periodic inbox sweep) decrypt new DMs with Chat XDK.
3. Each player gets one `in_progress` game keyed by `(user_id, ET date)`.
4. Win = correct guess. Lose = 20 used. Recap is a shareable PlayGorkle image in the DM.

## Project layout

```
gorkle/
  chat_core.py   Chat XDK wrapper (no HTTP)
  x_api.py       X Chat HTTP: keys, send, events, media, trends, activity stream
  bot.py         Decrypt inbound DMs, reply, share cards
  game.py        20-question rules
  grok.py        Daily pick + yes/no/guess
  copy.py        Welcome / help / win / lose templates
  store.py       SQLite or Postgres
  share_card.py  Spoiler-free recap image
  main.py        Process entry: health, stream, poll loop
```

## Deploy

Keep it alive. Anything that sleeps will drop the activity stream.

Any always-on host works. On Replit, publish as a **Reserved VM** (see [`replit.md`](replit.md)): put the same keys in Secrets, attach a SQL database (`DATABASE_URL`), and run `python -m gorkle.main`. Locally, with `DATABASE_URL` unset, it uses SQLite at `data/gorkle.sqlite`.

The process binds `0.0.0.0:$PORT` (`/` and `/health` return `{"ok":true}`). Replies run on a worker pool (`WORKERS`, default 16) so one player's Grok call does not stall everyone else.

`GET /2/chat/conversations` is **primary inbox only**. "Allow messages from anyone" does not move Message requests onto that list. The app Bearer stream is how those threads are discovered.

Until `X_BEARER_TOKEN` is set, either list handles in `CHAT_PEER_USER_IDS` or ask players to follow the bot first.

## License

MIT. See [`LICENSE`](LICENSE).

Share-card typefaces (Space Grotesk, Space Mono) are SIL Open Font License — notices live in [`gorkle/assets/fonts/`](gorkle/assets/fonts).
