# Security

This bot holds an X Chat identity. Treat `.env` as secret.

Never commit:

- OAuth user tokens (`X_ACCESS_TOKEN`)
- App Bearer tokens (`X_BEARER_TOKEN`)
- Juicebox PINs (`CHAT_PIN`)
- Chat key blobs (`CHAT_PRIVATE_KEYS_B64`)
- xAI API keys (`XAI_API_KEY`)

Do not log PINs, key blobs, unwrapped conversation keys, or plaintext DMs.

To report a vulnerability, please open a private security advisory on this repository.
