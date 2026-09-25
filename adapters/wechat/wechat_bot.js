#!/usr/bin/env node
/**
 * WeChat Adapter for Omni-Assistant
 * Directly connects to Tencent's official iLink Bot protocol (ilinkai.weixin.qq.com)
 * Uses core/ai_provider.js for OpenAI-compatible AI reasoning
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

const isDebugMode = process.argv.includes('--debug') || process.env.DEBUG === '1' || process.env.DEBUG === 'true';

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
const AUTH_POOL_FILE = path.join(DATA_DIR, 'auth_pool.json');
const SYNC_FILE = path.join(DATA_DIR, 'sync_buf.txt');
const LEGACY_CONV_FILE = path.join(DATA_DIR, 'conversations.json');
const userRegistry = new UserRegistry(DATA_DIR, LEGACY_CONV_FILE);
const bindingProcesses = new Map();

function atomicWriteFile(filePath, content) {
  const tempPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tempPath, content, 'utf8');
  fs.renameSync(tempPath, filePath);
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
  atomicWriteFile(AUTH_FILE, JSON.stringify(data, null, 2));
}

function loadConversations() {
  if (fs.existsSync(LEGACY_CONV_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(LEGACY_CONV_FILE, 'utf8'));
    } catch {
      return {};
    }
  }
  return {};
}

function saveConversations(data) {
  atomicWriteFile(LEGACY_CONV_FILE, JSON.stringify(data, null, 2));
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
  atomicWriteFile(SYNC_FILE, buf || '');
}

class AuthPool {
  constructor(poolFile, legacyAuthFile) {
    this.poolFile = poolFile;
    this.legacyAuthFile = legacyAuthFile;
    this.data = this.load();
  }

  load() {
    let pool = { version: 1, accounts: {} };
    if (fs.existsSync(this.poolFile)) {
      try {
        const raw = JSON.parse(fs.readFileSync(this.poolFile, 'utf8'));
        if (raw && typeof raw.accounts === 'object') {
          pool = raw;
        }
      } catch (e) {
        logError('AUTH_POOL', `读取 auth_pool.json 失败: ${e.message}`);
      }
    }

    // Auto-migrate legacy auth.json if present
    if (fs.existsSync(this.legacyAuthFile)) {
      try {
        const legacy = JSON.parse(fs.readFileSync(this.legacyAuthFile, 'utf8'));
        if (legacy && legacy.botToken && legacy.userId) {
          if (!pool.accounts[legacy.userId]) {
            log(`📥 [AUTH_POOL] 自动从 auth.json 迁移已有账号: ${legacy.userId}`);
            pool.accounts[legacy.userId] = {
              userId: legacy.userId,
              botToken: legacy.botToken,
              botId: legacy.botId || legacy.userId,
              baseUrl: legacy.baseUrl || DEFAULT_BASE_URL,
              loginTime: legacy.loginTime || new Date().toISOString(),
              syncBuf: loadSyncBuf() || '',
              status: 'active',
            };
            this.save(pool);
          }
        }
      } catch (e) {}
    }
    return pool;
  }

  save(poolData = null) {
    if (poolData) this.data = poolData;
    try {
      fs.writeFileSync(this.poolFile, JSON.stringify(this.data, null, 2), 'utf8');
    } catch (e) {
      logError('AUTH_POOL', `写入 auth_pool.json 失败: ${e.message}`);
    }
  }

  getAllAccounts() {
    return Object.values(this.data.accounts || {});
  }

  getActiveAccounts() {
    return this.getAllAccounts().filter((a) => a.status === 'active');
  }

  getAccount(userId) {
    return this.data.accounts ? this.data.accounts[userId] : null;
  }

  addOrUpdateAccount(account) {
    if (!this.data.accounts) this.data.accounts = {};
    const existing = this.data.accounts[account.userId] || {};
    const merged = {
      ...existing,
      ...account,
      status: account.status || existing.status || 'active',
      updatedAt: new Date().toISOString(),
    };
    this.data.accounts[account.userId] = merged;
    this.save();
    try {
      fs.writeFileSync(this.legacyAuthFile, JSON.stringify(merged, null, 2), 'utf8');
    } catch {}
    return merged;
  }

  updateAccount(userId, fields) {
    if (!this.data.accounts || !this.data.accounts[userId]) return;
    Object.assign(this.data.accounts[userId], fields, { updatedAt: new Date().toISOString() });
    this.save();
  }

  removeAccount(userId) {
    if (this.data.accounts && this.data.accounts[userId]) {
      delete this.data.accounts[userId];
      this.save();
    }
  }
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
  const decodedStr = b.toString('utf8').trim();
  if (/^[0-9a-fA-F]{32}$/.test(decodedStr)) {
    return Buffer.from(decodedStr, 'hex');
  }
  if (Buffer.byteLength(str, 'utf8') === 16) {
    return Buffer.from(str, 'utf8');
  }
  if (b.length > 16) {
    return b.slice(0, 16);
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

  const rawKey = media.aes_key || imageItem.aeskey || imageItem.aes_key;
  const key = parseAesKey(rawKey);
  if (isDebugMode) {
    log(`🐞 [DEBUG/IMAGE] 解析密钥: rawKey=${rawKey ? String(rawKey).slice(0, 16) + '...' : 'none'}, resolvedKeyBytes=${key ? key.length : 'null'}`);
  }
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
      if (isDebugMode) {
        log(`🐞 [DEBUG/IMAGE] AES 解密成功: 原始密文=${encryptedBuf.length} 字节, 解密明文=${decryptedBuf.length} 字节`);
      }
    } catch (decErr) {
      logError('DECRYPT_FAIL', `图片 AES 解密失败: ${decErr.message}`);
    }
  }

  fs.mkdirSync(mediaDir, { recursive: true, mode: 0o700 });
  cleanOldMediaFiles(mediaDir);
  const ext = detectImageExtension(decryptedBuf);
  const filename = `wechat_img_${Date.now()}_${crypto.randomBytes(4).toString('hex')}${ext}`;
  const filePath = path.join(mediaDir, filename);
  fs.writeFileSync(filePath, decryptedBuf);
  if (isDebugMode) {
    log(`🐞 [DEBUG/IMAGE] 图片落盘完毕: 格式=${ext}, 完整路径=${filePath}`);
  }
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

  const rawKey = media.aes_key || videoItem.aeskey || videoItem.aes_key;
  const key = parseAesKey(rawKey);
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

  const rawKey = media.aes_key || fileItem.aeskey || fileItem.aes_key;
  const key = parseAesKey(rawKey);
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
    } catch (decErr) {
      logError('DECRYPT_FAIL', `文档 AES 解密失败: ${decErr.message}`);
    }
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
    base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' },
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
      base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' },
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

async function processMessagesForAccount(auth, msgs, registry) {
  const shortId = (auth.userId || '').slice(0, 10);
  log(`📥 [${shortId}] 轮询获取到新消息批次，共计 ${msgs.length} 条原始记录`);
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
    logRecv(`📨 [${shortId}] 收到微信原始消息: from=${incoming.from_user_id || 'none'} | type=${incoming.message_type} | msg_id=${incoming.msg_id || 'none'} seq=${incoming.sequence_id || 'none'}\n           内容: ${summaryList.join(' ') || '(空内容条目)'}`);
    if (isDebugMode) {
      logRecv(`🐞 [DEBUG/PAYLOAD] 原始消息报文:\n` + JSON.stringify(incoming, null, 2));
    }
  }

  const validMsgs = msgs.filter(m => m.from_user_id !== auth.botId && m.message_type !== 2);
  if (msgs.length > 0 && validMsgs.length === 0) {
    log(`⚠️ [${shortId}] 消息全部被过滤: botId=${auth.botId || 'none'}`);
    return;
  }

  const userGroups = new Map();
  for (const msg of validMsgs) {
    const uid = msg.from_user_id;
    if (!uid) continue;
    if (!userGroups.has(uid)) userGroups.set(uid, []);
    userGroups.get(uid).push(msg);
  }

  for (const [fromUser, userMsgs] of userGroups.entries()) {
    const user = registry.get(fromUser) || registry.ensure(fromUser);
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
        const isBound = user.status === 'active' && user.boundAt;
        const bindingLine = isBound
          ? '✅ Microsoft To Do 已完成绑定'
          : '⚠️ Microsoft To Do 尚未绑定：请发送 /bind_todo 开始授权';
        const helpMsg = `🤖 Omni-Assistant 微信智能助手\n\n- 直接发送对话、任务需求、图片或文档开始交互\n- /bind_todo : 绑定你自己的 Microsoft To Do 账户\n- /binding_status : 查看个人绑定状态\n- /think on / off : 开启或关闭思考链阶段进度通知 (默认关闭)\n- /status : 查看当前系统与 AI 驱动状态\n- /reset  : 清除你的 agy 会话记忆\n- /help   : 查看本帮助说明\n\n${bindingLine}`;
        await sendWechatMessage(auth, fromUser, latestContextToken, helpMsg);
        continue;
      }

      if (rawText === '/think on' || rawText === '#think on') {
        await registry.update(fromUser, { thinkMode: true });
        await sendWechatMessage(auth, fromUser, latestContextToken, '💡 思考链阶段通知已开启！执行任务时，机器人将在关键节点（查询待办、同步微软、更新记忆）发送实时进度。发送 /think off 可随时关闭。');
        continue;
      }

      if (rawText === '/think off' || rawText === '#think off') {
        await registry.update(fromUser, { thinkMode: false });
        await sendWechatMessage(auth, fromUser, latestContextToken, '🔕 思考链阶段通知已关闭，将仅在任务全部完成后发送最终回复。发送 /think on 可重新开启。');
        continue;
      }

      if (rawText === '/bind_todo' || rawText === '#bind_todo') {
        await startTodoBinding(auth, fromUser, latestContextToken);
        continue;
      }

      if (rawText === '/binding_status' || rawText === '#binding_status') {
        const isBound = user.status === 'active' && user.boundAt;
        const statusText = isBound
          ? '✅ 你的 Microsoft To Do 已绑定，任务和 AI 会话均使用独立用户空间。'
          : user.status === 'authorizing'
            ? '🔐 你的 Microsoft To Do 授权正在进行中，请使用之前收到的 Microsoft 登录网址和设备码完成授权。'
          : '⚠️ 你的 Microsoft To Do 尚未绑定，请发送 /bind_todo 开始授权。';
        await sendWechatMessage(auth, fromUser, latestContextToken, statusText);
        continue;
      }

      if (rawText === '/reset' || rawText === '/clear' || rawText === '#reset' || rawText === '#clear') {
        await registry.resetConversation(fromUser);
        await sendWechatMessage(auth, fromUser, latestContextToken, '🔄 会话记忆已重置，接下来将开始全新对话。');
        continue;
      }

      if (rawText === '/status' || rawText === '#status') {
        const activeModel = AI_PROVIDER === 'agy' ? AGY_MODEL : LLM_MODEL;
        const thinkStatus = user.thinkMode ? '已开启 (ON)' : '已关闭 (OFF)';
        const isBound = user.status === 'active' && user.boundAt;
        const statusMsg = `📊 你的智能体状态报告:\n- 微信账号: ${fromUser}\n- AI 引擎: ${AI_PROVIDER} (${activeModel})\n- Microsoft To Do: ${isBound ? '已绑定' : '未绑定'}\n- 思考链阶段通知: ${thinkStatus}\n- 宿主负载: ${os.loadavg()[0].toFixed(2)}\n- 运行时间: ${(os.uptime() / 3600).toFixed(1)} 小时\n- 当前会话: ${conversationId || '无 (首轮)'}`;
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
      const sandboxHeader = `[安全隔离与工作区规范]:\n- 交互用户: ${fromUser}\n- 用户专属沙箱根目录: ${user.paths.root}\n- 用户专属工作区: ${user.paths.workspace}\n- 权限铁律: 你的文件操作与探测权限严格限制在当前用户的专属目录内。严禁探测宿主机代码、系统文件或其他用户数据，坚决杜绝越权访问！\n`;
      const finalPrompt = timeHeader + sandboxHeader + '\n' + promptForAI;
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
        sandboxRoot: user.paths.root,
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
        await registry.update(fromUser, { conversationId: result.conversationId });
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
}

async function runPollerForAccount(pollerState, authPool, registry) {
  const account = pollerState.account;
  const userId = account.userId;
  const shortId = (userId || '').slice(0, 10);
  log(`📡 [POLLER:${shortId}] 启动独立轮询监听 (Bot: ${account.botId})...`);

  try {
    await apiPost(account.baseUrl, 'ilink/bot/msg/notifystart', {
      base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' }
    }, account.botToken, 5000);
    log(`📡 [POLLER:${shortId}] 已发送 notifystart 就绪心跳`);
  } catch (e) {
    log(`⚠️ [POLLER:${shortId}] notifystart 心跳异常: ${e.message}`);
  }

  let syncBuf = account.syncBuf || '';

  while (!pollerState.stopped) {
    try {
      const updates = await apiPost(account.baseUrl, 'ilink/bot/getupdates', {
        get_updates_buf: syncBuf,
        base_info: {
          channel_version: '2.4.8',
          bot_agent: 'OmniAssistant/1.0',
        },
      }, account.botToken, 40000);

      if (pollerState.stopped) break;

      if (updates.timeout) {
        continue;
      }

      const isApiError = (updates.ret !== undefined && updates.ret !== 0) ||
                         (updates.errcode !== undefined && updates.errcode !== 0);

      if (isApiError) {
        log(`⚠️ [POLLER:${shortId}] 轮询返回 ret=${updates.ret}, errcode=${updates.errcode}, errmsg=${updates.errmsg || 'none'}`);
        if (updates.ret === -14 || updates.errcode === -14) {
          log(`⚠️ [POLLER:${shortId}] 微信会话凭据已失效 (-14)，该账号需重新扫码！`);
          authPool.updateAccount(userId, { status: 'expired' });
          break;
        }
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }

      if (updates.get_updates_buf || updates.sync_buf) {
        syncBuf = updates.get_updates_buf || updates.sync_buf;
        account.syncBuf = syncBuf;
        authPool.updateAccount(userId, { syncBuf });
      }

      const msgs = updates.msgs || [];
      if (msgs.length > 0) {
        await processMessagesForAccount(account, msgs, registry);
      }
    } catch (pollErr) {
      if (pollerState.stopped) break;
      log(`⚠️ [POLLER:${shortId}] 长轮询网络波动: ${pollErr.message}，3秒后重试`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

class BotManager {
  constructor(authPool, registry) {
    this.authPool = authPool;
    this.userRegistry = registry;
    this.pollers = new Map();
  }

  async startAll() {
    const accounts = this.authPool.getActiveAccounts();
    log(`👥 [BotManager] 启动所有已绑定账号的并发轮询通道 (共计 ${accounts.length} 个账号)...`);
    for (const acc of accounts) {
      this.startPoller(acc);
    }
  }

  startPoller(account) {
    const userId = account.userId;
    if (this.pollers.has(userId)) {
      log(`🔄 [BotManager] 账号 ${userId} 已有轮询器在运行，先停止旧实例...`);
      this.stopPoller(userId);
    }

    const pollerState = {
      stopped: false,
      account: { ...account },
      stop: () => {
        pollerState.stopped = true;
      }
    };

    this.pollers.set(userId, pollerState);

    runPollerForAccount(pollerState, this.authPool, this.userRegistry).catch(err => {
      logError('POLLER_ERR', `用户 ${userId} 轮询器异常退出: ${err.message}`, err);
    });

    log(`✅ [BotManager] 账号 ${userId} 独立轮询已启动 (当前并发轮询总数: ${this.pollers.size})`);
  }

  stopPoller(userId) {
    const p = this.pollers.get(userId);
    if (p) {
      p.stop();
      this.pollers.delete(userId);
      log(`⏹️ [BotManager] 账号 ${userId} 轮询器已停止 (当前并发轮询总数: ${this.pollers.size})`);
    }
  }

  getActivePollerCount() {
    return this.pollers.size;
  }
}

class QRLoginDaemon {
  constructor(authPool, botManager, baseUrl = DEFAULT_BASE_URL) {
    this.authPool = authPool;
    this.botManager = botManager;
    this.baseUrl = baseUrl;
    this.currentQR = null;
    this.isDaemonRunning = false;
    this.manualRefreshRequested = false;
  }

  async start() {
    if (this.isDaemonRunning) return;
    this.isDaemonRunning = true;
    this.loop().catch(err => {
      logError('QR_DAEMON', `扫码登录常驻守护异常退出: ${err.message}`, err);
    });
  }

  requestRefresh() {
    this.manualRefreshRequested = true;
    if (this.currentCheckAbort) {
      try { this.currentCheckAbort.abort(); } catch {}
    }
  }

  async loop() {
    log(`📱 [QR_DAEMON] 启动常驻后台扫码注册守护器 (支持新用户热插拔零重启)...`);
    while (this.isDaemonRunning) {
      try {
        await this.runOneCycle();
      } catch (err) {
        logError('QR_DAEMON', `获取/监听二维码出错: ${err.message}，5秒后重试`);
        await new Promise(r => setTimeout(r, 5000));
      }
    }
  }

  async runOneCycle() {
    this.manualRefreshRequested = false;
    log(`🔄 [QR_DAEMON] 正在向腾讯申请新登录二维码...`);
    const qrRes = await apiGet(`${this.baseUrl}/ilink/bot/get_bot_qrcode?bot_type=3`, null);
    if (!qrRes.qrcode) {
      throw new Error('获取二维码响应异常: ' + JSON.stringify(qrRes));
    }

    const qrcode = qrRes.qrcode;
    const qrUrl = qrRes.qrcode_img_content || `https://ilinkai.weixin.qq.com/ilink/bot/qrcode/${qrcode}`;
    let qrDataUrl = '';
    try {
      const QRCode = require('qrcode');
      qrDataUrl = await QRCode.toDataURL(qrUrl, { margin: 2, scale: 8 });
    } catch (e) {
      log(`⚠️ 生成二维码图片异常: ${e.message}`);
    }

    this.currentQR = {
      qrcode,
      qrUrl,
      qrDataUrl,
      status: 'wait',
      statusText: '⏳ 等待微信扫码',
      createTime: new Date().toISOString(),
    };

    this.updateStaticQrFile();

    log(`🆕 [QR_DAEMON] 新登录二维码已生成！`);
    log(`   Web 管理端: http://localhost:3000`);
    log(`   本地 HTML: ${path.join(DATA_DIR, 'wechat-login-qr.html')}`);
    log(`   二维码链接: ${qrUrl}`);
    if (qrcodeTerminal) {
      qrcodeTerminal.generate(qrUrl, { small: true });
    }

    while (this.isDaemonRunning && !this.manualRefreshRequested) {
      let checkRes;
      try {
        this.currentCheckAbort = new AbortController();
        const timeoutTimer = setTimeout(() => {
          try { this.currentCheckAbort?.abort(); } catch {}
        }, 35000);
        const checkUrl = `${this.baseUrl}/ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`;
        const res = await fetch(checkUrl, {
          method: 'GET',
          headers: buildHeaders(null),
          signal: this.currentCheckAbort.signal,
        });
        clearTimeout(timeoutTimer);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        checkRes = await res.json();
      } catch (err) {
        if (this.manualRefreshRequested) break;
        if (err.name !== 'AbortError') {
          log(`⚠️ [QR_DAEMON] 检查扫码状态网络异常: ${err.message}`);
        }
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }

      if (this.manualRefreshRequested) break;

      const status = checkRes.status;
      if (status === 'wait') {
        this.currentQR.status = 'wait';
        this.currentQR.statusText = '⏳ 等待微信扫码';
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }

      if (status === 'scaned') {
        this.currentQR.status = 'scaned';
        this.currentQR.statusText = '📱 已扫描二维码，请在微信端点击【确认授权】...';
        this.updateStaticQrFile();
        log(`📱 [QR_DAEMON] 发现扫码，正在等待用户在微信端点击确认...`);
        await new Promise(r => setTimeout(r, 1000));
        continue;
      }

      if (status === 'confirmed') {
        this.currentQR.status = 'confirmed';
        this.currentQR.statusText = '🎉 授权成功！正在热挂载并启动服务...';
        this.updateStaticQrFile();

        const authData = {
          botToken: checkRes.bot_token,
          botId: checkRes.ilink_bot_id || checkRes.bot_id || String(checkRes.bot_token || '').split(':', 1)[0],
          userId: checkRes.ilink_user_id,
          baseUrl: checkRes.baseurl || this.baseUrl,
          loginTime: new Date().toISOString(),
          status: 'active',
          syncBuf: '',
        };

        log(`🎉 [QR_DAEMON] 微信授权成功！用户ID: ${authData.userId}, BotID: ${authData.botId}`);
        this.authPool.addOrUpdateAccount(authData);
        userRegistry.ensure(authData.userId);
        await userRegistry.update(authData.userId, { status: 'active' });
        this.botManager.startPoller(authData);

        try {
          const welcomeMsg = `🎉 欢迎使用 Omni-Assistant 微信智能助手！\n\n已成功绑定你的微信号。你可以：\n- 直接发送日常问题、任务指令或图片文档与 AI 交互\n- 发送 /bind_todo 绑定你的 Microsoft To Do 任务清单\n- 发送 /help 查看更多功能指令`;
          await sendWechatMessage(authData, authData.userId, null, welcomeMsg);
        } catch (msgErr) {
          log(`⚠️ 发送欢迎消息异常: ${msgErr.message}`);
        }

        log(`🚀 [热插拔] 新用户 ${authData.userId} 已无缝接入，主进程持续稳定运行，无需重启！`);
        await new Promise(r => setTimeout(r, 3000));
        break;
      }

      if (status === 'expired') {
        this.currentQR.status = 'expired';
        this.currentQR.statusText = '⚠️ 二维码已过期，正在自动换新...';
        this.updateStaticQrFile();
        log(`⌛ [QR_DAEMON] 当前二维码已过期，准备自动刷新下一张...`);
        await new Promise(r => setTimeout(r, 1500));
        break;
      }

      await new Promise(r => setTimeout(r, 1500));
    }
  }

  updateStaticQrFile() {
    const qrPage = path.join(DATA_DIR, 'wechat-login-qr.html');
    const qrDataUrl = this.currentQR?.qrDataUrl || '';
    const qrUrl = this.currentQR?.qrUrl || '';
    const statusText = this.currentQR?.statusText || '等待扫码';
    const escapedQrUrl = qrUrl.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="3">
  <title>微信登录二维码 - Omni-Assistant</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; margin: 0; padding: 2rem; }
    .card { max-width: 480px; margin: 0 auto; background: #1e293b; border-radius: 16px; padding: 2rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #38bdf8; }
    .status { display: inline-block; padding: 0.4rem 1rem; border-radius: 9999px; background: #334155; color: #38bdf8; font-weight: bold; margin: 1rem 0; font-size: 0.95rem; }
    .qr-box { background: #ffffff; padding: 16px; border-radius: 12px; display: inline-block; margin: 1rem 0; }
    .qr-box img { display: block; width: 260px; height: 260px; }
    .tip { color: #94a3b8; font-size: 0.85rem; line-height: 1.5; }
    .link { color: #38bdf8; word-break: break-all; font-size: 0.8rem; text-decoration: none; }
  </style>
</head>
<body>
  <div class="card">
    <h1>📱 微信新用户扫码接入</h1>
    <div class="status">${statusText}</div>
    <div class="qr-box">
      ${qrDataUrl ? `<img src="${qrDataUrl}" alt="微信登录二维码">` : `<p>正在生成二维码...</p>`}
    </div>
    <p class="tip">微信扫码授权后即可直接使用，无需重启服务进程。<br>支持多用户同时并发在线。</p>
    <p><a class="link" href="${escapedQrUrl}" target="_blank">直接在手机打开扫码链接</a></p>
  </div>
</body>
</html>`;
    try {
      fs.writeFileSync(qrPage, html, 'utf8');
    } catch {}
  }
}

function startWebServer(authPool, qrDaemon, preferredPort = 3000) {
  const http = require('node:http');

  const server = http.createServer((req, res) => {
    const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const pathname = parsedUrl.pathname;

    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    if (pathname === '/api/status') {
      const activeAccounts = authPool.getAllAccounts().map(a => ({
        userId: a.userId,
        botId: a.botId,
        status: a.status,
        loginTime: a.loginTime,
        updatedAt: a.updatedAt,
      }));
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({
        qr: qrDaemon.currentQR,
        accounts: activeAccounts,
        activeCount: activeAccounts.filter(a => a.status === 'active').length,
        timestamp: new Date().toISOString(),
      }));
      return;
    }

    if (pathname === '/api/refresh-qr' && req.method === 'POST') {
      qrDaemon.requestRefresh();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ success: true, message: '正在申请新二维码...' }));
      return;
    }

    if (pathname === '/' || pathname === '/index.html') {
      const activeAccounts = authPool.getAllAccounts();
      const qrDataUrl = qrDaemon.currentQR?.qrDataUrl || '';
      const statusText = qrDaemon.currentQR?.statusText || '⏳ 等待微信扫码';

      const pageHtml = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Omni-Assistant 微信机器人控制台 & 扫码接入</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --card-border: #1f2937;
      --primary: #38bdf8;
      --primary-hover: #0ea5e9;
      --accent: #10b981;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 2rem 1rem;
    }
    .container {
      width: 100%;
      max-width: 860px;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }
    header {
      text-align: center;
      margin-bottom: 0.5rem;
    }
    header h1 {
      font-size: 1.85rem;
      font-weight: 700;
      background: linear-gradient(135deg, #38bdf8, #818cf8, #c084fc);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 0.5rem;
    }
    header p {
      color: var(--text-muted);
      font-size: 0.95rem;
    }
    .badge-running {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      margin-top: 0.5rem;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 1.5rem;
    }
    @media (min-width: 768px) {
      .grid {
        grid-template-columns: 1fr 1fr;
      }
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 1.75rem;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }
    .card h2 {
      font-size: 1.25rem;
      margin-bottom: 1rem;
      color: var(--primary);
    }
    .qr-wrapper {
      background: #ffffff;
      padding: 14px;
      border-radius: 14px;
      box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
      margin: 1rem 0;
      position: relative;
    }
    .qr-wrapper img {
      width: 240px;
      height: 240px;
      display: block;
    }
    .status-pill {
      display: inline-block;
      padding: 0.35rem 1rem;
      border-radius: 9999px;
      background: #1f2937;
      color: #38bdf8;
      font-size: 0.9rem;
      font-weight: 600;
      margin: 0.5rem 0;
      border: 1px solid #374151;
      transition: all 0.3s ease;
    }
    .status-scaned {
      background: rgba(245, 158, 11, 0.2);
      color: #fbbf24;
      border-color: rgba(245, 158, 11, 0.4);
    }
    .status-confirmed {
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      border-color: rgba(16, 185, 129, 0.4);
    }
    .btn {
      background: #2563eb;
      color: white;
      border: none;
      border-radius: 8px;
      padding: 0.6rem 1.2rem;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
      margin-top: 1rem;
    }
    .btn:hover { background: #1d4ed8; }
    .account-list {
      width: 100%;
      text-align: left;
      margin-top: 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      max-height: 380px;
      overflow-y: auto;
    }
    .account-item {
      background: #1f2937;
      border: 1px solid #374151;
      border-radius: 10px;
      padding: 0.75rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .account-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .account-id {
      font-family: monospace;
      font-size: 0.85rem;
      color: #67e8f9;
      font-weight: bold;
    }
    .account-tag {
      font-size: 0.75rem;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
    }
    .account-tag.expired {
      background: rgba(239, 68, 68, 0.2);
      color: #f87171;
    }
    .account-meta {
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .instructions {
      text-align: left;
      font-size: 0.85rem;
      line-height: 1.6;
      color: #cbd5e1;
    }
    .instructions li {
      margin-bottom: 0.5rem;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #10b981;
      display: inline-block;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🤖 Omni-Assistant 多账号微信服务网关</h1>
      <p>生产环境连续运行 · 新用户扫码秒级热挂载 · 零停机零重启</p>
      <div class="badge-running"><span class="dot"></span> 主服务常驻运行中 · 端口 ${preferredPort}</div>
    </header>

    <div class="grid">
      <div class="card">
        <h2>📱 扫码绑定新微信账号</h2>
        <div id="statusPill" class="status-pill">${statusText}</div>
        <div class="qr-wrapper">
          <img id="qrImg" src="${qrDataUrl || ''}" alt="微信登录二维码">
        </div>
        <p style="font-size: 0.85rem; color: #94a3b8; max-width: 300px;">
          使用微信直接扫码授权，系统将自动挂载后台独立轮询器，即可立即开始对话。
        </p>
        <button id="refreshBtn" class="btn" onclick="refreshQr()">🔄 手动刷新二维码</button>
      </div>

      <div class="card" style="align-items: stretch;">
        <h2>👥 当前已接入微信账号 (<span id="accCount">${activeAccounts.length}</span>)</h2>
        <div id="accountList" class="account-list">
          ${activeAccounts.map(a => `
            <div class="account-item">
              <div class="account-header">
                <span class="account-id">${a.userId}</span>
                <span class="account-tag ${a.status === 'active' ? '' : 'expired'}">${a.status === 'active' ? '🟢 运行中' : '🔴 已过期'}</span>
              </div>
              <div class="account-meta">绑定时间: ${new Date(a.loginTime).toLocaleString('zh-CN')} | Bot: ${a.botId || 'none'}</div>
            </div>
          `).join('') || '<p style="color: #64748b; text-align: center; margin-top: 2rem;">暂无已绑定账号，请使用左侧二维码扫码接入</p>'}
        </div>

        <div style="margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #1f2937;">
          <h3 style="font-size: 0.95rem; color: #94a3b8; margin-bottom: 0.5rem; text-align: left;">💡 使用须知</h3>
          <ul class="instructions">
            <li>每个微信扫码后拥有独立的 AI 会话记忆、独立沙箱与待办任务。</li>
            <li>新用户扫码完成后，无需重启服务进程，数秒内即可直接回复。</li>
            <li>若多位用户同时扫码，依次扫描当前界面的二维码即可无缝并发接入。</li>
          </ul>
        </div>
      </div>
    </div>
  </div>

  <script>
    async function checkStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        if (data.qr) {
          const pill = document.getElementById('statusPill');
          pill.innerText = data.qr.statusText || '等待扫码';
          if (data.qr.status === 'scaned') {
            pill.className = 'status-pill status-scaned';
          } else if (data.qr.status === 'confirmed') {
            pill.className = 'status-pill status-confirmed';
          } else {
            pill.className = 'status-pill';
          }
          if (data.qr.qrDataUrl) {
            document.getElementById('qrImg').src = data.qr.qrDataUrl;
          }
        }
        if (data.accounts) {
          document.getElementById('accCount').innerText = data.activeCount;
          const container = document.getElementById('accountList');
          if (data.accounts.length === 0) {
            container.innerHTML = '<p style="color: #64748b; text-align: center; margin-top: 2rem;">暂无已绑定账号，请使用左侧二维码扫码接入</p>';
          } else {
            container.innerHTML = data.accounts.map(a => \`
              <div class="account-item">
                <div class="account-header">
                  <span class="account-id">\${a.userId}</span>
                  <span class="account-tag \${a.status === 'active' ? '' : 'expired'}">\${a.status === 'active' ? '🟢 运行中' : '🔴 已过期'}</span>
                </div>
                <div class="account-meta">绑定时间: \${new Date(a.loginTime).toLocaleString('zh-CN')} | Bot: \${a.botId || 'none'}</div>
              </div>
            \`).join('');
          }
        }
      } catch (e) {}
    }
    setInterval(checkStatus, 2000);

    async function refreshQr() {
      const btn = document.getElementById('refreshBtn');
      btn.disabled = true;
      btn.innerText = '正在刷新...';
      try {
        await fetch('/api/refresh-qr', { method: 'POST' });
        await checkStatus();
      } catch (e) {}
      setTimeout(() => {
        btn.disabled = false;
        btn.innerText = '🔄 手动刷新二维码';
      }, 2000);
    }
  </script>
</body>
</html>`;
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(pageHtml);
      return;
    }

    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
  });

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      log(`⚠️ 端口 ${preferredPort} 被占用，尝试端口 ${preferredPort + 1}...`);
      startWebServer(authPool, qrDaemon, preferredPort + 1);
    } else {
      logError('WEB_SERVER', `Web 服务启动失败: ${err.message}`);
    }
  });

  server.listen(preferredPort, () => {
    log(`🌐 [WEB_PORTAL] 微信管理与扫码门户已启动: http://localhost:${preferredPort}`);
  });
  return server;
}

async function runDaemon() {
  log('====================================================');
  log('  Omni-Assistant: 多租户高可用微信智能体网关');
  log('====================================================');
  log(`📡 腾讯 iLink 官方网关: ${DEFAULT_BASE_URL}`);
  log(`🧠 AI 驱动模式: ${AI_PROVIDER} (${AI_PROVIDER === 'agy' ? AGY_MODEL : LLM_MODEL})`);
  log(`📂 工作区数据目录: ${DATA_DIR}`);
  if (isDebugMode) {
    log(`🐞 [DEBUG] 调试监控模式已启动：实时输出详细网络交互、报文体、媒体解密及 AI 推理指标`);
  }

  const authPool = new AuthPool(AUTH_POOL_FILE, AUTH_FILE);
  const botManager = new BotManager(authPool, userRegistry);

  // 1. Launch pollers for all existing active accounts
  await botManager.startAll();

  // 2. Launch background QR daemon (always ready to accept new users zero-downtime)
  const qrDaemon = new QRLoginDaemon(authPool, botManager, DEFAULT_BASE_URL);
  await qrDaemon.start();

  // 3. Start Web Server
  const webPort = parseInt(process.env.PORT || '3000', 10);
  startWebServer(authPool, qrDaemon, webPort);

  log(`🤖 微信多账号网关已就绪！`);
  log(`👥 当前在线账号数: ${botManager.getActivePollerCount()}`);
  log(`🌐 Web 扫码与状态面板: http://localhost:${webPort}`);
  log(`💬 正在持续监听所有微信账号消息，支持随时扫码热插拔接入新用户...`);

  return new Promise(() => {});
}

if (require.main === module) {
  runDaemon().catch((err) => {
    log(`❌ 微信适配器致命异常: ${err.message}`);
    process.exit(1);
  });
}

module.exports = {
  runDaemon,
  AuthPool,
  BotManager,
  QRLoginDaemon,
};
