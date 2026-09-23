# Production snapshot merge decisions

The private production archive was reviewed as a reference source. No runtime
file, credential, account identifier, preference, chat record, schedule, media,
or backup was copied into the repository.

| Production capability | Public implementation |
| --- | --- |
| WeChat iLink login, polling, quote handling | Retained with sender authorization and redacted logging |
| AES media download and retention | Retained with size/time limits and real HTTP image or bounded text delivery |
| Local CLI file analysis | Routed through configured CLI arguments without permission bypass |
| Process supervision and memory ceiling | Generic PM2 and least-privilege systemd configuration |
| To Do maintenance and categories | Unified task service, event history, confirmation, and one quadrant field |
| Course schedule | Generic recurrence, exception dates, and conflict checks |
| Restore procedure | Generic legacy import and authenticated encrypted SQLite backup |

Per-channel conversation files were replaced by hashed identity bindings and
persistent conversation history. JSON task state was replaced by transactional
SQLite and an explicit Microsoft To Do reconciliation boundary. Fixed deletion
and deduplication scripts were replaced by idempotent operations, history, and
explicit confirmation.

Excluded material includes sessions, OAuth caches, device identity, QR codes,
media, group history, task backups, course data, list IDs, user memory, root
services, permission bypass, and scripts that mutate fixed remote identifiers.

The source tree contains behavior and generic schemas only. Operators import
their own private state after deployment.
