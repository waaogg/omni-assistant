# Omni-Assistant 2.0 architecture

Omni-Assistant uses channel adapters for transport and one task domain for all
state changes. QQ events are persisted before analysis. The WeChat adapter
restricts senders, downloads bounded media, and passes actual image data to an
HTTP multimodal provider or local paths to a configured CLI provider.

```text
QQ / WeChat / Dashboard
        |
message identity, sender authorization, attachment limits
        |
SQLite queue -> structured decision -> confirmation inbox
        |
TaskService -> local task history -> Microsoft To Do adapter
        |
search / digest / review / follow-up / schedule conflict checks
```

`data/omni.db` is the authoritative v2 state store. It contains hashed channel
identifiers, processing state, tasks, source links, task events, confirmation
items, explicit preferences, search documents, identities, conversations,
synchronization cursors, and scheduled notifications.

Credentials remain in environment variables or provider-owned credential
stores. They are never written to SQLite. Message and document bodies can
contain private user content, so runtime databases and encrypted backups remain
outside source control.

Messages are stored before analysis and marked done only after every action
succeeds. Remote task failure does not create a successful local record. Each
task update records before and after state. Ambiguous time, relevance, or
confidence is routed to the inbox.

HTTP providers receive actual image content and bounded textual attachments.
Local CLI providers receive paths inside the configured workspace. Large,
unsupported, or corrupt files must produce a degraded result, not a claim that
their contents were analyzed.
