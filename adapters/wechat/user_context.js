const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const REGISTRY_VERSION = 1;
const TODO_SKILL_SOURCE = path.resolve(__dirname, '../../../agy-agent/agy-config/skills/todo_assistant/SKILL.md');
const MULTIMODAL_SKILL_SOURCE = path.resolve(__dirname, '../../../agy-agent/agy-config/skills/multimodal_attachment_extractor/SKILL.md');

function resolveTodoMcpModule() {
  if (process.env.MS_TODO_MCP_PATH && fs.existsSync(process.env.MS_TODO_MCP_PATH)) {
    return process.env.MS_TODO_MCP_PATH;
  }
  const candidates = [
    path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@mag-cie', 'mcp-microsoft-todo', 'dist', 'index.js'),
    '/usr/lib/node_modules/@mag-cie/mcp-microsoft-todo/dist/index.js',
    '/usr/local/lib/node_modules/@mag-cie/mcp-microsoft-todo/dist/index.js',
  ];
  try {
    const resolved = require.resolve('@mag-cie/mcp-microsoft-todo/dist/index.js');
    if (resolved && fs.existsSync(resolved)) return resolved;
  } catch {}
  return candidates.find((c) => fs.existsSync(c)) || candidates[0];
}

function safeUserKey(userId) {
  return crypto.createHash('sha256').update(String(userId)).digest('hex').slice(0, 32);
}

function atomicWriteJson(filePath, value) {
  const tempPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tempPath, JSON.stringify(value, null, 2), { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tempPath, filePath);
}

function appendChatRecord(filePath, direction, content, raw = null) {
  const timestamp = new Date().toISOString();
  const text = content === undefined || content === null ? '' : String(content);
  const lines = [
    `\n## ${timestamp} | ${direction}`,
    '',
    text,
  ];
  if (raw !== null && raw !== undefined) {
    lines.push('', '### 完整原始消息', '', '```json', JSON.stringify(raw, null, 2), '```');
  }
  lines.push('');
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  fs.appendFileSync(filePath, `${lines.join('\n')}\n`, { encoding: 'utf8', mode: 0o600 });
}

class UserRegistry {
  constructor(dataRoot, legacyConversationFile = null) {
    this.dataRoot = path.resolve(dataRoot);
    this.usersRoot = path.join(this.dataRoot, 'users');
    this.registryFile = path.join(this.dataRoot, 'users.json');
    this.writeChain = Promise.resolve();
    this.legacyConversations = {};
    if (legacyConversationFile && fs.existsSync(legacyConversationFile)) {
      try {
        this.legacyConversations = JSON.parse(fs.readFileSync(legacyConversationFile, 'utf8')) || {};
      } catch {}
    }
    fs.mkdirSync(this.usersRoot, { recursive: true, mode: 0o700 });
    this.registry = this.load();
  }

  load() {
    if (!fs.existsSync(this.registryFile)) {
      return { version: REGISTRY_VERSION, users: {} };
    }
    try {
      const data = JSON.parse(fs.readFileSync(this.registryFile, 'utf8'));
      if (data && data.version === REGISTRY_VERSION && data.users && typeof data.users === 'object') {
        return data;
      }
    } catch {}
    throw new Error(`用户绑定注册表损坏: ${this.registryFile}`);
  }

  refresh() {
    this.registry = this.load();
    return this.registry;
  }

  get(userId) {
    this.refresh();
    const id = String(userId);
    const record = this.registry.users[id];
    if (!record) return null;
    const paths = this.pathsFor(id);
    for (const dir of [paths.root, paths.media, paths.auth, paths.workspace]) {
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    }
    this.ensureAgyConfig(paths);
    return { ...record, paths };
  }

