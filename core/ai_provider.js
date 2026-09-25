/**
 * Unified AI Provider for Omni-Assistant (Node.js)
 * Supports:
 * 1. OpenAI-compatible API (Default: DeepSeek, OpenAI, etc.)
 * 2. Google Antigravity CLI (agy) subprocess
 */
const { spawn } = require('node:child_process');
const path = require('node:path');


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

const colors = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  cyan: '\x1b[36m',
  yellow: '\x1b[33m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  magenta: '\x1b[35m',
  blue: '\x1b[34m',
  gray: '\x1b[90m',
};

function getCategoryColor(category) {
  if (category.startsWith('SPAWN')) return colors.cyan + colors.bold;
  if (category.startsWith('SANDBOX')) return colors.cyan;
  if (category.startsWith('SECURITY')) return colors.red + colors.bold;
  if (category.startsWith('PROCESS')) return colors.magenta;
  if (category.startsWith('TOOL/ACTIVE')) return colors.yellow + colors.bold;
  if (category.startsWith('TOOL/DONE')) return colors.green;
  if (category.startsWith('TOOL/ERROR')) return colors.red + colors.bold;
  if (category.startsWith('REASONING')) return colors.blue;
  if (category.startsWith('RESULT')) return colors.green + colors.bold;
  if (category.startsWith('CLOSE')) return colors.gray;
  if (category.startsWith('ERROR') || category.startsWith('STDERR') || category.startsWith('TIMEOUT')) return colors.red + colors.bold;
  return colors.reset;
}

