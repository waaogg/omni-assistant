import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_user_context_isolates_users(tmp_path):
    script = r"""
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { UserRegistry, saveConversation, loadConversation, appendChatRecord } = require('./adapters/wechat/user_context.js');
const root = process.argv[1];
(async () => {
  const registry = new UserRegistry(root);
  const a = registry.ensure('wx-user-a');
  const b = registry.ensure('wx-user-b');
  if (a.paths.root === b.paths.root || a.paths.media === b.paths.media || a.paths.workspace === b.paths.workspace || a.paths.agyHome === b.paths.agyHome) process.exit(10);
  if (!fs.existsSync(a.paths.workspace) || !fs.existsSync(b.paths.workspace) || !fs.existsSync(a.paths.agyHome)) process.exit(15);
  saveConversation(a.paths.conversationFile, 'agy-a');
  if (loadConversation(a.paths.conversationFile) !== 'agy-a') process.exit(11);
  if (fs.existsSync(b.paths.conversationFile)) process.exit(12);
  await registry.update('wx-user-a', { status: 'active', todoAccountId: 'todo-a' });
  const activeA = registry.get('wx-user-a');
  const stillPendingB = registry.get('wx-user-b');
  if (activeA.status !== 'active' || stillPendingB.status !== 'pending_todo_binding') process.exit(13);
  if (!fs.existsSync(activeA.paths.workspace) || !fs.existsSync(stillPendingB.paths.workspace)) process.exit(16);
  appendChatRecord(activeA.paths.chatHistoryFile, 'user', '测试消息', { message_type: 1, item_list: [{ type: 1, text_item: { text: '测试消息' } }] });
  const history = fs.readFileSync(activeA.paths.chatHistoryFile, 'utf8');
  if (!history.includes('测试消息') || !history.includes('"message_type": 1')) process.exit(17);
})().catch(() => process.exit(14));
"""
    subprocess.run(
        ["node", "-e", script, str(tmp_path)],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
