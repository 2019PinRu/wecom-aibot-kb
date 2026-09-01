# -*- coding: utf-8 -*-
"""检索应答引擎：回复格式化、前缀拼接与无结果记录。"""
import logging
import sqlite3

from models.db import execute
from retriever.fts5 import search

logger = logging.getLogger(__name__)


def format_reply(prefix: str, content: str, chattype: str, asker: str | None = None) -> str:
    """按会话类型拼接回复文本，群聊追加 @提问人 前缀。

    参数：
        prefix: 回复前缀。
        content: 回复正文。
        chattype: 会话类型（single/group）。
        asker: 提问人 userid（群聊时用于 @ 强提醒变通前缀）。

    返回：
        格式化后的回复文本。
    """
    text = f"{prefix}{content}"
    if chattype == "group" and asker:
        text = f"@{asker} {text}"
    return text


class Responder:
    """检索应答器：承接单聊直答与群聊聚合完成，统一做检索与回复。"""

    def __init__(self, config, reply_client, db_path: str) -> None:
        """初始化应答器。

        参数：
            config: 配置对象（Config 实例）。
            reply_client: 回复客户端（ReplyClient 实例）。
            db_path: SQLite 数据库文件路径。
        """
        self._config = config
        self._reply_client = reply_client
        self._db_path = db_path

    async def handle_question(self, question: str, req_id: str, meta: dict) -> None:
        """统一入口：检索 → 置信度判定 → 回复或记录待补充问题。

        参数：
            question: 完整问题文本。
            req_id: 消息回调 headers.req_id，回复时透传。
            meta: 元信息 dict（chattype/chatid/userid/asker）。
        """
        chattype = meta.get("chattype", "single")
        asker = meta.get("asker") or meta.get("userid")
        hits = search(
            self._db_path,
            question,
            self._config.get("retriever.top_k", 5),
            self._config.get("retriever.score_threshold", 0.3),
        )
        if not hits:
            self._record_pending(question, chattype, meta)
            content = self._config.get("reply.no_answer", "抱歉，暂时没有找到相关答案。")
        else:
            content = hits[0]["content"] or hits[0]["title"]
        text = format_reply(self._config.get("reply.prefix", "[AI自动回复]："), content, chattype, asker)
        await self._reply_client.reply(req_id, text)

    def _record_pending(self, question: str, chattype: str, meta: dict) -> None:
        """记录待补充问题到 pending_questions 表。

        参数：
            question: 原始问题文本。
            chattype: 会话类型。
            meta: 元信息 dict（chatid/userid）。
        """
        sql = (
            "INSERT INTO pending_questions (question, asker, chattype, chatid) "
            "VALUES (?, ?, ?, ?)"
        )
        params = (question, meta.get("userid"), chattype, meta.get("chatid"))
        try:
            execute(self._db_path, sql, params)
        except sqlite3.Error as exc:
            logger.error("记录待补充问题失败: %s", exc)
