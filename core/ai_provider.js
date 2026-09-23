/**
 * Unified AI Provider for Omni-Assistant (Node.js)
 * Supports:
 * OpenAI-compatible API or optional local agent CLIs.
 */
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
function getEnv(key, fallback = '') {
  return process.env[key] !== undefined ? process.env[key] : fallback;
}

const AI_PROVIDER = getEnv('AI_PROVIDER', 'openai').toLowerCase().trim();
const LLM_BASE_URL = getEnv('LLM_BASE_URL', 'https://api.deepseek.com/v1').replace(/\/+$/, '');
const LLM_API_KEY = getEnv('LLM_API_KEY', '').trim();
const LLM_MODEL = getEnv('LLM_MODEL', 'deepseek-chat').trim();
const LLM_TEMPERATURE = parseFloat(getEnv('LLM_TEMPERATURE', '0.1')) || 0.1;
const AUTO_INSTALL_CLI = ['true', '1', 'yes', 'on'].includes(getEnv('AUTO_INSTALL_CLI', 'false').toLowerCase());
const METRICS_FILE = getEnv('AI_METRICS_FILE', path.resolve(__dirname, '../data/ai_metrics.ndjson'));
const CLI_SPECS = {
  agy: { executable: getEnv('AGY_BIN_PATH', 'agy'), package: null, args: (p) => ['-p', p, '--output-format', 'json'] },
  codex: { executable: getEnv('CODEX_BIN_PATH', 'codex'), package: '@openai/codex', args: (p) => ['exec', '--json', p] },
  opencode: { executable: getEnv('OPENCODE_BIN_PATH', 'opencode'), package: 'opencode-ai', args: (p) => ['run', '--format', 'json', p] },
  claude: { executable: getEnv('CLAUDE_BIN_PATH', 'claude'), package: '@anthropic-ai/claude-code', args: (p) => ['-p', p, '--output-format', 'json'] },
};

function cliArgs(provider, prompt) {
  const custom = getEnv(`${provider.toUpperCase()}_ARGS`, '').trim();
  if (custom) {
    return custom.replaceAll('{prompt}', prompt).match(/"[^"]*"|'[^']*'|\S+/g)
      .map((part) => part.replace(/^["']|["']$/g, ''));
  }
  return CLI_SPECS[provider].args(prompt);
}

function ensureCli(provider) {
  const spec = CLI_SPECS[provider];
  if (!spec) throw new Error(`Unsupported AI provider: ${provider}`);
  try {
    execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', [spec.executable], { stdio: 'ignore' });
    return spec;
  } catch {}
  if (!AUTO_INSTALL_CLI) throw new Error(`${provider} CLI not found; set AUTO_INSTALL_CLI=true to install it`);
  if (!spec.package) {
    const installCommand = getEnv(`${provider.toUpperCase()}_INSTALL_COMMAND`, '').trim();
    if (!installCommand) throw new Error(`${provider} CLI is missing; configure ${provider.toUpperCase()}_INSTALL_COMMAND`);
    execFileSync(installCommand, { shell: true, stdio: 'inherit' });
  } else {
    execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['install', '--global', spec.package], { stdio: 'inherit' });
  }
  try {
    execFileSync(process.platform === 'win32' ? 'where.exe' : 'which', [spec.executable], { stdio: 'ignore' });
  } catch {
    throw new Error(`${provider} CLI installation completed but '${spec.executable}' is still unavailable`);
  }
  return spec;
}

function parseCliOutput(text) {
  const findText = (value) => {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (Array.isArray(value)) {
      for (const child of [...value].reverse()) {
        const found = findText(child);
        if (found) return found;
      }
    }
    if (value && typeof value === 'object') {
      for (const key of ['response', 'output', 'text', 'message', 'content']) {
        const found = findText(value[key]);
        if (found) return found;
      }
      for (const child of Object.values(value)) {
        if (typeof child === 'string' && ['item.completed', 'tool_call', 'final'].includes(child)) continue;
        const found = findText(child);
        if (found) return found;
      }
    }
    return '';
  };

  for (const line of text.split(/\r?\n/).reverse()) {
    try {
      const value = JSON.parse(line.trim());
      const found = findText(value);
      if (found) return found;
    } catch {}
  }
  return text.trim();
}

function callCli(promptText, timeoutMs = 300000) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    let settled = false;
    const finish = (success, responseOrError) => {
      if (settled) return;
      settled = true;
      recordMetric(startedAt, success, promptText.length, success ? responseOrError.response.length : 0);
      if (success) resolve(responseOrError);
      else reject(responseOrError);
    };
    let spec;
    try { spec = ensureCli(AI_PROVIDER); } catch (err) { finish(false, err); return; }
    const child = spawn(spec.executable, cliArgs(AI_PROVIDER, promptText), {
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => { child.kill('SIGTERM'); finish(false, new Error(`${AI_PROVIDER} CLI timed out`)); }, timeoutMs);
    child.stdout.on('data', (data) => { stdout += data.toString(); });
    child.stderr.on('data', (data) => { stderr += data.toString(); });
    child.on('error', (err) => { clearTimeout(timer); finish(false, err); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) return finish(false, new Error(`${AI_PROVIDER} CLI failed (${code})`));
      const response = parseCliOutput(stdout);
      if (!response) return finish(false, new Error(`${AI_PROVIDER} CLI returned an empty response`));
      finish(true, { response, conversationId: null });
    });
  });
}

