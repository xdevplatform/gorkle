# PlayGorkle (formerly PlayGrokkle) on Replit

## Run

- Production: private Reserved VM only. Do not run a workspace workflow alongside it (causes duplicate replies).
- Command: `python -m groks_secret.main`
- Bot account: `@PlayGorkle` (user id `2100292761698099200`, set via `CHAT_BOT_USER_ID`). The former `@PlayGrokkle` account no longer exists on X.
- Health service: `0.0.0.0:$PORT` (`8080` in the development workflow)
- Health routes: `/` and `/health`

## Environment

Replit's managed PostgreSQL database provides `DATABASE_URL` automatically. The bot creates its tables on startup and must log `store=postgres`.

Required Replit Secrets:

- `X_ACCESS_TOKEN`
- `X_BEARER_TOKEN`
- `XAI_API_KEY`
- `CHAT_SIGNING_KEY_VERSION`
- One Chat identity credential: `CHAT_PIN` or `CHAT_PRIVATE_KEYS_B64`

If both Chat identity credentials are present, `CHAT_PIN` takes precedence.

## Publish

Publish as a Reserved VM so the X Chat stream and polling loop remain active. Use:

- Build command: `pip install -r requirements.txt`
- Run command: `python -m groks_secret.main`

After publishing, confirm the deployment logs include `health_listening` and `playgorkle_running` with `store=postgres`.