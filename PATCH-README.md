# NexSandglass 补丁说明（tdx1146/nyx fork）

> 上游：sixgodgit/NexSandglass-Agent-DedicatedMemory（为 hermes 准备的沙漏）
> 本 fork 追加 **8 个补丁 commit + 1 个扩展提交**，全部**纯新增、零侵入原逻辑、环境变量可配**，OpenClaw 体系部署必用。
> ⚠️ 2026-08-12 起 **tdx1146/nyx main 已含全部补丁**（此前只推了前 4 个，失忆根因三件套漏推，姐姐 clone 会拿到"会失忆的版本"——已修）。

## 补丁清单

| commit | 内容 | 环境变量 |
|--------|------|---------|
| `8ced6c9` | **P0-1 落沙幂等去重**：同 sender+text 时间窗内只写一次（修编辑器双写导致的行重复） | `SANDGLASS_DEDUP_WINDOW`（秒，默认 10） |
| `81b1b15` | **P0-2 sender 归一化 + 去截断**：sister→user 映射（救活织线三元组提取）；落沙不再 500 字截断（注：编辑器侧 edit-web.py 仍有 `content[:500]`，见 SYSTEM.md 坑 6 备注） | `SANDGLASS_SENDER_MAP`（JSON，默认 {"sister":"user"}）；`SANDGLASS_MAX_TEXT_LEN`（默认 0=不截断） |
| `f894d96` | **P0-2b 写锁修复**：shadow_sand 立即提交释放写锁（修同进程织线线程 database is locked） | 无 |
| `c2afb84` | **P0-3 落沙总线事件**：落沙成功后发布 `sandglass.entry` 到 AgentOS 事件总线（供 LMS /feed 塑形） | `SANDGLASS_BUS_FILE`（总线 jsonl 路径） |
| `84e2c1f` | **失忆根因-2：FTS 冻结修复** — sandglass.db 的 FTS5 表 schema 规范化 + txt 权威去重回填 + shadow_sand 全写路径立即提交（清写锁） | 无 |
| `3d7ea05` | **失忆根因-3：搜索工具修复** — 17333 查询前增量同步 + 多关键词 OR 匹配 + embedding 改 FTS5 优先 + 短查询跳过 simhash 重排；shouji_memory_mcp 本机直连优先（修 Connection reset） | 无 |
| `ba568e6` | **失忆根因-2(补)：_parse_entries 校验时间戳格式** — 含 `' | '` 的续行不再被误判为条目（修复 id=1960 脏行） | 无 |
| `扩展提交` | **MCP 扩展工具 + 单实例锁**：sandglass_mcp 加 `read_backlog`/`web_search`(Bing 免key)/`openalex_search`(学术)/`self_pulse`(自主脉冲) 工具 + TCP 单实例锁（防 SIGUSR1 双实例）；workspace 路径改 `WORKSPACE_HOME` 环境变量可配 | `WORKSPACE_HOME`（默认 `/vol1/@apphome/trim.openclaw/data/workspace`，新机器建议设置） |

## 部署方式
```bash
git clone https://github.com/tdx1146/nyx.git
# 或用上游 main + cherry-pick 以上 commit
# 环境变量在 .env / env.local 配置（见 Agent OS SYSTEM.md §3）
```

## 与 AgentOS 的咬合
- P0-3 依赖 AgentOS 总线格式（event_schema v1.1），consumer 侧 LmsFeedHandler 已订阅 sandglass.entry → LMS /feed
- 失忆根因三件套（FTS/搜索工具/时间戳解析）是 2026-08-11 修"关掉窗口就失忆"的关键修复，**部署必须包含**
- 完整系统构造见 https://github.com/tdx1146/agent-os/blob/main/SYSTEM.md

## 数据说明
- 本仓库只含**代码**；`sandglass.txt` / `snapshots/` 等是运行数据（个人记忆），**不随仓库分发**。新机器首次运行自动创建空数据目录。
