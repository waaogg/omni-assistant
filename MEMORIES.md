# 智能体永久记忆档案 (Agent Permanent Memory)
> 智能体历史操作经验、决策演进与踩坑复盘永久记忆库。
> 本文件由 iron-clad-rules 自动化脚本持续维护与索引。

---

### 💡 [LEARNING] 2026-09-25 17:33:39
- **内容**：todo-sync 项目 Antigravity CLI 与 Microsoft To Do MCP 深度排障与提速：1. 模块寻址陷阱：Windows 环境下 @mag-cie/mcp-microsoft-todo 硬编码 Linux 路径导致 MCP 服务静默瘫痪；且 auth.js 仅用于 Device Code 设备码握手，stdio MCP Server 主入口为 dist/index.js。已通过 resolveTodoMcpModule() 跨平台动态嗅探修复。2. 参数 Schema 与提速优化：原技能仅有文字原则，模型执行时反复在 node_modules 探索参数定义导致超时（>2分钟）。通过在 SKILL.md 中完整内嵌 snake_case 参数规范（due_date, reminder_date_time, time_zone: Asia/Shanghai）与 JSON 示例，直接将端到端耗时压缩至 45~60 秒内，并实现 100% 零重复的原地 update_task 覆写与 synced_todos.json 持久化。3. 测试套件隔离：test_desensitization 需在 os.walk 中排除 data/、media/ 等运行时生成目录，确保安全合规检测 10/10 全绿通过。
- **标签**：`#learning` `#2026-09-25`
