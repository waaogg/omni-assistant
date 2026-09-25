#!/usr/bin/env node
/**
 * Mark one user's Microsoft To Do binding active after the existing MCP
 * authorization flow has completed in that user's isolated auth directory.
 */
const path = require('node:path');
const { UserRegistry } = require('../adapters/wechat/user_context.js');

const userId = process.argv[2];
const todoAccountId = process.argv[3] || null;
if (!userId) {
  console.error('Usage: node scripts/complete_todo_binding.js <wechat-user-id> [todo-account-id]');
  process.exit(2);
}

const dataDir = path.resolve(process.env.WECHAT_DATA_DIR || path.join(__dirname, '..', 'data', 'wechat'));
const registry = new UserRegistry(dataDir);
registry.update(userId, {
  status: 'active',
  todoAccountId,
  boundAt: new Date().toISOString(),
}).then(() => {
  console.log(`Microsoft To Do binding activated for ${userId}`);
}).catch((err) => {
  console.error(`Failed to activate binding: ${err.message}`);
  process.exit(1);
});
