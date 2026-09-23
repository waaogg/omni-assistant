# Omni-Assistant

一个自托管的 QQ / 微信消息助手。它把通知先放进本地收件箱，再按规则提取任务、处理时间、同步到 Microsoft To Do（可选），并保留每一步的来源和修改记录。

它不试图替人做决定：日期、对象或任务相关性不明确时，项目会把内容放进确认箱，等你确认后再创建任务。

## 适合什么场景

- 在 QQ 群里静默收集通知，私聊提醒管理员
- 用微信私聊输入任务、查询任务，或发送包含图片、文本附件的消息
- 把确定的任务同步到 Microsoft To Do
- 在本机查看任务、确认箱、未来两周日程和服务状态
- 把运行数据留在自己的机器上，而不是交给项目仓库

如果你只需要一个聊天机器人，这个项目偏重了；它的重点是消息落库、去重、确认、任务变更记录和可恢复性。

## 它如何工作

```text
QQ / 微信 / Dashboard
          │
          ▼
    消息入库、去重、重试
          │
          ▼
   结构化判断与时间解析
       │              │
       ▼              ▼
   确定的任务       确认箱
       │
       ▼
SQLite 任务历史 ──── Microsoft To Do（可选）
```

SQLite 是本地状态的唯一来源。渠道标识会先哈希；消息、任务、偏好和文档内容只存在运行目录中的数据库，不应提交到 Git。

## 先跑起来

需要 Python 3.10+、Node.js 18+。只有启用微信或使用 Node 侧功能时才需要安装 npm 依赖。

```bash
git clone https://github.com/your-account/omni-assistant.git
cd omni-assistant
python -m pip install -r requirements.txt
npm install

# 开发或运行测试时
python -m pip install -r requirements-dev.txt

# 从模板创建自己的配置
cp .env.example .env
```

最小配置示例：先只打开一个通道。

```dotenv
ENABLE_QQ=false
ENABLE_WECHAT=true

AI_PROVIDER=openai
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=填入你自己的密钥
LLM_MODEL=deepseek-chat
```

先检查配置，再启动：

```bash
python main.py --check-only
python main.py
```

`--check-only` 不连接 QQ、微信或 Microsoft To Do；它只检查配置格式和本机依赖。完整配置项见 [`.env.example`](.env.example)。

## 接入渠道

### QQ

QQ 使用 NapCat 的 OneBot v11 HTTP 与 WebSocket 接口。启用后，适配器只监听 `TARGET_GROUP_IDS` 中的群，不在这些群里发言；识别到的内容会通过私聊发给 `ADMIN_QQ`。

```dotenv
ENABLE_QQ=true
ADMIN_QQ=你的 QQ 号
TARGET_GROUP_IDS=群号1,群号2
NAPCAT_HTTP_URL=http://127.0.0.1:3000
NAPCAT_WS_URL=ws://127.0.0.1:3001
NAPCAT_TOKEN=
```

### 微信

微信适配器使用腾讯 iLink 网关。首次启动需要在终端完成扫码登录；登录状态保存在 `WECHAT_DATA_DIR`，默认是 `data/wechat`，不要把这个目录提交或打包分享。

```dotenv
ENABLE_WECHAT=true
WECHAT_BASE_URL=https://ilinkai.weixin.qq.com
WECHAT_DATA_DIR=./data/wechat
```

可以用 `ALLOWED_WECHAT_USER_IDS` 限制允许发送消息的微信用户；不设置时，适配器只接受网关绑定的用户。

## 任务与确认机制

- 同一渠道消息会按外部消息 ID 去重；处理失败会按配置重试。
- 一条通知中有多个独立事项时，会分别生成候选任务。
- `AUTO_APPLY_CONFIDENCE` 以上且对象、时间明确的任务可以自动落库；其他任务进入确认箱。
- 删除任务要求明确确认；任务更新、完成、延期会写入历史，可撤销。
- 到期时间、提醒、周期日程和日程冲突由本地规则处理。无法确定年份或日期时，不会凭空补全。

Microsoft To Do 是可选的远端镜像，不是本地数据库的替代品。远端写入失败时，系统不会假装任务已经创建成功。

```dotenv
ENABLE_MS_TODO=true
MS_TODO_AUTH_MODULE_PATH=/path/to/auth.js
MS_TODO_DEFAULT_LIST_ID=
DEFAULT_REMINDER_ADVANCE_MINUTES=15
```

## Dashboard

本地面板默认只监听 `127.0.0.1:8765`：

```bash
python dashboard.py
```

它能查看服务状态、任务、确认箱、近期日程和不含内容的 AI 调用指标，也能编辑受支持的配置项。若要通过反向代理暴露面板，先设置 `DASHBOARD_TOKEN`，再由代理提供 TLS 和访问控制；不要直接把本地面板暴露到公网。

## 数据、迁移与备份

运行后，主要数据在 `data/omni.db`。该文件包含私有内容，应当和代码分开保存。

从旧 JSON 状态迁移时，先预览数量：

```bash
python scripts/migrate_legacy.py --database data/omni.db \
  --tasks /private/path/synced_todos.json \
  --history /private/path/group_history.json --dry-run
```

确认后去掉 `--dry-run`。需要备份时，使用带认证加密的备份脚本：

```bash
python scripts/secure_backup.py create data/omni.db backups/state.omnibak
python scripts/secure_backup.py restore backups/state.omnibak data/restored.db
```

备份密码来自 `OMNI_BACKUP_PASSWORD` 或交互输入，至少 12 个字符。恢复到新文件后，先执行 SQLite `PRAGMA integrity_check`，不要直接覆盖正在运行的数据库。

## 日常运维

```bash
# 本地端到端检查（使用模拟远端，不会写入你的账号）
python scripts/verify_e2e.py

# 测试与语法检查
python -m pytest -q
python -m compileall -q core adapters dashboard scripts main.py
node --check core/ai_provider.js
node --check adapters/wechat/wechat_bot.js
```

部署样例在 `deploy/`：Docker Compose、systemd 和 PM2。它们是起点，不是可直接照抄的生产答案；部署前需要检查端口、运行账户、数据卷、备份位置和各渠道凭据。

更多细节：

- [架构说明](docs/ARCHITECTURE.md)
- [运行与排障](docs/OPERATIONS.md)
- [安全与隐私边界](docs/SECURITY.md)
- [迁移说明](docs/MIGRATION.md)

## 隐私边界

仓库只放代码和中性示例，不应包含你的账号、群号、聊天内容、偏好、日程、二维码、会话文件、媒体、令牌或备份。`.env`、`data/omni.db` 和 `data/wechat/` 都应只留在你控制的环境中。

## 许可证

[MIT](LICENSE)
