/**
 * Unified AI Provider for Omni-Assistant (Node.js)
 * Supports:
 * 1. OpenAI-compatible API (Default: DeepSeek, OpenAI, etc.)
 * 2. Google Antigravity CLI (agy) subprocess
 */
const { spawn } = require('node:child_process');

function getEnv(key, fallback = '') {
  return process.env[key] !== undefined ? process.env[key] : fallback;
}

const AI_PROVIDER = getEnv('AI_PROVIDER', 'agy').toLowerCase().trim();
const LLM_BASE_URL = getEnv('LLM_BASE_URL', 'https://api.deepseek.com/v1').replace(/\/+$/, '');
const LLM_API_KEY = getEnv('LLM_API_KEY', '').trim();
const LLM_MODEL = getEnv('LLM_MODEL', 'deepseek-chat').trim();
const LLM_TEMPERATURE = parseFloat(getEnv('LLM_TEMPERATURE', '0.1')) || 0.1;

const AGY_BIN_PATH = getEnv('AGY_BIN_PATH', 'agy').trim();
const AGY_MODEL = getEnv('AGY_MODEL', 'gemini-3.8-flash-low').trim();

function resolveAgentCommand() {
  return { command: AGY_BIN_PATH, compatibility: 'agy' };
}

/**
 * Call OpenAI-compatible Chat Completions API
 */
async function callOpenAICompatible(messages, timeoutMs = 60000) {
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
      throw new Error(`HTTP ${res.status}: ${text}`);
    }

    const data = JSON.parse(text);
    const choices = data.choices || [];
    if (!choices.length) {
      throw new Error(`LLM returned empty choices: ${text}`);
    }
    const content = choices[0]?.message?.content || '';
    return content.trim();
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

/**
 * Call Antigravity CLI (agy)
 */
async function callAntigravityCLI(promptText, conversationId, timeoutMs = 180000, options = {}) {
  return new Promise((resolve, reject) => {
    const agent = resolveAgentCommand();
    const args = [
      '-p', promptText,
      '--model', AGY_MODEL,
      '--output-format', 'json',
    ];
    args.push('--dangerously-skip-permissions');
    if (conversationId) {
      args.push('--conversation', conversationId);
    }

    const child = spawn(agent.command, args, {
      cwd: options.cwd || process.env.AGY_CWD || process.cwd(),
      env: { ...process.env, ...(options.env || {}) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`Antigravity CLI 执行超时 (${Math.round(timeoutMs / 1000)}秒)，请稍后重试`));
    }, timeoutMs);

    child.stdout.on('data', (d) => { stdout += d.toString(); });
    child.stderr.on('data', (d) => { stderr += d.toString(); });

    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(stderr || `agy exited with code ${code}`));
      }
      try {
        const jsonMatch = stdout.trim().match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const parsed = JSON.parse(jsonMatch[0]);
          return resolve({
            response: (parsed.response || stdout).trim(),
            conversationId: parsed.conversation_id || conversationId,
          });
        }
      } catch {}
      resolve({
        response: stdout.trim() || '（已执行完成）',
        conversationId,
      });
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      if (err.code === 'ENOENT') {
        return reject(new Error(`未找到 Antigravity/agy CLI。请安装 agy，或设置 AGY_BIN_PATH；当前尝试路径: ${agent.command}`));
      }
      if (err.code === 'EINVAL') {
        return reject(new Error(`Windows 无法启动 Antigravity/agy CLI（EINVAL）。请确认 AGY_BIN_PATH 指向可执行的 agy/agy.cmd 文件；当前路径: ${agent.command}，工作目录: ${options.cwd || process.env.AGY_CWD || process.cwd()}`));
      }
      reject(err);
    });
  });
}

/**
 * Unified executeAI function
 * Returns: { response: string, conversationId?: string }
 */
async function executeAI(promptText, conversationId = null, systemPrompt = null, options = {}) {
  if (AI_PROVIDER === 'agy') {
    try {
      return await callAntigravityCLI(promptText, conversationId, 180000, options);
    } catch (err) {
      if (conversationId && err.message.includes('执行超时')) {
        return await callAntigravityCLI(promptText, null, 180000, options);
      }
      throw err;
    }
  }

  // OpenAI-compatible providers remain available when explicitly selected.
  const messages = [];
  if (systemPrompt) {
    messages.push({ role: 'system', content: systemPrompt });
  }
  messages.push({ role: 'user', content: promptText });

  const responseText = await callOpenAICompatible(messages);
  return {
    response: responseText,
    conversationId: conversationId || `conv_${Date.now()}`
  };
}

module.exports = {
  executeAI,
  callOpenAICompatible,
  callAntigravityCLI,
  AI_PROVIDER,
  LLM_MODEL,
  AGY_MODEL,
  LLM_BASE_URL,
};
