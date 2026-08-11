"""
NexSandglass SQLite FTS5 加速层
================================
V1.4.5（失忆根因-2 修复，2026-08-11）：
  - 修复 FTS 同步冻结：原代码向 `sandglass_fts(rowid, tokens)` 写入，但真实
    db schema 是 `sandglass(id, timestamp, sender, text)` + 外链式 FTS5
    (timestamp, sender, text, content='sandglass') → `no such column: tokens`
    → sync_all 返回 -1 / sync_incremental 返回 0（异常被 except 吞掉）→
    db 冻结在 8/2（906 行 vs txt 4871 行）。
  - 现在统一规范 schema：内容表用 `timestamp` 列（与 17333 API / night_patrol
    一致），FTS 表为独立 fts5(tokens)（rowid = 行号 = sandglass.id）。
    _get_db() 自动检测并重建不匹配的表（db 是 txt 的检索镜像，可安全重建）。
  - 全量/增量同步均基于 txt 权威源，含多行续行拼接 + (ts, sender, text) 去重
    （txt 有双写历史），避免重复条目。
  - 搜索：多关键词（空格分隔）→ OR 语义（原 AND 导致多关键词 0 命中）；
    单关键词中文 → AND（2-gram 全命中保证精度）；英文 → OR。
  - 增量同步 mtime 门控 + 对已存在行做文本 UPDATE（覆盖续行延展）。
纯 stdlib，零依赖。FTS5挂了自动降级。
"""

import os, re, sqlite3, threading

from sandglass_paths import _NB
_DB = os.path.join(_NB, "sandglass.db")
_lock = threading.Lock()
_last_sync_mtime = 0  # 记录上次同步时的 sandglass.txt 修改时间
_schema_checked = False  # 每进程只做一次 schema 规范化

# 期望 schema（txt 权威 → db 镜像；timestamp 列名与 17333 API/night_patrol 一致）
_EXPECT_SANDBOX_SQL = (
    "CREATE TABLE sandglass (id INTEGER PRIMARY KEY, timestamp TEXT, "
    "sender TEXT, text TEXT)"
)
_EXPECT_FTS_SQL = "CREATE VIRTUAL TABLE sandglass_fts USING fts5(tokens)"


def _tokenize(text: str) -> str:
    """FTS5专用分词：英文全词 + 中文2-gram。不用滑动窗口（滑动窗口用于mmap OR匹配）。"""
    import re as _re
    tokens = set()
    t = text.lower()
    # 英文全词（2+字母的数字词）
    tokens.update(_re.findall(r"[a-zA-Z0-9_]{2,}", t))
    # 中文2字词
    chars = "".join(_re.findall(r"[\u4e00-\u9fff]", text))
    for i in range(len(chars) - 1):
        tokens.add(chars[i : i + 2])
    return " ".join(sorted(t for t in tokens if t))


def _match_expr(query: str) -> str:
    """构造 FTS5 MATCH 表达式。
    - 多关键词（空格分隔 ≥2 项）：OR 语义（原来 AND 导致多关键词 0 命中）
    - 单关键词英文：OR（n-gram 碎片化兜底）
    - 单关键词中文：AND（2-gram 全命中，精度优先）"""
    tokens = _tokenize(query)
    if not tokens.strip():
        return ""
    terms = [t for t in query.split() if t.strip()]
    tok_list = tokens.split()
    if len(terms) > 1:
        return " OR ".join(tok_list)
    if any(c.isascii() and c.isalpha() for c in query):
        return " OR ".join(tok_list)
    return " ".join(tok_list)


def _get_db():
    global _schema_checked
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    conn = sqlite3.connect(_DB, timeout=15.0)  # busy_timeout 15s（写锁竞争容错）
    conn.execute("PRAGMA journal_mode=WAL")  # 支持多进程并发
    conn.execute("PRAGMA synchronous=NORMAL")  # 性能优化，安全够用
    conn.execute("PRAGMA busy_timeout=15000")
    if not _schema_checked:
        # schema 规范化：db 是 txt 的检索镜像，表结构不符就直接重建（数据从 txt 来）
        try:
            cur = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sandglass'"
            ).fetchone()
            need_sandbox_rebuild = True
            if cur and cur[0]:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(sandglass)")]
                need_sandbox_rebuild = "timestamp" not in cols
            if need_sandbox_rebuild:
                conn.execute("DROP TABLE IF EXISTS sandglass")
                conn.execute(_EXPECT_SANDBOX_SQL)

            cur = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sandglass_fts'"
            ).fetchone()
            need_fts_rebuild = True
            if cur and cur[0]:
                # 期望独立 fts5(tokens)；外链式(content=) 或列结构不符 → 重建
                sql = (cur[0] or "").lower()
                need_fts_rebuild = "fts5" not in sql or "tokens" not in sql or "content=" in sql
            if need_fts_rebuild:
                conn.execute("DROP TABLE IF EXISTS sandglass_fts")
                conn.execute(_EXPECT_FTS_SQL)
            conn.commit()
        except Exception:
            pass  # 规范化失败不阻塞（后续 sync 会再尝试）
        _schema_checked = True
    return conn


