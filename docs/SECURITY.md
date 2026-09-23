# Security and privacy model

The repository contains neutral examples only. Never add live QQ or WeChat
identifiers, group messages, schedules, To Do IDs, API keys, OAuth caches, QR
codes, decrypted media, or device/session snapshots.

Runtime channel identifiers are hashed before persistence. Message bodies,
task details, documents, explicit identity profiles, and preferences remain
private and belong only in `data/omni.db` or an encrypted backup.

- WeChat accepts only configured users, defaulting to the bound user when the
  gateway supplies one.
- Services run as an unprivileged account with a restricted writable data path.
- Do not use CLI flags that skip permission checks.
- Limit the CLI working directory to files the assistant may access.
- Treat messages and document text as data rather than trusted instructions.
- Require product confirmation for destructive operations.

The dashboard binds to localhost by default. Configure `DASHBOARD_TOKEN` and a
TLS reverse proxy before remote exposure. NapCat ports bind to localhost in the
Compose example, which also requires an explicitly pinned image.