function formatAgyLog(category, message, details = null) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const color = getCategoryColor(category);
  console.log(`[${ts}] ${color}[AGY/${category}]${colors.reset} ${message}`);
  if (details !== null && details !== undefined) {
    if (typeof details === 'object') {
      try {
        const text = JSON.stringify(details, null, 2);
        console.log(text.split('\n').map((l) => `           ${colors.dim}${l}${colors.reset}`).join('\n'));
      } catch {
        console.log(`           ${details}`);
      }
    } else {
      console.log(`           ${details}`);
    }
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
      '--output-format', 'stream-json',
    ];
    args.push('--dangerously-skip-permissions');
    if (conversationId) {
      args.push('--conversation', conversationId);
    }

    const startTime = Date.now();
    const cwd = options.cwd || process.env.AGY_CWD || process.cwd();
    const sandboxRoot = options.sandboxRoot || options.env?.OMNI_USER_DATA_DIR || cwd;

    formatAgyLog('SPAWN', `🚀 启动 Antigravity CLI 引擎 (Model: ${AGY_MODEL})`, {
      binary: agent.command,
      model: AGY_MODEL,
      conversationId: conversationId || '(新会话 - 初始无记忆)',
      cwd,
      sandboxRoot,
      envOverrides: {
        OMNI_USER_ID: options.env?.OMNI_USER_ID || 'none',
        AGY_CWD: options.env?.AGY_CWD || cwd,
        MS_TODO_USER_DATA_DIR: options.env?.MS_TODO_USER_DATA_DIR || 'default',
        HOME: options.env?.HOME || 'default',
      },
      promptPreview: promptText.replace(/\s+/g, ' ').slice(0, 120) + (promptText.length > 120 ? '...' : ''),
    });

    formatAgyLog('SANDBOX', `🔒 用户工作区安全沙箱严格锁定`, {
      activeUser: options.env?.OMNI_USER_ID || 'unknown',
      sandboxBoundary: sandboxRoot,
      workspaceCwd: cwd,
      securityPolicy: '严格仅限访问当前用户专属沙箱，严禁越权跨目录探测',
    });

    const child = spawn(agent.command, args, {
      cwd,
      env: { ...process.env, ...(options.env || {}) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    formatAgyLog('PROCESS', `⚡ 子进程已就绪 (PID: ${child.pid})，等待流式推理事件与工具调度...`);

    let stdout = '';
    let stderr = '';
    let streamBuffer = '';
    let finalResult = null;
    let lastConversationId = conversationId;

    const turnStepMap = new Map();
    function formatStepTag(globalStepIndex) {
      if (!turnStepMap.has(globalStepIndex)) {
        turnStepMap.set(globalStepIndex, turnStepMap.size + 1);
      }
      const turnStep = turnStepMap.get(globalStepIndex);
      return `[Step ${globalStepIndex} (本轮第 ${turnStep} 步)]`;
    }

    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      formatAgyLog('TIMEOUT', `❌ 进程超时 (${Math.round(timeoutMs / 1000)}秒)，已被强制终止 (PID: ${child.pid})`);
      reject(new Error(`Antigravity CLI 执行超时 (${Math.round(timeoutMs / 1000)}秒)，请稍后重试`));
    }, timeoutMs);

    child.stdout.on('data', (d) => {
      const chunk = d.toString();
      stdout += chunk;
      streamBuffer += chunk;
      const lines = streamBuffer.split('\n');
      streamBuffer = lines.pop();
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const item = JSON.parse(trimmed);
          if (item.event === 'init' && item.conversation_id) {
            lastConversationId = item.conversation_id;
            formatAgyLog('INIT', `🔗 会话初始化完成 (ID: ${item.conversation_id})`);
          } else if (item.event === 'step_update' && item.step_update) {
            const su = item.step_update;
            if (options.onProgress) {
              options.onProgress(su);
            }
            if (su.step_type === 'tool' && su.state === 'ACTIVE') {
              const p = su.tool_info?.parameters || {};
              const stepTag = formatStepTag(su.step_index);

              // 校验是否访问了沙箱外的路径
              const targetPath = p.AbsolutePath || p.TargetFile || p.path;
              if (targetPath && sandboxRoot) {
                try {
                  const resolvedTarget = path.resolve(String(targetPath));
                  const resolvedSandbox = path.resolve(String(sandboxRoot));
                  const isAllowed = resolvedTarget.toLowerCase().startsWith(resolvedSandbox.toLowerCase());
                  if (!isAllowed) {
                    formatAgyLog('SECURITY', `🚨 越权访问警报: 探测到沙箱外路径!`, {
                      attemptedPath: targetPath,
                      allowedSandbox: sandboxRoot,
                      action: '严禁越权跨用户访问'
                    });
                  } else {
                    formatAgyLog('SANDBOX', `🛡️ 沙箱校验通过: ${path.relative(resolvedSandbox, resolvedTarget) || '.'}`);
                  }
                } catch {}
              }

              if (su.tool_name === 'call_mcp_tool') {
                let parsedArgs = p.Arguments;
                try {
                  if (typeof parsedArgs === 'string') parsedArgs = JSON.parse(parsedArgs);
                } catch {}
                formatAgyLog('TOOL/ACTIVE', `🔧 ${stepTag} 正在调用 MCP 工具: ${p.ServerName || 'mcp'} -> ${p.ToolName}`, {
                  server: p.ServerName,
                  tool: p.ToolName,
                  arguments: parsedArgs
                });
              } else {
                formatAgyLog('TOOL/ACTIVE', `🔧 ${stepTag} 正在调用内置工具: ${su.tool_name}`, p);
              }
            } else if (su.step_type === 'tool' && su.state === 'DONE') {
              const stepTag = formatStepTag(su.step_index);
              const output = su.tool_info?.output;
              let outputSnippet = null;
              if (output !== undefined && output !== null) {
                if (typeof output === 'object') {
                  try { outputSnippet = JSON.stringify(output); } catch { outputSnippet = String(output); }
                } else {
                  outputSnippet = String(output).trim();
                }
                if (outputSnippet.length > 250) {
                  outputSnippet = outputSnippet.slice(0, 250) + '... (已截断)';
                }
              }
              formatAgyLog('TOOL/DONE', `✅ ${stepTag} 工具执行完毕 (耗时: ${su.duration_seconds?.toFixed(2) || 0}s, 工具: ${su.tool_name})`, outputSnippet ? { output: outputSnippet } : null);
            } else if (su.step_type === 'tool' && su.state === 'ERROR') {
              const stepTag = formatStepTag(su.step_index);
              formatAgyLog('TOOL/ERROR', `❌ ${stepTag} 工具执行失败 (耗时: ${su.duration_seconds?.toFixed(2) || 0}s, 工具: ${su.tool_name})`, su.tool_info?.error || su.error || '未知错误');
            } else if (su.step_type === 'agent_response' && su.state === 'DONE') {
              const stepTag = formatStepTag(su.step_index);
              const u = su.usage || {};
              const tokenStr = `Tokens: 输入=${u.input_tokens || 0}, 输出=${u.output_tokens || 0}, 思考=${u.thinking_tokens || 0}`;
              formatAgyLog('REASONING', `💭 ${stepTag} 阶段性思考生成完成 (耗时: ${su.duration_seconds?.toFixed(2) || 0}s | ${tokenStr})`);
            }
          } else if (item.event === 'result' && item.result) {
            finalResult = item.result;
            const r = item.result;
            const u = r.usage || {};
            const tokenSummary = `总耗时=${r.duration_seconds?.toFixed(2) || 0}s | 轮数=${r.num_turns || 1} | 总Token: 输入=${u.input_tokens || 0}, 输出=${u.output_tokens || 0}, 思考=${u.thinking_tokens || 0}`;
            formatAgyLog('RESULT', `🏁 全流程推理圆满完成! [${tokenSummary}]`);
          } else if (item.event === 'error') {
            formatAgyLog('ERROR', `❌ Antigravity CLI 事件错误`, item);
          }
        } catch {}
      }
    });

    child.stderr.on('data', (d) => {
      const errStr = d.toString().trim();
      stderr += errStr;
      if (errStr) {
        formatAgyLog('STDERR', `⚠️ ${errStr}`);
      }
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      const totalElapsed = ((Date.now() - startTime) / 1000).toFixed(2);
      formatAgyLog('CLOSE', `🛑 Antigravity 进程生命周期结束 (PID: ${child.pid}, 退出码: ${code}, 总历时: ${totalElapsed}s)`);

      if (finalResult && finalResult.response) {
        return resolve({
          response: finalResult.response.trim(),
          conversationId: finalResult.conversation_id || lastConversationId || conversationId,
        });
      }
      if (code !== 0 && !stdout.trim()) {
        return reject(new Error(stderr || `agy exited with code ${code}`));
      }
      try {
        const jsonMatch = stdout.trim().match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const parsed = JSON.parse(jsonMatch[0]);
          return resolve({
            response: (parsed.response || stdout).trim(),
            conversationId: parsed.conversation_id || lastConversationId || conversationId,
          });
        }
      } catch {}
      resolve({
        response: stdout.trim() || '（已执行完成）',
        conversationId: lastConversationId || conversationId,
      });
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      formatAgyLog('ERROR', `❌ Antigravity 启动失败: ${err.message}`);
      if (err.code === 'ENOENT') {
        return reject(new Error(`未找到 Antigravity/agy CLI。请安装 agy，或设置 AGY_BIN_PATH；当前尝试路径: ${agent.command}`));
      }
      if (err.code === 'EINVAL') {
        return reject(new Error(`Windows 无法启动 Antigravity/agy CLI（EINVAL）。请确认 AGY_BIN_PATH 指向可执行的 agy/agy.cmd 文件；当前路径: ${agent.command}，工作目录: ${cwd}`));
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
