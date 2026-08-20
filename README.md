# PlayGrokkle

Daily **PlayGrokkle** over [X Chat](https://docs.x.com/xchat/introduction.md) as [@groksecret](https://x.com/groksecret).

At midnight Eastern the bot pulls live X trends (app-bearer WOEID + X News), Grok picks one niche secret, and anyone who DMs `@groksecret` can ask yes/no questions (20 max, one game per person per day). After they finish, they get a spoiler-free PlayGrokkle share image with their score.

## Setup

Python 3.10+.

```bash
cd ~/Projects/groks-secret
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Fill in `.env`:

| Variable | What |
| --- | --- |
| `X_ACCESS_TOKEN` | OAuth 2.0 **user** token for `@groksecret` with `dm.read dm.write tweet.read users.read media.write` |
| `X_BEARER_TOKEN` | App-only Bearer (needed for inbox discovery **and** `GET /2/trends/by/woeid`) |
| `CHAT_BOT_USER_ID` | `2089844175688597504` |
| `CHAT_PIN` | Juicebox PIN for the Chat identity |
| `CHAT_SIGNING_KEY_VERSION` | Registered `public_key_version` |
| `CHAT_FINGERPRINT` | Optional `public_key_fingerprint` check |
| `XAI_API_KEY` | xAI key for Grok |
| `XAI_MODEL` | Daily topic pick (default `grok-4.3`) |
| `XAI_ANSWER_MODEL` | Per-question yes/no (default `grok-4.20-0309-non-reasoning` — no reasoning) |

Then:

```bash
python -m groks_secret.main
```

Daily topic selection happens on the first poll after midnight America/New_York — no extra cron job required.

## Replit (always on)

This is a long-running chat bot. Publish it as a **Reserved VM**, not Autoscale — Autoscale sleeps and drops the X Chat stream.

1. Import this repo into Replit (or `rsync` the project).
2. In **Secrets**, paste the same keys as `.env` (`X_ACCESS_TOKEN`, `X_BEARER_TOKEN`, `CHAT_PIN` or `CHAT_PRIVATE_KEYS_B64`, `CHAT_SIGNING_KEY_VERSION`, `XAI_API_KEY`, `CHAT_BOT_USER_ID`, …).
3. Create a **SQL Database** in the Replit pane. That sets `DATABASE_URL` (Postgres). Games, scores, and seen-message ids persist across publishes. The Replit disk does **not**.
4. Publish → **Reserved VM**. Run command: `python -m groks_secret.main`.
5. Confirm logs show `health_listening` then `playgrokkle_running` with `store=postgres`.

The process binds `0.0.0.0:$PORT` so Replit health checks pass (`/` and `/health` return `{"ok":true}`). Replies run on a worker pool (`WORKERS`, default 16) so players are answered in parallel — one person's Grok call does not stall everyone else. If Replit Secrets still has `XAI_MODEL=grok-4.3`, that only affects the once-a-day topic pick; yes/no uses `XAI_ANSWER_MODEL` unless you override it.

Locally, if `DATABASE_URL` is unset, it still uses SQLite at `data/groks_secret.sqlite`.

`GET /2/chat/conversations` only returns the **primary inbox**. DMs from people who don't follow `@groksecret` sit in Message requests (`meta.has_message_requests: true`) and are omitted from that list. "Allow messages from anyone" does not move those threads into the inbox.

To auto-discover those senders, set `X_BEARER_TOKEN` to the **app-only** Bearer token from the same X developer app (not the `xcbot_` user token). The bot already subscribes to `chat.received`; the stream is what delivers sender/conversation ids, and then `GET /2/chat/conversations/{id}` works even for request threads.

Until that token is set, either put handles in `CHAT_PEER_USER_IDS` or ask players to follow `@groksecret` first.

```bash
python -m unittest discover -s tests
```

## How a day works

1. First poll after 00:00 ET: live X trends + news → Grok picks one guessable niche topic → stored in Postgres (or SQLite locally).
2. Inbox poll (`GET /2/chat/conversations`): decrypt new encrypted DMs with Chat XDK.
3. Each player gets one `in_progress` game keyed by `(user_id, ET date)`.
4. Win = correct guess. Lose = 20 used. Recap is a shareable PlayGrokkle image in the DM (the bot does not post).

Secrets stay in `.env`. Never commit it.
