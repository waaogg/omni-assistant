#!/usr/bin/env node
/**
 * Run the installed Microsoft To Do MCP module's normal authorization flow
 * in one user's isolated HOME, then activate that user's binding.
 */
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { UserRegistry } = require('../adapters/wechat/user_context.js');

const userId = process.argv[2];
if (!userId) {
  console.error('Usage: node scripts/bind_todo_user.js <wechat-user-id> [todo-account-id]');
  process.exit(2);
}

const dataDir = path.resolve(process.env.WECHAT_DATA_DIR || path.join(__dirname, '..', 'data', 'wechat'));
const registry = new UserRegistry(dataDir);
const user = registry.ensure(userId);
const authModule = process.env.MS_TODO_AUTH_MODULE_PATH ||
  (() => {
    try {
      return require.resolve('@mag-cie/mcp-microsoft-todo/dist/auth.js');
    } catch {
      const candidates = [
        path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@mag-cie', 'mcp-microsoft-todo', 'dist', 'auth.js'),
        '/usr/lib/node_modules/@mag-cie/mcp-microsoft-todo/dist/auth.js',
      ];
      return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
    }
  })();

process.env.HOME = user.paths.auth;
process.env.USERPROFILE = user.paths.auth;
process.env.XDG_CONFIG_HOME = user.paths.auth;
process.env.OMNI_USER_ID = userId;
process.env.MS_TODO_USER_DATA_DIR = user.paths.auth;

const authImportTarget = path.isAbsolute(authModule)
  ? pathToFileURL(authModule).href
  : authModule;

import(authImportTarget).then(async (m) => {
  if (typeof m.getAccessToken !== 'function') {
    throw new Error(`授权模块未导出 getAccessToken: ${authModule}`);
  }
  await m.getAccessToken();
  await registry.update(userId, {
    status: 'active',
    todoAccountId: process.argv[3] || null,
    boundAt: new Date().toISOString(),
  });
  console.log(`Microsoft To Do binding activated for ${userId}`);
}).catch((err) => {
  console.error(`Microsoft To Do authorization failed: ${err.message}`);
  process.exit(1);
});
