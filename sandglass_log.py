"""
NexSandglass 通用落沙 — 任何 Agent 都能用
==========================================
不依赖 Hermes plugin。任何 Python 脚本 import 即可。
V2.4.0: 去掉 DPAPI/base64 加密，明文存储。靠 OS 层全盘加密保护（BitLocker/FileVault/LUKS）。

用法：
  from sandglass_log import log_message
  log_message("用户：今天天气真好")
  log_message("Assistant：明天有雨，记得带伞")
"""

import hashlib
import json
import logging
import os
import re
import time as _time
from datetime import datetime

logger = logging.getLogger(__name__)

# ── P0-1 落沙幂等去重（沙漏侧，2026-08-10）──
# 双写根因在编辑器侧（momo_handler 先 _sandglass_log 再 inject_via_websocket
# 内部又落沙一次；且 _sandglass_log 在 inject 锁检查之前执行，前端双 POST 也会双写）。
# 按硬约束不改编辑器，在沙漏写入口做幂等去重：同一 sender+text 在时间窗内只写一次。
# 去重状态存文件（每次落沙是独立 Popen 进程，内存态跨进程无效）。
_DEDUP_WINDOW = float(os.environ.get("SANDGLASS_DEDUP_WINDOW", "10"))  # 秒，可配
_DEDUP_MAX = 500

# ── AI无意义回复过滤器（V2.1.10修复：长度判断替代^锚定）──
_AI_TRIVIAL = re.compile(
    r'(好的|明白了|没问题|请稍等|我来看看|是的|对的|'
    r'你说得对|当然可以|不用担心|不客气|谢谢|可以|'
    r'好|嗯|OK|ok|嗯嗯|好的呢|没问题呢|知道了|收到)'
)


def _estimate_info_value(text: str) -> float:
    """评估消息信息量。0.0=纯确认词，1.0=高价值。"""
    score = 0.3
    if len(text) > 50:                score += 0.2
    if re.search(r'\d+', text):       score += 0.2
    if re.search(r'[。：；]', text):  score += 0.1
    if any(kw in text for kw in [
        '建议', '需要', '注意', '因为', '方案',
        '步骤', '第一种', '第二种', '推荐',
        '区别', '对比', '优点是', '缺点是',
    ]):                                 score += 0.2
    # 短文本+纯确认词 → 零价值；长文本开头是确认词 → 仍可加分
    stripped = text.strip()
    if _AI_TRIVIAL.match(stripped) and len(stripped) <= 10:
        score = 0.0
    return min(score, 1.0)


from sandglass_paths import _NB

_SANDGLASS = os.path.join(_NB, "sandglass.txt")
# P0-1：去重状态文件放沙漏数据目录（_NB 由环境变量/相对推导，不硬编码绝对路径）
_DEDUP_FILE = os.path.join(_NB, ".sandglass_dedup.json")

# ── P0-2 sender 归一化（2026-08-10）──
# 双写/错标根因在编辑器侧：awake/momo 面板（现为主发送通道）一律 bypass_lock=True
# → edit-web.py:209 标 'sister'；且 momo_handler:40 硬编码 'sister'。
# 按硬约束不改编辑器，在沙漏写入口归一化：'sister'（主会话对话）→ 'user'，
# 救活 weavethread（仅 sender=='user' 提取三元组，L3 织线自 8/1 停摆）。
# 可用 SANDGLASS_SENDER_MAP 覆盖（JSON dict，如 '{"sister":"user","agent":"assistant"}'）。
_SENDER_MAP = {}
_SENDER_MAP_RAW = os.environ.get("SANDGLASS_SENDER_MAP", '{"sister": "user"}')
try:
    import json as _json
    _SENDER_MAP = _json.loads(_SENDER_MAP_RAW)
    if not isinstance(_SENDER_MAP, dict):
        _SENDER_MAP = {}
except Exception:
    _SENDER_MAP = {}

# ── P0-2 落沙长度（2026-08-10）──
# 编辑器侧 edit-web.py:302 的 content[:500] 属编辑器代码，按硬约束不改；
# 沙漏侧本身不再设 500 截断：SANDGLASS_MAX_TEXT_LEN 可配（默认 0=不截断，
# 完整保留；编辑器若放开 500 限制，沙漏将全量保存）。
_MAX_TEXT_LEN = int(os.environ.get("SANDGLASS_MAX_TEXT_LEN", "0") or "0")


