# Operations and verification

```bash
python main.py --check-only
python -m pytest -q
python -m compileall -q .
node --check core/ai_provider.js
node --check adapters/wechat/wechat_bot.js
```

The dashboard health endpoint separates process state from database, runtime,
channel, To Do, and AI configuration. Task, inbox, search, digest, review, and
preference APIs support day-to-day operation.

For failures, inspect message state in this order: `pending`, `processing`,
`retry`, `done`, or `failed`. Retry rows retain the last error and next attempt.
Task events explain every persisted change. Pending notifications show digest
or follow-up work waiting for delivery.

A recovery drill creates an encrypted backup, restores it under a new name,
runs `PRAGMA integrity_check`, compares task/event counts, then starts with
external writes disabled before cutover.
