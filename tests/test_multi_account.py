#!/usr/bin/env python3
"""
Test WeChat Multi-Account Isolation and AuthPool Management
Ensures that multiple accounts can coexist, maintain independent syncBuf, and hot-register without conflict.
"""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_auth_pool_multi_account_isolation(tmp_path):
    script = r"""
const fs = require('node:fs');
const path = require('node:path');
const { AuthPool } = require('./adapters/wechat/wechat_bot.js');

const root = process.argv[1];
const poolFile = path.join(root, 'auth_pool.json');
const authFile = path.join(root, 'auth.json');

// 1. Initial pool with legacy auth migration
fs.writeFileSync(authFile, JSON.stringify({
  botToken: 'bot1:token1',
  botId: 'bot1',
  userId: 'user1@im.wechat',
  baseUrl: 'https://ilinkai.weixin.qq.com',
  loginTime: '2026-09-25T12:00:00.000Z'
}), 'utf8');

const pool = new AuthPool(poolFile, authFile);
const acc1 = pool.getAccount('user1@im.wechat');
if (!acc1 || acc1.botToken !== 'bot1:token1') process.exit(10);

// 2. Add second account (dynamic hot-join simulation)
pool.addOrUpdateAccount({
  botToken: 'bot2:token2',
  botId: 'bot2',
  userId: 'user2@im.wechat',
  baseUrl: 'https://ilinkai.weixin.qq.com',
  loginTime: '2026-09-25T12:05:00.000Z',
  syncBuf: 'buf2_initial',
  status: 'active'
});

// 3. Add third account
pool.addOrUpdateAccount({
  botToken: 'bot3:token3',
  botId: 'bot3',
  userId: 'user3@im.wechat',
  baseUrl: 'https://ilinkai.weixin.qq.com',
  loginTime: '2026-09-25T12:10:00.000Z',
  syncBuf: '',
  status: 'active'
});

const allActive = pool.getActiveAccounts();
if (allActive.length !== 3) process.exit(11);

// 4. Update syncBuf independently
pool.updateAccount('user1@im.wechat', { syncBuf: 'buf1_updated' });
pool.updateAccount('user2@im.wechat', { syncBuf: 'buf2_updated' });

if (pool.getAccount('user1@im.wechat').syncBuf !== 'buf1_updated') process.exit(12);
if (pool.getAccount('user2@im.wechat').syncBuf !== 'buf2_updated') process.exit(13);
if (pool.getAccount('user3@im.wechat').syncBuf !== '') process.exit(14);

// 5. Expiration handling
pool.updateAccount('user3@im.wechat', { status: 'expired' });
if (pool.getActiveAccounts().length !== 2) process.exit(15);
if (pool.getAccount('user3@im.wechat').status !== 'expired') process.exit(16);

// 6. Persistence check: reload pool from disk
const reloadedPool = new AuthPool(poolFile, authFile);
if (reloadedPool.getActiveAccounts().length !== 2) process.exit(17);
if (reloadedPool.getAccount('user1@im.wechat').syncBuf !== 'buf1_updated') process.exit(18);

process.exit(0);
"""
    subprocess.run(
        ["node", "-e", script, str(tmp_path)],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
