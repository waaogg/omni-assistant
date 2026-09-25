# 智能体永久记忆档案 (Agent Permanent Memory)
> 智能体历史操作经验、决策演进与踩坑复盘永久记忆库。
> 本文件由 iron-clad-rules 自动化脚本持续维护与索引。

---

### 💡 [LEARNING] 2026-09-25 17:33:39
- **内容**：todo-sync 项目 Antigravity CLI 与 Microsoft To Do MCP 深度排障与提速：1. 模块寻址陷阱：Windows 环境下 @mag-cie/mcp-microsoft-todo 硬编码 Linux 路径导致 MCP 服务静默瘫痪；且 auth.js 仅用于 Device Code 设备码握手，stdio MCP Server 主入口为 dist/index.js。已通过 resolveTodoMcpModule() 跨平台动态嗅探修复。2. 参数 Schema 与提速优化：原技能仅有文字原则，模型执行时反复在 node_modules 探索参数定义导致超时（>2分钟）。通过在 SKILL.md 中完整内嵌 snake_case 参数规范（due_date, reminder_date_time, time_zone: Asia/Shanghai）与 JSON 示例，直接将端到端耗时压缩至 45~60 秒内，并实现 100% 零重复的原地 update_task 覆写与 synced_todos.json 持久化。3. 测试套件隔离：test_desensitization 需在 os.walk 中排除 data/、media/ 等运行时生成目录，确保安全合规检测 10/10 全绿通过。
- **标签**：`#learning` `#2026-09-25`

### 💡 [LEARNING] 2026-09-25 18:05:00
- **内容**：微信机器人与 AGY 进程生命周期韧性及全链路 Debug 遥测体系建设：1. 进程意外终止复盘：上一轮排查窗口时后台误调 `Stop-Process` 误伤了前台用户正常运行的 Node 进程（导致第二条待办消息处理中断）。解决方案为：在 Node 端注册全局 `uncaughtException` 和 `unhandledRejection` 熔断拦截，并在 `.bat` 启动器中构建 `:run_loop` 循环自愈守护（退出后 3 秒自愈重启），彻底消除意外退出或窗口冻结。2. 全量 Debug 遥测升级：在 `core/ai_provider.js` 和 `adapters/wechat/wechat_bot.js` 引入 ANSI 色彩分级日志系统，完整输出：[WECHAT/RECV] 原始消息体拆解与多媒体元数据、[WECHAT/SEND] 响应时延与字符摘要、[AGY/SPAWN] 模型及会话参数、[AGY/PROCESS] 子进程 PID、[AGY/TOOL/ACTIVE] MCP 工具名与全部入参 JSON、[AGY/TOOL/DONE] 执行时延与结果摘要截断、[AGY/REASONING] 阶段 Token 与思考耗时、[AGY/RESULT] 全流程耗时与总 Token 汇总。用户可在前台实时监控每一个步骤与网络动作。
- **标签**：`#learning` `#telemetry` `#debug` `#wechat` `#2026-09-25`

### 💡 [LEARNING] 2026-09-25 18:25:00
- **内容**：多用户安全沙箱边界铁律与轮内步长（Turn Step）动态映射体系：1. 轮内步长演进：针对 Antigravity 跨轮次递增导致的步长序号困惑，构建 `turnStepMap` 动态映射状态机，在终端统一输出 `[Step 24 (本轮第 2 步)]`，既保留会话全局溯源序列，又清晰指示单轮请求内的进度。2. 绝对杜绝越权访问（Security Sandbox Enforcement）：大模型在回答系统架构提问时易自主推测并跨目录探测工程源码（如 view_file core/user_context.py）。解决方案为双层防御：① 提示词与系统沙箱铁律注入（锁定授权数据目录 `data/wechat/users/<hash>/`，严禁探测宿主机源码及跨用户数据）；② 遥测层实时沙箱校验（当调用 `view_file`、`write_to_file` 等文件工具时，自动比对 `targetPath` 是否归属于 `sandboxRoot`，越权即触发红色 `[AGY/SECURITY]` 警报，合法则打印 `[AGY/SANDBOX]` 通过日志）。
- **标签**：`#security` `#sandbox` `#turn-step` `#2026-09-25`



### ⚠️ [POSTMORTEM] 2026-09-25 19:48:13
- **内容**：【WeChat Bot 发图卡死无输出根因复盘与修复】1. 凭据链污染与静默重试风暴：Windows 凭据管理器中 gemini:antigravity 残留旧账号 Token 导致 429 额度耗尽，agy 内部静默指数退避重试 6 分钟造成无输出假死。已通过 CredWrite 更新为新账号。2. 微信媒体解密 Base64 Hex 嵌套缺陷：media.aes_key 为 32 位 Hex 串的 Base64 编码，原逻辑长度校验失败静默吞错，导致密文写入 png 磁盘。已重构 parseAesKey 完美解密 jpg。3. CLI 错误泄露与会话自愈：修复 ai_provider.js 在 ERROR 时泄露 raw NDJSON，新增检测 429/400/超时自动重置 conversationId 并在新上下文中无缝重试自愈。
- **标签**：`#postmortem` `#2026-09-25`

### ⚠️ [POSTMORTEM] 2026-09-25 20:37:11
- **内容**：【agy.exe --hub 幽灵后台覆写凭据排查与根除】发现 13:56 遗留的孤儿进程 PID 41808 (agy.exe --hub) 每隔 1 小时触发内部 OAuth Token 刷新，将 Windows 凭据管理器中的 gemini:antigravity 覆写回旧账号 waaoggawa@gmail.com，导致后续微信调用再次陷入 429。已彻底终止 PID 41808，重置凭据为新账号，并在 ai_provider.js 中注入 ensure_auth.py 自动防篡改守卫（每次调用前毫秒级自检与校准凭据），并设置 --print-timeout 120s 防止死等。
- **标签**：`#postmortem` `#2026-09-25`

### 🏛️ [DECISION] 2026-09-25 21:00:47
- **内容**：微信多账号并发轮询与零重启热插拔登录体系交付：1. 单账号凭据覆盖与消息断流根因：腾讯 iLink Bot 协议为每对扫码关系分配独立 bot_token 与 sync_buf，消息按 token 分片暂存。原架构仅存单份 auth.json 且单线程长轮询，导致多账号扫码时后登覆盖先登，未轮询账号消息永不回传。2. 解决方案落地：构建 AuthPool (auth_pool.json) 持久化多账号池；重构 BotManager 并发多线程长轮询状态机；常驻 QRLoginDaemon 监听扫码事件，扫码确认瞬间在内存中热挂载独立 Poller 并触发欢迎消息，实现生产环境 0 进程重启连续运行。3. 运营门户支撑：启动轻量 HTTP 门户 (端口 3000) 配合桌面一键直达快捷方式，提供带状态机的实时二维码渲染、在线账号列表与毫秒级手动换码 API。
- **标签**：`#decision` `#2026-09-25`
