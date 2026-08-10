# NexSandglass 补丁说明（tdx1146/nyx fork）

> 上游：sixgodgit/NexSandglass-Agent-DedicatedMemory（为 hermes 准备的沙漏）
> 本 fork 追加 4 个补丁 commit，全部**纯新增、零侵入原逻辑、环境变量可配**，OpenClaw 体系部署必用。

## 补丁清单

| commit | 内容 | 环境变量 |
|--------|------|---------|
| `8ced6c9` | **P0-1 落沙幂等去重**：同 sender+text 时间窗内只写一次（修编辑器双写导致的行重复） | `SANDGLASS_DEDUP_WINDOW`（秒，默认 10） |
| `81b1b15` | **P0-2 sender 归一化 + 去截断**：sister→user 映射（救活织线三元组提取）；落沙不再 500 字截断 | `SANDGLASS_SENDER_MAP`（JSON，默认 {"sister":"user"}）；`SANDGLASS_MAX_TEXT_LEN`（默认 0=不截断） |
| `f894d96` | **P0-2b 写锁修复**：shadow_sand 立即提交释放写锁（修同进程织线线程 database is locked） | 无 |
| `c2afb84` | **P0-3 落沙总线事件**：落沙成功后发布 `sandglass.entry` 到 AgentOS 事件总线（供 LMS /feed 塑形） | `SANDGLASS_BUS_FILE`（总线 jsonl 路径） |

## 部署方式
```bash
git clone https://github.com/tdx1146/nyx.git
# 或用上游 main + cherry-pick 以上 4 commit
# 环境变量在 .env / env.local 配置（见 Agent OS SYSTEM.md §3）
```

## 与 AgentOS 的咬合
- P0-3 依赖 AgentOS 总线格式（event_schema v1.1），consumer 侧 LmsFeedHandler 已订阅 sandglass.entry → LMS /feed
- 完整系统构造见 https://github.com/tdx1146/agent-os/blob/main/SYSTEM.md
