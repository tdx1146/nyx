"""
NexSandglass MCP Server V2.6.14
===============================
标准 MCP 协议——任何 MCP 兼容 Agent 可直接调用。
启动: python sandglass_mcp.py
"""

import sys, os, json
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandglass_paths import __version__

# ─── MCP 单实例锁 ──────────────────────────────────────────────────
# 防止 SIGUSR1 热重载 spawn 出双实例

LOCK_PORT = 23622

def try_acquire_lock():
    """尝试绑定 TCP 端口。失败说明已有实例在运行。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('127.0.0.1', LOCK_PORT))
        sock.listen(1)
        sock.close()
        return True
    except OSError as e:
        if e.errno == 98 or e.errno == 48:  # EADDRINUSE (Linux/macOS)
            return False
        return True
    finally:
        sock.close()

if not try_acquire_lock():
    msg = json.dumps({
        "jsonrpc": "2.0", "id": None, "method": "notifications/initialized",
        "params": {"_warning": "[单实例锁] 另一 sandglass_mcp 实例已在运行，退出"}
    }) + '\n'
    sys.stderr.write(msg)
    sys.exit(0)



# ─── workspace 根路径（OpenClaw 数据目录）─────────────────────
# 新增工具（read_backlog/web_search/self_pulse）需要读 workspace 文件；
# 优先取环境变量 WORKSPACE_HOME（新机器可配），缺省回退本机路径（向后兼容）。
WORKSPACE_HOME = os.environ.get('WORKSPACE_HOME', '/vol1/@apphome/trim.openclaw/data/workspace')

def _rpc_response(id, result, wrap=True):
    """wrap=True for tools/call (MCP content blocks). wrap=False for initialize, tools/list (bare JSON)."""
    if not wrap:
        return json.dumps({"jsonrpc": "2.0", "id": id, "result": result})
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result}]
    }})


def _rpc_error(id, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


def _handle_tool(name, args, request_id):
    try:
        if name == "sandglass_ping":
            from sandglass_vault import count
            from sandglass_think import _current_stage
            return _rpc_response(request_id, {
                "status": "ok", "sands": count(), "stage": _current_stage()
            })

        elif name == "sandglass_search":
            from sandglass_vault import search
            r = search(args.get("query", ""), limit=args.get("limit", 10))
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_semantic":
            from sandglass_think import search_semantic
            r = search_semantic(args.get("query", ""), limit=args.get("limit", 5))
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_recent":
            from sandglass_vault import recent
            r = recent(args.get("limit", 10))
            return _rpc_response(request_id, [
                {"line": ln, "ts": ts, "text": txt[:200]} for ln, ts, txt, *_ in r
            ])

        elif name == "sandglass_offset":
            from sandglass_think import comprehensive_offset
            r = comprehensive_offset()
            return _rpc_response(request_id, r)

        elif name == "sandglass_persona":
            from sandglass_think import _current_stage
            import persona_l3
            p = persona_l3._local_persona_extract()
            return _rpc_response(request_id, {"stage": _current_stage(), "persona": p[:500]})

        elif name == "sandglass_tasks":
            from l3_tasks import task_pending
            return _rpc_response(request_id, task_pending())

        elif name == "read_backlog":
            """读取轻如烟编辑器待办系统 — backlog.md"""
            import os as _os
            backlog_path = _os.path.join(WORKSPACE_HOME, 'memory', 'backlog.md')
            try:
                with open(backlog_path, 'r', encoding='utf-8') as _f:
                    content = _f.read()
                pending = content.count('- [ ] ')
                done = content.count('- [x] ')
                return _rpc_response(request_id, {
                    'ok': True,
                    'content': content,
                    'pending': pending,
                    'done': done,
                    'path': backlog_path
                })
            except Exception as _e:
                return _rpc_response(request_id, {
                    'ok': False,
                    'error': str(_e),
                    'path': backlog_path
                })

        elif name == "sandglass_echo":
            from l3_search_core import _sentiment_wind
            return _rpc_response(request_id, {"wind": _sentiment_wind()})

        elif name == "sandglass_dream":
            from emotion_l3 import entropy_ghost
            r = entropy_ghost(args.get("question", "如果选另一个选项"))
            return _rpc_response(request_id, r)

        elif name == "sandglass_chart":
            from sandglass_think import entropy_chart
            return _rpc_response(request_id, {"chart": entropy_chart(args.get("n", 10))})

        elif name == "sandglass_migrate":
            from sandglass_think import memory_migrate
            path = memory_migrate(args.get("output", ""))
            return _rpc_response(request_id, {"exported": path})

        elif name == "sandglass_soul_export":
            from soul_diff import export_soul
            path = export_soul(args.get("output", ""))
            return _rpc_response(request_id, {"soul": path})

        elif name == "sandglass_soul_merge":
            from soul_diff import merge_soul
            n = merge_soul(args.get("source", ""))
            return _rpc_response(request_id, {"merged": n})

        elif name == "sandglass_import":
            from sandglass_vault import sandglass_import
            r = sandglass_import(args.get("source_path", ""), args.get("format", "sandglass"))
            return _rpc_response(request_id, r)

        elif name == "sandglass_export":
            from sandglass_vault import sandglass_export
            path = sandglass_export(args.get("output_path"), args.get("limit"), args.get("month", ""))
            return _rpc_response(request_id, {"exported": path})

        elif name == "sandglass_thread":
            from weavethread import wthread_query
            r = wthread_query(args.get("entity"), args.get("relation"), args.get("limit", 20))
            return _rpc_response(request_id, r)

        elif name == "sandglass_thread_graph":
            from weavethread import wthread_graph
            r = wthread_graph(args.get("entity", ""), args.get("depth", 1))
            return _rpc_response(request_id, r)

        elif name == "sandglass_thread_weave":
            from weavethread import wthread_weave
            r = wthread_weave(args.get("limit", 3))
            return _rpc_response(request_id, {"causal_summary": r})

        elif name == "sandglass_thread_add":
            from weavethread import wthread_add
            ok = wthread_add(args.get("subject", "user"), args.get("relation", ""), args.get("object", ""))
            return _rpc_response(request_id, {"added": ok})

        elif name == "web_search":
            """Bing.cn HTML 搜索 — 免费无key，不经过bundle-mcp"""
            import subprocess
            query = args.get("query", "")
            count = min(int(args.get("count", 10)), 20)
            if not query:
                return _rpc_response(request_id, {"error": "query required"})
            try:
                bing_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "workspace", "scripts", "bing_search.py")
                if not os.path.exists(bing_script):
                    bing_script = os.path.join(WORKSPACE_HOME, "scripts", "bing_search.py")
                result = subprocess.run(
                    ["python3", bing_script, query, str(count)],
                    capture_output=True, text=True, timeout=25
                )
                import json as _json
                out = _json.loads(result.stdout)
                return _rpc_response(request_id, out)
            except Exception as e:
                return _rpc_response(request_id, {"error": str(e), "stderr": result.stderr[:200] if 'result' in dir() else ""})

        elif name == "openalex_search":
            """OpenAlex 学术搜索 — 免费无key，搜索学术论文/研究"""
            import urllib.request as _ur, urllib.parse as _up, json as _json
            query = args.get("query", "")
            limit = min(int(args.get("count", 10)), 20)
            if not query:
                return _rpc_response(request_id, {"error": "query required"})
            try:
                url = "https://api.openalex.org/works?search=" + _up.quote(query) + \
                      "&per_page=" + str(limit) + "&sort=relevance_score:desc"
                req = _ur.Request(url, headers={"User-Agent": "OpenClawBot/1.0 (mailto:internal@openclaw)"})
                resp = _ur.urlopen(req, timeout=20)
                data = _json.loads(resp.read())
                results = []
                for r in data.get("results", [])[:limit]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": "https://openalex.org/" + r.get("id", "").split("/")[-1] if r.get("id") else "",
                        "year": r.get("publication_year", ""),
                        "citations": r.get("cited_by_count", 0),
                        "snippet": (r.get("abstract_inverted_index") and " ".join(r.get("abstract_inverted_index", {}).keys())[:200] or ""),
                        "source": "openalex",
                    })
                return _rpc_response(request_id, results)
            except Exception as e:
                return _rpc_response(request_id, {"error": str(e)})

        elif name == "self_pulse":
            """自主脉冲——用户不在时，自己决定做什么。每6h触发，最多5轮。"""
            import subprocess as _sp, json as _json
            now = __import__("time").time()
            _SELF = WORKSPACE_HOME

            # 1. 确认当前轮次（存 /tmp/self_pulse_round.txt）
            round_file = "/tmp/self_pulse_round.txt"
            max_rounds = int(args.get("max_rounds", 5))
            try:
                with open(round_file) as f:
                    rnd = int(f.read().strip())
            except:
                rnd = 0

            # 2. 读 backlog
            backlog_path = _SELF + "/memory/backlog.md"
            try:
                with open(backlog_path, encoding="utf-8") as f:
                    backlog_content = f.read()
                pending_count = backlog_content.count("- [ ] ")
            except:
                backlog_content = ""
                pending_count = 0

            # 3. 决定做什么
            decision = "无待办"
            action = ""
            if rnd < max_rounds:
                if pending_count > 0:
                    # 选第1条待办推进（信息增益最大的简配版）
                    for line in backlog_content.split("\n"):
                        if "- [ ] " in line:
                            decision = "推进待办: " + line.replace("- [ ] ", "").strip()[:80]
                            action = "advance_todo"
                            break
                else:
                    # 无待办时：写守夜感知
                    decision = "守夜感知：无待办时画像漂移检查"
                    action = "vigil"
                rnd += 1
                with open(round_file, "w") as f:
                    f.write(str(rnd))
            else:
                decision = "已达最大轮次 " + str(max_rounds)
                # 清理轮次文件
                try: os.remove(round_file)
                except: pass

            # 4. 写 sand 记录
            _SANDBASE = os.environ.get('NEXSANDBASE_HOME', os.path.join(_SELF, 'sandglass'))
            sand_path = os.path.join(_SANDBASE, "sandglass.txt")
            ts = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = f"{ts} | system | 🌫️ self_pulse round {rnd}/{max_rounds}: {decision}"
            try:
                with open(sand_path, "a", encoding="utf-8") as f:
                    f.write(entry + "\n")
            except:
                pass

            return _rpc_response(request_id, {
                "round": rnd,
                "max_rounds": max_rounds,
                "pending": pending_count,
                "decision": decision,
                "action": action,
                "sand_written": True
            })

        else:
            return _rpc_error(request_id, -32601, f"Unknown tool: {name}")

    except Exception as e:
        return _rpc_error(request_id, -32000, str(e))


def main():
    """MCP stdio 主循环"""
    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
            method = req.get("method", "")

            # JSON-RPC 2.0 spec: messages without "id" are notifications.
            # Servers MUST NOT reply to notifications. The MCP handshake sends
            # `notifications/initialized` right after `initialize`; replying to
            # it with a fake id=0 corrupts subsequent response correlation and
            # breaks strict clients (opencode / Claude Desktop / Cursor).
            if "id" not in req:
                continue
            tid = req["id"]

            if method == "tools/list":
                tools = [
                    {"name": "sandglass_ping", "description": "健康检查——返回沙漏总数和当前阶段", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "sandglass_search", "description": "关键词搜索记忆", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}, "limit": {"type": "integer", "description": "最大返回条数"}}, "required": ["query"]}},
                    {"name": "sandglass_semantic", "description": "语义搜索记忆(同义词+SimHash+TF-IDF)", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "语义搜索查询"}, "limit": {"type": "integer", "description": "最大返回条数"}}, "required": ["query"]}},
                    {"name": "sandglass_recent", "description": "最近N条记忆", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "返回条数，默认10"}}}},
                    {"name": "sandglass_offset", "description": "当前偏移率(省钱/愿投/放弃)", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "sandglass_persona", "description": "当前阶段画像", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "sandglass_tasks", "description": "待办事项列表", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "read_backlog", "description": "读取轻如烟系统待办 backlog.md——dandan可视化的公共待办", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "sandglass_echo", "description": "当前回音折风向", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "sandglass_dream", "description": "幽灵决策——'如果选另一个选项会怎样'", "inputSchema": {"type": "object", "properties": {"question": {"type": "string", "description": "替代选项的问题"}}, "required": ["question"]}},
                    {"name": "sandglass_chart", "description": "情绪熵 ASCII 可视化图表", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "description": "显示最近N条，默认10"}}}},
                    {"name": "sandglass_migrate", "description": "一键导出全部记忆数据为 tar.gz", "inputSchema": {"type": "object", "properties": {"output": {"type": "string", "description": "输出路径"}}}},
                    {"name": "sandglass_soul_export", "description": "导出灵魂差分(偏移率+决策+回音折)", "inputSchema": {"type": "object", "properties": {"output": {"type": "string", "description": "输出路径"}}}},
                    {"name": "sandglass_soul_merge", "description": "合并外部灵魂差分", "inputSchema": {"type": "object", "properties": {"source": {"type": "string", "description": "源文件路径"}}, "required": ["source"]}},
                    {"name": "sandglass_import", "description": "导入外部沙漏或ChatGPT/Claude对话导出", "inputSchema": {"type": "object", "properties": {"source_path": {"type": "string", "description": "源文件路径"}, "format": {"type": "string", "description": "格式：sandglass/chatgpt/claude"}}, "required": ["source_path"]}},
                    {"name": "sandglass_export", "description": "导出沙漏为可迁移文件", "inputSchema": {"type": "object", "properties": {"output_path": {"type": "string", "description": "输出路径"}, "limit": {"type": "integer", "description": "最大导出条数"}, "month": {"type": "string", "description": "指定月份(YYYY-MM)"}}}},
                    {"name": "sandglass_thread", "description": "查询织线知识图谱——实体关系三元组", "inputSchema": {"type": "object", "properties": {"entity": {"type": "string", "description": "查询的实体名"}, "relation": {"type": "string", "description": "关系类型"}, "limit": {"type": "integer", "description": "最大返回数"}}}},
                    {"name": "sandglass_thread_graph", "description": "织线实体子图——展开N跳关系", "inputSchema": {"type": "object", "properties": {"entity": {"type": "string", "description": "中心实体名"}, "depth": {"type": "integer", "description": "展开跳数，默认1"}}, "required": ["entity"]}},
                    {"name": "sandglass_thread_weave", "description": "织线→织布机桥接——因果链摘要", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "最大摘要数，默认3"}}}},
                    {"name": "sandglass_thread_add", "description": "手动补入三元组——Agent发现漏抓时调用", "inputSchema": {"type": "object", "properties": {"subject": {"type": "string", "description": "主体"}, "relation": {"type": "string", "description": "关系"}, "object": {"type": "string", "description": "客体"}}, "required": ["subject", "relation", "object"]}},
                    {"name": "self_pulse", "description": "自主脉冲——用户不在时自主决定做什么。每6h触发，最多5轮", "inputSchema": {"type": "object", "properties": {"max_rounds": {"type": "integer", "description": "最大轮次，默认5"}}}},
                    {"name": "web_search", "description": "Search internet via Bing.cn HTML - free no key", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "search query"}, "count": {"type": "integer", "description": "result count"}}, "required": ["query"]}},
                    {"name": "openalex_search", "description": "Search academic papers/research via OpenAlex API - free no key", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "academic search query"}, "count": {"type": "integer", "description": "result count"}}, "required": ["query"]}},
                ]
                print(_rpc_response(tid, {"tools": tools}, wrap=False), flush=True)

            elif method == "tools/call":
                name = req.get("params", {}).get("name", "")
                args = req.get("params", {}).get("arguments", {})
                print(_handle_tool(name, args, tid), flush=True)

            elif method == "initialize":
                print(_rpc_response(tid, {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "NexSandglass", "version": __version__}
                }, wrap=False), flush=True)

            else:
                print(_rpc_error(tid, -32601, f"Unknown method: {method}"), flush=True)

        except json.JSONDecodeError:
            print(_rpc_error(0, -32700, "Parse error"), flush=True)
        except Exception as e:
            print(_rpc_error(0, -32000, str(e)), flush=True)


if __name__ == "__main__":
    main()
