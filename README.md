# Omni-Assistant

**English** | [简体中文](README_CN.md)

A self-hosted personal message assistant for QQ and WeChat. It ingests notices into a local queue, extracts actionable tasks and schedule constraints based on deterministic rules, optionally mirrors them to Microsoft To Do, and preserves source references and an audit trail for every step.

It never attempts to guess for you: whenever dates, assignees, or relevance are ambiguous, items are placed into a confirmation inbox waiting for your explicit approval before tasks are created.

## Use Cases

- Silently monitor announcements in configured QQ groups and privately notify the administrator
- Input and query tasks or submit messages with images and text attachments via WeChat direct messages
- Synchronize confirmed tasks to Microsoft To Do
- Inspect tasks, confirmation inbox, 14-day schedule, and service status locally on the dashboard
- Retain all runtime data strictly on your own machine rather than sending it to an external repository

If you only need a casual chatbot, this project is too heavyweight; its core focus is message persistence, deduplication, confirmation workflows, task change audit history, and recoverability.

## How It Works

```text
QQ / WeChat / Dashboard
          │
          ▼
Message Ingestion, Deduplication & Retries
          │
          ▼
Structured Evaluation & Time Parsing
      │                      │
      ▼                      ▼
Confirmed Tasks      Confirmation Inbox
      │
      ▼
SQLite Task History ─── Microsoft To Do (Optional)
```

SQLite is the single source of truth for local state. Channel identifiers are hashed before storage; message bodies, tasks, preferences, and document contents remain exclusively in the local database directory and must never be committed to Git.

## Getting Started

Requires Python 3.10+ and Node.js 18+. npm dependencies are only required when enabling WeChat or utilizing Node-side utilities.

```bash
git clone https://github.com/waaogg/omni-assistant.git
cd omni-assistant
python -m pip install -r requirements.txt
npm install

# For development or running tests
python -m pip install -r requirements-dev.txt

# Create your configuration from template
cp .env.example .env
```

Minimal configuration example (recommended to enable one channel first):

```dotenv
ENABLE_QQ=false
ENABLE_WECHAT=true

AI_PROVIDER=openai
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=deepseek-chat
```

Validate configuration before starting:

```bash
python main.py --check-only
python main.py
```

`--check-only` validates configuration formatting and local dependencies without connecting to QQ, WeChat, or Microsoft To Do. See [`.env.example`](.env.example) for all supported options.

## Channel Configuration

### QQ

QQ integrates via NapCat's OneBot v11 HTTP and WebSocket interfaces. When enabled, the adapter strictly listens to `TARGET_GROUP_IDS` without speaking in those groups; parsed notices are delivered privately to `ADMIN_QQ`.

```dotenv
ENABLE_QQ=true
ADMIN_QQ=your_admin_qq_number
TARGET_GROUP_IDS=group_id_1,group_id_2
NAPCAT_HTTP_URL=http://127.0.0.1:3000
NAPCAT_WS_URL=ws://127.0.0.1:3001
NAPCAT_TOKEN=
```

### WeChat

The WeChat adapter connects via Tencent's official iLink gateway. On initial startup, complete the QR code login in your terminal. Session credentials are saved in `WECHAT_DATA_DIR` (defaults to `data/wechat`); do not commit or share this directory.

```dotenv
ENABLE_WECHAT=true
WECHAT_BASE_URL=https://ilinkai.weixin.qq.com
WECHAT_DATA_DIR=./data/wechat
```

Use `ALLOWED_WECHAT_USER_IDS` to restrict permitted WeChat senders; when omitted, the adapter only accepts the account bound to the gateway.

## Task & Confirmation Mechanics

- Messages within each channel are deduplicated by external message ID; failures retry automatically according to configuration.
- Compound notices containing multiple independent items produce separate candidate tasks.
- Tasks meeting or exceeding `AUTO_APPLY_CONFIDENCE` with clear assignees and timestamps are persisted automatically; all other items route to the confirmation inbox.
- Deleting a task requires an explicit confirmation command; updates, completions, and defers are recorded in history and support undo.
- Due dates, reminders, recurring schedules, and conflict detection are evaluated deterministically. Missing years or ambiguous dates are never hallucinated.

Microsoft To Do serves as an optional remote mirror, not a substitute for the local database. Remote sync failures will never falsely report that a task was created successfully.

```dotenv
ENABLE_MS_TODO=true
MS_TODO_AUTH_MODULE_PATH=/path/to/auth.js
MS_TODO_DEFAULT_LIST_ID=
DEFAULT_REMINDER_ADVANCE_MINUTES=15
```

## Dashboard

The local setup and operations dashboard listens on `127.0.0.1:8765` by default:

```bash
python dashboard.py
```

It allows inspecting service status, tasks, confirmation inbox, upcoming schedules, and content-free AI telemetry metrics, as well as editing supported configuration settings. To expose the panel behind a reverse proxy, set `DASHBOARD_TOKEN` and let the proxy provide TLS and access control; never expose the local dashboard directly to the public internet.

## Data, Migration & Backups

During runtime, state is primarily stored in `data/omni.db`. This file contains private user data and must be stored separately from the code checkout.

To migrate from legacy JSON files, preview the item counts first:

```bash
python scripts/migrate_legacy.py --database data/omni.db \
  --tasks /private/path/synced_todos.json \
  --history /private/path/group_history.json --dry-run
```

Remove `--dry-run` after verifying the output. When creating backups, use the authenticated encryption script:

```bash
python scripts/secure_backup.py create data/omni.db backups/state.omnibak
python scripts/secure_backup.py restore backups/state.omnibak data/restored.db
```

The backup password is read from `OMNI_BACKUP_PASSWORD` or an interactive prompt (minimum 12 characters). Always verify restored databases using SQLite `PRAGMA integrity_check` before replacing an active database.

## Operations & Verification

```bash
# Offline end-to-end check (uses fake remote without touching live accounts)
python scripts/verify_e2e.py

# Run tests and syntax checks
python -m pytest -q
python -m compileall -q core adapters dashboard scripts main.py
node --check core/ai_provider.js
node --check adapters/wechat/wechat_bot.js
```

Deployment templates are provided in `deploy/`: Docker Compose, systemd, and PM2. They are reference baselines rather than drop-in production answers; inspect ports, service accounts, volumes, backup paths, and channel credentials before deployment.

Further details:

- [Architecture Guide](docs/ARCHITECTURE.md)
- [Operations & Troubleshooting](docs/OPERATIONS.md)
- [Security & Privacy Model](docs/SECURITY.md)
- [Migration Guide](docs/MIGRATION.md)

## Privacy Boundaries

The repository contains code and neutral examples only. Never commit account IDs, group numbers, chat messages, preferences, schedules, QR codes, session files, media, access tokens, or backups. Keep `.env`, `data/omni.db`, and `data/wechat/` strictly within environments you control.

## License

[MIT](LICENSE)
