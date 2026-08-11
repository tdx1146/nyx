#!/usr/bin/env python3
"""
Sandglass HTTP API Server
提供沙漏系统的HTTP接口，供手机MCP调用
端口：17333

失忆根因-3 修复（2026-08-11）：
  - 每个查询前先 sync_incremental()（txt 权威 → db+FTS 镜像增量同步），
    修复 db 冻结导致检索对 8/2 后内容全盲。
  - 多关键词（空格分隔）→ OR 匹配（原来整串 LIKE / 精确子串 → 多关键词 0 命中）。
  - embedding_search 改为 FTS5 优先（修复后命中 8/10 质疑系统讨论），
    语义搜索仅作兜底。
"""

import sys
import os
import json
import sqlite3

DB_PATH = '/vol2/1000/AI专用/所有自动化/轻如烟/sandglass/sandglass.db'
from http.server import HTTPServer, BaseHTTPRequestHandler

# 添加sandglass路径
sys.path.insert(0, '/vol2/1000/AI专用/所有自动化/轻如烟/sandglass_source')

from sandglass_vault import search, recent, count
from sandglass_think import search_semantic, comprehensive_offset


def _sync_db() -> None:
    """查询前增量同步（fail-open：同步失败不阻塞查询）。"""
    try:
        from sandglass_sqlite import sync_incremental
        sync_incremental()
    except Exception:
        pass


def _like_clause(terms: list) -> str:
    """多关键词 OR 的 LIKE 子句。terms 为空时返回 '1=0'（无命中）。"""
    if not terms:
        return "1=0"
    return " OR ".join(["text LIKE ?"] * len(terms))


def _split_terms(query: str) -> list:
    """把查询拆成关键词（空白分隔，去空）。"""
    return [t for t in query.split() if t.strip()]


class SandglassAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """健康检查"""
        if self.path == '/api/health':
            self.send_json({
                'status': 'ok',
                'service': 'sandglass-http-api',
                'sandglass_count': count()
            })
        else:
            self.send_error(404, 'Not Found')

    def do_POST(self):
        """处理POST请求"""
        try:
            # 读取请求体
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            data = json.loads(body) if body else {}

            # 路由
            if self.path == '/api/memory_search':
                result = self.handle_memory_search(data)
            elif self.path == '/api/embedding_search':
                result = self.handle_embedding_search(data)
            elif self.path == '/api/facts_lookup':
                result = self.handle_facts_lookup(data)
            elif self.path == '/api/sandglass_query':
                result = self.handle_sandglass_query(data)
            else:
                self.send_error(404, 'Not Found')
                return

            self.send_json(result)

        except Exception as e:
            self.send_json({'error': str(e)}, status=500)

    def handle_memory_search(self, data):
        """记忆搜索：SQLite LIKE 优先（查询前增量同步），无命中时 txt 权威源 LIKE 兜底。
        多关键词（空格分隔）→ OR 匹配。"""
        query = data.get('query', '')
        limit = data.get('limit', 10)
        terms = _split_terms(query)
        results = []
        _sync_db()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, timestamp, text FROM sandglass WHERE %s ORDER BY id DESC LIMIT ?"
                % _like_clause(terms),
                (*[f'%{t}%' for t in terms], limit),
            )
            results = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
            conn.close()
        except Exception:
            pass
        if not results:
            # 兜底：txt 权威源（同样多关键词 OR）
            results = self._txt_like(terms, limit)
        return {
            'results': [
                {'line': ln, 'ts': ts, 'text': txt[:200]}
                for ln, ts, txt, *_ in results
            ]
        }

    def _txt_like(self, terms: list, limit: int = 10):
        """txt 权威源 LIKE 兜底检索（多关键词 OR）：返回 [(行号, 时间, 明文), ...]"""
        txt_path = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'sandglass', 'sandglass.txt'))
        hits = []
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                for ln, line in enumerate(f, 1):
                    if any(t in line for t in terms):
                        parts = line.split('|', 2)
                        ts = parts[0].strip() if len(parts) > 0 else ''
                        text = parts[2].strip() if len(parts) > 2 else line.strip()
                        hits.append((ln, ts, text))
                        if len(hits) >= limit:
                            break
        except Exception:
            pass
        return hits

    def handle_embedding_search(self, data):
        """向量搜索：FTS5 全文优先（修复后精确命中），语义搜索兜底。"""
        query = data.get('query', '')
        limit = data.get('limit', 5)
        results = []
        _sync_db()
        try:
            from sandglass_sqlite import search as fts5_search
            results = fts5_search(query, limit)
        except Exception:
            pass
        if not results:
            # 兜底：语义搜索（SearchRouter 多路 + 密度重排）
            results = search_semantic(query, limit=limit)
        return {
            'results': [
                {'line': ln, 'ts': ts, 'text': txt[:200]}
                for ln, ts, txt, *_ in results
            ]
        }

    def handle_facts_lookup(self, data):
        """事实字典查询"""
        keyword = data.get('keyword', '')
        facts_path = '/vol1/@apphome/trim.openclaw/data/workspace/memory/facts.dict.md'

        try:
            with open(facts_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 简单的关键词匹配
            lines = content.split('\n')
            matches = [
                line for line in lines
                if keyword.lower() in line.lower()
            ][:10]

            return {'results': matches}
        except Exception as e:
            return {'error': str(e)}

    def handle_sandglass_query(self, data):
        """沙漏查询（综合）：FTS5/ LIKE 优先，再语义搜索。"""
        query = data.get('query', '')
        limit = data.get('limit', 10)
        terms = _split_terms(query)

        # 先试 SQLite LIKE（查询前增量同步）
        _sync_db()
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, timestamp, text FROM sandglass WHERE %s ORDER BY id DESC LIMIT ?"
            % _like_clause(terms),
            (*[f'%{t}%' for t in terms], limit),
        )
        results = [(row[0], row[1], row[2]) for row in cursor.fetchall()]
        conn.close()
        if not results:
            results = search_semantic(query, limit=limit)

        return {
            'results': [
                {'line': ln, 'ts': ts, 'text': txt[:200]}
                for ln, ts, txt, *_ in results
            ]
        }

    def send_json(self, data, status=200):
        """发送JSON响应"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        """简化日志"""
        print(f"[{self.path}] {args[0] if args else ''}")


def main():
    port = 17333
    server = HTTPServer(('0.0.0.0', port), SandglassAPIHandler)
    print(f"Sandglass HTTP API running on port {port}")
    print(f"Endpoints:")
    print(f"  GET  /api/health")
    print(f"  POST /api/memory_search")
    print(f"  POST /api/embedding_search")
    print(f"  POST /api/facts_lookup")
    print(f"  POST /api/sandglass_query")
    server.serve_forever()

if __name__ == '__main__':
    main()