  ensure(userId) {
    const id = String(userId);
    let record = this.registry.users[id];
    if (!record) {
      const now = new Date().toISOString();
      record = {
        userId: id,
        status: 'pending_todo_binding',
        createdAt: now,
        updatedAt: now,
        conversationId: this.legacyConversations[id] || null,
      };
      this.registry.users[id] = record;
    }
    const paths = this.pathsFor(id);
    for (const dir of [paths.root, paths.media, paths.auth, paths.workspace]) {
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    }
    if (!fs.existsSync(paths.memoryFile)) {
      atomicWriteJson(paths.memoryFile, {});
    }
    this.ensureAgyConfig(paths);
    return { ...record, paths };
  }

  pathsFor(userId) {
    const root = path.join(this.usersRoot, safeUserKey(userId));
    return {
      root,
      media: path.join(root, 'media'),
      auth: path.join(root, 'todo-auth'),
      agyHome: path.join(root, 'agy-home'),
      workspace: path.join(root, 'agy-workspace'),
      conversationFile: path.join(root, 'conversation.json'),
      syncFile: path.join(root, 'sync_buf.txt'),
      memoryFile: path.join(root, 'synced_todos.json'),
      chatHistoryFile: path.join(root, 'chat-history.md'),
    };
  }

  ensureAgyConfig(paths) {
    const configRoot = path.join(paths.agyHome, '.gemini', 'config');
    const skillsRoot = path.join(configRoot, 'skills');
    const todoSkillDir = path.join(skillsRoot, 'todo_assistant');
    const multiSkillDir = path.join(skillsRoot, 'multimodal_attachment_extractor');
    fs.mkdirSync(todoSkillDir, { recursive: true, mode: 0o700 });
    fs.mkdirSync(multiSkillDir, { recursive: true, mode: 0o700 });

    if (fs.existsSync(TODO_SKILL_SOURCE)) {
      const skill = fs.readFileSync(TODO_SKILL_SOURCE, 'utf8')
        .replaceAll('/root/qqbot/synced_todos.json', paths.memoryFile)
        .replaceAll('{{SYNCED_TODOS_PATH}}', paths.memoryFile);
      fs.writeFileSync(path.join(todoSkillDir, 'SKILL.md'), skill, { mode: 0o600 });
    }
    if (fs.existsSync(MULTIMODAL_SKILL_SOURCE)) {
      const multiSkill = fs.readFileSync(MULTIMODAL_SKILL_SOURCE, 'utf8');
      fs.writeFileSync(path.join(multiSkillDir, 'SKILL.md'), multiSkill, { mode: 0o600 });
    }

    const mcpModulePath = resolveTodoMcpModule();
    const mcpConfig = {
      mcpServers: {
        'microsoft-todo': {
          command: process.execPath,
          args: [mcpModulePath],
          env: {
            MS_TENANT: process.env.MS_TENANT || 'common',
            HOME: paths.auth,
            USERPROFILE: paths.auth,
            XDG_CONFIG_HOME: paths.auth,
          },
          disabled: false,
        },
      },
    };
    atomicWriteJson(path.join(configRoot, 'mcp_config.json'), mcpConfig);
  }

  async update(userId, changes) {
    const id = String(userId);
    const current = this.ensure(id);
    this.registry.users[id] = {
      ...this.registry.users[id],
      ...changes,
      userId: id,
      updatedAt: new Date().toISOString(),
    };
    this.writeChain = this.writeChain.then(() => {
      atomicWriteJson(this.registryFile, this.registry);
    });
    await this.writeChain;
    return { ...this.registry.users[id], paths: current.paths };
  }

  async resetConversation(userId) {
    const current = this.ensure(userId);
    try {
      fs.unlinkSync(current.paths.conversationFile);
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
    return this.update(userId, { conversationId: null });
  }
}

function loadConversation(filePath) {
  if (!fs.existsSync(filePath)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    return data && typeof data.conversationId === 'string' ? data.conversationId : null;
  } catch {
    return null;
  }
}

function saveConversation(filePath, conversationId) {
  atomicWriteJson(filePath, { conversationId, updatedAt: new Date().toISOString() });
}

module.exports = {
  UserRegistry,
  loadConversation,
  saveConversation,
  appendChatRecord,
  safeUserKey,
};
