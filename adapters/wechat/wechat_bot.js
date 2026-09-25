#!/usr/bin/env node
/**
 * WeChat Adapter for Omni-Assistant
 * Directly connects to Tencent's official iLink Bot protocol (ilinkai.weixin.qq.com)
 * Uses core/ai_provider.js for multi-model AI reasoning (DeepSeek, OpenAI, agy CLI)
 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');

// Load environment from root .env if present
const rootEnvPath = path.resolve(__dirname, '../../.env');
if (fs.existsSync(rootEnvPath)) {
  try {
    const lines = fs.readFileSync(rootEnvPath, 'utf8').split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
      const [k, ...v] = trimmed.split('=');
      const key = k.trim();
      const val = v.join('=').trim().replace(/^['"]|['"]$/g, '');
      if (process.env[key] === undefined) {
        process.env[key] = val;
      }
    }
  } catch {}
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

function formatTs() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function printDetails(details) {
  if (details === null || details === undefined) return;
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

function log(msg, details = null) {
  console.log(`[${formatTs()}] ${colors.cyan}[WeChat]${colors.reset} ${msg}`);
  if (details) printDetails(details);
}

function logRecv(msg, details = null) {
  console.log(`[${formatTs()}] ${colors.yellow}${colors.bold}[WECHAT/RECV]${colors.reset} ${msg}`);
  if (details) printDetails(details);
}

function logSend(msg, details = null) {
  console.log(`[${formatTs()}] ${colors.green}${colors.bold}[WECHAT/SEND]${colors.reset} ${msg}`);
  if (details) printDetails(details);
}

function logNet(method, endpoint, status, latencyMs, summary = '') {
  const isOk = status === 200 || status === '200' || status === 0;
  const col = isOk ? colors.gray : colors.red;
  console.log(`[${formatTs()}] ${col}[WECHAT/NET]${colors.reset} ${method} ${endpoint} (Status: ${status}, ${latencyMs}ms) ${summary}`);
}

function logError(cat, msg, err = null) {
  console.error(`[${formatTs()}] ${colors.red}${colors.bold}[WECHAT/${cat}]${colors.reset} ${msg}`);
  if (err) {
    if (err.stack) {
      console.error(err.stack.split('\n').map(l => `           ${colors.red}${l}${colors.reset}`).join('\n'));
    } else {
      console.error(`           ${colors.red}${err}${colors.reset}`);
    }
  }
}

// Global process crash guard
process.on('uncaughtException', (err) => {
  logError('CRASH_PREVENTED', `🚨 未捕获全局异常 (uncaughtException): ${err.message}`, err);
});

process.on('unhandledRejection', (reason, promise) => {
  logError('CRASH_PREVENTED', `🚨 未处理 Promise 拒绝 (unhandledRejection): ${reason?.message || reason}`, reason);
});


// 1. Channel Enable Check
const enableWechat = (process.env.ENABLE_WECHAT || 'false').toLowerCase().trim();
if (enableWechat !== 'true' && enableWechat !== '1') {
  log('ℹ️ 微信通道未启用 (ENABLE_WECHAT=false)，跳过启动。');
  process.exit(0);
}

let qrcodeTerminal;
try {
  qrcodeTerminal = require('qrcode-terminal');
} catch (e) {
  // If not installed in current node_modules, try global or skip terminal rendering
  try {
    qrcodeTerminal = require('/usr/lib/node_modules/qrcode-terminal');
  } catch {}
}

const { executeAI, AI_PROVIDER, LLM_MODEL, AGY_MODEL } = require('../../core/ai_provider.js');
const {
  UserRegistry,
  loadConversation,
  saveConversation,
  appendChatRecord,
} = require('./user_context.js');

const DEFAULT_BASE_URL = (process.env.WECHAT_BASE_URL || 'https://ilinkai.weixin.qq.com').replace(/\/+$/, '');
const CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c';

const DATA_DIR = process.env.WECHAT_DATA_DIR
  ? path.resolve(process.env.WECHAT_DATA_DIR)
  : path.join(__dirname, '../../data/wechat');
const AUTH_FILE = path.join(DATA_DIR, 'auth.json');
const SYNC_FILE = path.join(DATA_DIR, 'sync_buf.txt');
const LEGACY_CONV_FILE = path.join(DATA_DIR, 'conversations.json');
const userRegistry = new UserRegistry(DATA_DIR, LEGACY_CONV_FILE);
const bindingProcesses = new Map();

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}
function loadAuth() {
  if (fs.existsSync(AUTH_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8'));
    } catch {
      return null;
    }
  }
  return null;
}

function saveAuth(data) {
  fs.writeFileSync(AUTH_FILE, JSON.stringify(data, null, 2), 'utf8');
}

function loadSyncBuf() {
  if (fs.existsSync(SYNC_FILE)) {
    try {
      return fs.readFileSync(SYNC_FILE, 'utf8');
    } catch {
      return '';
    }
  }
  return '';
}

function saveSyncBuf(buf) {
  fs.writeFileSync(SYNC_FILE, buf || '', 'utf8');
}

function randomWechatUin() {
  const uint32 = crypto.randomBytes(4).readUInt32BE(0);
  return Buffer.from(String(uint32), 'utf8').toString('base64');
}

function buildHeaders(token) {
  const headers = {
    'Content-Type': 'application/json',
    AuthorizationType: 'ilink_bot_token',
    'X-WECHAT-UIN': randomWechatUin(),
    'iLink-App-Id': 'bot',
    'iLink-App-ClientVersion': '131584',
  };
  if (token) {
    headers.Authorization = `Bearer ${token.trim()}`;
  }
  return headers;
}

async function apiPost(baseUrl, endpoint, body, token, timeoutMs = 35000) {
  const url = `${baseUrl.replace(/\/+$/, '')}/${endpoint.replace(/^\/+/, '')}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const start = Date.now();

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: buildHeaders(token),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    clearTimeout(timer);
    const latency = Date.now() - start;
    if (!res.ok) {
      logNet('POST', endpoint, res.status, latency, `HTTP Error: ${text.slice(0, 120)}`);
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    const parsed = JSON.parse(text);
    if (endpoint !== 'ilink/bot/getupdates' && endpoint !== 'ilink/bot/sendtyping') {
      logNet('POST', endpoint, res.status, latency, `ret=${parsed.ret}, errcode=${parsed.errcode || 0}`);
    }
    return parsed;
  } catch (err) {
    clearTimeout(timer);
    const latency = Date.now() - start;
    if (err.name === 'AbortError') {
      return { ret: 0, timeout: true };
    }
    logNet('POST', endpoint, 'ERR', latency, err.message);
    throw err;
  }
}

async function apiGet(url, token, timeoutMs = 25000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: buildHeaders(token),
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

async function loginFlow() {
  log('====================================================');
  log('  Omni-Assistant: 微信扫码接入授权流程');
  log('====================================================');

  const baseUrl = DEFAULT_BASE_URL;
  const qrRes = await apiGet(`${baseUrl}/ilink/bot/get_bot_qrcode?bot_type=3`, null);
  if (!qrRes.qrcode) {
    throw new Error('获取微信登录二维码失败: ' + JSON.stringify(qrRes));
  }

  const qrcode = qrRes.qrcode;
  const qrUrl = qrRes.qrcode_img_content ||
    `https://ilinkai.weixin.qq.com/ilink/bot/qrcode/${qrcode}`;
  const qrPage = path.join(DATA_DIR, 'wechat-login-qr.html');
  const escapedQrUrl = qrUrl.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  fs.writeFileSync(
    qrPage,
    `<!doctype html><meta charset="utf-8"><title>微信登录二维码</title>` +
      `<style>body{font-family: sans-serif;text-align:center;margin:3rem}` +
      `img{width:min(80vw,480px);image-rendering:auto}</style>` +
      `<h1>请使用微信扫描二维码</h1>` +
      `<p>如果二维码过期，请重新启动适配器。</p>` +
      `<img src="${escapedQrUrl}" alt="微信登录二维码">` +
      `<p><a href="${escapedQrUrl}">${escapedQrUrl}</a></p>`,
    { encoding: 'utf8', mode: 0o600 }
  );
  log(`二维码链接（可复制到浏览器打开）: ${qrUrl}`);
  log(`本地二维码页面: ${qrPage}`);
  log(`\n请使用微信扫描下方二维码以绑定助理机器人：`);
  if (qrcodeTerminal) {
    qrcodeTerminal.generate(qrUrl, { small: true });
  } else {
    log(`二维码链接: ${qrUrl}`);
  }

  log(`\n长轮询等待微信确认授权中...`);

  while (true) {
    const checkRes = await apiGet(
      `${baseUrl}/ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`,
      null,
      40000
    );

    const status = checkRes.status;
    if (status === 'wait') {
      process.stdout.write('.');
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }

    if (status === 'scaned') {
      console.log('\n');
      log('📱 已扫描二维码，请在微信端点击【确认授权】...');
      continue;
    }

    if (status === 'confirmed') {
      console.log('\n');
      log('🎉 微信授权成功！正在保存登录态凭据...');
      const authData = {
        botToken: checkRes.bot_token,
        botId: checkRes.ilink_bot_id || checkRes.bot_id ||
          String(checkRes.bot_token || '').split(':', 1)[0],
        userId: checkRes.ilink_user_id,
        baseUrl: checkRes.baseurl || baseUrl,
        loginTime: new Date().toISOString(),
      };
      saveAuth(authData);
      return authData;
    }

    if (status === 'expired') {
      throw new Error('二维码已过期，请重新启动适配器获取新二维码。');
    }

    await new Promise((r) => setTimeout(r, 2000));
  }
}

function parseAesKey(rawKey) {
  if (!rawKey) return null;
  const str = String(rawKey).trim();
  if (/^[0-9a-fA-F]{32}$/.test(str)) {
    return Buffer.from(str, 'hex');
  }
  const b = Buffer.from(str, 'base64');
  if (b.length === 16) {
    return b;
  }
  if (Buffer.byteLength(str, 'utf8') === 16) {
    return Buffer.from(str, 'utf8');
  }
  return b;
}

function decryptAesEcb(encryptedBuf, keyBuf) {
  const decipher = crypto.createDecipheriv('aes-128-ecb', keyBuf, null);
  decipher.setAutoPadding(true);
  return Buffer.concat([decipher.update(encryptedBuf), decipher.final()]);
}

function detectImageExtension(buf) {
  if (buf.length >= 4) {
    if (buf[0] === 0xFF && buf[1] === 0xD8 && buf[2] === 0xFF) return '.jpg';
    if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4E && buf[3] === 0x47) return '.png';
    if (buf[0] === 0x47 && buf[1] === 0x49 && buf[2] === 0x46) return '.gif';
    if (buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46 &&
        buf.slice(8, 12).toString('ascii') === 'WEBP') return '.webp';
  }
  return '.png';
}

function cleanOldMediaFiles(mediaDir) {
  try {
    const files = fs.readdirSync(mediaDir);
    const now = Date.now();
    const SEVEN_DAYS = 7 * 24 * 3600 * 1000;
    for (const file of files) {
      const p = path.join(mediaDir, file);
      const stat = fs.statSync(p);
      if (now - stat.mtimeMs > SEVEN_DAYS) {
        fs.unlinkSync(p);
      }
    }
  } catch {}
}

async function downloadAndSaveWechatImage(imageItem, mediaDir) {
  if (!imageItem) return null;
  const media = imageItem.media || {};
  let url = media.full_url;
  if (!url && media.encrypt_query_param) {
    url = `${CDN_BASE_URL}/download?encrypted_query_param=${encodeURIComponent(media.encrypt_query_param)}`;
  }
  if (!url) return null;

  const key = parseAesKey(media.aes_key);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);

  let res;
  try {
    res = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    throw new Error(`微信 CDN 下载失败: HTTP ${res.status}`);
  }
  const encryptedBuf = Buffer.from(await res.arrayBuffer());

  let decryptedBuf = encryptedBuf;
  if (key) {
    try {
      decryptedBuf = decryptAesEcb(encryptedBuf, key);
    } catch {}
  }

  fs.mkdirSync(mediaDir, { recursive: true, mode: 0o700 });
  cleanOldMediaFiles(mediaDir);
  const ext = detectImageExtension(decryptedBuf);
  const filename = `wechat_img_${Date.now()}_${crypto.randomBytes(4).toString('hex')}${ext}`;
  const filePath = path.join(mediaDir, filename);
  fs.writeFileSync(filePath, decryptedBuf);
  return filePath;
}

async function downloadAndSaveWechatVideo(videoItem, mediaDir) {
  if (!videoItem) return null;
  const media = videoItem.media || {};
  let url = media.full_url;
  if (!url && media.encrypt_query_param) {
    url = `${CDN_BASE_URL}/download?encrypted_query_param=${encodeURIComponent(media.encrypt_query_param)}`;
  }

  const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
  if (videoItem.video_size && videoItem.video_size > MAX_VIDEO_BYTES) {
    if (videoItem.thumb_media) {
      const thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media }, mediaDir);
      return { thumbPath, duration: videoItem.play_length, fallback: true, size: videoItem.video_size };
    }
    return null;
  }

  if (!url) {
    if (videoItem.thumb_media) {
      const thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media }, mediaDir);
      return { thumbPath, duration: videoItem.play_length, fallback: true };
    }
    return null;
  }

  const key = parseAesKey(media.aes_key);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 35000);

  let res;
  try {
    res = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    throw new Error(`微信视频 CDN 下载失败: HTTP ${res.status}`);
  }
  const encryptedBuf = Buffer.from(await res.arrayBuffer());

  let decryptedBuf = encryptedBuf;
  if (key) {
    try {
      decryptedBuf = decryptAesEcb(encryptedBuf, key);
    } catch {}
  }

  fs.mkdirSync(mediaDir, { recursive: true, mode: 0o700 });
  cleanOldMediaFiles(mediaDir);
  const filename = `wechat_video_${Date.now()}_${crypto.randomBytes(4).toString('hex')}.mp4`;
  const filePath = path.join(mediaDir, filename);
  fs.writeFileSync(filePath, decryptedBuf);

  let thumbPath = null;
  if (videoItem.thumb_media) {
    try {
      thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media }, mediaDir);
    } catch {}
  }

  return {
    filePath,
    thumbPath,
    duration: videoItem.play_length,
    size: decryptedBuf.length
  };
}

async function downloadAndSaveWechatFile(fileItem, mediaDir) {
  if (!fileItem) return null;
  const media = fileItem.media || {};
  let url = media.full_url;
  if (!url && media.encrypt_query_param) {
    url = `${CDN_BASE_URL}/download?encrypted_query_param=${encodeURIComponent(media.encrypt_query_param)}`;
  }
  if (!url) return null;

  const MAX_FILE_BYTES = 50 * 1024 * 1024;
  if (fileItem.file_size && fileItem.file_size > MAX_FILE_BYTES) {
    return { fileName: fileItem.file_name || 'unknown', size: fileItem.file_size, skipped: true };
  }

  const key = parseAesKey(media.aes_key);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 35000);

  let res;
  try {
    res = await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    throw new Error(`微信文件 CDN 下载失败: HTTP ${res.status}`);
  }
  const encryptedBuf = Buffer.from(await res.arrayBuffer());

  let decryptedBuf = encryptedBuf;
  if (key) {
    try {
      decryptedBuf = decryptAesEcb(encryptedBuf, key);
    } catch {}
  }

  fs.mkdirSync(mediaDir, { recursive: true, mode: 0o700 });
  cleanOldMediaFiles(mediaDir);
  const rawName = fileItem.file_name || 'document';
  const ext = path.extname(rawName) || '.bin';
  const safeBase = path.basename(rawName, ext).replace(/[^\w\u4e00-\u9fa5_-]/g, '_');
  const filename = `wechat_file_${Date.now()}_${safeBase}${ext}`;
  const filePath = path.join(mediaDir, filename);
  fs.writeFileSync(filePath, decryptedBuf);

  return {
    filePath,
    fileName: rawName,
    size: decryptedBuf.length
  };
}

function formatQuoteContext(refMsg) {
  if (!refMsg) return '';
  const parts = [];
  if (refMsg.title) {
    parts.push(`"${refMsg.title.trim()}"`);
  }
  if (refMsg.message_item) {
    const item = refMsg.message_item;
    if (item.type === 1 && item.text_item && item.text_item.text) {
      parts.push(`"${item.text_item.text.trim()}"`);
    } else if (item.type === 2) {
      parts.push('[图片]');
    } else if (item.type === 5) {
      parts.push('[视频]');
    } else if (item.type === 4 && item.file_item) {
      parts.push(`[文件: ${item.file_item.file_name || '文档'}]`);
    }
  }
  if (parts.length === 0) return '';
  return `[用户引用了历史消息]: ${parts.join(' ')}\n`;
}

async function sendWechatMessage(auth, toUserId, contextToken, text) {
  const clientId = `cli_${Date.now()}_${crypto.randomBytes(3).toString('hex')}`;
  const textPreview = String(text || '').replace(/\s+/g, ' ').slice(0, 100) + (String(text || '').length > 100 ? '...' : '');
  logSend(`📤 发送微信回复至 ${toUserId} (${String(text || '').length} 字符): "${textPreview}"`);

  const body = {
    msg: {
    from_user_id: '',
      to_user_id: toUserId,
      client_id: clientId,
    message_type: 2,
      message_state: 2,
      context_token: contextToken || undefined,
      item_list: [
        {
          type: 1,
          text_item: { text: text || '（已处理）' },
        },
      ],
    },
    base_info: { channel_version: '2.4.8', bot_agent: 'AntigravityClawBot/1.0' },
  };

  const start = Date.now();
  const result = await apiPost(auth.baseUrl, 'ilink/bot/sendmessage', body, auth.botToken, 15000);
  const latency = Date.now() - start;
  const recipient = userRegistry.get(toUserId) || userRegistry.ensure(toUserId);
  try {
    appendChatRecord(recipient.paths.chatHistoryFile, 'assistant', text || '（已处理）');
  } catch (err) {
    logError('STORAGE', `保存聊天记录失败: ${err.message}`);
  }
  if (result && (result.ret !== undefined && result.ret !== 0 || result.errcode !== undefined && result.errcode !== 0)) {
    logError('SEND_FAIL', `回复接口返回异常: ret=${result.ret}, errcode=${result.errcode}, errmsg=${result.errmsg || 'none'}`);
  } else {
    logSend(`✅ 回复发送成功 (耗时: ${latency}ms, ret=0, to=${toUserId})`);
  }
  return result;
}

async function sendTypingStatus(auth, toUserId, typingTicket) {
  if (!typingTicket) return;
  try {
    await apiPost(auth.baseUrl, 'ilink/bot/sendtyping', {
      ilink_user_id: toUserId,
      typing_ticket: typingTicket,
      status: 1,
      base_info: { channel_version: '2.4.8', bot_agent: 'AntigravityClawBot/1.0' },
    }, auth.botToken, 5000);
  } catch {}
}

async function stopTypingStatus(auth, toUserId, typingTicket, interval) {
  if (interval) clearInterval(interval);
  if (!typingTicket) return;
  try {
    await apiPost(auth.baseUrl, 'ilink/bot/sendtyping', {
      ilink_user_id: toUserId,
      typing_ticket: typingTicket,
      status: 0,
      base_info: { channel_version: '2.4.8', bot_agent: 'AntigravityClawBot/1.0' },
    }, auth.botToken, 5000);
  } catch (err) {
    log(`⚠️ 停止输入状态失败: ${err.message}`);
  }
}

async function startTodoBinding(auth, userId, contextToken) {
  if (bindingProcesses.has(userId)) {
    await sendWechatMessage(auth, userId, contextToken, '🔐 你的 Microsoft To Do 授权已经在进行中，请打开之前收到的链接完成授权。');
    return;
  }

  const script = path.join(__dirname, '../../scripts/bind_todo_user.js');
  const user = userRegistry.ensure(userId);
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
  const child = spawn(process.execPath, [script, userId], {
    cwd: path.resolve(__dirname, '../..'),
    env: {
      ...process.env,
      MS_TODO_AUTH_MODULE_PATH: authModule,
      HOME: user.paths.auth,
      USERPROFILE: user.paths.auth,
      XDG_CONFIG_HOME: user.paths.auth,
      OMNI_USER_ID: userId,
      MS_TODO_USER_DATA_DIR: user.paths.auth,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  bindingProcesses.set(userId, child);
  await userRegistry.update(userId, { status: 'authorizing' });
  await sendWechatMessage(
    auth,
    userId,
    contextToken,
    '🔐 已启动你的 Microsoft To Do 独立授权流程。请稍候，登录网址和设备码会自动发送给你。'
  );

  let stderrBuffer = '';
  child.stderr.on('data', async (chunk) => {
    stderrBuffer += chunk.toString();
    const blocks = stderrBuffer.split(/\r?\n\r?\n/);
    stderrBuffer = blocks.pop() || '';
    for (const block of blocks.map((value) => value.trim()).filter(Boolean)) {
      try {
        await sendWechatMessage(auth, userId, contextToken, `🌐 Microsoft To Do 授权提示：\n${block}`);
      } catch (err) {
        log(`⚠️ 无法发送授权提示: ${err.message}`);
      }
    }
  });
  child.on('close', async (code) => {
    bindingProcesses.delete(userId);
    const current = userRegistry.get(userId);
    if (code === 0) {
      if (!current || current.status !== 'active') {
        await userRegistry.update(userId, { status: 'active', boundAt: new Date().toISOString() });
      }
      await sendWechatMessage(auth, userId, contextToken, '✅ Microsoft To Do 授权成功！现在可以直接发送待办指令了。');
    } else if (code !== 0) {
      await userRegistry.update(userId, { status: 'pending_todo_binding' });
      await sendWechatMessage(auth, userId, contextToken, '❌ Microsoft To Do 授权未完成，请重新发送 /bind_todo 再试。');
    }
  });
  child.on('error', async (err) => {
    bindingProcesses.delete(userId);
    await userRegistry.update(userId, { status: 'pending_todo_binding' });
    log(`⚠️ Microsoft To Do 授权进程失败: ${err.message}`);
    try {
      await sendWechatMessage(auth, userId, contextToken, `❌ 无法启动 Microsoft To Do 授权：${err.message}`);
    } catch (sendErr) {
      log(`⚠️ 无法发送授权启动错误: ${sendErr.message}`);
    }
  });
}

function getNowGmt8Str() {
  const d = new Date(Date.now() + 8 * 3600 * 1000);
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  const h = String(d.getUTCHours()).padStart(2, '0');
  const min = String(d.getUTCMinutes()).padStart(2, '0');
  const s = String(d.getUTCSeconds()).padStart(2, '0');
  const w = weekdays[d.getUTCDay()];
  return `${y}年${m}月${day}日 ${w} ${h}:${min}:${s} (GMT+8)`;
}

function createProgressNotifier(sendFn) {
  const sentPhases = new Set();
  let lastSentTime = 0;
  const MIN_INTERVAL_MS = 2500;

  return async (stepUpdate) => {
    if (!stepUpdate || stepUpdate.state !== 'ACTIVE') return;

    let message = null;
    let phaseKey = null;

    if (stepUpdate.step_type === 'tool') {
      const toolName = stepUpdate.tool_name;
      const toolInfo = stepUpdate.tool_info || {};
      const params = toolInfo.parameters || {};

      if (toolName === 'call_mcp_tool') {
        const mcpTool = params.ToolName || '';
        if (mcpTool === 'list_task_lists' || mcpTool === 'list_tasks') {
          phaseKey = 'query_tasks';
          message = '🔍 [思考中 1/3] 正在查询 Microsoft To Do 待办清单与比对查重...';
        } else if (mcpTool === 'create_task') {
          phaseKey = 'create_task';
          message = '📝 [思考中 2/3] 正在创建 Microsoft To Do 待办事项并设置闹钟...';
        } else if (mcpTool === 'update_task') {
          phaseKey = 'update_task';
          message = '⚡ [思考中 2/3] 发现重合待办，正在依据最新通知覆写更正...';
        } else if (mcpTool === 'complete_task') {
          phaseKey = 'complete_task';
          message = '✅ [思考中 2/3] 正在将待办标记为已完成...';
        } else if (mcpTool === 'delete_task') {
          phaseKey = 'delete_task';
          message = '🗑️ [思考中 2/3] 正在删除指定待办...';
        }
      } else if (toolName === 'view_file') {
        const filePath = String(params.AbsolutePath || '');
        if (filePath.includes('synced_todos.json')) {
          phaseKey = 'read_memory';
          message = '📖 [思考中 1/3] 正在检索本地持久化记忆库...';
        } else if (filePath.includes('media')) {
          phaseKey = 'read_media';
          message = '🖼️ [思考中 1/3] 正在解构分析附件/图片内容...';
        }
      } else if (toolName === 'write_to_file') {
        const filePath = String(params.TargetFile || '');
        if (filePath.includes('synced_todos.json')) {
          phaseKey = 'write_memory';
          message = '💾 [思考中 3/3] 正在更新本地同步记忆库...';
        }
      }
    }

    if (phaseKey && !sentPhases.has(phaseKey)) {
      const now = Date.now();
      if (now - lastSentTime >= MIN_INTERVAL_MS) {
        sentPhases.add(phaseKey);
        lastSentTime = now;
        try {
          await sendFn(message);
        } catch (e) {
          // ignore notification sending error to not disrupt main task
        }
      }
    }
  };
}

async function runDaemon() {
  let auth = loadAuth();
  if (!auth || !auth.botToken) {
    log('⚠️ 未检测到有效微信登录态，开始执行扫码登录流程...');
    auth = await loginFlow();
  }

  log(`🤖 微信通道适配器已就绪！`);
  log(`📡 腾讯 iLink 官方网关: ${auth.baseUrl}`);
  log(`🧠 AI 驱动模式: ${AI_PROVIDER} (${AI_PROVIDER === 'agy' ? AGY_MODEL : LLM_MODEL})`);
  log(`📂 工作区数据目录: ${DATA_DIR}`);
  log(`💬 开始监听微信私聊消息...`);

  try {
    await apiPost(auth.baseUrl, 'ilink/bot/msg/notifystart', {
      base_info: { channel_version: '2.4.8', bot_agent: 'AntigravityClawBot/1.0' }
    }, auth.botToken, 5000);
    log('📡 已向腾讯网关发送 notifystart 就绪心跳');
  } catch (e) {
    log('⚠️ 发送 notifystart 心跳异常: ' + e.message);
  }

  let syncBuf = loadSyncBuf();

  while (true) {
    try {
      const updates = await apiPost(auth.baseUrl, 'ilink/bot/getupdates', {
        get_updates_buf: syncBuf,
        base_info: {
          channel_version: '2.4.8',
          bot_agent: 'AntigravityClawBot/1.0',
        },
      }, auth.botToken, 40000);

      if (updates.timeout) {
        continue;
      }

      const isApiError = (updates.ret !== undefined && updates.ret !== 0) ||
                         (updates.errcode !== undefined && updates.errcode !== 0);

      if (isApiError) {
        log(`⚠️ 轮询返回 ret=${updates.ret}, errcode=${updates.errcode}, errmsg=${updates.errmsg || 'none'}`);
        if (updates.ret === -14 || updates.errcode === -14) {
          log('⚠️ 微信会话凭据已失效，准备重新登录...');
          auth = await loginFlow();
          continue;
        }
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }

      if (updates.get_updates_buf || updates.sync_buf) {
        syncBuf = updates.get_updates_buf || updates.sync_buf;
        saveSyncBuf(syncBuf);
      }

      const msgs = updates.msgs || [];
      if (msgs.length > 0) {
        log(`📥 轮询获取到新消息批次，共计 ${msgs.length} 条原始记录`);
        for (const incoming of msgs) {
          const items = incoming.item_list || [];
          const summaryList = items.map((it) => {
            if (it.type === 1) return `[文本: "${it.text_item?.text?.trim() || ''}"]`;
            if (it.type === 2) return `[图片: aes_key=${it.image_item?.media?.aes_key ? 'yes' : 'no'}]`;
            if (it.type === 3) return `[语音: "${it.voice_item?.text || ''}"]`;
            if (it.type === 4) return `[文件: "${it.file_item?.file_name || '文档'}"]`;
            if (it.type === 5) return `[视频: 时长=${it.video_item?.play_length || 0}s]`;
            return `[类型 ${it.type}]`;
          });
          logRecv(`📨 收到微信原始消息: from=${incoming.from_user_id || 'none'} | type=${incoming.message_type} | msg_id=${incoming.msg_id || 'none'} seq=${incoming.sequence_id || 'none'}\n           内容: ${summaryList.join(' ') || '(空内容条目)'}`);
        }
      }


      const validMsgs = msgs.filter(m => m.from_user_id !== auth.botId && m.message_type !== 2);
      if (msgs.length > 0 && validMsgs.length === 0) {
        log(`⚠️ 消息全部被过滤: botId=${auth.botId || 'none'}`);
      }

      const userGroups = new Map();
      for (const msg of validMsgs) {
        const uid = msg.from_user_id;
        if (!uid) continue;
        if (!userGroups.has(uid)) userGroups.set(uid, []);
        userGroups.get(uid).push(msg);
      }

      for (const [fromUser, userMsgs] of userGroups.entries()) {
        const user = userRegistry.get(fromUser) || userRegistry.ensure(fromUser);
        for (const incoming of userMsgs) {
          try {
            appendChatRecord(
              user.paths.chatHistoryFile,
              'user',
              '微信原始消息（完整 JSON 见下方）',
              incoming
            );
          } catch (err) {
            log(`⚠️ 保存用户聊天记录失败: ${err.message}`);
          }
        }
        const conversationId = loadConversation(user.paths.conversationFile) || user.conversationId;
        const textParts = [];
        const imageItems = [];
        const videoItems = [];
        const fileItems = [];
        let latestContextToken = null;

        for (const msg of userMsgs) {
          if (msg.context_token) latestContextToken = msg.context_token;
          const items = msg.item_list || [];
          for (const item of items) {
            let quotePrefix = '';
            if (item.ref_msg) {
              quotePrefix = formatQuoteContext(item.ref_msg);
            }

            if (item.type === 1 && item.text_item && item.text_item.text) {
              const t = item.text_item.text.trim();
              if (t) textParts.push(`${quotePrefix}${t}`);
            } else if (item.type === 3 && item.voice_item && item.voice_item.text) {
              const t = item.voice_item.text.trim();
              if (t) textParts.push(`${quotePrefix}${t}`);
            } else if (item.type === 2 && item.image_item) {
              imageItems.push(item.image_item);
              if (quotePrefix) textParts.push(quotePrefix.trim());
            } else if (item.type === 5 && item.video_item) {
              videoItems.push(item.video_item);
              if (quotePrefix) textParts.push(quotePrefix.trim());
            } else if (item.type === 4 && item.file_item) {
              fileItems.push(item.file_item);
              if (quotePrefix) textParts.push(quotePrefix.trim());
            }
          }
        }

        const rawText = textParts.join('\n').trim();
        if (!rawText && imageItems.length === 0 && videoItems.length === 0 && fileItems.length === 0) {
          continue;
        }

        if (imageItems.length === 0 && videoItems.length === 0 && fileItems.length === 0) {
          if (rawText === '/help' || rawText === '#help') {
            const bindingLine = user.status === 'active'
              ? '✅ Microsoft To Do 已完成绑定'
              : '⚠️ Microsoft To Do 尚未绑定：请发送 /bind_todo 开始授权';
            const helpMsg = `🤖 Omni-Assistant 微信智能助手\n\n- 直接发送对话、任务需求、图片或文档开始交互\n- /bind_todo : 绑定你自己的 Microsoft To Do 账户\n- /binding_status : 查看个人绑定状态\n- /think on / off : 开启或关闭思考链阶段进度通知 (默认关闭)\n- /status : 查看当前系统与 AI 驱动状态\n- /reset  : 清除你的 agy 会话记忆\n- /help   : 查看本帮助说明\n\n${bindingLine}`;
            await sendWechatMessage(auth, fromUser, latestContextToken, helpMsg);
            continue;
          }

          if (rawText === '/think on' || rawText === '#think on') {
            await userRegistry.update(fromUser, { thinkMode: true });
            await sendWechatMessage(auth, fromUser, latestContextToken, '💡 思考链阶段通知已开启！执行任务时，机器人将在关键节点（查询待办、同步微软、更新记忆）发送实时进度。发送 /think off 可随时关闭。');
            continue;
          }

          if (rawText === '/think off' || rawText === '#think off') {
            await userRegistry.update(fromUser, { thinkMode: false });
            await sendWechatMessage(auth, fromUser, latestContextToken, '🔕 思考链阶段通知已关闭，将仅在任务全部完成后发送最终回复。发送 /think on 可重新开启。');
            continue;
          }

          if (rawText === '/bind_todo' || rawText === '#bind_todo') {
            await startTodoBinding(auth, fromUser, latestContextToken);
            continue;
          }

          if (rawText === '/binding_status' || rawText === '#binding_status') {
            const statusText = user.status === 'active'
              ? '✅ 你的 Microsoft To Do 已绑定，任务和 AI 会话均使用独立用户空间。'
              : user.status === 'authorizing'
                ? '🔐 你的 Microsoft To Do 授权正在进行中，请使用之前收到的 Microsoft 登录网址和设备码完成授权。'
              : '⚠️ 你的 Microsoft To Do 尚未绑定，请发送 /bind_todo 开始授权。';
            await sendWechatMessage(auth, fromUser, latestContextToken, statusText);
            continue;
          }

          if (rawText === '/reset' || rawText === '/clear' || rawText === '#reset' || rawText === '#clear') {
            await userRegistry.resetConversation(fromUser);
            await sendWechatMessage(auth, fromUser, latestContextToken, '🔄 会话记忆已重置，接下来将开始全新对话。');
            continue;
          }

          if (rawText === '/status' || rawText === '#status') {
            const activeModel = AI_PROVIDER === 'agy' ? AGY_MODEL : LLM_MODEL;
            const thinkStatus = user.thinkMode ? '已开启 (ON)' : '已关闭 (OFF)';
            const statusMsg = `📊 你的智能体状态报告:\n- AI 引擎: ${AI_PROVIDER} (${activeModel})\n- Microsoft To Do: ${user.status === 'active' ? '已绑定' : '未绑定'}\n- 思考链阶段通知: ${thinkStatus}\n- 宿主负载: ${os.loadavg()[0].toFixed(2)}\n- 运行时间: ${(os.uptime() / 3600).toFixed(1)} 小时\n- 当前会话: ${conversationId || '无 (首轮)'}`;
            await sendWechatMessage(auth, fromUser, latestContextToken, statusMsg);
            continue;
          }
        }

        const downloadedImages = [];
        if (imageItems.length > 0) {
          for (const item of imageItems) {
            try {
              const p = await downloadAndSaveWechatImage(item, user.paths.media);
              if (p) downloadedImages.push(p);
            } catch {}
          }
        }

        const downloadedVideos = [];
        if (videoItems.length > 0) {
          for (const item of videoItems) {
            try {
              const v = await downloadAndSaveWechatVideo(item, user.paths.media);
              if (v) downloadedVideos.push(v);
            } catch {}
          }
        }

        const downloadedFiles = [];
        if (fileItems.length > 0) {
          for (const item of fileItems) {
            try {
              const f = await downloadAndSaveWechatFile(item, user.paths.media);
              if (f) downloadedFiles.push(f);
            } catch {}
          }
        }

        let promptForAI = '';
        if (downloadedImages.length === 0 && downloadedVideos.length === 0 && downloadedFiles.length === 0) {
          promptForAI = rawText;
        } else {
          const mediaLines = [];
          if (downloadedImages.length > 0) {
            mediaLines.push(`【用户发送了 ${downloadedImages.length} 张图片，本地已落盘至】：`);
            downloadedImages.forEach((p, idx) => mediaLines.push(`- 图片 ${idx + 1}: ${p}`));
          }
          if (downloadedVideos.length > 0) {
            mediaLines.push(`【用户发送了 ${downloadedVideos.length} 个视频，本地已解密落盘至】：`);
            downloadedVideos.forEach((v, idx) => {
              if (v.filePath) mediaLines.push(`- 视频 ${idx + 1}: ${v.filePath} (时长约 ${v.duration || 0}秒)`);
              if (v.thumbPath) mediaLines.push(`  └ 封面帧: ${v.thumbPath}`);
            });
          }
          if (downloadedFiles.length > 0) {
            mediaLines.push(`【用户发送了 ${downloadedFiles.length} 个文件/文档】：`);
            downloadedFiles.forEach((f, idx) => {
              if (f.filePath) mediaLines.push(`- 文档 ${idx + 1}: ${f.fileName} (本地路径: ${f.filePath}, 大小: ${(f.size / 1024).toFixed(1)}KB)`);
            });
          }
          const header = mediaLines.join('\n');
          if (rawText) {
            promptForAI = `${header}\n\n用户附带指令/提问：\n${rawText}`;
          } else {
            promptForAI = `${header}\n\n请详细识别并分析上述文件/媒体内容，向用户汇报核心要点。`;
          }
        }

        if (!promptForAI) continue;

        if (user.status !== 'active') {
          await sendWechatMessage(
            auth,
            fromUser,
            latestContextToken,
            '⚠️ 你的 Microsoft To Do 账户尚未绑定。为避免不同用户之间串用任务和会话，请先发送 /bind_todo 完成个人授权。'
          );
          continue;
        }

        let typingInterval = null;
        let typingTicket = null;
        try {
          const cfg = await apiPost(auth.baseUrl, 'ilink/bot/getconfig', {
            ilink_user_id: fromUser,
            context_token: latestContextToken,
            base_info: { channel_version: '2.4.8', bot_agent: 'AntigravityClawBot/1.0' },
          }, auth.botToken, 5000);

          if (cfg.typing_ticket) {
            typingTicket = cfg.typing_ticket;
            await sendTypingStatus(auth, fromUser, typingTicket);
            typingInterval = setInterval(() => {
              sendTypingStatus(auth, fromUser, typingTicket);
            }, 4000);
          }
        } catch (e) {}

        try {
          const timeHeader = `[当前北京时间: ${getNowGmt8Str()}]\n`;
          const finalPrompt = timeHeader + promptForAI;
          const convId = loadConversation(user.paths.conversationFile) || user.conversationId;
          log(`⚙️ 正在调度 AI (${AI_PROVIDER}) 推理执行: 用户=${fromUser}, 思考链模式=${user.thinkMode ? '开启' : '关闭'}, 会话ID=${convId || '(首轮)'}`);

          let onProgress = null;
          if (user.thinkMode) {
            onProgress = createProgressNotifier(async (msg) => {
              log(`💡 [阶段进度通知 -> ${fromUser}]: ${msg}`);
              try {
                await sendWechatMessage(auth, fromUser, latestContextToken, msg);
              } catch (notifyErr) {
                logError('NOTIFY_FAIL', `阶段通知发送失败: ${notifyErr.message}`);
              }
            });
          }

          const result = await executeAI(finalPrompt, convId, null, {
            cwd: user.paths.workspace,
            onProgress,
            env: {
              OMNI_USER_ID: fromUser,
              OMNI_USER_DATA_DIR: user.paths.root,
              AGY_CWD: user.paths.workspace,
              MS_TODO_USER_DATA_DIR: user.paths.auth,
              HOME: user.paths.agyHome,
              USERPROFILE: user.paths.agyHome,
              XDG_CONFIG_HOME: user.paths.agyHome,
            },
          });

          if (result.conversationId) {
            saveConversation(user.paths.conversationFile, result.conversationId);
            await userRegistry.update(fromUser, { conversationId: result.conversationId });
          }

          await stopTypingStatus(auth, fromUser, typingTicket, typingInterval);
          typingInterval = null;
          await sendWechatMessage(auth, fromUser, latestContextToken, result.response);
        } catch (execErr) {
          await stopTypingStatus(auth, fromUser, typingTicket, typingInterval);
          typingInterval = null;
          logError('AI_EXEC', `AI 推理或执行失败: ${execErr.message}`, execErr);
          await sendWechatMessage(auth, fromUser, latestContextToken, `⚠️ 执行出错: ${execErr.message}`);
        }
      }
    } catch (pollErr) {
      log(`长轮询异常: ${pollErr.message}，3秒后重试`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

if (require.main === module) {
  runDaemon().catch((err) => {
    log(`❌ 微信适配器致命异常: ${err.message}`);
    process.exit(1);
  });
}

module.exports = {
  runDaemon,
};