/**
 * Call OpenAI-compatible Chat Completions API
 */
async function callOpenAICompatible(messages, timeoutMs = 60000) {
  const startedAt = Date.now();
  const inputChars = JSON.stringify(messages).length;
  let url = LLM_BASE_URL;
  if (!url.endsWith('/chat/completions')) {
    url = `${url}/chat/completions`;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers = {
    'Content-Type': 'application/json',
  };
  if (LLM_API_KEY) {
    headers['Authorization'] = `Bearer ${LLM_API_KEY}`;
  }

  const payload = {
    model: LLM_MODEL,
    messages: messages,
    temperature: LLM_TEMPERATURE,
  };

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const text = await res.text();
    clearTimeout(timer);

    if (!res.ok) {
      recordMetric(startedAt, false, inputChars);
      // Provider bodies can echo private request content.
      const error = new Error(`LLM endpoint returned HTTP ${res.status}`);
      error.metricRecorded = true;
      throw error;
    }

    const data = JSON.parse(text);
    const choices = data.choices || [];
    if (!choices.length) {
      recordMetric(startedAt, false, inputChars);
      const error = new Error('LLM endpoint returned no choices');
      error.metricRecorded = true;
      throw error;
    }
    const content = choices[0]?.message?.content || '';
    recordMetric(startedAt, true, inputChars, content.length);
    return content.trim();
  } catch (err) {
    clearTimeout(timer);
    if (!err.metricRecorded) recordMetric(startedAt, false, inputChars);
    throw err;
  }
}

function buildMultimodalContent(promptText, attachments = []) {
  const content = [{ type: 'text', text: promptText }];
  const textChunks = [];
  for (const filePath of attachments) {
    if (!filePath || !fs.existsSync(filePath)) continue;
    const stat = fs.statSync(filePath);
    if (!stat.isFile() || stat.size > 10 * 1024 * 1024) continue;
    const ext = path.extname(filePath).toLowerCase();
    const mime = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp' }[ext];
    if (mime) {
      const encoded = fs.readFileSync(filePath).toString('base64');
      content.push({ type: 'image_url', image_url: { url: `data:${mime};base64,${encoded}` } });
    } else if (['.txt', '.md', '.json', '.csv', '.log', '.py', '.js', '.ts'].includes(ext) && stat.size <= 2 * 1024 * 1024) {
      textChunks.push(`\n\n[Attachment: ${path.basename(filePath)}]\n${fs.readFileSync(filePath, 'utf8')}`);
    }
  }
  content[0].text += textChunks.join('');
  return content.length === 1 && textChunks.length === 0 ? promptText : content;
}

/**
 * Unified executeAI function
 * Returns: { response: string, conversationId?: string }
 */
async function executeAI(promptText, conversationId = null, systemPrompt = null, attachments = []) {
  if (!['openai', 'deepseek', 'default'].includes(AI_PROVIDER)) {
    return await callCli(promptText);
  }
  const messages = [];
  if (systemPrompt) {
    messages.push({ role: 'system', content: systemPrompt });
  }
  messages.push({ role: 'user', content: buildMultimodalContent(promptText, attachments) });

  const responseText = await callOpenAICompatible(messages);
  return {
    response: responseText,
    conversationId: conversationId || `conv_${Date.now()}`
  };
}

module.exports = {
  executeAI,
  callOpenAICompatible,
  callCli,
  AI_PROVIDER,
  LLM_MODEL,
  LLM_BASE_URL,
  buildMultimodalContent,
};

function recordMetric(startedAt, success, inputChars, outputChars = 0) {
  const item = {
    at: new Date().toISOString(),
    provider: AI_PROVIDER,
    model: LLM_MODEL,
    duration_ms: Math.max(0, Date.now() - startedAt),
    success: Boolean(success),
    input_chars: Math.max(0, inputChars || 0),
    output_chars: Math.max(0, outputChars || 0),
  };
  try {
    fs.mkdirSync(path.dirname(METRICS_FILE), { recursive: true });
    fs.appendFileSync(METRICS_FILE, `${JSON.stringify(item)}\n`, 'utf8');
  } catch {}
}
