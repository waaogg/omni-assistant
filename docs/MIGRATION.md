# Migration from a private production snapshot

Keep production sessions, tokens, chat histories, course data, and backups out
of this repository. Keep the code checkout and private state in separate
directories.

Preview a legacy JSON import:

```bash
python scripts/migrate_legacy.py --database data/omni.db \
  --tasks /private/path/synced_todos.json \
  --history /private/path/group_history.json --dry-run
```

Remove `--dry-run` after reviewing the count. Channel and sender identifiers
are hashed during import. Message bodies and task descriptions remain private
content in the runtime database.

Create and restore an authenticated encrypted state backup:

```bash
python scripts/secure_backup.py create data/omni.db backups/state.omnibak
python scripts/secure_backup.py restore backups/state.omnibak data/restored.db
```

The password comes from `OMNI_BACKUP_PASSWORD` or an interactive prompt. The
format uses scrypt and AES-GCM. Validate a restored copy with SQLite
`PRAGMA integrity_check` before replacing a live database.

For cutover, stop writers, back up, import into a new database, and start one
channel in shadow mode. Do not run old and new WeChat pollers against the same
session cursor. Database rollback cannot reverse Microsoft To Do mutations;
use task events to identify compensating changes.