def _parse_entries() -> list:
    """解析 txt 权威源 → 条目列表 [(行号, ts, sender, text), ...]。

    - 多行消息：无时间戳的续行拼接到上一条 text（换行分隔）
    - 去重：(ts, sender, text) 完全相同只保留首次出现的行号（txt 有双写历史）
    """
    from sandglass_vault import _SANDGLASS, _parse_line
    entries = []          # (lineno, ts, sender, text)
    seen = set()
    if not os.path.exists(_SANDGLASS):
        return entries
    with open(_SANDGLASS, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            ts, sender, text = _parse_line(line)
            if ts:
                key = (ts, sender, text)
                if key in seen:
                    continue
                seen.add(key)
                entries.append([n, ts, sender, text])
            else:
                # 续行：拼到上一条（若上一条未被去重跳过）
                if entries and line.strip():
                    entries[-1][3] += "\n" + line.strip()
    return entries


def sync_all() -> int:
    """全量同步（txt 权威 → db+FTS 镜像，含去重）。返回条数，失败返回-1。"""
    try:
        with _lock:
            conn = _get_db()
            entries = _parse_entries()
            conn.execute("DELETE FROM sandglass")
            conn.execute("DELETE FROM sandglass_fts")
            rows = [(e[0], e[1], e[2], e[3]) for e in entries]
            fts = [(e[0], _tokenize(e[3])) for e in entries]
            if rows:
                conn.executemany(
                    "INSERT INTO sandglass (id, timestamp, sender, text) VALUES(?,?,?,?)",
                    rows,
                )
                conn.executemany(
                    "INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)", fts
                )
            conn.commit()
            return len(rows)
    except Exception:
        return -1


def sync_incremental() -> int:
    """增量同步。文件没变则跳过。返回新增条数。"""
    global _last_sync_mtime
    try:
        from sandglass_vault import _SANDGLASS
        # mtime检查——文件没变就跳过
        if os.path.exists(_SANDGLASS):
            mtime = os.path.getmtime(_SANDGLASS)
            if mtime == _last_sync_mtime and _last_sync_mtime > 0:
                return 0
            _last_sync_mtime = mtime
        with _lock:
            conn = _get_db()
            cur = conn.execute("SELECT MAX(id) FROM sandglass")
            max_id = cur.fetchone()[0] or 0
            entries = _parse_entries()
            added = 0
            for lineno, ts, sender, text in entries:
                if lineno > max_id:
                    conn.execute(
                        "INSERT INTO sandglass (id, timestamp, sender, text) VALUES(?,?,?,?)",
                        (lineno, ts, sender, text),
                    )
                    conn.execute(
                        "INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)",
                        (lineno, _tokenize(text)),
                    )
                    added += 1
                else:
                    # 已存在行：文本可能因续行延展而变长 → UPDATE（含 FTS 重建该行）
                    old = conn.execute(
                        "SELECT text FROM sandglass WHERE id = ?", (lineno,)
                    ).fetchone()
                    if old is None:
                        conn.execute(
                            "INSERT INTO sandglass (id, timestamp, sender, text) VALUES(?,?,?,?)",
                            (lineno, ts, sender, text),
                        )
                        conn.execute(
                            "INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)",
                            (lineno, _tokenize(text)),
                        )
                        added += 1
                    elif old[0] != text:
                        conn.execute(
                            "UPDATE sandglass SET text = ?, sender = ? WHERE id = ?",
                            (text, sender, lineno),
                        )
                        conn.execute(
                            "DELETE FROM sandglass_fts WHERE rowid = ?", (lineno,)
                        )
                        conn.execute(
                            "INSERT INTO sandglass_fts(rowid, tokens) VALUES(?,?)",
                            (lineno, _tokenize(text)),
                        )
            conn.commit()
            return added
    except Exception:
        return 0


def search_in(line_ids: list, query: str, limit: int = 100) -> list:
    """FTS5 在指定行号列表中搜索排序。用于 mmap 初筛后的精排。"""
    try:
        tokens = _match_expr(query)
        if not tokens.strip() or not line_ids:
            return []
        ids_str = ",".join(str(int(i)) for i in line_ids)
        with _lock:
            conn = _get_db()
            sql = (
                "SELECT s.id, s.timestamp, s.text FROM sandglass_fts f "
                "JOIN sandglass s ON s.id=f.rowid WHERE s.id IN (%s) "
                "AND sandglass_fts MATCH ? ORDER BY rank" % ids_str
            )
            cur = conn.execute(sql, (tokens,))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def search_year(query: str, year: str, limit: int = -1) -> list:
    """FTS5 按年份搜索。year='2026' 只搜该年。"""
    try:
        tokens = _match_expr(query)
        if not tokens.strip():
            return []
        with _lock:
            conn = _get_db()
            sql = (
                "SELECT s.id, s.timestamp, s.text FROM sandglass_fts f "
                "JOIN sandglass s ON s.id=f.rowid WHERE s.timestamp LIKE ? "
                "AND sandglass_fts MATCH ? ORDER BY rank"
            )
            if limit > 0:
                sql += f" LIMIT {limit}"
            cur = conn.execute(sql, (f"{year}%", tokens))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def search(query: str, limit: int = 10) -> list:
    """FTS5搜索。limit=-1 全量。返回[(行号,时间,明文),...]。
    多关键词OR语义（修复多关键词0命中），单关键词中文AND，英文OR。"""
    try:
        tokens = _match_expr(query)
        if not tokens.strip():
            return []
        with _lock:
            conn = _get_db()
            sql = (
                "SELECT s.id, s.timestamp, s.text FROM sandglass_fts f "
                "JOIN sandglass s ON s.id = f.rowid WHERE sandglass_fts MATCH ? "
                "ORDER BY rank"
            )
            if limit > 0:
                sql += f" LIMIT {limit}"
            cur = conn.execute(sql, (tokens,))
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]
    except Exception:
        return []


def count() -> int:
    try:
        with _lock:
            return _get_db().execute("SELECT COUNT(*) FROM sandglass").fetchone()[0]
    except Exception:
        return 0