def _normalize_sender(sender: str) -> str:
    """P0-2：sender 归一化（主会话对话不再错标 sister）。"""
    return _SENDER_MAP.get(sender, sender)


def _dedup_check_and_mark(sender: str, text: str) -> bool:
    """幂等去重：同一 (sender, text) 在时间窗内已写过 → 返回 True（应跳过写入）。

    - 状态持久化到 _DEDUP_FILE（JSON: {hash: ts}），跨进程生效
    - 过期条目惰性清理；fail-open：任何异常不阻塞写入（返回 False=不重复）
    """
    try:
        key = hashlib.md5(f"{sender}\x00{text}".encode("utf-8")).hexdigest()
        now = _time.time()
        recent = {}
        if os.path.exists(_DEDUP_FILE):
            try:
                with open(_DEDUP_FILE, "r", encoding="utf-8") as f:
                    recent = json.load(f)
            except Exception:
                recent = {}
        # 惰性清理过期条目
        recent = {k: t for k, t in recent.items() if now - t < _DEDUP_WINDOW}
        if key in recent:
            return True
        recent[key] = now
        # 防无限膨胀：只保留最近 _DEDUP_MAX 条
        if len(recent) > _DEDUP_MAX:
            recent = dict(sorted(recent.items(), key=lambda kv: -kv[1])[:_DEDUP_MAX])
        tmp = _DEDUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(recent, f)
        os.replace(tmp, _DEDUP_FILE)
        return False
    except Exception as e:
        logger.warning(f"落沙去重状态读写失败（fail-open 不阻塞写入）: {e}")
        return False


def log_message(text: str, sender: str = "agent") -> bool:
    """写入一条消息到沙漏。明文存储——OS层全盘加密保护。
    返回 True 表示写入成功。
    V2.4.0: 去掉DPAPI，落沙提速~2ms，FTS5可直接索引中文。"""
    try:
        # P0-2：sender 归一化（编辑器 bypass 路径错标 sister → user）
        sender = _normalize_sender(sender)
        # P0-2：落沙长度可配（默认 0=不截断，完整保留）
        if _MAX_TEXT_LEN > 0 and len(text) > _MAX_TEXT_LEN:
            text = text[:_MAX_TEXT_LEN]

        # AI低价值回复过滤（V2.1）
        if sender == "agent" and _estimate_info_value(text) < 0.3:
            return False

        os.makedirs(os.path.dirname(_SANDGLASS), exist_ok=True)
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {sender} | {text}\n"

        # 文件锁——指数退避：3次×5s=15s（V2.4.0修复：超时不裸写，重试+告警）
        lock = _SANDGLASS + ".lock"
        for attempt in range(3):
            deadline = _time.time() + 5
            while _time.time() < deadline:
                try:
                    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    break
                except FileExistsError:
                    _time.sleep(0.01)
            else:
                continue  # 本轮超时，重试
            break  # 获取锁成功
        else:
            # 3次重试全部超时——记录告警但继续写入
            logger.error(f"落沙锁 3 次重试均超时（15s），强制写入（可能并发冲突）")

        try:
            # P0-1：锁内幂等去重（同一 sender+text 时间窗内只写一次）
            if _dedup_check_and_mark(sender, text):
                return True
            with open(_SANDGLASS, "a", encoding="utf-8") as f:
                f.write(line)
        finally:
            try:
                os.unlink(lock)
            except OSError as e:
                logger.warning(f"锁文件清理失败（可能残留，下次会超时自愈）: {e}")
                try:
                    if os.path.exists(lock):
                        os.remove(lock)
                except Exception as e:
                    logger.warning(f"锁文件二次删除也失败: {e}")

        # 影子沙——落沙后同步索引
        try:
            from shadow_sand import shadow_index
            shadow_index(text, line_num=0)
        except Exception as e:
            logger.warning(f"影子沙索引同步跳过(锁冲突): {e}")

        # 知识图谱——落沙后提取三元组 (V2.9.3-dev)
        if sender == "user":
            try:
                from weavethread import wthread_store
                wthread_store(text, line_num=0)
            except Exception:
                pass

        return True
    except Exception as e:
        logger.error(f"沙漏写入失败: {e}")
        return False


def log_conversation(user_msg: str, agent_msg: str) -> int:
    """写入一轮对话（用户+Agent）。返回新写入的行数。"""
    count = 0
    if user_msg:
        if log_message(user_msg, sender="user"): count += 1
    if agent_msg:
        if log_message(agent_msg, sender="agent"): count += 1
    return count
