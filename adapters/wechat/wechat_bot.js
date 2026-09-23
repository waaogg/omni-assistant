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

function log(msg) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  let safe = String(msg).replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]');
  for (const secret of [process.env.LLM_API_KEY, process.env.NAPCAT_TOKEN]) {
    if (secret) safe = safe.split(secret).join('[REDACTED]');
  }
  console.log(`[${ts}] [WeChat] ${safe}`);
}

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

const { executeAI, AI_PROVIDER, LLM_MODEL } = require('../../core/ai_provider.js');

const DEFAULT_BASE_URL = (process.env.WECHAT_BASE_URL || 'https://ilinkai.weixin.qq.com').replace(/\/+$/, '');
const CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c';

const DATA_DIR = process.env.WECHAT_DATA_DIR
  ? path.resolve(process.env.WECHAT_DATA_DIR)
  : path.join(__dirname, '../../data/wechat');
const AUTH_FILE = path.join(DATA_DIR, 'auth.json');
const CONV_FILE = path.join(DATA_DIR, 'conversations.json');
const SYNC_FILE = path.join(DATA_DIR, 'sync_buf.txt');
const MEDIA_DIR = path.join(DATA_DIR, 'media');

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}
if (!fs.existsSync(MEDIA_DIR)) {
  fs.mkdirSync(MEDIA_DIR, { recursive: true });
}

function atomicWriteFile(filePath, content) {
  const tempPath = `${filePath}.${process.pid}.tmp`;
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
  if (fs.existsSync(CONV_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(CONV_FILE, 'utf8'));
    } catch {
      return {};
    }
  }
  return {};
}

