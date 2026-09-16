# Deploying Gorkle on Replit

This is a long-running Chat Agent. Publish it as a **Reserved VM**, not Autoscale — Autoscale sleeps and drops the X Chat stream.

## Run

- Command: `python -m gorkle.main`
- Health service: `0.0.0.0:$PORT` (default `8080`)
- Health routes: `/` and `/health`

## Environment

Attach a SQL database so Replit sets `DATABASE_URL`. The bot creates its tables on startup and should log `store=postgres`.

Required Secrets (same as `.env.example`):

- `X_ACCESS_TOKEN`
- `X_BEARER_TOKEN`
- `XAI_API_KEY`
- `CHAT_SIGNING_KEY_VERSION`
- `CHAT_BOT_USER_ID` (numeric id of your bot account)
- One Chat identity credential: `CHAT_PIN` or `CHAT_PRIVATE_KEYS_B64`

If both Chat identity credentials are present, `CHAT_PIN` takes precedence.

## Publish

- Build command: `pip install -r requirements.txt`
- Run command: `python -m gorkle.main`

After publishing, confirm the logs include `health_listening` and `playgorkle_running` with `store=postgres`.