function saveConversations(data) {
  atomicWriteFile(CONV_FILE, JSON.stringify(data, null, 2));
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

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: buildHeaders(token),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    clearTimeout(timer);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${text}`);
    }
    return JSON.parse(text);
  } catch (err) {
    clearTimeout(timer);
    if (err.name === 'AbortError') {
      return { ret: 0, timeout: true };
    }
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
  const qrUrl = `https://ilinkai.weixin.qq.com/ilink/bot/qrcode/${qrcode}`;
  log(`\n请使用微信扫描下方二维码以绑定助理机器人：`);
  if (qrcodeTerminal) {
    qrcodeTerminal.generate(qrUrl, { small: true });
  } else {
    throw new Error('缺少 qrcode-terminal，无法安全显示登录二维码');
  }

  log(`\n长轮询等待微信确认授权中...`);

  while (true) {
    const checkRes = await apiGet(
      `${baseUrl}/ilink/bot/check_bot_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`,
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
        botId: checkRes.bot_id,
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

function cleanOldMediaFiles() {
  try {
    const files = fs.readdirSync(MEDIA_DIR);
    const now = Date.now();
    const SEVEN_DAYS = 7 * 24 * 3600 * 1000;
    for (const file of files) {
      const p = path.join(MEDIA_DIR, file);
      const stat = fs.statSync(p);
      if (now - stat.mtimeMs > SEVEN_DAYS) {
        fs.unlinkSync(p);
      }
    }
  } catch {}
}

async function downloadAndSaveWechatImage(imageItem) {
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

  cleanOldMediaFiles();
  const ext = detectImageExtension(decryptedBuf);
  const filename = `wechat_img_${Date.now()}_${crypto.randomBytes(4).toString('hex')}${ext}`;
  const filePath = path.join(MEDIA_DIR, filename);
  fs.writeFileSync(filePath, decryptedBuf);
  return filePath;
}

async function downloadAndSaveWechatVideo(videoItem) {
  if (!videoItem) return null;
  const media = videoItem.media || {};
  let url = media.full_url;
  if (!url && media.encrypt_query_param) {
    url = `${CDN_BASE_URL}/download?encrypted_query_param=${encodeURIComponent(media.encrypt_query_param)}`;
  }

  const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
  if (videoItem.video_size && videoItem.video_size > MAX_VIDEO_BYTES) {
    if (videoItem.thumb_media) {
      const thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media });
      return { thumbPath, duration: videoItem.play_length, fallback: true, size: videoItem.video_size };
    }
    return null;
  }

  if (!url) {
    if (videoItem.thumb_media) {
      const thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media });
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

  cleanOldMediaFiles();
  const filename = `wechat_video_${Date.now()}_${crypto.randomBytes(4).toString('hex')}.mp4`;
  const filePath = path.join(MEDIA_DIR, filename);
  fs.writeFileSync(filePath, decryptedBuf);

  let thumbPath = null;
  if (videoItem.thumb_media) {
    try {
      thumbPath = await downloadAndSaveWechatImage({ media: videoItem.thumb_media });
    } catch {}
  }

  return {
    filePath,
    thumbPath,
    duration: videoItem.play_length,
    size: decryptedBuf.length
  };
}

async function downloadAndSaveWechatFile(fileItem) {
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

  cleanOldMediaFiles();
  const rawName = fileItem.file_name || 'document';
  const ext = path.extname(rawName) || '.bin';
  const safeBase = path.basename(rawName, ext).replace(/[^\w\u4e00-\u9fa5_-]/g, '_');
  const filename = `wechat_file_${Date.now()}_${safeBase}${ext}`;
  const filePath = path.join(MEDIA_DIR, filename);
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
  const body = {
    msg: {
      from_user_id: auth.botId,
      to_user_id: toUserId,
      client_id: clientId,
      message_type: 1,
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

  return await apiPost(auth.baseUrl, 'ilink/bot/sendmessage', body, auth.botToken, 15000);
}

async function sendTypingStatus(auth, toUserId, typingTicket) {
  if (!typingTicket) return;
  try {
    await apiPost(auth.baseUrl, 'ilink/bot/sendtyping', {
      ilink_user_id: toUserId,
      typing_ticket: typingTicket,
      base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' },
    }, auth.botToken, 5000);
  } catch {}
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

function executeUnifiedTextAgent(userId, text) {
  return new Promise((resolve, reject) => {
    const python = process.env.PYTHON_BIN_PATH || (process.platform === 'win32' ? 'python.exe' : 'python3');
    const script = path.resolve(__dirname, '../../scripts/channel_agent.py');
    const child = spawn(python, [script, '--channel', 'wechat'], {
      cwd: path.resolve(__dirname, '../..'),
      env: { ...process.env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error('统一智能体执行超时')); }, 300000);
    child.stdout.on('data', data => { stdout += data.toString(); });
    child.stderr.on('data', data => { stderr += data.toString(); });
    child.on('error', err => { clearTimeout(timer); reject(err); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0) return reject(new Error(`统一智能体失败 (${code}): ${stderr.slice(0, 300)}`));
      try {
        const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
        const parsed = JSON.parse(lines[lines.length - 1]);
        resolve({ response: String(parsed.response || ''), conversationId: null });
      } catch {
        reject(new Error('统一智能体返回了无效 JSON'));
      }
    });
    child.stdin.end(JSON.stringify({ user: String(userId), text }));
  });
}

async function runDaemon() {
  let auth = loadAuth();
  if (!auth || !auth.botToken) {
    log('⚠️ 未检测到有效微信登录态，开始执行扫码登录流程...');
    auth = await loginFlow();
  }

  log(`🤖 微信通道适配器已就绪！`);
  log('📡 腾讯 iLink 官方网关已配置');
  log(`🧠 AI 驱动模式: ${AI_PROVIDER} (${LLM_MODEL})`);
  log('📂 工作区数据目录已配置');
  log(`💬 开始监听微信私聊消息...`);

  try {
    await apiPost(auth.baseUrl, 'ilink/bot/msg/notifystart', {
      base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' }
    }, auth.botToken, 5000);
    log('📡 已向腾讯网关发送 notifystart 就绪心跳');
  } catch (e) {
    log('⚠️ 发送 notifystart 心跳异常: ' + e.message);
  }

  const conversations = loadConversations();
  let syncBuf = loadSyncBuf();
  const configuredAllowedUsers = new Set(
    (process.env.ALLOWED_WECHAT_USER_IDS || '').split(',').map(v => v.trim()).filter(Boolean)
  );
  if (configuredAllowedUsers.size === 0 && auth.userId) configuredAllowedUsers.add(String(auth.userId));

  while (true) {
    try {
      const updates = await apiPost(auth.baseUrl, 'ilink/bot/getupdates', {
        get_updates_buf: syncBuf,
        base_info: {
          channel_version: '2.4.8',
          bot_agent: 'OmniAssistant/1.0',
        },
      }, auth.botToken, 40000);

      if (updates.timeout) {
        continue;
      }

      const isApiError = (updates.ret !== undefined && updates.ret !== 0) ||
                         (updates.errcode !== undefined && updates.errcode !== 0);

      if (isApiError) {
        log(`⚠️ 轮询返回 ret=${updates.ret}, errcode=${updates.errcode}`);
        if (updates.ret === -14 || updates.errcode === -14) {
          log('⚠️ 微信会话凭据已失效，准备重新登录...');
          auth = await loginFlow();
          continue;
        }
        await new Promise((r) => setTimeout(r, 2000));
        continue;
      }

      if (updates.get_updates_buf) {
        syncBuf = updates.get_updates_buf;
        saveSyncBuf(syncBuf);
      }

      const msgs = updates.msgs || [];
      const validMsgs = msgs.filter(m => {
        if (m.from_user_id === auth.botId || m.message_type === 2) return false;
        return configuredAllowedUsers.size === 0 || configuredAllowedUsers.has(String(m.from_user_id));
      });

      const userGroups = new Map();
      for (const msg of validMsgs) {
        const uid = msg.from_user_id;
        if (!uid) continue;
        if (!userGroups.has(uid)) userGroups.set(uid, []);
        userGroups.get(uid).push(msg);
      }

      for (const [fromUser, userMsgs] of userGroups.entries()) {
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
            const helpMsg = `🤖 Omni-Assistant 微信智能助手\n\n- 直接发送对话、任务需求、图片或文档开始交互\n- /status : 查看当前系统与 AI 驱动状态\n- /reset  : 清除会话记忆，开启新对话\n- /help   : 查看本帮助说明`;
            await sendWechatMessage(auth, fromUser, latestContextToken, helpMsg);
            continue;
          }

          if (rawText === '/reset' || rawText === '/clear' || rawText === '#reset' || rawText === '#clear') {
            delete conversations[fromUser];
            saveConversations(conversations);
            await sendWechatMessage(auth, fromUser, latestContextToken, '🔄 会话记忆已重置，接下来将开始全新对话。');
            continue;
          }

          if (rawText === '/status' || rawText === '#status') {
            const statusMsg = `📊 智能体状态报告:\n- AI 引擎: ${AI_PROVIDER} (${LLM_MODEL})\n- 宿主负载: ${os.loadavg()[0].toFixed(2)}\n- 运行时间: ${(os.uptime() / 3600).toFixed(1)} 小时\n- 当前会话: ${conversations[fromUser] ? conversations[fromUser] : '无 (首轮)'}`;
            await sendWechatMessage(auth, fromUser, latestContextToken, statusMsg);
            continue;
          }
        }

        const downloadedImages = [];
        if (imageItems.length > 0) {
          for (const item of imageItems) {
            try {
              const p = await downloadAndSaveWechatImage(item);
              if (p) downloadedImages.push(p);
            } catch {}
          }
        }

        const downloadedVideos = [];
        if (videoItems.length > 0) {
          for (const item of videoItems) {
            try {
              const v = await downloadAndSaveWechatVideo(item);
              if (v) downloadedVideos.push(v);
            } catch {}
          }
        }

        const downloadedFiles = [];
        if (fileItems.length > 0) {
          for (const item of fileItems) {
            try {
              const f = await downloadAndSaveWechatFile(item);
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
        try {
          const cfg = await apiPost(auth.baseUrl, 'ilink/bot/getconfig', {
            ilink_user_id: fromUser,
            context_token: latestContextToken,
            base_info: { channel_version: '2.4.8', bot_agent: 'OmniAssistant/1.0' },
          }, auth.botToken, 5000);

          if (cfg.typing_ticket) {
            await sendTypingStatus(auth, fromUser, cfg.typing_ticket);
            typingInterval = setInterval(() => {
              sendTypingStatus(auth, fromUser, cfg.typing_ticket);
            }, 4000);
          }
        } catch (e) {}

        try {
          const timeHeader = `[当前北京时间: ${getNowGmt8Str()}]\n`;
          const finalPrompt = timeHeader + promptForAI;
          log(`⚙️ 正在调用 AI (${AI_PROVIDER}) 推理执行...`);
          const convId = conversations[fromUser];
          const attachments = [
            ...downloadedImages,
            ...downloadedVideos.flatMap(v => [v.filePath, v.thumbPath].filter(Boolean)),
            ...downloadedFiles.map(f => f.filePath).filter(Boolean),
          ];
          const result = attachments.length === 0
            ? await executeUnifiedTextAgent(fromUser, finalPrompt)
            : await executeAI(finalPrompt, convId, null, attachments);

          if (result.conversationId) {
            conversations[fromUser] = result.conversationId;
            saveConversations(conversations);
          }

          if (typingInterval) clearInterval(typingInterval);

          log(`正在生成并发送回复...`);
          await sendWechatMessage(auth, fromUser, latestContextToken, result.response);
          log(`✅ 回复发送成功！`);
        } catch (execErr) {
          if (typingInterval) clearInterval(typingInterval);
          log(`❌ AI 执行失败 (${execErr.name || 'Error'})`);
          await sendWechatMessage(auth, fromUser, latestContextToken, '⚠️ 执行出错，请稍后重试。');
        }
      }
    } catch (pollErr) {
      log(`长轮询异常 (${pollErr.name || 'Error'})，3秒后重试`);
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

if (require.main === module) {
  runDaemon().catch((err) => {
    log(`❌ 微信适配器致命异常 (${err.name || 'Error'})`);
    process.exit(1);
  });
}

module.exports = {
  runDaemon,
};
